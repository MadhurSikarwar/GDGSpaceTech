"""
Tool Registry for Decision Agent.
Registers the Phase 1 deterministic tools with type schemas and safe execution wrappers.
"""

import logging
from typing import Dict, Any, Callable, Optional, Tuple
from pydantic import BaseModel, Field

from shared.tools import (
    get_object_state,
    propagate_trajectory,
    compute_pc,
    run_screening,
    evaluate_maneuver_constraints,
    check_ground_station_visibility,
    get_space_weather,
    ToolError,
)
import httpx
from shared.schemas.risk import RiskAssessment
from shared.schemas.maneuver import ManeuverCandidates
from services.optimizer.app.agent.state import DecisionContext, ToolCallRecord

logger = logging.getLogger(__name__)


class ToolDefinition(BaseModel):
    """Schema descriptor for a deterministic tool exposed to the Decision Agent."""
    name: str
    description: str
    parameters: Dict[str, Any]


class ToolRegistry:
    """Registry maintaining available Phase 1 tools and executing them deterministically."""

    def __init__(self):
        self._tools: Dict[str, Callable] = {}
        self._definitions: Dict[str, ToolDefinition] = {}
        self._register_phase1_tools()

    def _register_phase1_tools(self):
        # 1. get_object_state
        self.register(
            name="get_object_state",
            description="Retrieve the current or target-epoch propagated state vector (position, velocity, altitude) in TEME frame.",
            parameters={
                "type": "object",
                "properties": {
                    "catalog_id": {"type": "string", "description": "NORAD Catalog ID or unique object ID"}
                },
                "required": ["catalog_id"]
            },
            func=get_object_state
        )

        # 2. propagate_trajectory
        self.register(
            name="propagate_trajectory",
            description="Propagate future ephemeris trajectory for an object over a specified horizon in minutes.",
            parameters={
                "type": "object",
                "properties": {
                    "catalog_id": {"type": "string", "description": "Target NORAD catalog ID"},
                    "horizon_minutes": {"type": "integer", "description": "Forward minutes to propagate (default 90)", "default": 90},
                    "step_minutes": {"type": "number", "description": "Step interval in minutes (default 1.0)", "default": 1.0}
                },
                "required": ["catalog_id"]
            },
            func=propagate_trajectory
        )

        # 3. compute_pc
        self.register(
            name="compute_pc",
            description="Numerically integrate Foster (1992) 2D collision probability (Pc) over encounter plane covariance.",
            parameters={
                "type": "object",
                "properties": {
                    "conjunction_id": {"type": "string", "description": "Conjunction candidate identifier"},
                    "primary_catalog_id": {"type": "string", "description": "Primary satellite catalog ID"},
                    "secondary_catalog_id": {"type": "string", "description": "Secondary debris catalog ID"}
                }
            },
            func=compute_pc
        )

        # 4. run_screening
        self.register(
            name="run_screening",
            description="Execute 2-stage coarse altitude overlap and SGP4 fine conjunction screening.",
            parameters={
                "type": "object",
                "properties": {
                    "horizon_minutes": {"type": "integer", "description": "Screening horizon in minutes (default 90)", "default": 90},
                    "threshold_km": {"type": "number", "description": "Distance threshold in km (default 50.0)", "default": 50.0}
                }
            },
            func=run_screening
        )

        # 5. assess_risk
        self.register(
            name="assess_risk",
            description="Evaluate encounter hazard factors (separation, time-to-TCA, velocity, Pc) into 0-100 risk score and risk tier.",
            parameters={
                "type": "object",
                "properties": {
                    "conjunction_id": {"type": "string", "description": "Target conjunction candidate event ID"}
                },
                "required": ["conjunction_id"]
            },
            func=self._assess_risk_http
        )

        # 6. generate_maneuver_candidates
        self.register(
            name="generate_maneuver_candidates",
            description="Generate CW linearized SLSQP fuel-optimal and directional avoidance maneuver candidates.",
            parameters={
                "type": "object",
                "properties": {
                    "conjunction_id": {"type": "string", "description": "Conjunction candidate identifier to avoid"},
                    "satellite_id": {"type": "string", "description": "Primary satellite catalog ID"}
                },
                "required": ["conjunction_id"]
            },
            func=self._generate_maneuvers_http
        )

        # 7. evaluate_maneuver_constraints
        self.register(
            name="evaluate_maneuver_constraints",
            description="Evaluate a maneuver candidate against Δv bounds, Pc safety threshold, slot drift, and ground station visibility.",
            parameters={
                "type": "object",
                "properties": {
                    "conjunction_id": {"type": "string", "description": "Conjunction ID providing context"},
                    "maneuver_id": {"type": "string", "description": "ID of the specific maneuver candidate to test"},
                    "check_ground_station": {"type": "boolean", "description": "Whether to verify ground station visibility", "default": True}
                },
                "required": ["conjunction_id"]
            },
            func=self._evaluate_constraints_wrapper
        )

        # 8. check_ground_station_visibility
        self.register(
            name="check_ground_station_visibility",
            description="Calculate AOS/LOS line-of-sight visibility windows with polar ground stations (Svalbard, Fairbanks, McMurdo).",
            parameters={
                "type": "object",
                "properties": {
                    "catalog_id": {"type": "string", "description": "Satellite catalog ID to inspect for pass windows"},
                    "horizon_minutes": {"type": "integer", "description": "Observation horizon in minutes", "default": 90}
                },
                "required": ["catalog_id"]
            },
            func=check_ground_station_visibility
        )

        # 9. get_space_weather
        self.register(
            name="get_space_weather",
            description="Retrieve live NOAA space weather indices (Kp, Ap, F10.7) and thermospheric drag scalar.",
            parameters={
                "type": "object",
                "properties": {
                    "force_refresh": {"type": "boolean", "description": "Force live NOAA API refresh", "default": False}
                }
            },
            func=get_space_weather
        )

    def register(self, name: str, description: str, parameters: Dict[str, Any], func: Callable):
        self._tools[name] = func
        self._definitions[name] = ToolDefinition(name=name, description=description, parameters=parameters)

    def get_definitions(self) -> Dict[str, ToolDefinition]:
        return self._definitions

    def has_tool(self, name: str) -> bool:
        return name in self._tools

    def execute(self, name: str, args: Dict[str, Any], context: DecisionContext) -> Tuple[bool, Any, str]:
        """
        Execute a registered deterministic tool and update the DecisionContext.
        Guarantees that tool outputs cannot be fabricated and errors are captured cleanly.
        """
        if name not in self._tools:
            err_msg = f"Unknown tool '{name}'. Available tools: {list(self._tools.keys())}"
            record = ToolCallRecord(tool_name=name, arguments=args, success=False, summary=err_msg, error=err_msg)
            context.tool_history.append(record)
            return False, None, err_msg

        func = self._tools[name]
        try:
            if name == "evaluate_maneuver_constraints":
                res = func(args=args, context=context)
            elif name == "assess_risk" and "candidate" not in args and context.conjunction_candidate and args.get("conjunction_id") == context.conjunction_id:
                # HTTP wrapper expects the payload candidate and the args
                filtered_args = {k: v for k, v in args.items() if k != "conjunction_id"}
                res = func(candidate=context.conjunction_candidate, **filtered_args)
            elif name == "generate_maneuver_candidates" and "candidate" not in args and context.conjunction_candidate and args.get("conjunction_id") == context.conjunction_id:
                # HTTP wrapper expects the payload candidate and the args
                filtered_args = {k: v for k, v in args.items() if k != "conjunction_id"}
                res = func(candidate=context.conjunction_candidate, **filtered_args)
            else:
                res = func(**args)

            summary = self._summarize_and_integrate(name, res, context)
            record = ToolCallRecord(tool_name=name, arguments=args, success=True, summary=summary)
            context.tool_history.append(record)
            return True, res, summary

        except Exception as exc:
            err_msg = f"Tool '{name}' failed with error: {str(exc)}"
            logger.warning(err_msg)
            record = ToolCallRecord(tool_name=name, arguments=args, success=False, summary=err_msg, error=str(exc))
            context.tool_history.append(record)
            return False, None, err_msg

    def _evaluate_constraints_wrapper(self, args: Dict[str, Any], context: DecisionContext):
        """Wrapper for evaluate_maneuver_constraints to resolve candidates from context if needed."""
        maneuver_id = args.get("maneuver_id")
        check_gs = args.get("check_ground_station", True)
        
        # If no candidates currently in context, generate them first or fail
        if not context.maneuver_candidates or not context.maneuver_candidates.candidates:
            from shared.tools import generate_maneuver_candidates
            candidates = generate_maneuver_candidates(conjunction_id=context.conjunction_id)
            context.maneuver_candidates = candidates

        candidates_to_check = context.maneuver_candidates.candidates
        if maneuver_id:
            candidates_to_check = [c for c in candidates_to_check if c.maneuver_id == maneuver_id]
            if not candidates_to_check:
                raise ValueError(f"Maneuver ID '{maneuver_id}' not found among generated candidates.")

        evaluations = {}
        for c in candidates_to_check:
            ev = evaluate_maneuver_constraints(
                candidate=c,
                conjunction=context.conjunction_candidate,
                conjunction_id=context.conjunction_id,
                primary_catalog_id=context.primary_object_id,
                check_ground_station=check_gs,
            )
            evaluations[c.maneuver_id] = ev
            context.constraint_evaluations[c.maneuver_id] = ev

        return evaluations

    def _assess_risk_http(self, candidate=None, conjunction_id=None) -> RiskAssessment:
        # Request risk assessment from Risk Agent via HTTP
        with httpx.Client(timeout=10.0) as client:
            if candidate:
                resp = client.post("http://localhost:8001/assess-risk", json=candidate.model_dump(mode="json"))
            else:
                resp = client.post(f"http://localhost:8001/assess-risk?conjunction_id={conjunction_id}")
            resp.raise_for_status()
            return RiskAssessment.model_validate(resp.json())

    def _generate_maneuvers_http(self, candidate=None, conjunction_id=None, satellite_id=None) -> ManeuverCandidates:
        # Request maneuver generation from Maneuver Agent via HTTP
        with httpx.Client(timeout=30.0) as client:
            params = {}
            if not conjunction_id and candidate:
                conjunction_id = candidate.conjunction_id
            if conjunction_id:
                params["conjunction_id"] = conjunction_id
            if satellite_id:
                params["satellite_id"] = satellite_id
            
            # If we don't have a direct risk assessment body, Maneuver agent will fetch by conjunction_id
            resp = client.post("http://localhost:8002/generate-maneuvers", params=params)
            resp.raise_for_status()
            return ManeuverCandidates.model_validate(resp.json())

    def _summarize_and_integrate(self, name: str, result: Any, context: DecisionContext) -> str:
        """Deterministically assimilate tool output into DecisionContext."""
        if name == "get_space_weather":
            context.space_weather = result
            return f"Space weather: Kp={result.kp_index}, Ap={result.ap_index}, F10.7={result.f107_sfu} sfu, drag_scalar={result.drag_activity_scalar:.2f} ({result.activity_level}, live={result.live})"

        if name == "assess_risk":
            context.risk_assessment = result
            pc_str = f", Pc={result.collision_probability:.2e}" if result.collision_probability is not None else ""
            return f"Risk assessment: score={result.risk_score}/100, level={result.risk_level}, miss_dist={result.factors.closest_approach_km:.2f} km{pc_str}"

        if name == "compute_pc":
            context.pc_result = result
            return f"Collision probability Pc={result.probability_of_collision:.3e} (method={result.method})"

        if name == "generate_maneuver_candidates":
            context.maneuver_candidates = result
            count = len(result.candidates)
            c_summaries = [f"{c.maneuver_id}({c.burn_direction}, {c.delta_v_m_s}m/s -> {c.new_separation_km}km {c.resulting_risk})" for c in result.candidates[:3]]
            return f"Generated {count} candidates: {', '.join(c_summaries)}"

        if name == "evaluate_maneuver_constraints":
            feasible = [m_id for m_id, ev in result.items() if ev.is_feasible]
            infeasible = [m_id for m_id, ev in result.items() if not ev.is_feasible]
            context.feasible_candidate_ids = feasible
            context.infeasible_candidate_ids = infeasible
            for m_id, ev in result.items():
                if not ev.is_feasible:
                    reasons = []
                    if not ev.delta_v_satisfied: reasons.append(f"Δv {ev.delta_v_m_s}m/s > {ev.max_delta_v_m_s}m/s")
                    if ev.pc_satisfied is False: reasons.append(f"Pc {ev.predicted_pc} > {ev.pc_threshold}")
                    if ev.slot_drift_satisfied is False: reasons.append(f"drift {ev.sma_drift_km}km > {ev.max_sma_drift_km}km")
                    context.rejection_reasons[m_id] = ", ".join(reasons)
            return f"Evaluated constraints for {len(result)} candidates: {len(feasible)} feasible ({feasible}), {len(infeasible)} infeasible ({infeasible})"

        if name == "check_ground_station_visibility":
            context.ground_station_passes = result.passes
            return f"Found {len(result.passes)} ground station AOS/LOS visibility windows over {result.horizon_minutes}m"

        return f"Executed {name} successfully."
