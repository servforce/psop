from __future__ import annotations

from app.agent_harness.sandbox.base import AgentSandboxProvider
from app.agent_harness.sandbox.local import LocalAgentSandboxProvider
from app.core.config import Settings


def build_sandbox_provider(settings: Settings) -> AgentSandboxProvider:
    if settings.agent_harness_sandbox_provider == "local":
        return LocalAgentSandboxProvider(settings)
    raise ValueError(f"不支持的 Agent Harness sandbox provider：{settings.agent_harness_sandbox_provider}")
