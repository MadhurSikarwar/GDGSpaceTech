"""
LLM Client for Decision Agent Orchestration.
Communicates with Groq API or injected LLM callable to select tools and interpret results.
Includes timeouts, retries, and strict output parsing.
"""

import json
import logging
import os
import re
from typing import Dict, Any, Optional, List
import httpx

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """You are the OrbitalGuard Autonomous Decision Agent for space domain awareness and collision avoidance.
Your responsibility is to analyze a space conjunction event, decide which deterministic tools to invoke, interpret their evidence, compare avoidance maneuver options against hard physical constraints, and formulate a safe operational recommendation for human approval.

CRITICAL RULES:
1. You DO NOT calculate physics, orbital states, delta-V, or collision probabilities yourself. You must invoke the authoritative deterministic tools provided to obtain this data.
2. You CANNOT declare an avoidance maneuver feasible if the deterministic constraint evaluator reports it as infeasible or violated.
3. If no feasible maneuver exists, you must conclude NO_FEASIBLE_MANEUVER.
4. Human approval is ALWAYS required before any maneuver recommendation can be executed.
5. Base all justifications strictly on actual tool results (distances, Pc numbers, delta-V, and drift values).

AVAILABLE ACTIONS:
You must respond with valid JSON matching one of the two formats:

Option 1: Call a deterministic tool
{
  "action": "call_tool",
  "tool_name": "<exact_tool_name>",
  "tool_args": { ... },
  "reasoning": "<short sentence explaining why this tool is needed>"
}

Option 2: Final Decision Recommendation (when sufficient deterministic evidence has been gathered)
{
  "action": "make_decision",
  "selected_maneuver_id": "<ID of chosen feasible candidate, or 'NONE', or 'NO_FEASIBLE_MANEUVER'>",
  "explanation": "<concise professional justification referencing actual Pc, delta-V, and separation numbers>"
}
"""


class LLMClient:
    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        self.api_key = api_key or os.getenv("GROQ_API_KEY")
        self.model = model or os.getenv("GROQ_FAST_MODEL", "llama-3.1-8b-instant")

    def query_decision(
        self,
        context_summary: str,
        available_tools: Dict[str, Any],
        tool_history: List[Dict[str, Any]],
        llm_override: Optional[Any] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Query the LLM for the next orchestration action.
        Supports dependency injection via llm_override for unit testing without live API keys.
        """
        if llm_override is not None:
            try:
                if callable(llm_override):
                    return llm_override(context_summary, available_tools, tool_history)
                elif isinstance(llm_override, dict):
                    return llm_override
            except Exception as e:
                logger.warning(f"LLM override failed: {e}")
                return None

        if not self.api_key or "gsk_" not in self.api_key:
            logger.info("No valid Groq API key configured; falling back to deterministic orchestrator.")
            return None

        tools_desc = "\n".join([f"- {name}: {t.description} (params: {list(t.parameters.get('properties', {}).keys())})" for name, t in available_tools.items()])
        history_desc = "\n".join([f"{h.get('timestamp', '')} | Tool: {h.get('tool_name')} | Success: {h.get('success')} | Result: {h.get('summary')}" for h in tool_history[-5:]]) if tool_history else "None (initial step)"

        user_prompt = f"""CURRENT ENCOUNTER CONTEXT:
{context_summary}

TOOL INVOCATION HISTORY:
{history_desc}

AVAILABLE TOOLS:
{tools_desc}

Decide your next action (either call a tool to gather missing data, or make the final decision if sufficient evidence exists).
Respond with ONLY valid JSON:"""

        candidate_models = [self.model, "llama-3.1-8b-instant", "groq/compound-mini"]
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

        for model in dict.fromkeys(candidate_models):
            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt}
                ],
                "temperature": 0.1,
                "max_tokens": 250,
                "response_format": {"type": "json_object"}
            }
            try:
                with httpx.Client(timeout=4.0) as client:
                    resp = client.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=payload)
                    if resp.status_code == 200:
                        content = resp.json()["choices"][0]["message"]["content"].strip()
                        parsed = self._clean_and_parse_json(content)
                        if parsed and "action" in parsed:
                            return parsed
            except Exception as e:
                logger.warning(f"Groq query with model {model} failed: {e}")
                continue

        return None

    def _clean_and_parse_json(self, text: str) -> Optional[Dict[str, Any]]:
        try:
            return json.loads(text)
        except Exception:
            # Attempt to extract JSON substring
            m = re.search(r'\{.*\}', text, re.DOTALL)
            if m:
                try:
                    return json.loads(m.group(0))
                except Exception:
                    pass
        return None
