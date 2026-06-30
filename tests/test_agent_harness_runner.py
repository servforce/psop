from __future__ import annotations

from types import SimpleNamespace

from app.agent_harness.events import AgentEventWriter
from app.agent_harness.runners.langchain_agent_executor import LangChainAgentExecutor
from app.agent_harness.sandbox.local import LocalAgentSandboxProvider
from app.agent_harness.schemas import AgentDefinition, AgentInvocation
from app.core.config import Settings


def test_builder_executor_continues_when_required_artifact_is_missing(tmp_path) -> None:
    settings = Settings(
        database_url="sqlite+pysqlite:///:memory:",
        agent_harness_sandbox_root=str(tmp_path / "agent-runs"),
    )
    sandbox = LocalAgentSandboxProvider(settings).acquire(input_payload={})
    writer = AgentEventWriter(sandbox.events_path)
    agent = _ArtifactOnSecondInvokeAgent(sandbox)

    result = LangChainAgentExecutor().invoke(
        agent=agent,
        invocation=AgentInvocation(agent_key="psop.builder", input={"text": "构建 Skill。"}),
        definition=AgentDefinition(agent_key="psop.builder"),
        sandbox=sandbox,
        event_writer=writer,
    )

    assert agent.call_count == 2
    assert result.status == "succeeded"
    assert any(event.event_type == "agent.required_artifact.missing" for event in result.events)
    assert any(artifact.artifact_type == "skill_draft_candidate" for artifact in result.artifacts)


class _ArtifactOnSecondInvokeAgent:
    def __init__(self, sandbox) -> None:
        self.sandbox = sandbox
        self.call_count = 0

    def invoke(self, payload, *, context):
        self.call_count += 1
        messages = list(payload.get("messages") or [])
        if self.call_count == 1:
            messages.append(SimpleNamespace(content="我将要提交候选产物。"))
            return {"messages": messages}
        self.sandbox.write_text("/mnt/psop/outputs/builder-result.json", "{}")
        messages.append(SimpleNamespace(content="候选产物已提交。"))
        return {"messages": messages}
