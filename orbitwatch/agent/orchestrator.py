"""The decision agent: an LLM (Groq) chooses which deterministic tools to call,
reads their results and recommends what to do about one close approach.

Carried over from OrbitalGuard's decision agent and rebuilt on OrbitWatch:
  * native tool calling (OpenAI-compatible Groq API), at most MAX_TURNS turns;
  * the risk tier drives the workflow (LOW monitor, MEDIUM analyse, HIGH and
    CRITICAL generate and evaluate manoeuvres);
  * guardrails after the LLM: a recommended manoeuvre must be a candidate the
    constraint evaluator passed, LOW risk never gets a burn, Pc >= 1e-4 always
    gets manoeuvre analysis, and every manoeuvre needs human approval;
  * without an API key, or if the LLM fails, the same workflow runs
    deterministically, so the feature always works;
  * every step is written to MySQL agent_step as it happens (the UI polls it).
"""
import json
import logging
import os
import re
import threading
import time

import requests

from orbitwatch import db, orbital
from orbitwatch.agent.tools import RISK_ORDER, TOOL_SCHEMAS, AgentTools, DemoAgentTools

log = logging.getLogger(__name__)

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
MAX_TURNS = 10
DECISIONS = ("MONITOR", "MANEUVER_RECOMMENDED", "NO_FEASIBLE_MANEUVER", "DATA_UNAVAILABLE")

SYSTEM_PROMPT = """You are OrbitWatch's collision-avoidance decision agent. For one predicted close approach
between a watched satellite and another object, gather evidence with the tools, then recommend what operators should do.

Rules:
1. Never compute physics yourself. Every distance, probability, delta-v and time you mention must come from a tool result.
2. Start with get_conjunction, get_space_weather, compute_collision_probability and assess_risk.
3. Follow the risk tier from assess_risk:
   - LOW: no manoeuvre; decide MONITOR.
   - MEDIUM: look deeper (for example ground_station_passes) and normally decide MONITOR with re-screening advice.
   - HIGH or CRITICAL: call generate_maneuver_candidates, then evaluate_maneuver_constraints for the candidates,
     and recommend the lowest delta-v candidate that is FEASIBLE. If none is feasible, decide NO_FEASIBLE_MANEUVER.
4. Never recommend a candidate the evaluator rejected, and never relax a constraint.
5. If the time of closest approach has passed or data is missing, decide DATA_UNAVAILABLE.
6. Any manoeuvre needs human approval; say so.

When you have enough evidence, call submit_decision with your decision, the chosen candidate id (or null)
and a 3-5 sentence explanation for an operator that cites the actual values (Pc, miss distance, delta-v, burn time)
rather than tool names or step numbers."""

SUBMIT_TOOL = {"name": "submit_decision", "description": "Submit the final recommendation (ends the assessment).",
               "parameters": {"type": "object", "required": ["decision", "explanation"], "properties": {
                   "decision": {"type": "string", "enum": list(DECISIONS)},
                   "selected_candidate_id": {"type": ["string", "null"], "description": "e.g. M2; null unless MANEUVER_RECOMMENDED"},
                   "explanation": {"type": "string"}}}}
LLM_TOOLS = [{"type": "function", "function": t} for t in TOOL_SCHEMAS + [SUBMIT_TOOL]]


# Groq retires models over time; if the configured one is gone, use the first of these that the key can access.
PREFERRED_MODELS = ("openai/gpt-oss-120b", "openai/gpt-oss-20b", "qwen/qwen3.8-27b", "llama-3.3-70b-versatile")
_resolved_models = {}


def llm_config():
    key = os.getenv("GROQ_API_KEY") or ""
    model = os.getenv("GROQ_REASONING_MODEL") or PREFERRED_MODELS[0]
    return {"enabled": key.startswith("gsk_"), "model": model, "configured_model": model, "key": key}


def resolve_model(cfg):
    """The configured model if Groq still serves it, otherwise the best available tool-calling model."""
    want = cfg["configured_model"]
    if want not in _resolved_models:
        try:
            r = requests.get("https://api.groq.com/openai/v1/models", timeout=15,
                             headers={"Authorization": f"Bearer {cfg['key']}"})
            ids = {m["id"] for m in r.json().get("data", []) if m.get("active", True)}
            _resolved_models[want] = want if want in ids else next((m for m in PREFERRED_MODELS if m in ids), want)
        except (requests.RequestException, ValueError, KeyError):
            return want
    return _resolved_models[want]


class LLMUnavailable(Exception):
    pass


class DecisionAgent:
    def __init__(self, assessment_id, event_id, account, synthetic=False, feedback=None, min_miss_km=None):
        self.assessment_id = assessment_id
        self.event_id = event_id
        self.account = account
        self.synthetic = synthetic
        self.feedback = feedback
        self.min_miss_km = min_miss_km
        self.tools = (DemoAgentTools if synthetic else AgentTools)(event_id, account, min_miss_km=min_miss_km)
        self.step = 0
        self.seen_calls = {}

    # ---- trace -----------------------------------------------------------
    def record(self, actor, summary, tool_name=None, args=None, payload=None):
        self.step += 1
        clean = {k: v for k, v in (payload or {}).items() if not str(k).startswith("_")} if isinstance(payload, dict) else payload
        db.execute(self.account, "INSERT INTO agent_step (assessment_id, step_no, actor, tool_name, arguments, summary, "
                                 "payload) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                   (self.assessment_id, self.step, actor, tool_name, json.dumps(args) if args is not None else None,
                    str(summary)[:1000], json.dumps(clean, default=str) if clean is not None else None))

    def call_tool(self, name, args, from_llm=False):
        key = (name, json.dumps(args or {}, sort_keys=True))
        if key in self.seen_calls:
            result = self.seen_calls[key]
            if from_llm:
                self.record("guardrail", f"repeated {name} call answered from the earlier result", name, args)
            return result
        result = self.tools.call(name, args or {})
        self.seen_calls[key] = result
        self.record("tool", result.get("summary", ""), name, args, result)
        return result

    # ---- LLM loop ----------------------------------------------------------
    def _chat(self, cfg, messages):
        """One Groq call. On a rate limit, wait as Groq suggests, then fail over to a lighter model.
        If the model calls a tool that does not exist, it is reminded once of the real tool names."""
        models = [cfg["model"]] + [m for m in ("openai/gpt-oss-20b",) if m != cfg["model"]]
        reminded = 0
        for attempt in range(6):
            model = models[min(attempt // 2, len(models) - 1)]
            body = {"model": model, "messages": messages, "temperature": 0.1, "max_tokens": 1200,
                    "tools": LLM_TOOLS, "tool_choice": "auto",
                    "parallel_tool_calls": True}
            if model.startswith("openai/gpt-oss"):
                body["reasoning_effort"] = "low"
            try:
                r = requests.post(GROQ_URL, json=body, timeout=60,
                                  headers={"Authorization": f"Bearer {cfg['key']}", "Content-Type": "application/json"})
            except requests.RequestException as exc:
                raise LLMUnavailable(f"Groq unreachable: {exc}") from None
            if r.status_code == 200:
                if model != cfg["model"]:
                    self.record("system", f"rate-limited on {cfg['model']}; continuing on {model}")
                    cfg["model"] = model
                return r.json()["choices"][0]["message"]
            malformed = r.status_code == 400 and any(
                marker in r.text for marker in ("tool_use_failed", "failed_generation", "Parsing failed", "output_parse"))
            if malformed:
                # The model produced output Groq could not turn into a valid tool call (an unknown
                # tool name, or arguments that are not valid JSON). Salvage a decision if it is in
                # there; otherwise remind the model of the contract and let it try again.
                try:
                    failed = r.json()["error"].get("failed_generation") or ""
                except (ValueError, AttributeError):
                    failed = ""
                if self._parse(failed):
                    self.record("guardrail", "model emitted its decision in a malformed tool call; decision recovered")
                    return {"content": failed}
                if reminded < 2:
                    reminded += 1
                    self.record("guardrail", "model output was not a valid tool call; reminded it of the tool contract")
                    messages = messages + [{"role": "user", "content": "Your last reply was not a valid tool call. "
                                            "Only call the listed tools by their exact names with JSON arguments. "
                                            "To finish, call submit_decision."}]
                    continue
                raise LLMUnavailable(f"Groq rejected the model output: {r.text[:200]}")
            if r.status_code == 429 and attempt < 4:
                m = re.search(r"try again in ([0-9.]+)s", r.text)
                wait = min(float(r.headers.get("retry-after") or (m.group(1) if m else 6)), 20.0)
                self.record("system", f"Groq rate limit on {model}: waiting {wait:.0f} s")
                time.sleep(wait)
                continue
            raise LLMUnavailable(f"Groq HTTP {r.status_code}: {r.text[:200]}")
        raise LLMUnavailable("Groq rate limit persisted")

    @staticmethod
    def _for_llm(result):
        """Compact tool result for the model: the numbers it needs, not the full payload."""
        drop = {"encounter_plane_miss_km", "primary_ric_sigma_km", "secondary_ric_sigma_km", "checks", "method"}
        out = {k: v for k, v in result.items() if k not in drop}
        if "passes" in out:
            out["passes"] = [{k: p[k] for k in ("station", "aos", "max_elevation_deg")} for p in out["passes"][:4]]
        if "candidates" in out:
            out["candidates"] = [{k: c[k] for k in ("candidate_id", "dv_mps", "direction", "lead_time_min", "pc_after",
                                                    "sma_drift_km", "converged")} for c in out["candidates"]]
        return json.dumps(out, default=str)[:1800]

    def run_llm(self, cfg):
        # The facts every assessment needs are gathered first; the model spends its turns
        # deciding what further analysis is warranted and what to recommend.
        evidence = {name: self.call_tool(name, {}) for name in
                    ("get_conjunction", "get_space_weather", "compute_collision_probability", "assess_risk")}
        brief = "\n".join(f"- {name}: {self._for_llm(r)}" for name, r in evidence.items())
        subject = (f"SYNTHETIC demo close approach #{self.event_id} (injected debris, a training scenario)"
                   if self.synthetic else f"Close approach #{self.event_id}")
        review = ""
        if self.feedback or self.min_miss_km:
            review = ("\nA human reviewer REJECTED the previous recommendation for this event"
                      + (f" with this feedback: \"{self.feedback}\"" if self.feedback else "")
                      + (f". The burn must now leave at least {self.min_miss_km:g} km miss distance; the candidate "
                         "generator and evaluator enforce this." if self.min_miss_km else ".")
                      + " Address the feedback in your explanation.\n")
        messages = [{"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": f"{subject}. Evidence gathered so far:\n{brief}\n{review}"
                                                "Call any further tools you need (several at once is fine), "
                                                "then finish by calling submit_decision."}]
        for _turn in range(MAX_TURNS):
            msg = self._chat(cfg, messages)
            calls = msg.get("tool_calls") or []
            thought = (msg.get("content") or msg.get("reasoning") or "").strip()
            submit = next((c for c in calls if c["function"]["name"] == "submit_decision"), None)
            if submit:
                try:
                    decision = json.loads(submit["function"].get("arguments") or "{}")
                except json.JSONDecodeError:
                    decision = {}
                if decision.get("decision") in DECISIONS:
                    self.record("agent", (thought[:300] + " -> " if thought else "") + "submits " + decision["decision"],
                                payload=decision)
                    return decision
            if calls:
                names = ", ".join(c["function"]["name"] for c in calls)
                self.record("agent", (thought[:400] + " -> " if thought else "") + f"calls {names}",
                            payload={"tool_calls": [c["function"]["name"] for c in calls]})
                messages.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls})
                for c in calls:
                    try:
                        args = json.loads(c["function"].get("arguments") or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    result = self.call_tool(c["function"]["name"], args, from_llm=True)
                    messages.append({"role": "tool", "tool_call_id": c["id"], "content": self._for_llm(result)})
                continue
            decision = self._parse(msg.get("content") or "")
            if decision:
                self.record("agent", decision.get("explanation", "decision"), payload=decision)
                return decision
            messages.append({"role": "assistant", "content": msg.get("content") or ""})
            messages.append({"role": "user", "content": "Finish now by calling submit_decision."})
        raise LLMUnavailable("the model did not reach a decision within the turn limit")

    @staticmethod
    def _parse(text):
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            return None
        try:
            d = json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
        return d if d.get("decision") in DECISIONS else None

    # ---- deterministic workflow (no LLM, or LLM failure) ---------------------
    def run_deterministic(self):
        for name in ("get_conjunction", "get_space_weather", "compute_collision_probability", "assess_risk"):
            self.call_tool(name, {})
        risk = self.tools.cache["risk"]
        if risk["tca_passed"]:
            return {"decision": "DATA_UNAVAILABLE", "selected_candidate_id": None, "explanation": "The time of closest approach has passed."}
        if risk["risk_tier"] == "MEDIUM":
            self.call_tool("ground_station_passes", {"hours": 12})
        if risk["risk_tier"] in ("LOW", "MEDIUM"):
            return {"decision": "MONITOR", "selected_candidate_id": None, "explanation": ""}
        gen = self.call_tool("generate_maneuver_candidates", {"count": 3 if risk["risk_tier"] == "HIGH" else 4})
        for c in gen.get("candidates", []):
            self.call_tool("evaluate_maneuver_constraints", {"candidate_id": c["candidate_id"]})
        best = self._best_feasible()
        return {"decision": "MANEUVER_RECOMMENDED" if best else "NO_FEASIBLE_MANEUVER",
                "selected_candidate_id": best, "explanation": ""}

    def _best_feasible(self):
        ok = [e for e in self.tools.evaluations.values() if e["feasible"]]
        return min(ok, key=lambda e: e["dv_mps"])["candidate_id"] if ok else None

    # ---- guardrails --------------------------------------------------------
    def guard(self, d):
        t = self.tools
        for name in ("get_conjunction", "compute_collision_probability", "assess_risk"):
            if name not in {k[0] for k in self.seen_calls}:
                self.call_tool(name, {})
        risk = t.cache["risk"]
        decision, chosen = d.get("decision"), d.get("selected_candidate_id")
        if risk["tca_passed"]:
            return {**d, "decision": "DATA_UNAVAILABLE", "selected_candidate_id": None}
        if risk["risk_tier"] == "LOW" and decision == "MANEUVER_RECOMMENDED":
            self.record("guardrail", "LOW risk never warrants a burn: changed to MONITOR")
            return {**d, "decision": "MONITOR", "selected_candidate_id": None}
        if risk["pc"] >= 1e-4 and decision == "MONITOR":
            self.record("guardrail", f"Pc {risk['pc']:.1e} is above 1e-4: manoeuvre analysis is mandatory")
            decision = "MANEUVER_RECOMMENDED"
        if decision == "NO_FEASIBLE_MANEUVER":
            # "Nothing is feasible" is only true once every candidate has been checked.
            if not t.candidates and risk["risk_tier"] in ("HIGH", "CRITICAL"):
                self.record("guardrail", "no candidates were generated: generating them before concluding nothing is feasible")
                self.call_tool("generate_maneuver_candidates", {"count": 4})
            unchecked = [cid for cid in t.candidates if cid not in t.evaluations]
            if unchecked:
                self.record("guardrail", f"{', '.join(unchecked)} not evaluated yet: checking before concluding nothing is feasible")
                for cid in unchecked:
                    self.call_tool("evaluate_maneuver_constraints", {"candidate_id": cid})
            best = self._best_feasible()
            if best:
                self.record("guardrail", f"{best} passes every constraint: recommending it instead of NO_FEASIBLE_MANEUVER")
                decision, chosen = "MANEUVER_RECOMMENDED", best
                d = {**d, "explanation": ""}
        if decision == "MANEUVER_RECOMMENDED":
            if not t.candidates:
                self.call_tool("generate_maneuver_candidates", {"count": 4})
            for cid in t.candidates:
                if cid not in t.evaluations:
                    self.call_tool("evaluate_maneuver_constraints", {"candidate_id": cid})
            best = self._best_feasible()
            if chosen not in t.evaluations or not t.evaluations[chosen]["feasible"]:
                if chosen:
                    self.record("guardrail", f"{chosen} is not a feasible evaluated candidate: "
                                             + (f"using {best}, the lowest delta-v feasible one" if best else "none is feasible"))
                chosen = best
                d = {**d, "explanation": ""}
            elif best and best != chosen and t.evaluations[best]["dv_mps"] < 0.95 * t.evaluations[chosen]["dv_mps"]:
                self.record("guardrail", f"{best} is also feasible and needs less delta-v "
                                         f"({t.evaluations[best]['dv_mps']:.3f} vs {t.evaluations[chosen]['dv_mps']:.3f} m/s): "
                                         f"recommending {best} instead of {chosen}")
                chosen = best
                d = {**d, "explanation": ""}
            if chosen is None:
                decision = "NO_FEASIBLE_MANEUVER"
        return {**d, "decision": decision, "selected_candidate_id": chosen if decision == "MANEUVER_RECOMMENDED" else None}

    def explain(self, d):
        """Deterministic explanation built only from tool numbers (used when the LLM gave none)."""
        t = self.tools
        conj, risk, pc = t.cache.get("conjunction") or t.get_conjunction(), t.cache["risk"], t.cache["pc"]
        base = (f"{conj['primary']['name']} passes {conj['miss_distance_km']} km from {conj['secondary']['name']} at "
                f"{conj['relative_velocity_km_s']} km/s in {conj['hours_to_tca']} h; Pc {pc['pc']:.1e} puts it in the "
                f"{risk['risk_tier']} tier.")
        if d["decision"] == "MONITOR":
            return base + " No manoeuvre is warranted: keep monitoring and re-screen when fresher element sets arrive."
        if d["decision"] == "MANEUVER_RECOMMENDED":
            e, c = t.evaluations[d["selected_candidate_id"]], t.candidates[d["selected_candidate_id"]]
            return (base + f" Recommend {d['selected_candidate_id']}: a {c['dv_mps']:.3f} m/s {c['direction'].lower()} burn at "
                    f"{c['burn_time_utc'][:16].replace('T', ' ')} UTC lowers Pc to {c['pc_after']:.1e} with "
                    f"{c['sma_drift_km']:.2f} km of slot drift; commands can be uplinked via {e['uplink_station']}. "
                    "Requires human approval before execution.")
        if d["decision"] == "NO_FEASIBLE_MANEUVER":
            return base + " No candidate burn satisfies every constraint; escalate to the operator for a manual plan."
        return base + " The assessment could not be completed with the available data."


def start(event_id, user_id, account, background=True, demo_event_id=None, parent_assessment_id=None,
          feedback=None, min_miss_km=None):
    """Create an assessment row and run the agent (in a thread by default). Returns assessment_id.

    Either event_id (a real close approach) or demo_event_id (a synthetic demo one) is given. A re-plan
    after a rejection carries the parent assessment, the reviewer's feedback and an optional minimum miss.
    """
    cfg = llm_config()
    engine = f"groq:{cfg['model']}" if cfg["enabled"] else "deterministic"
    _, assessment_id = db.execute(account, """
        INSERT INTO agent_assessment (event_id, demo_event_id, requested_by, engine, parent_assessment_id, feedback,
                                      min_miss_km)
        VALUES (%s, %s, %s, %s, %s, %s, %s)""",
        (None if demo_event_id else event_id, demo_event_id, user_id, engine, parent_assessment_id,
         feedback[:1000] if feedback else None, min_miss_km))
    subject = demo_event_id or event_id
    kwargs = {"synthetic": bool(demo_event_id), "feedback": feedback, "min_miss_km": min_miss_km}
    if background:
        threading.Thread(target=_run, args=(assessment_id, subject, account, cfg), kwargs=kwargs, daemon=True,
                         name=f"agent-{assessment_id}").start()
    else:
        _run(assessment_id, subject, account, cfg, **kwargs)
    return assessment_id


def _run(assessment_id, event_id, account, cfg, synthetic=False, feedback=None, min_miss_km=None):
    agent = DecisionAgent(assessment_id, event_id, account, synthetic=synthetic, feedback=feedback,
                          min_miss_km=min_miss_km)
    if synthetic:
        agent.record("system", "SYNTHETIC demo close approach: injected debris, not a real catalogue object")
    if feedback or min_miss_km:
        agent.record("system", "re-plan after a reviewer rejection"
                     + (f': "{feedback[:300]}"' if feedback else "")
                     + (f"; required miss distance >= {min_miss_km:g} km" if min_miss_km else ""))
    engine = f"groq:{cfg['model']}" if cfg["enabled"] else "deterministic"
    try:
        decision = None
        if cfg["enabled"]:
            try:
                cfg = {**cfg, "model": resolve_model(cfg)}
                engine = f"groq:{cfg['model']}"
                if cfg["model"] != cfg["configured_model"]:
                    agent.record("system", f"configured model {cfg['configured_model']} is no longer served by Groq; "
                                           f"using {cfg['model']}")
                agent.record("system", f"LLM agent started ({cfg['model']} via Groq)")
                decision = agent.run_llm(cfg)
            except LLMUnavailable as exc:
                agent.record("system", f"LLM unavailable ({exc}); finishing with the deterministic workflow")
                engine = "deterministic (LLM fallback)"
        else:
            agent.record("system", "No Groq API key configured: running the deterministic workflow")
        if decision is None:
            decision = agent.run_deterministic()
        decision = agent.guard(decision)
        explanation = decision.get("explanation") or ""
        if not explanation or len(explanation) < 40:
            explanation = agent.explain(decision)
        t = agent.tools
        risk = t.cache.get("risk", {})
        chosen = decision.get("selected_candidate_id")
        cand = t.candidates.get(chosen) if chosen else None
        db.execute(account, """
            UPDATE agent_assessment SET status = 'complete', engine = %s, risk_tier = %s, decision = %s,
                   pc_before = %s, pc_after = %s, delta_v_mps = %s, burn_time = %s, burn_direction = %s, maneuver = %s,
                   explanation = %s, human_approval_required = %s, finished_at = CURRENT_TIMESTAMP(3)
             WHERE assessment_id = %s""",
                   (engine, risk.get("risk_tier"), decision["decision"], risk.get("pc"),
                    cand["pc_after"] if cand else None, cand["dv_mps"] if cand else None,
                    cand["_burn"] if cand else None, cand["direction"] if cand else None,
                    json.dumps({k: v for k, v in cand.items() if not k.startswith("_")}, default=str) if cand else None,
                    explanation[:5000], decision["decision"] == "MANEUVER_RECOMMENDED", assessment_id))
        agent.record("system", f"decision: {decision['decision']}" + (f" ({chosen})" if chosen else ""))
    except Exception as exc:  # noqa: BLE001 - the run is recorded as failed, never left 'running'
        log.exception("agent assessment %s failed", assessment_id)
        try:
            agent.record("system", f"failed: {exc}")
        finally:
            db.execute(account, "UPDATE agent_assessment SET status = 'failed', explanation = %s, "
                                "finished_at = CURRENT_TIMESTAMP(3) WHERE assessment_id = %s", (str(exc)[:5000], assessment_id))
