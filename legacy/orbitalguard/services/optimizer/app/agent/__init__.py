"""
Decision Agent Package for OrbitalGuard.
Exposes the DecisionAgentOrchestrator, ToolRegistry, DecisionContext, and Phase 3 AdaptiveWorkflowManager.
"""

from services.optimizer.app.agent.state import DecisionContext, ToolCallRecord
from services.optimizer.app.agent.registry import ToolRegistry, ToolDefinition
from services.optimizer.app.agent.llm_client import LLMClient
from services.optimizer.app.agent.orchestrator import DecisionAgentOrchestrator
from services.optimizer.app.agent.workflow import (
    AdaptiveWorkflowManager,
    WorkflowState,
    CandidateFailureReason,
    CandidateEvaluationRecord,
)

__all__ = [
    "DecisionContext",
    "ToolCallRecord",
    "ToolRegistry",
    "ToolDefinition",
    "LLMClient",
    "DecisionAgentOrchestrator",
    "AdaptiveWorkflowManager",
    "WorkflowState",
    "CandidateFailureReason",
    "CandidateEvaluationRecord",
]
