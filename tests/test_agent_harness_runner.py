from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.agent_harness.agents.psop.runner.schemas import (
    RUNNER_OBSERVATION_VIRTUAL_PATH,
    validate_runner_observation,
)
from app.agent_harness.agents.registry import default_agent_registry
from app.agent_harness.events import AgentEventWriter
from app.agent_harness.memory.file_store import FileMemoryStore
from app.agent_harness.models.factory import create_chat_model
from app.agent_harness.models.scripted_runner_chat_model import ScriptedRunnerChatModel
from app.agent_harness.sandbox.local import LocalAgentSandboxProvider
from app.agent_harness.schemas import AgentInvocation, AgentInvocationAttachment
from app.agent_harness.service import AgentHarnessService
from app.agent_harness.skills.loader import SkillLoader
from app.agent_harness.tools.builtin.runner import register_runner_tools
from app.agent_harness.tools.registry import ToolExecutionContext, ToolRegistry
from app.core.config import Settings


FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "psop_runner" / "minimal.json"


class RecordingChatModel:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs


def test_psop_runner_definition_and_skills_load() -> None:
    settings = Settings(database_url="sqlite+pysqlite:///:memory:")
    package = default_agent_registry(settings.backend_root).load("psop.runner")
    loader = SkillLoader(settings.repo_root / "skills")

    assert package.definition.description.startswith("在 PSOP Skill 运行过程中")
    assert package.definition.memory_scope == "psop.runner"
    assert package.definition.factory == "make_runner_agent"
    assert package.definition.skills == ["psop-runner"]
    assert "psop.runner.submit_observation" in package.definition.tools
    for skill_name in package.definition.skills:
        skill = loader.load_metadata(skill_name)
        assert skill.description
        assert skill.name == "psop-runner"
        assert "psop.runner.submit_observation" in skill.allowed_tools
    resource = loader.load_resource("psop-runner", "core/SKILL.md")
    assert "PSOP Runner Core" in resource["content"]


def test_psop_runner_config_and_prompt_guard_observation_format() -> None:
    settings = Settings(database_url="sqlite+pysqlite:///:memory:")
    package = default_agent_registry(settings.backend_root).load("psop.runner")
    prompt = package.read_system_prompt()
    model_events_config = next(
        (
            middleware.config
            for middleware in package.definition.middleware
            if not isinstance(middleware, str) and middleware.name == "model_events"
        ),
        None,
    )

    assert model_events_config == {"max_model_calls": 5}
    assert "输入上下文怎么读" in prompt
    assert "输出字段语义" in prompt
    assert "输出示例" in prompt
    assert "示例只展示判断思路和字段形态" in prompt
    assert "RunnerTurnContext" in prompt
    assert "按需工具" in prompt
    assert "`schema` 的值必须是 `psop.runner.observation.v1`" in prompt
    assert "不要使用 `kind` 字段" in prompt
    assert "不能传 `null`" in prompt
    assert "`terminal_event:N`" in prompt
    assert "`runtime_contract.workflow_steps.<id>`" in prompt
    assert "`runtime_contract.expected_evidence.<id>`" in prompt
    assert "`runtime_contract.wait_checkpoints.<id>`" in prompt
    assert "`prompt_view.*`" in prompt
    assert "`current_checkpoint.*`" in prompt
    assert "`trace_summary:N`" in prompt
    assert "`runtime_contract.safety_constraints`" in prompt
    assert "`task_identity.*`" in prompt
    assert "`first_step_instruction`" in prompt
    assert '"decision": "need_more_evidence"' in prompt
    assert '`decision: "continue"`' in prompt
    assert '`decision: "complete"`' in prompt
    assert "流程推进由运行时根据执行图完成" in prompt
    assert "`next_phase`：兼容字段，固定传空字符串" in prompt
    assert prompt.count("next_phase") <= 4
    assert '"next_phase": "motherboard_preinstall"' not in prompt


def test_agent_harness_model_explicitly_disables_thinking(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.agent_harness.models.factory._resolve_class",
        lambda _path: RecordingChatModel,
    )
    settings = Settings(
        database_url="sqlite+pysqlite:///:memory:",
        llm_text_model="qwen3.7-max-2026-06-08",
        llm_text_enable_thinking=True,
        llm_text_thinking_budget=8192,
    )

    model = create_chat_model(settings=settings, thinking_enabled=False)

    assert model.kwargs["extra_body"] == {"enable_thinking": False}


def test_agent_harness_model_keeps_thinking_budget_when_enabled(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.agent_harness.models.factory._resolve_class",
        lambda _path: RecordingChatModel,
    )
    settings = Settings(
        database_url="sqlite+pysqlite:///:memory:",
        llm_text_model="qwen3.7-max-2026-06-08",
        llm_text_enable_thinking=True,
        llm_text_thinking_budget=8192,
    )

    model = create_chat_model(settings=settings, thinking_enabled=True)

    assert model.kwargs["extra_body"] == {"enable_thinking": True, "thinking_budget": 8192}


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

    event_types = [event.event_type for event in result.events]
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
    loaded_resources = {
        str(event.payload.get("resource_path") or "")
        for event in result.events
        if event.event_type == "agent.skill.resource.loaded"
    }
    observation_path = Path(result.sandbox_path or "") / "outputs" / "runner-observation.json"
    observation = json.loads(observation_path.read_text(encoding="utf-8"))

    assert result.status == "succeeded"
    assert "agent.memory.read" in event_types
    assert loaded_skills == set()
    assert loaded_resources == set()
    assert completed_tools == {"psop.runner.submit_observation"}
    assert any(artifact.artifact_type == "runner_observation" for artifact in result.artifacts)
    assert observation["schema"] == "psop.runner.observation.v1"
    assert observation["decision"] == "continue"
    assert observation["runtime_decision"] == "proceed"
    validate_runner_observation(
        observation,
        invocation_input=payload["input"],
        invocation_context=payload["context"],
    )


def test_psop_runner_optional_read_tools_still_work(tmp_path) -> None:
    settings = Settings(
        database_url="sqlite+pysqlite:///:memory:",
        agent_harness_sandbox_root=str(tmp_path / "agent-runs"),
    )
    service = AgentHarnessService(
        settings=settings,
        chat_model_factory=lambda _definition: ScriptedRunnerChatModel(use_optional_reads=True),
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

    assert result.status == "succeeded"
    assert loaded_skills == set()
    assert {
        "psop.runner.read_prompt_view",
        "psop.runner.read_runtime_contract",
        "psop.runner.read_current_checkpoint",
        "psop.runner.list_terminal_events",
        "psop.runner.read_latest_evidence",
        "psop.runner.list_step_reference_images",
        "psop.runner.submit_observation",
    }.issubset(completed_tools)


def test_psop_runner_multimodal_attachment_is_redacted_from_persistence_surfaces(tmp_path) -> None:
    settings = Settings(
        database_url="sqlite+pysqlite:///:memory:",
        agent_harness_sandbox_root=str(tmp_path / "agent-runs"),
    )
    service = AgentHarnessService(
        settings=settings,
        chat_model_factory=lambda _definition: ScriptedRunnerChatModel(),
    )
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    payload["context"]["input_attachments"] = [
        {
            "attachment_id": "terminal_event:1:image_1",
            "source_ref": "terminal_event:1:image_1",
            "terminal_event_seq": 1,
            "part_id": "image_1",
            "filename": "site.jpg",
            "media_type": "image/jpeg",
            "size_bytes": 11,
            "checksum": "sha256:test",
            "artifact_object_id": "artifact-object-1",
        }
    ]

    result = service.invoke(
        AgentInvocation(
            agent_key="psop.runner",
            input=payload["input"],
            context=payload["context"],
            attachments=[
                AgentInvocationAttachment(
                    attachment_id="terminal_event:1:image_1",
                    source_ref="terminal_event:1:image_1",
                    terminal_event_seq=1,
                    part_id="image_1",
                    filename="site.jpg",
                    media_type="image/jpeg",
                    size_bytes=11,
                    checksum="sha256:test",
                    artifact_object_id="artifact-object-1",
                    content_base64="aW1hZ2UtYnl0ZXM=",
                )
            ],
        )
    )

    sandbox_input = (Path(result.sandbox_path or "") / "input.json").read_text(encoding="utf-8")
    serialized_result = json.dumps(result.model_dump(mode="json"), ensure_ascii=False)
    prepared_events = [
        event for event in result.events if event.event_type == "agent.multimodal.attachments.prepared"
    ]

    assert result.status == "succeeded"
    assert prepared_events
    assert prepared_events[0].payload["image_attachment_count"] == 1
    assert "aW1hZ2UtYnl0ZXM=" not in sandbox_input
    assert "aW1hZ2UtYnl0ZXM=" not in serialized_result
    assert "data:image/jpeg;base64" not in serialized_result


def test_runner_submit_observation_validates_and_writes_outputs(tmp_path) -> None:
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
    observation = {
        "schema": "psop.runner.observation.v1",
        "node_id": "evaluate_collect_context",
        "decision": "continue",
        "terminal_message": "已确认当前证据，可以继续最终核验。",
        "reason": "用户提交了当前步骤说明。",
        "next_phase": "",
        "wait_reason": "",
        "expected_inputs": [],
        "evidence_assessment": {
            "accepted_event_refs": ["terminal_event:1"],
            "rejected_event_refs": [],
            "missing_evidence": [],
            "unsafe_or_ambiguous_facts": [],
        },
        "reference_images": [],
        "safety_flags": [],
        "final_response": "",
        "source_refs": ["runtime_contract.workflow_steps.collect_context", "terminal_event:1"],
        "confidence": "high",
    }

    result = registry.execute("psop.runner.submit_observation", observation, context)

    assert result["status"] == "success"
    assert result["runtime_decision"] == "proceed"
    assert sandbox.resolve_virtual_path(RUNNER_OBSERVATION_VIRTUAL_PATH).exists()


def test_runner_submit_observation_rejects_forged_source_ref(tmp_path) -> None:
    settings = Settings(
        database_url="sqlite+pysqlite:///:memory:",
        agent_harness_sandbox_root=str(tmp_path / "agent-runs"),
    )
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    sandbox = LocalAgentSandboxProvider(settings).acquire(input_payload={})
    writer = AgentEventWriter(sandbox.events_path)
    registry = ToolRegistry()
    register_runner_tools(registry)
    context = ToolExecutionContext(
        sandbox=sandbox,
        memory_store=FileMemoryStore(sandbox.memory_path),
        memory_scope="psop.runner",
        event_writer=writer,
        invocation_context=payload["context"],
        invocation_input=payload["input"],
    )

    result = registry.execute(
        "psop.runner.submit_observation",
        {
            "schema": "psop.runner.observation.v1",
            "node_id": "evaluate_collect_context",
            "decision": "continue",
            "terminal_message": "继续。",
            "reason": "测试。",
            "next_phase": "",
            "source_refs": ["terminal_event:999"],
        },
        context,
    )

    assert result["status"] == "error"
    assert "terminal_event:999" in result["message"]
    assert writer.events[-1].event_type == "agent.validation.failed"
    assert not sandbox.resolve_virtual_path(RUNNER_OBSERVATION_VIRTUAL_PATH).exists()


def test_runner_observation_validates_strict_source_refs() -> None:
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    context = payload["context"]
    context["prompt_view"]["node"] = {"id": "evaluate_collect_context"}
    context["runtime_contract"]["wait_checkpoints"] = [
        {"checkpoint_id": "collect_context_evidence", "workflow_step_id": "collect_context"}
    ]
    context["trace_summary"] = [{"seq_no": 7, "event_type": "runtime.wait_checkpoint.entered"}]
    context["task_identity"] = {"name": "测试 Skill", "description": "测试任务", "version": 1}
    context["runtime_contract"]["applicability"] = {"applies_when": ["测试现场"]}
    context["runtime_contract"]["safety_constraints"] = ["确认安全后继续。"]
    context["runtime_contract"]["completion_criteria"] = ["当前步骤完成。"]
    context["terminal_cursor"] = 1
    observation = _valid_runner_observation()
    observation["source_refs"] = [
        "runtime_contract.workflow_steps.collect_context",
        "runtime_contract.expected_evidence.collect_context",
        "runtime_contract.wait_checkpoints.collect_context_evidence",
        "task_identity.name",
        "runtime_contract.execution_goal",
        "runtime_contract.applicability",
        "runtime_contract.safety_constraints",
        "runtime_contract.completion_criteria",
        "prompt_view.node.id",
        "current_checkpoint.checkpoint_id",
        "trace_summary:7",
        "terminal_event:1",
        "terminal_event:1:text_1",
    ]
    observation["evidence_assessment"]["accepted_event_refs"] = ["terminal_event:1:text_1"]

    validated = validate_runner_observation(
        observation,
        invocation_input=payload["input"],
        invocation_context=context,
    )

    assert validated["runtime_decision"] == "proceed"


def test_runner_observation_validates_requirement_results() -> None:
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    payload["context"]["evidence_progress"] = {
        "checkpoint_id": "collect_context_evidence",
        "workflow_step_id": "collect_context",
        "requirements": [
            {
                "requirement_key": "evidence_1",
                "description": "现场文字说明",
                "status": "missing",
                "accepted_event_refs": [],
                "rejected_event_refs": [],
                "latest_event_refs": [],
            }
        ],
    }
    observation = _valid_runner_observation()
    observation["evidence_assessment"]["requirement_results"] = [
        {
            "requirement_key": "evidence_1",
            "status": "accepted",
            "event_refs": ["terminal_event:1"],
            "reason": "现场说明满足要求。",
        }
    ]

    validated = validate_runner_observation(
        observation,
        invocation_input=payload["input"],
        invocation_context=payload["context"],
    )

    assert validated["evidence_assessment"]["requirement_results"][0]["requirement_key"] == "evidence_1"


def test_runner_observation_rejects_unknown_requirement_key() -> None:
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    payload["context"]["evidence_progress"] = {
        "checkpoint_id": "collect_context_evidence",
        "workflow_step_id": "collect_context",
        "requirements": [{"requirement_key": "evidence_1", "description": "现场文字说明"}],
    }
    observation = _valid_runner_observation()
    observation["evidence_assessment"]["requirement_results"] = [
        {
            "requirement_key": "missing_key",
            "status": "accepted",
            "event_refs": ["terminal_event:1"],
            "reason": "测试。",
        }
    ]

    with pytest.raises(ValueError, match="requirement_key"):
        validate_runner_observation(
            observation,
            invocation_input=payload["input"],
            invocation_context=payload["context"],
        )


def test_runner_observation_accepts_allowed_reference_image_ref() -> None:
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    allowed_ref = payload["context"]["step_reference_images"][0]["reference_image_ref"]
    observation = _valid_runner_observation()
    observation["reference_images"] = [{"reference_image_ref": allowed_ref}]

    validated = validate_runner_observation(
        observation,
        invocation_input=payload["input"],
        invocation_context=payload["context"],
    )

    assert validated["reference_images"][0]["reference_image_ref"] == allowed_ref


def test_runner_observation_rejects_unlisted_reference_image_ref() -> None:
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    observation = _valid_runner_observation()
    observation["reference_images"] = [{"reference_image_ref": "skill-reference://steps/collect-context/missing"}]

    with pytest.raises(ValueError, match="reference_image_ref"):
        validate_runner_observation(
            observation,
            invocation_input=payload["input"],
            invocation_context=payload["context"],
        )

    payload["context"]["step_reference_images"] = []
    observation["reference_images"] = [{"reference_image_ref": "skill-reference://steps/collect-context/site-overview"}]
    with pytest.raises(ValueError, match="没有允许"):
        validate_runner_observation(
            observation,
            invocation_input=payload["input"],
            invocation_context=payload["context"],
        )


@pytest.mark.parametrize(
    "source_ref",
    [
        "runtime_contract.workflow_steps.missing_step",
        "runtime_contract.expected_evidence.missing_step",
        "runtime_contract.wait_checkpoints.missing_checkpoint",
        "prompt_view.node.missing",
        "current_checkpoint.missing",
        "trace_summary:999",
        "unsupported.prefix",
        "",
    ],
)
def test_runner_observation_rejects_invalid_source_refs(source_ref: str) -> None:
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    payload["context"]["prompt_view"]["node"] = {"id": "evaluate_collect_context"}
    observation = _valid_runner_observation()
    observation["source_refs"] = [source_ref]

    with pytest.raises(ValueError):
        validate_runner_observation(
            observation,
            invocation_input=payload["input"],
            invocation_context=payload["context"],
        )


def test_runner_observation_rejects_terminal_event_after_cursor() -> None:
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    later_event = {**payload["context"]["terminal_events"][0], "seq_no": 2}
    payload["context"]["terminal_events"].append(later_event)
    payload["context"]["terminal_cursor"] = 1
    observation = _valid_runner_observation()
    observation["source_refs"] = ["terminal_event:2"]

    with pytest.raises(ValueError, match="terminal cursor"):
        validate_runner_observation(
            observation,
            invocation_input=payload["input"],
            invocation_context=payload["context"],
        )


def test_runner_observation_rejects_missing_terminal_part_ref() -> None:
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    observation = _valid_runner_observation()
    observation["source_refs"] = ["terminal_event:1:missing_part"]

    with pytest.raises(ValueError, match="terminal event"):
        validate_runner_observation(
            observation,
            invocation_input=payload["input"],
            invocation_context=payload["context"],
        )


def test_runner_observation_evidence_refs_only_allow_terminal_events() -> None:
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    observation = _valid_runner_observation()
    observation["evidence_assessment"]["accepted_event_refs"] = ["runtime_contract.workflow_steps.collect_context"]

    with pytest.raises(ValueError, match="只能引用 terminal_event"):
        validate_runner_observation(
            observation,
            invocation_input=payload["input"],
            invocation_context=payload["context"],
        )


def test_runner_terminal_part_tool_does_not_expose_object_key(tmp_path) -> None:
    settings = Settings(
        database_url="sqlite+pysqlite:///:memory:",
        agent_harness_sandbox_root=str(tmp_path / "agent-runs"),
    )
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    payload["context"]["terminal_events"][0]["parts"][0]["metadata"] = {
        "filename": "site.jpg",
        "object_key": "private/minio/key",
        "download_url": "https://internal.example/download",
    }
    payload["context"]["input_attachments"] = [
        {
            "attachment_id": "terminal_event:1:text_1",
            "source_ref": "terminal_event:1:text_1",
        }
    ]
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

    result = registry.execute(
        "psop.runner.read_terminal_event_part",
        {"seq_no": 1, "part_id": "text_1"},
        context,
    )

    assert result["status"] == "success"
    assert result["part"]["attachment_source_ref"] == "terminal_event:1:text_1"
    assert result["part"]["attachment_available"] is True
    serialized = json.dumps(result, ensure_ascii=False)
    assert "private/minio/key" not in serialized
    assert "internal.example" not in serialized


def _valid_runner_observation() -> dict:
    return {
        "schema": "psop.runner.observation.v1",
        "node_id": "evaluate_collect_context",
        "decision": "continue",
        "terminal_message": "已确认当前证据，可以继续最终核验。",
        "reason": "用户提交了当前步骤说明。",
        "next_phase": "",
        "wait_reason": "",
        "expected_inputs": [],
        "evidence_assessment": {
            "accepted_event_refs": ["terminal_event:1"],
            "rejected_event_refs": [],
            "missing_evidence": [],
            "unsafe_or_ambiguous_facts": [],
        },
        "reference_images": [],
        "safety_flags": [],
        "final_response": "",
        "source_refs": ["runtime_contract.workflow_steps.collect_context", "terminal_event:1"],
        "confidence": "high",
    }
