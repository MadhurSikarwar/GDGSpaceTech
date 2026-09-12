"""
OrbitalGuard Decision Agent Orchestrator — Phase 3 enhanced.

Orchestrates Phase 1 deterministic tools via an intelligent agent loop with
strict physical boundaries, safeguards against infinite loops, deterministic
candidate selection, and Phase 3 adaptive risk-tier-driven workflow branching.

Phase 3 additions:
- AdaptiveWorkflowManager guides action selection based on risk tier.
- Iterative maneuver generate -> evaluate -> reject/retry loop.
- Explicit failure states (NO_FEASIBLE_MANEUVER, PC_STILL_TOO_HIGH, etc.).
- Loop safety limits for retries, generation attempts, and total iterations.
- LLM context summary includes workflow state hints.
"""

import logging
from typing import Optional, Dict, Any, List

from shared.schemas.conjunction import ConjunctionCandidate
from shared.schemas.decision import ManeuverDecision, DecisionInfo, SimulationInfo
from shared.schemas.maneuver import ManeuverCandidates, ManeuverCandidate
from services.optimizer.app.agent.state import DecisionContext
from services.optimizer.app.agent.registry import ToolRegistry
from services.optimizer.app.agent.llm_client import LLMClient
from services.optimizer.app.agent.workflow import (
    AdaptiveWorkflowManager,
    WorkflowState,
    CandidateFailureReason,
)
from services.risk.app.database import get_conjunction_from_db, load_fixture_conjunctions

logger = logging.getLogger(__name__)


class DecisionAgentOrchestrator:
    """
    Intelligent agent orchestrator that dynamically selects and executes Phase 1
    deterministic tools, interprets evidence, and enforces strict physics constraints.

    Phase 3: Integrates AdaptiveWorkflowManager for risk-tier-driven branching
    and iterative maneuver candidate evaluation with explicit failure states.
    """

    def __init__(
        self,
        registry: Optional[ToolRegistry] = None,
        llm_client: Optional[LLMClient] = None,
        max_iterations: int = 8,
        max_maneuver_retries: int = 6,
    ):
        self.registry = registry or ToolRegistry()
        self.llm = llm_client or LLMClient()
        self.max_iterations = max_iterations
        self.max_maneuver_retries = max_maneuver_retries
        self.active_contexts: Dict[str, DecisionContext] = {}

    def run(
        self,
        conjunction_id: Optional[str] = None,
        candidate_payload: Optional[ConjunctionCandidate] = None,
        preloaded_candidates: Optional[ManeuverCandidates] = None,
        llm_override: Optional[Any] = None,
    ) -> DecisionContext:
        """
        Execute the Decision Agent loop for a given conjunction encounter.
        Phase 3: Uses AdaptiveWorkflowManager to enforce risk-tier branching
        and the iterative maneuver retry loop.
        """
        # 1. Initialize context
        cid = conjunction_id or (candidate_payload.conjunction_id if candidate_payload else "UNKNOWN_CONJUNCTION")
        primary_id = candidate_payload.primary_object if candidate_payload else "25544"
        secondary_id = candidate_payload.secondary_object if candidate_payload else None

        # Resolve conjunction if missing
        conjunction = candidate_payload
        if conjunction is None and conjunction_id:
            conjunction = get_conjunction_from_db(conjunction_id)
            if not conjunction:
                fixtures = load_fixture_conjunctions()
                for f in fixtures:
                    if f.conjunction_id == conjunction_id:
                        conjunction = f
                        break
            if conjunction:
                primary_id = conjunction.primary_object
                secondary_id = conjunction.secondary_object

        from services.propagation.app.database.repository import DatabaseRepository, SessionLocal
        session = SessionLocal()
        try:
            repo = DatabaseRepository(session)
            db_feedbacks = repo.get_decision_feedback_for_conjunction(cid)
            historical_feedback = [{"maneuver_id": f.maneuver_id, "status": f.status, "reason": f.reason} for f in db_feedbacks]
        finally:
            session.close()

        context = DecisionContext(
            conjunction_id=cid,
            primary_object_id=primary_id,
            secondary_object_id=secondary_id,
            conjunction_candidate=conjunction,
            maneuver_candidates=preloaded_candidates,
            status="ANALYZING",
            max_iterations=self.max_iterations,
            max_maneuver_retries=self.max_maneuver_retries,
            historical_feedback=historical_feedback,
        )

        # Register active context for real-time activity trace polling
        self.active_contexts[cid] = context

        # Fast-path: If preloaded candidates list is explicitly empty AND no conjunction
        # data is available, there is nothing to analyze — return immediately.
        # This preserves backward-compatible behavior for empty-payload calls.
        if (
            preloaded_candidates is not None
            and len(preloaded_candidates.candidates) == 0
            and conjunction is None
        ):
            context.selected_maneuver_id = "NONE"
            context.status = "RECOMMENDATION_GENERATED"
            context.human_approval_required = False
            context.workflow_state = WorkflowState.REQUIRED_DATA_UNAVAILABLE.value
            context.explanation = (
                "No candidates provided and conjunction data unavailable; "
                "no maneuver analysis can be performed."
            )
            return context

        # 2. Initialize Phase 3 adaptive workflow manager (one per orchestrator run)
        workflow = AdaptiveWorkflowManager(
            max_maneuver_retries=self.max_maneuver_retries,
            max_workflow_iterations=self.max_iterations,
        )

        seen_tool_calls = set()

        # 3. Agent Decision Loop
        while context.iteration_count < self.max_iterations:
            context.iteration_count += 1

            # Sync workflow state into context for observability
            context.workflow_state = workflow.workflow_state.value
            workflow.annotate_context_with_workflow_state(context)

            # After risk is known, update context.risk_tier
            if context.risk_assessment and context.risk_tier is None:
                context.risk_tier = context.risk_assessment.risk_level

            # Process any newly evaluated constraints into the workflow's candidate records
            self._sync_constraint_evaluations_to_workflow(context, workflow)

            # Build context summary including Phase 3 workflow hints
            summary = self._build_context_summary(context, workflow)

            # Query LLM (or override) for next action
            llm_action = self.llm.query_decision(
                context_summary=summary,
                available_tools=self.registry.get_definitions(),
                tool_history=[t.model_dump() for t in context.tool_history],
                llm_override=llm_override,
            )

            # If LLM is unavailable, use Phase 3 adaptive deterministic fallback
            if not llm_action:
                logger.info(
                    f"LLM did not provide action at iteration {context.iteration_count}; "
                    "executing Phase 3 adaptive deterministic step."
                )
                llm_action = workflow.next_deterministic_action(context)

            action_type = llm_action.get("action")

            # --- BRANCH A: TOOL INVOCATION ---
            if action_type == "call_tool":
                tool_name = llm_action.get("tool_name", "")
                tool_args = llm_action.get("tool_args", {})

                # Phase 3: Check if this tool is appropriate for the current risk tier
                # (warn but do not block — the LLM may have a valid reason in MEDIUM tier)
                tier = workflow.get_risk_tier(context)
                if tier and not workflow.is_tool_allowed_for_context(tool_name, context):
                    logger.warning(
                        f"Tool '{tool_name}' is not in the allowed set for {tier} risk tier. "
                        "Agent may be deviating from the expected workflow — proceeding but logging."
                    )

                # Safeguard: Prevent exact duplicate tool calls in a loop
                call_sig = (tool_name, str(sorted(tool_args.items())))
                if call_sig in seen_tool_calls:
                    logger.warning(f"Duplicate tool call detected for {tool_name}; using workflow step instead.")
                    # Phase 3: Use adaptive next action instead of forcing candidate eval
                    fallback_action = workflow.next_deterministic_action(context)
                    if fallback_action.get("action") == "make_decision":
                        proposed_maneuver_id = fallback_action.get("selected_maneuver_id")
                        proposed_explanation = fallback_action.get("explanation")
                        self._finalize_decision(context, workflow, proposed_maneuver_id, proposed_explanation)
                        break
                    # Try the fallback tool call
                    tool_name = fallback_action.get("tool_name", tool_name)
                    tool_args = fallback_action.get("tool_args", tool_args)
                    new_sig = (tool_name, str(sorted(tool_args.items())))
                    if new_sig in seen_tool_calls:
                        logger.warning("Fallback tool also duplicated; forcing finalization.")
                        self._finalize_decision(context, workflow, None, None)
                        break

                seen_tool_calls.add(call_sig)

                # Ensure required IDs are passed
                if tool_name in ("assess_risk", "generate_maneuver_candidates") and "conjunction_id" not in tool_args:
                    tool_args["conjunction_id"] = context.conjunction_id
                if tool_name == "check_ground_station_visibility" and "catalog_id" not in tool_args:
                    tool_args["catalog_id"] = context.primary_object_id
                if tool_name == "evaluate_maneuver_constraints" and "conjunction_id" not in tool_args:
                    tool_args["conjunction_id"] = context.conjunction_id

                # Execute tool via registry
                success, result, tool_summary = self.registry.execute(tool_name, tool_args, context)

                # Phase 3: After constraint evaluation, sync results into workflow tracker
                if tool_name == "evaluate_maneuver_constraints" and success and result:
                    self._sync_constraint_evaluations_to_workflow(context, workflow)
                    # Check if all candidates are now exhausted and none feasible
                    if workflow.all_candidates_exhausted(context) and not workflow.has_feasible_candidate(context):
                        # Check retry budget
                        if workflow.should_retry_maneuver(context):
                            workflow.increment_retry()
                            context.maneuver_retry_count = workflow.maneuver_retry_count
                            workflow.transition_to(WorkflowState.ITERATING_RETRY)
                        # else will be caught on next iteration by next_deterministic_action

                # Phase 3: Track generation attempts
                if tool_name == "generate_maneuver_candidates" and success:
                    context.candidate_generation_attempts = workflow._generation_attempts

            # --- BRANCH B: FINAL DECISION REQUESTED ---
            elif action_type == "make_decision":
                proposed_maneuver_id = llm_action.get("selected_maneuver_id")
                proposed_explanation = llm_action.get("explanation")

                # Phase 3: For LOW risk decisions, bypass constraint evaluation
                tier = workflow.get_risk_tier(context)
                if proposed_maneuver_id == "NONE" and tier == "LOW":
                    context.selected_maneuver_id = "NONE"
                    context.status = "RECOMMENDATION_GENERATED"
                    context.human_approval_required = False
                    context.workflow_state = WorkflowState.MONITOR_ONLY.value
                    context.explanation = proposed_explanation or (
                        f"Risk tier is LOW (score={context.risk_assessment.risk_score if context.risk_assessment else 'N/A'}/100). "
                        "No avoidance maneuver required. Continued routine monitoring recommended."
                    )
                    break

                self._finalize_decision(context, workflow, proposed_maneuver_id, proposed_explanation)
                break

            else:
                logger.warning(f"Unrecognized agent action: {action_type}")
                # Phase 3: Use adaptive step instead of blind deterministic step
                fallback = workflow.next_deterministic_action(context)
                if fallback.get("action") == "make_decision":
                    self._finalize_decision(context, workflow, fallback.get("selected_maneuver_id"), fallback.get("explanation"))
                    break

        # 4. If loop terminated due to iteration limit without final decision
        if context.status == "ANALYZING":
            if context.iteration_count >= self.max_iterations:
                logger.warning("Decision agent reached maximum iterations without completing analysis.")
                context.failure_state = CandidateFailureReason.MAX_ITERATIONS_REACHED.value
                context.workflow_state = WorkflowState.MAX_ITERATIONS_REACHED.value
                self._finalize_decision(context, workflow, None, None)

        # Final sync
        context.workflow_state = workflow.workflow_state.value
        workflow.annotate_context_with_workflow_state(context)
        if context.risk_assessment and context.risk_tier is None:
            context.risk_tier = context.risk_assessment.risk_level

        return context

    def _sync_constraint_evaluations_to_workflow(
        self,
        context: DecisionContext,
        workflow: AdaptiveWorkflowManager,
    ):
        """
        Synchronise the context's constraint_evaluations dict into the workflow's
        candidate records so the retry logic has accurate per-candidate outcomes.
        """
        for candidate_id, evaluation in context.constraint_evaluations.items():
            # Skip if already recorded
            existing = workflow.get_candidate_record(candidate_id)
            if existing and existing.evaluated:
                continue

            record = workflow.record_candidate_result(
                candidate_id=candidate_id,
                feasible=evaluation.is_feasible,
                evaluation=evaluation,
                context=context,
            )

            # Propagate failure reason into context for observability
            if not evaluation.is_feasible and record.failure_reason:
                context.candidate_failure_reasons[candidate_id] = record.failure_reason.value

    def _build_context_summary(self, context: DecisionContext, workflow: AdaptiveWorkflowManager) -> str:
        """Format current verified evidence into a clear text summary for LLM,
        including Phase 3 workflow state hints."""
        lines = [
            f"Conjunction ID: {context.conjunction_id} "
            f"(Primary: {context.primary_object_id}, Secondary: {context.secondary_object_id or 'Unknown'})"
        ]

        if context.conjunction_candidate:
            c = context.conjunction_candidate
            lines.append(
                f"Encounter TCA: {c.tca} | Miss Distance: {c.closest_approach.distance_km} km | "
                f"Relative Velocity: {c.closest_approach.relative_velocity_km_s} km/s"
            )

        if context.risk_assessment:
            r = context.risk_assessment
            lines.append(
                f"Risk Assessment: Score {r.risk_score}/100 -> Tier {r.risk_level} "
                f"(Pc: {r.collision_probability})"
            )
        else:
            lines.append("Risk Assessment: NOT YET EVALUATED")

        if context.space_weather:
            w = context.space_weather
            lines.append(f"Space Weather: Kp={w.kp_index}, Ap={w.ap_index}, Drag Scalar={w.drag_activity_scalar:.2f}")

        if context.maneuver_candidates and context.maneuver_candidates.candidates:
            lines.append(
                f"Maneuver Options: {len(context.maneuver_candidates.candidates)} candidates "
                f"({[c.maneuver_id for c in context.maneuver_candidates.candidates]})"
            )
        else:
            lines.append("Maneuver Options: NOT YET GENERATED")

        if context.constraint_evaluations:
            lines.append(f"Evaluated Constraints for: {list(context.constraint_evaluations.keys())}")
            lines.append(f"  -> Feasible: {context.feasible_candidate_ids}")
            lines.append(f"  -> Infeasible: {context.infeasible_candidate_ids}")
            for m_id, reason in context.rejection_reasons.items():
                lines.append(f"     * {m_id} REJECTED: {reason}")
        else:
            lines.append("Constraint Evaluations: NOT YET EVALUATED")

        if context.historical_feedback:
            lines.append("Historical Human Feedback:")
            for fb in context.historical_feedback:
                lines.append(f"  - Maneuver {fb['maneuver_id']} was {fb['status']} (Reason: {fb['reason']})")

        # Phase 3: Append workflow state hint
        lines.append("")
        lines.append(workflow.build_workflow_context_hint(context))

        return "\n".join(lines)

    def _finalize_decision(
        self,
        context: DecisionContext,
        workflow: AdaptiveWorkflowManager,
        proposed_candidate_id: Optional[str],
        proposed_explanation: Optional[str],
    ):
        """
        Deterministically selects the optimal feasible maneuver and verifies explanation.
        STRICT PHYSICAL BOUNDARY: LLM cannot declare an infeasible candidate feasible!
        Phase 3: Updates workflow state and failure_state fields explicitly.
        """
        # LOW risk or NONE explicitly requested
        if proposed_candidate_id == "NONE":
            context.selected_maneuver_id = "NONE"
            context.status = "RECOMMENDATION_GENERATED"
            context.human_approval_required = False
            context.workflow_state = WorkflowState.MONITOR_ONLY.value if (
                context.risk_assessment and context.risk_assessment.risk_level == "LOW"
            ) else workflow.workflow_state.value
            context.explanation = proposed_explanation or "No maneuver required based on current risk assessment."
            return

        # Explicit NO_FEASIBLE_MANEUVER from workflow
        if proposed_candidate_id == "NO_FEASIBLE_MANEUVER":
            context.selected_maneuver_id = "NO_FEASIBLE_MANEUVER"
            context.status = "NO_FEASIBLE_MANEUVER"
            context.failure_state = CandidateFailureReason.NO_FEASIBLE_MANEUVER.value
            context.human_approval_required = True
            context.workflow_state = WorkflowState.NO_FEASIBLE_MANEUVER.value
            reasons = [f"{m_id}: {r}" for m_id, r in context.rejection_reasons.items()]
            context.explanation = proposed_explanation or (
                f"NO FEASIBLE MANEUVER: All evaluated candidates violate operational constraints. "
                f"Violations: {'; '.join(reasons)}. Human flight director intervention required."
            )
            return

        # 1. LOW risk guard (even if no explicit NONE was sent)
        if (context.risk_assessment
                and context.risk_assessment.risk_level == "LOW"
                and (context.risk_assessment.collision_probability is None
                     or context.risk_assessment.collision_probability < 1e-5)):
            context.selected_maneuver_id = "NONE"
            context.status = "RECOMMENDATION_GENERATED"
            context.human_approval_required = False
            context.workflow_state = WorkflowState.MONITOR_ONLY.value
            context.explanation = proposed_explanation or (
                "Conjunction risk is within acceptable safe clearance limits; no avoidance burn is necessary."
            )
            return

        # 2. Ensure constraints have been evaluated
        if context.maneuver_candidates and context.maneuver_candidates.candidates:
            if not context.constraint_evaluations:
                self.registry.execute(
                    "evaluate_maneuver_constraints",
                    {"conjunction_id": context.conjunction_id},
                    context,
                )
                self._sync_constraint_evaluations_to_workflow(context, workflow)

            all_candidates = context.maneuver_candidates.candidates
            rejected_maneuver_ids = {fb["maneuver_id"] for fb in context.historical_feedback if fb["status"] == "REJECTED" and fb["maneuver_id"]}
            
            physically_feasible_candidates = [
                c for c in all_candidates
                if context.constraint_evaluations.get(c.maneuver_id)
                and context.constraint_evaluations[c.maneuver_id].is_feasible
            ]
            
            feasible_candidates = [
                c for c in physically_feasible_candidates
                if c.maneuver_id not in rejected_maneuver_ids
            ]

            # Case A: No physically feasible candidates at all
            if not physically_feasible_candidates:
                context.selected_maneuver_id = "NO_FEASIBLE_MANEUVER"
                context.status = "NO_FEASIBLE_MANEUVER"
                context.failure_state = CandidateFailureReason.NO_FEASIBLE_MANEUVER.value
                context.human_approval_required = True
                context.workflow_state = WorkflowState.NO_FEASIBLE_MANEUVER.value
                reasons = [f"{m_id}: {r}" for m_id, r in context.rejection_reasons.items()]
                context.explanation = (
                    f"NO FEASIBLE MANEUVER: All evaluated candidates violate operational constraints. "
                    f"Violations: {'; '.join(reasons)}. Human flight director intervention required."
                )
                return

            # Case B: Feasible candidates exist
            # Verify LLM's proposed candidate is actually physically feasible
            chosen = None
            if proposed_candidate_id and proposed_candidate_id not in ("NONE", "NO_FEASIBLE_MANEUVER"):
                chosen = next(
                    (c for c in physically_feasible_candidates if c.maneuver_id == proposed_candidate_id),
                    None,
                )
                if not chosen:
                    logger.warning(
                        f"LLM proposed candidate '{proposed_candidate_id}' is NOT physically feasible or does not exist. "
                        "Overriding with authoritative deterministic minimum delta-V solution."
                    )

            # Authoritative deterministic selection: prefer LOW resulting_risk, minimum delta-V
            if not chosen:
                pool = feasible_candidates if feasible_candidates else physically_feasible_candidates
                low_risk_viable = [c for c in pool if c.resulting_risk == "LOW"]
                if low_risk_viable:
                    chosen = min(low_risk_viable, key=lambda c: c.delta_v_m_s)
                else:
                    chosen = min(pool, key=lambda c: c.delta_v_m_s)

            context.selected_candidate = chosen
            context.selected_maneuver_id = chosen.maneuver_id
            context.status = "AWAITING_HUMAN_APPROVAL"
            context.human_approval_required = True
            context.workflow_state = WorkflowState.RECOMMENDATION_READY.value

            eval_info = context.constraint_evaluations.get(chosen.maneuver_id)
            drift_str = (
                f"with {eval_info.sma_drift_km:.2f} km slot drift"
                if eval_info and eval_info.sma_drift_km is not None else ""
            )
            pc_str = (
                f"predicted Pc={chosen.predicted_pc:.2e}"
                if chosen.predicted_pc is not None
                else f"resulting risk {chosen.resulting_risk}"
            )

            default_explanation = (
                f"Selected {chosen.maneuver_id} ({chosen.burn_direction} {chosen.delta_v_m_s:.2f} m/s) "
                f"providing {chosen.new_separation_km:.1f} km post-burn separation ({pc_str}) {drift_str}. "
                f"All physical and slot retention constraints are verified satisfied. Escalated for human approval."
            )
            
            # If the LLM didn't provide a valid explanation, use the default
            final_explanation = proposed_explanation if proposed_explanation else default_explanation
            
            # Explicitly append/prepend notice if we are forced to retain a rejected option
            if chosen.maneuver_id in rejected_maneuver_ids:
                rejection_note = (
                    f"[NOTE: Retaining {chosen.maneuver_id}] Unable to satisfy rejection criteria "
                    f"because the physics engine did not generate any safer or further options. "
                    f"This remains the best available option ({chosen.new_separation_km:.1f} km). "
                )
                if not proposed_explanation or "Unable to satisfy" not in proposed_explanation:
                    final_explanation = rejection_note + final_explanation

            context.explanation = final_explanation
        else:
            # No candidates available
            context.selected_maneuver_id = "NONE"
            context.status = "INCOMPLETE_ANALYSIS"
            context.human_approval_required = True
            context.workflow_state = WorkflowState.REQUIRED_DATA_UNAVAILABLE.value
            context.failure_state = CandidateFailureReason.REQUIRED_DATA_UNAVAILABLE.value
            context.explanation = "Unable to complete maneuver decision analysis; no candidate options available."

    def to_maneuver_decision(self, context: DecisionContext) -> ManeuverDecision:
        """Convert DecisionContext into the standardized ManeuverDecision Pydantic schema."""
        recommended_id = context.selected_maneuver_id or "NONE"
        reason_text = context.explanation or "Decision completed."
        new_dist = context.selected_candidate.new_separation_km if context.selected_candidate else None

        return ManeuverDecision(
            conjunction_id=context.conjunction_id,
            decision=DecisionInfo(
                recommended_maneuver_id=recommended_id,
                reason=reason_text,
            ),
            human_approval_required=context.human_approval_required,
            simulation=SimulationInfo(
                status="PENDING" if context.human_approval_required else "EXECUTED",
                new_tca_distance_km=new_dist,
            )
        )
