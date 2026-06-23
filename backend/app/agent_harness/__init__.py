from app.agent_harness.schemas import AgentInvocation, AgentResult

__all__ = ["AgentHarnessService", "AgentInvocation", "AgentResult"]


def __getattr__(name: str):
    if name == "AgentHarnessService":
        from app.agent_harness.service import AgentHarnessService

        return AgentHarnessService
    raise AttributeError(name)
