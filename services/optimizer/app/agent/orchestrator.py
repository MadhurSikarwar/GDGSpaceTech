"""
OrbitalGuard Decision Agent Orchestrator.
Orchestrates Phase 1 deterministic tools via an intelligent agent loop with
strict physical boundaries, safeguards against infinite loops, and deterministic candidate selection.
"""

import logging
from typing import Optional, Dict, Any, List

from shared.schemas.conjunction import ConjunctionCandidate
from shared.schemas.decision import ManeuverDecision, DecisionInfo, SimulationInfo
from shared.schemas.maneuver import ManeuverCandidates, ManeuverCandidate
from services.optimizer.app.agent.state import DecisionContext
from services.optimizer.app.agent.registry import ToolRegistry
from services.optimizer.app.agent.llm_client import LLMClient
from services.risk.app.database import get_conjunction_from_db, load_fixture_conjunctions

logger = logging.getLogger(__name__)


class DecisionAgentOrchestrator:
    """
    Intelligent agent orchestrator that dynamically selects and executes Phase 1
    deterministic tools, interprets evidence, and enforces strict physics constraints.
    """

    def __init__(
        self,
        registry: Optional[ToolRegistry] = None,
        llm_client: Optional[LLMClient] = None,
        max_iterations: int = 8,
    ):
        self.registry = registry or ToolRegistry()
        self.llm = llm_client or LLMClient()
        self.max_iterations = max_iterations

    def run(
        self,
        conjunction_id: Optional[str] = None,
        candidate_payload: Optional[ConjunctionCandidate] = None,
        preloaded_candidates: Optional[ManeuverCandidates] = None,
        llm_override: Optional[Any] = None,
    ) -> DecisionContext:
        """
        Execute the Decision Agent loop for a given conjunction encounter.
        """
        # 1. Initialize context
        cid = conjunction_id or (candidate_payload.conjunction_id if candidate_payload else "UNKNOWN_CONJUNCTION")
        primary_id = candidate_payload.primary_object if candidate_payload else "25544"
        secondary_id = candidate_payload.secondary_object if candidate_payload else None

        # Resolve candidate if missing
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

        context = DecisionContext(
            conjunction_id=cid,
            primary_object_id=primary_id,
            secondary_object_id=secondary_id,
            conjunction_candidate=conjunction,
            maneuver_candidates=preloaded_candidates,
            status="ANALYZING",
            max_iterations=self.max_iterations,
        )

        seen_tool_calls = set()

        # 2. Agent Decision Loop
        while context.iteration_count < self.max_iterations:
            context.iteration_count += 1

            # Prepare current context summary for reasoning
            summary = self._build_context_summary(context)

            # Query LLM (or mock) for next action
            llm_action = self.llm.query_decision(
                context_summary=summary,
                available_tools=self.registry.get_definitions(),
                tool_history=[t.model_dump() for t in context.tool_history],
                llm_override=llm_override,
            )

            # If LLM is unavailable or failed, trigger graceful deterministic fallback
            if not llm_action:
                logger.info(f"LLM did not provide an action at iteration {context.iteration_count}; executing deterministic fallback step.")
                should_continue = self._deterministic_step(context)
                if not should_continue:
                    break
                continue

            action_type = llm_action.get("action")

            # --- BRANCH A: TOOL INVOCATION ---
            if action_type == "call_tool":
                tool_name = llm_action.get("tool_name", "")
                tool_args = llm_action.get("tool_args", {})

                # Safeguard: Prevent exact duplicate tool calls in a loop
                call_sig = (tool_name, str(sorted(tool_args.items())))
                if call_sig in seen_tool_calls:
                    logger.warning(f"Duplicate tool call detected for {tool_name}; breaking duplicate loop.")
                    # Force moving towards candidate evaluation or finalization
                    self._deterministic_step(context)
                    continue

                seen_tool_calls.add(call_sig)

                # Ensure required conjunction_id or catalog_id is passed if omitted by LLM
                if tool_name in ("assess_risk", "generate_maneuver_candidates") and "conjunction_id" not in tool_args:
                    tool_args["conjunction_id"] = context.conjunction_id
                if tool_name == "check_ground_station_visibility" and "catalog_id" not in tool_args:
                    tool_args["catalog_id"] = context.primary_object_id
                if tool_name == "evaluate_maneuver_constraints" and "conjunction_id" not in tool_args:
                    tool_args["conjunction_id"] = context.conjunction_id

                # Execute tool via registry
                success, result, tool_summary = self.registry.execute(tool_name, tool_args, context)

                # Continue next iteration to evaluate result

            # --- BRANCH B: FINAL DECISION REQUESTED BY LLM ---
            elif action_type == "make_decision":
                proposed_maneuver_id = llm_action.get("selected_maneuver_id")
                proposed_explanation = llm_action.get("explanation")
                self._finalize_decision(context, proposed_maneuver_id, proposed_explanation)
                break

            else:
                logger.warning(f"Unrecognized agent action: {action_type}")
                self._deterministic_step(context)

        # 3. If loop terminated due to iteration limit without final decision
        if context.status == "ANALYZING":
            if context.iteration_count >= self.max_iterations:
                logger.warning("Decision agent reached maximum iterations without completing analysis.")
                self._finalize_decision(context, None, None)

        return context

    def _build_context_summary(self, context: DecisionContext) -> str:
        """Format current verified evidence into a clear text summary for LLM."""
        lines = [f"Conjunction ID: {context.conjunction_id} (Primary: {context.primary_object_id}, Secondary: {context.secondary_object_id or 'Unknown'})"]

        if context.conjunction_candidate:
            c = context.conjunction_candidate
            lines.append(f"Encounter TCA: {c.tca} | Miss Distance: {c.closest_approach.distance_km} km | Relative Velocity: {c.closest_approach.relative_velocity_km_s} km/s")

        if context.risk_assessment:
            r = context.risk_assessment
            lines.append(f"Risk Assessment: Score {r.risk_score}/100 -> Tier {r.risk_level} (Pc: {r.collision_probability})")
        else:
            lines.append("Risk Assessment: NOT YET EVALUATED")

        if context.space_weather:
            w = context.space_weather
            lines.append(f"Space Weather: Kp={w.kp_index}, Ap={w.ap_index}, Drag Scalar={w.drag_activity_scalar:.2f}")

        if context.maneuver_candidates and context.maneuver_candidates.candidates:
            lines.append(f"Maneuver Options: {len(context.maneuver_candidates.candidates)} candidates available ({[c.maneuver_id for c in context.maneuver_candidates.candidates]})")
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

        return "\n".join(lines)

    def _deterministic_step(self, context: DecisionContext) -> bool:
        """
        Executes the next logically required deterministic tool when LLM is unavailable or needs guidance:
        1. assess_risk -> 2. generate_maneuver_candidates -> 3. evaluate_maneuver_constraints -> finalize
        """
        # Step 1: Ensure risk assessment is performed
        if not context.risk_assessment:
            success, _, _ = self.registry.execute("assess_risk", {"conjunction_id": context.conjunction_id}, context)
            return True

        # Check if risk is LOW (no maneuver required)
        if context.risk_assessment.risk_level == "LOW" and (context.risk_assessment.collision_probability is None or context.risk_assessment.collision_probability < 1e-5):
            self._finalize_decision(context, "NONE", "No maneuver required. Conjunction risk is already within acceptable limits.")
            return False

        # Step 2: Ensure maneuver candidates are generated
        if not context.maneuver_candidates or not context.maneuver_candidates.candidates:
            success, _, _ = self.registry.execute("generate_maneuver_candidates", {"conjunction_id": context.conjunction_id, "satellite_id": context.primary_object_id}, context)
            return True

        # Step 3: Ensure maneuver constraints are evaluated
        if not context.constraint_evaluations:
            success, _, _ = self.registry.execute("evaluate_maneuver_constraints", {"conjunction_id": context.conjunction_id}, context)
            return True

        # Step 4: Finalize decision
        self._finalize_decision(context, None, None)
        return False

    def _finalize_decision(
        self,
        context: DecisionContext,
        proposed_candidate_id: Optional[str],
        proposed_explanation: Optional[str],
    ):
        """
        Deterministically selects the optimal feasible maneuver and verifies explanation.
        STRICT PHYSICAL BOUNDARY: LLM cannot declare an infeasible candidate feasible!
        """
        # 1. If risk is LOW or empty candidates
        if context.risk_assessment and context.risk_assessment.risk_level == "LOW" and (context.risk_assessment.collision_probability is None or context.risk_assessment.collision_probability < 1e-5):
            context.selected_maneuver_id = "NONE"
            context.status = "RECOMMENDATION_GENERATED"
            context.human_approval_required = False
            context.explanation = proposed_explanation or "Conjunction risk is within acceptable safe clearance limits; no avoidance burn is necessary."
            return

        # 2. Ensure constraints have been evaluated for all candidates
        if context.maneuver_candidates and context.maneuver_candidates.candidates:
            if not context.constraint_evaluations:
                self.registry.execute("evaluate_maneuver_constraints", {"conjunction_id": context.conjunction_id}, context)

            all_candidates = context.maneuver_candidates.candidates
            feasible_candidates = [
                c for c in all_candidates
                if context.constraint_evaluations.get(c.maneuver_id) and context.constraint_evaluations[c.maneuver_id].is_feasible
            ]

            # Case A: No feasible candidates exist!
            if not feasible_candidates:
                context.selected_maneuver_id = "NO_FEASIBLE_MANEUVER"
                context.status = "NO_FEASIBLE_MANEUVER"
                context.human_approval_required = True
                reasons = [f"{m_id}: {r}" for m_id, r in context.rejection_reasons.items()]
                context.explanation = (
                    f"NO FEASIBLE MANEUVER: All evaluated candidates violate operational constraints. "
                    f"Violations: {'; '.join(reasons)}. Human flight director intervention required."
                )
                return

            # Case B: Feasible candidates exist
            # Check if LLM proposed a valid, FEASIBLE candidate
            chosen = None
            if proposed_candidate_id and proposed_candidate_id != "NONE":
                chosen = next((c for c in feasible_candidates if c.maneuver_id == proposed_candidate_id), None)
                if not chosen:
                    logger.warning(
                        f"LLM proposed candidate '{proposed_candidate_id}' which is NOT feasible or does not exist. "
                        f"Overriding with authoritative deterministic minimum delta-V solution."
                    )

            # Authoritative deterministic selection:
            # Pick candidate that achieves LOW risk with minimum delta-V
            if not chosen:
                low_risk_viable = [c for c in feasible_candidates if c.resulting_risk == "LOW"]
                if low_risk_viable:
                    chosen = min(low_risk_viable, key=lambda c: c.delta_v_m_s)
                else:
                    chosen = min(feasible_candidates, key=lambda c: c.delta_v_m_s)

            context.selected_candidate = chosen
            context.selected_maneuver_id = chosen.maneuver_id
            context.status = "AWAITING_HUMAN_APPROVAL"
            context.human_approval_required = True

            # Use LLM explanation if factual, or build authoritative factual explanation
            eval_info = context.constraint_evaluations.get(chosen.maneuver_id)
            drift_str = f"with {eval_info.sma_drift_km:.2f} km slot drift" if eval_info and eval_info.sma_drift_km is not None else ""
            pc_str = f"predicted Pc={chosen.predicted_pc:.2e}" if chosen.predicted_pc is not None else f"resulting risk {chosen.resulting_risk}"

            default_explanation = (
                f"Selected {chosen.maneuver_id} ({chosen.burn_direction} {chosen.delta_v_m_s:.2f} m/s) "
                f"providing {chosen.new_separation_km:.1f} km post-burn separation ({pc_str}) {drift_str}. "
                f"All physical and slot retention constraints are verified satisfied. Escalated for human approval."
            )

            context.explanation = proposed_explanation if (proposed_explanation and chosen.maneuver_id in proposed_explanation) else default_explanation
        else:
            # No candidates available and unable to generate
            context.selected_maneuver_id = "NONE"
            context.status = "INCOMPLETE_ANALYSIS"
            context.human_approval_required = True
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
