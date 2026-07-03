from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from app.agent_harness.agents.psop.runner.schemas import (
    RUNNER_OBSERVATION_SCHEMA,
    validate_runner_observation,
)
from app.agent_harness.agents.registry import default_agent_registry
from app.agent_harness.events import AgentEventWriter
from app.agent_harness.memory.file_store import FileMemoryStore
from app.agent_harness.models.scripted_runner_chat_model import ScriptedRunnerChatModel
from app.agent_harness.runners.langchain_agent_executor import LangChainAgentExecutor
from app.agent_harness.sandbox.local import LocalAgentSandboxProvider
from app.agent_harness.schemas import AgentDefinition, AgentInvocation
from app.agent_harness.service import AgentHarnessService
from app.agent_harness.skills.loader import SkillLoader
from app.agent_harness.tools.builtin.runner import register_runner_tools
from app.agent_harness.tools.registry import ToolExecutionContext, ToolRegistry
from app.core.config import Settings


FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "psop_runner" / "minimal.json"


def test_psop_runner_definition_and_skills_load() -> None:
    settings = Settings(database_url="sqlite+pysqlite:///:memory:")
    package = default_agent_registry(settings.backend_root).load("psop.runner")
    loader = SkillLoader(settings.repo_root / "skills")

    assert package.definition.factory == "make_runner_agent"
    assert package.definition.memory_scope == "psop.runner"
    assert package.definition.skills == [
        "psop-runner-core",
        "psop-runner-terminal-guidance",
        "psop-runner-evidence-evaluation",
    ]
    definition_tools = set(package.definition.tools)
    for skill_name in package.definition.skills:
        skill = loader.load_metadata(skill_name)
        assert skill.description
        assert set(skill.allowed_tools).issubset(definition_tools)
        assert "psop.runner.submit_observation" in skill.allowed_tools


def test_runner_tools_validate_and_write_observation(tmp_path) -> None:
    settings = Settings(
        database_url="sqlite+pysqlite:///:memory:",
        agent_harness_sandbox_root=str(tmp_path / "agent-runs"),
    )
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    sandbox = LocalAgentSandboxProvider(settings).acquire(input_payload={})
    registry = ToolRegistry()
    register_runner_tools(registry)
    context = ToolExecutionContext(
        sandbox=sandbox,
        memory_store=FileMemoryStore(sandbox.memory_path),
        memory_scope="psop.runner",
        event_writer=AgentEventWriter(sandbox.events_path),
        invocation_context=payload["context"],
        invocation_input=payload["input"],
    )
    observation = _valid_observation()
    invalid = {**observation, "node_id": "wrong_node"}

    invalid_result = registry.execute("psop.runner.submit_observation", invalid, context)
    assert invalid_result["status"] == "error"
    assert not sandbox.resolve_virtual_path("/mnt/psop/outputs").joinpath("runner-observation.json").exists()

    result = registry.execute("psop.runner.submit_observation", observation, context)

    output_path = sandbox.resolve_virtual_path("/mnt/psop/outputs/runner-observation.json")
    written = json.loads(output_path.read_text(encoding="utf-8"))
    assert result["status"] == "success"
    assert result["artifact_ref"] == "sandbox://outputs/runner-observation.json"
    assert written["schema"] == RUNNER_OBSERVATION_SCHEMA
    assert written["decision"] == "need_more_evidence"
    assert written["reference_images"][0]["reference_image_ref"] == "collect_context:ppe-example"


def test_runner_tools_reject_cross_step_reference_image(tmp_path) -> None:
    settings = Settings(
        database_url="sqlite+pysqlite:///:memory:",
        agent_harness_sandbox_root=str(tmp_path / "agent-runs"),
    )
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    sandbox = LocalAgentSandboxProvider(settings).acquire(input_payload={})
    registry = ToolRegistry()
    register_runner_tools(registry)
    context = ToolExecutionContext(
        sandbox=sandbox,
        memory_store=FileMemoryStore(sandbox.memory_path),
        memory_scope="psop.runner",
        event_writer=AgentEventWriter(sandbox.events_path),
        invocation_context=payload["context"],
        invocation_input=payload["input"],
    )
    observation = _valid_observation()
    observation["reference_images"] = [{"reference_image_ref": "other_step:image", "display_order": 1}]

    result = registry.execute("psop.runner.submit_observation", observation, context)

    assert result["status"] == "error"
    assert "不属于当前步骤" in result["message"]
    assert not sandbox.resolve_virtual_path("/mnt/psop/outputs").joinpath("runner-observation.json").exists()


def test_runner_read_tools_return_structured_error_without_context(tmp_path) -> None:
    settings = Settings(
        database_url="sqlite+pysqlite:///:memory:",
        agent_harness_sandbox_root=str(tmp_path / "agent-runs"),
    )
    sandbox = LocalAgentSandboxProvider(settings).acquire(input_payload={})
    registry = ToolRegistry()
    register_runner_tools(registry)
    context = ToolExecutionContext(
        sandbox=sandbox,
        memory_store=FileMemoryStore(sandbox.memory_path),
        memory_scope="psop.runner",
        event_writer=AgentEventWriter(sandbox.events_path),
        invocation_context={},
        invocation_input={},
    )

    result = registry.execute("psop.runner.read_prompt_view", {}, context)

    assert result["status"] == "error"
    assert result["type"] == "not_found"
    assert result["next_valid_actions"] == ["psop.runner.read_prompt_view"]


def test_psop_runner_scripted_run_creates_observation_artifact(tmp_path) -> None:
    settings = Settings(
        database_url="sqlite+pysqlite:///:memory:",
        agent_harness_sandbox_root=str(tmp_path / "agent-runs"),
    )
    service = AgentHarnessService(
        settings=settings,
        chat_model_factory=lambda _definition: ScriptedRunnerChatModel(),
    )
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    result = service.invoke(
        AgentInvocation(
            agent_key="psop.runner",
            input=payload["input"],
            context=payload["context"],
        )
    )

    completed_tools = {
        str(event.payload.get("tool_name") or "")
        for event in result.events
        if event.event_type == "agent.tool.completed"
    }
    loaded_skills = {
        str(event.payload.get("skill_name") or "")
        for event in result.events
        if event.event_type == "agent.skill.loaded"
    }
    observation_path = Path(result.sandbox_path or "") / "outputs" / "runner-observation.json"
    observation_payload = json.loads(observation_path.read_text(encoding="utf-8"))

    assert result.status == "succeeded"
    assert loaded_skills == {
        "psop-runner-core",
        "psop-runner-terminal-guidance",
        "psop-runner-evidence-evaluation",
    }
    assert {
        "psop.runner.read_prompt_view",
        "psop.runner.read_runtime_contract",
        "psop.runner.read_current_checkpoint",
        "psop.runner.list_step_reference_images",
        "psop.runner.list_terminal_events",
        "psop.runner.read_latest_evidence",
        "psop.runner.submit_observation",
    }.issubset(completed_tools)
    assert any(artifact.artifact_type == "runner_observation" for artifact in result.artifacts)
    validate_runner_observation(
        observation_payload,
        node_id="instruct_collect_context",
        output_contract=payload["context"]["output_contract"],
        step_reference_images=payload["context"]["step_reference_images"],
        terminal_cursor=1,
    )


def test_runner_executor_fails_when_required_artifact_is_missing(tmp_path) -> None:
    settings = Settings(
        database_url="sqlite+pysqlite:///:memory:",
        agent_harness_sandbox_root=str(tmp_path / "agent-runs"),
    )
    sandbox = LocalAgentSandboxProvider(settings).acquire(input_payload={})
    writer = AgentEventWriter(sandbox.events_path)
    agent = _NoArtifactAgent()

    result = LangChainAgentExecutor().invoke(
        agent=agent,
        invocation=AgentInvocation(agent_key="psop.runner", input={"text": "运行节点。"}),
        definition=AgentDefinition(agent_key="psop.runner"),
        sandbox=sandbox,
        event_writer=writer,
    )

    assert agent.call_count == 3
    assert result.status == "failed"
    assert "sandbox://outputs/runner-observation.json" in result.error_message


def test_runner_executor_fails_when_required_interactions_are_missing(tmp_path) -> None:
    settings = Settings(
        database_url="sqlite+pysqlite:///:memory:",
        agent_harness_sandbox_root=str(tmp_path / "agent-runs"),
    )
    sandbox = LocalAgentSandboxProvider(settings).acquire(input_payload={})
    writer = AgentEventWriter(sandbox.events_path)
    agent = _RunnerArtifactWithoutInteractionsAgent(sandbox)

    result = LangChainAgentExecutor().invoke(
        agent=agent,
        invocation=AgentInvocation(agent_key="psop.runner", input={"text": "运行节点。"}),
        definition=AgentDefinition(agent_key="psop.runner"),
        sandbox=sandbox,
        event_writer=writer,
    )

    assert agent.call_count == 3
    assert result.status == "failed"
    assert "必需交互" in result.error_message


def _valid_observation() -> dict:
    return {
        "schema": RUNNER_OBSERVATION_SCHEMA,
        "node_id": "instruct_collect_context",
        "decision": "need_more_evidence",
        "terminal_message": "请补充当前步骤照片。",
        "reason": "当前证据不足。",
        "next_phase": "waiting",
        "wait_reason": "等待用户补充证据。",
        "expected_inputs": ["text", "image"],
        "evidence_assessment": {
            "accepted_event_refs": ["terminal_event:1"],
            "rejected_event_refs": [],
            "missing_evidence": ["现场照片"],
            "unsafe_or_ambiguous_facts": [],
        },
        "reference_images": [{"reference_image_ref": "collect_context:ppe-example", "display_order": 1}],
        "safety_flags": [],
        "final_response": "",
        "source_refs": ["terminal_event:1"],
        "confidence": "medium",
    }


class _NoArtifactAgent:
    def __init__(self) -> None:
        self.call_count = 0

    def invoke(self, payload, *, context):
        self.call_count += 1
        messages = list(payload.get("messages") or [])
        messages.append(SimpleNamespace(content="我将提交 observation。"))
        return {"messages": messages}


class _RunnerArtifactWithoutInteractionsAgent:
    def __init__(self, sandbox) -> None:
        self.sandbox = sandbox
        self.call_count = 0

    def invoke(self, payload, *, context):
        self.call_count += 1
        messages = list(payload.get("messages") or [])
        self.sandbox.write_text("/mnt/psop/outputs/runner-observation.json", json.dumps(_valid_observation(), ensure_ascii=False))
        messages.append(SimpleNamespace(content="observation 已提交。"))
        return {"messages": messages}
