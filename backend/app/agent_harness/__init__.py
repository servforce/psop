"""Agent harness infrastructure package."""

from app.agent_harness.agent_decision import AgentDecision
from app.agent_harness.guardrails import InputGuardrail, OutputGuardrail
from app.agent_harness.model import AgentModelClient
from app.agent_harness.tools import ToolPolicy, ToolPolicyDecision

__all__ = [
    "AgentDecision",
    "AgentModelClient",
    "AgentRunner",
    "InputGuardrail",
    "OutputGuardrail",
    "ToolPolicy",
    "ToolPolicyDecision",
]


def __getattr__(name: str):
    if name == "AgentRunner":
        from app.agent_harness.runner import AgentRunner

        return AgentRunner
    raise AttributeError(name)
