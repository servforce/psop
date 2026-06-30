from __future__ import annotations


class AgentHarnessError(RuntimeError):
    """Base error for Agent Harness failures."""


class AgentDefinitionNotFoundError(AgentHarnessError):
    """Raised when an agent definition cannot be resolved."""


class AgentBudgetExceededError(AgentHarnessError):
    """Raised when an agent run exceeds a harness-enforced budget."""
