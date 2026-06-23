class AgentHarnessError(RuntimeError):
    """Base error for Agent Harness failures."""


class AgentDefinitionNotFoundError(AgentHarnessError):
    """Raised when an agent definition cannot be resolved."""
