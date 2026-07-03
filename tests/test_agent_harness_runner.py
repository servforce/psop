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


def test_compiler_executor_fails_when_required_artifact_is_missing(tmp_path) -> None:
    settings = Settings(
        database_url="sqlite+pysqlite:///:memory:",
        agent_harness_sandbox_root=str(tmp_path / "agent-runs"),
    )
    sandbox = LocalAgentSandboxProvider(settings).acquire(input_payload={})
    writer = AgentEventWriter(sandbox.events_path)
    agent = _NoArtifactAgent()

    result = LangChainAgentExecutor().invoke(
        agent=agent,
        invocation=AgentInvocation(agent_key="psop.compiler", input={"text": "编译 Skill。"}),
        definition=AgentDefinition(agent_key="psop.compiler"),
        sandbox=sandbox,
        event_writer=writer,
    )

    assert agent.call_count == 3
    assert result.status == "failed"
    assert "sandbox://outputs/compiler-result.json" in result.error_message
    assert any(event.event_type == "agent.required_artifact.missing" for event in result.events)


def test_compiler_executor_collects_required_artifact(tmp_path) -> None:
    settings = Settings(
        database_url="sqlite+pysqlite:///:memory:",
        agent_harness_sandbox_root=str(tmp_path / "agent-runs"),
    )
    sandbox = LocalAgentSandboxProvider(settings).acquire(input_payload={})
    writer = AgentEventWriter(sandbox.events_path)
    agent = _CompilerArtifactOnSecondInvokeAgent(sandbox, writer)

    result = LangChainAgentExecutor().invoke(
        agent=agent,
        invocation=AgentInvocation(agent_key="psop.compiler", input={"text": "编译 Skill。"}),
        definition=AgentDefinition(agent_key="psop.compiler"),
        sandbox=sandbox,
        event_writer=writer,
    )

    assert agent.call_count == 2
    assert result.status == "succeeded"
    assert any(artifact.artifact_type == "eg_compile_candidate" for artifact in result.artifacts)


def test_compiler_executor_fails_when_required_interactions_are_missing(tmp_path) -> None:
    settings = Settings(
        database_url="sqlite+pysqlite:///:memory:",
        agent_harness_sandbox_root=str(tmp_path / "agent-runs"),
    )
    sandbox = LocalAgentSandboxProvider(settings).acquire(input_payload={})
    writer = AgentEventWriter(sandbox.events_path)
    agent = _CompilerArtifactWithoutInteractionsAgent(sandbox)

    result = LangChainAgentExecutor().invoke(
        agent=agent,
        invocation=AgentInvocation(agent_key="psop.compiler", input={"text": "编译 Skill。"}),
        definition=AgentDefinition(agent_key="psop.compiler"),
        sandbox=sandbox,
        event_writer=writer,
    )

    assert agent.call_count == 3
    assert result.status == "failed"
    assert "必需交互" in result.error_message
    assert any(event.event_type == "agent.required_interaction.missing" for event in result.events)


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


class _NoArtifactAgent:
    def __init__(self) -> None:
        self.call_count = 0

    def invoke(self, payload, *, context):
        self.call_count += 1
        messages = list(payload.get("messages") or [])
        messages.append(SimpleNamespace(content="我将要提交候选产物。"))
        return {"messages": messages}


class _CompilerArtifactOnSecondInvokeAgent:
    def __init__(self, sandbox, writer: AgentEventWriter) -> None:
        self.sandbox = sandbox
        self.writer = writer
        self.call_count = 0

    def invoke(self, payload, *, context):
        self.call_count += 1
        messages = list(payload.get("messages") or [])
        if self.call_count == 1:
            messages.append(SimpleNamespace(content="我将要提交 EG candidate。"))
            return {"messages": messages}
        _record_compiler_required_interactions(self.writer)
        self.sandbox.write_text(
            "/mnt/psop/outputs/compiler-result.json",
            '{"artifact":{"formal_revision":"psop-eg-formal/v5","nodes":[]}}',
        )
        messages.append(SimpleNamespace(content="候选产物已提交。"))
        return {"messages": messages}


class _CompilerArtifactWithoutInteractionsAgent:
    def __init__(self, sandbox) -> None:
        self.sandbox = sandbox
        self.call_count = 0

    def invoke(self, payload, *, context):
        self.call_count += 1
        messages = list(payload.get("messages") or [])
        self.sandbox.write_text(
            "/mnt/psop/outputs/compiler-result.json",
            '{"artifact":{"formal_revision":"psop-eg-formal/v5","nodes":[]}}',
        )
        messages.append(SimpleNamespace(content="候选产物已提交。"))
        return {"messages": messages}


def _record_compiler_required_interactions(writer: AgentEventWriter) -> None:
    writer.record("agent.skill.loaded", {"skill_name": "psop-compiler"})
    for resource_path in (
        "core/SKILL.md",
        "contract/SKILL.md",
        "mapping/SKILL.md",
        "review/SKILL.md",
    ):
        writer.record(
            "agent.skill.resource.loaded",
            {"skill_name": "psop-compiler", "resource_path": resource_path},
        )
    for tool_name in (
        "psop.compiler.read_skill_source",
        "psop.compiler.read_manifest_snapshot",
        "psop.compiler.read_allowed_runtime",
        "psop.compiler.read_domain_pack",
        "psop.compiler.build_formal_v5_scaffold",
        "psop.compiler.validate_formal_v5",
        "psop.compiler.submit_candidate",
    ):
        writer.record("agent.tool.completed", {"tool_name": tool_name})
