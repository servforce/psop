from __future__ import annotations

import json
from pathlib import Path

from app.agent_harness.agents.psop.compiler.schemas import validate_compiler_candidate
from app.agent_harness.agents.registry import default_agent_registry
from app.agent_harness.events import AgentEventWriter
from app.agent_harness.memory.file_store import FileMemoryStore
from app.agent_harness.models.scripted_compiler_chat_model import ScriptedCompilerChatModel
from app.agent_harness.sandbox.local import LocalAgentSandboxProvider
from app.agent_harness.schemas import AgentInvocation
from app.agent_harness.service import AgentHarnessService
from app.agent_harness.skills.loader import SkillLoader
from app.agent_harness.tools.builtin.compiler import register_compiler_tools
from app.agent_harness.tools.registry import ToolExecutionContext, ToolRegistry
from app.core.config import Settings
from app.domain.compiler.formal_v5 import validate_and_normalize_artifact


FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "psop_compiler" / "minimal.json"


def test_psop_compiler_definition_and_skills_load() -> None:
    settings = Settings(database_url="sqlite+pysqlite:///:memory:")
    package = default_agent_registry(settings.backend_root).load("psop.compiler")
    loader = SkillLoader(settings.repo_root / "skills")

    assert package.definition.description.startswith("将冻结的 PSOP Skill source")
    assert package.definition.memory_scope == "psop.compiler"
    assert package.definition.factory == "make_compiler_agent"
    assert "psop.standard.search" not in package.definition.tools
    assert package.definition.skills == ["psop-compiler"]
    for skill_name in package.definition.skills:
        skill = loader.load_metadata(skill_name)
        assert skill.description
        assert skill.name == "psop-compiler"
        assert "psop.compiler.submit_candidate" in skill.allowed_tools
    resource = loader.load_resource("psop-compiler", "core/SKILL.md")
    assert "PSOP Compiler Core" in resource["content"]


def test_psop_compiler_scripted_run_creates_candidate_artifact(tmp_path) -> None:
    settings = Settings(
        database_url="sqlite+pysqlite:///:memory:",
        agent_harness_sandbox_root=str(tmp_path / "agent-runs"),
    )
    service = AgentHarnessService(
        settings=settings,
        chat_model_factory=lambda _definition: ScriptedCompilerChatModel(),
    )
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    result = service.invoke(
        AgentInvocation(
            agent_key="psop.compiler",
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
    candidate_path = Path(result.sandbox_path or "") / "outputs" / "compiler-result.json"
    eg_path = Path(result.sandbox_path or "") / "outputs" / "eg.compile.artifact.json"
    candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    eg_artifact = json.loads(eg_path.read_text(encoding="utf-8"))
    validation = validate_and_normalize_artifact(eg_artifact)

    assert result.status == "succeeded"
    assert "agent.memory.read" in event_types
    assert loaded_skills == {"psop-compiler"}
    assert {
        "core/SKILL.md",
        "contract/SKILL.md",
        "mapping/SKILL.md",
        "review/SKILL.md",
    }.issubset(loaded_resources)
    assert {
        "psop.compiler.read_skill_source",
        "psop.compiler.build_formal_v5_scaffold",
        "psop.compiler.validate_formal_v5",
        "psop.compiler.submit_candidate",
    }.issubset(completed_tools)
    assert any(artifact.artifact_type == "eg_compile_candidate" for artifact in result.artifacts)
    assert any(artifact.artifact_type == "eg_compile_artifact_candidate" for artifact in result.artifacts)
    assert validation.artifact is not None
    assert not validation.has_errors
    validate_compiler_candidate(candidate)


def test_compiler_submit_candidate_validates_and_writes_outputs(tmp_path) -> None:
    settings = Settings(
        database_url="sqlite+pysqlite:///:memory:",
        agent_harness_sandbox_root=str(tmp_path / "agent-runs"),
    )
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    sandbox = LocalAgentSandboxProvider(settings).acquire(input_payload={})
    registry = ToolRegistry()
    register_compiler_tools(registry)
    context = ToolExecutionContext(
        sandbox=sandbox,
        memory_store=FileMemoryStore(sandbox.memory_path),
        memory_scope="psop.compiler",
        event_writer=AgentEventWriter(sandbox.events_path),
        invocation_context=payload["context"],
        invocation_input=payload["input"],
    )
    artifact = _minimal_artifact()
    candidate = {
        "artifact": artifact,
        "compile_reason": "测试提交 compiler candidate。",
        "source_map": [
            {
                "target": "runtime_contract.workflow_steps[*]",
                "source_file": "SKILL.md",
                "source_summary": "测试 source map。",
            }
        ],
        "diagnostics": [],
        "repair_history": [],
        "validator_summary": {"status": "passed", "error_count": 0, "warning_count": 0},
    }

    result = registry.execute("psop.compiler.submit_candidate", candidate, context)

    assert result["status"] == "success"
    assert sandbox.resolve_virtual_path("/mnt/psop/outputs/compiler-result.json").exists()
    assert sandbox.resolve_virtual_path("/mnt/psop/outputs/eg.compile.artifact.json").exists()
    assert result["validation_summary"]["status"] == "passed"


def test_compiler_build_formal_v5_scaffold_creates_valid_candidate(tmp_path) -> None:
    settings = Settings(
        database_url="sqlite+pysqlite:///:memory:",
        agent_harness_sandbox_root=str(tmp_path / "agent-runs"),
    )
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    sandbox = LocalAgentSandboxProvider(settings).acquire(input_payload={})
    registry = ToolRegistry()
    register_compiler_tools(registry)
    context = ToolExecutionContext(
        sandbox=sandbox,
        memory_store=FileMemoryStore(sandbox.memory_path),
        memory_scope="psop.compiler",
        event_writer=AgentEventWriter(sandbox.events_path),
        invocation_context=payload["context"],
        invocation_input=payload["input"],
    )

    result = registry.execute(
        "psop.compiler.build_formal_v5_scaffold",
        {
            "execution_goal": "帮助用户完成测试 Skill。",
            "workflow_steps": [
                {
                    "id": "collect_context",
                    "title": "收集上下文",
                    "goal": "确认用户任务与现场约束。",
                    "source_evidence": "SKILL.md 要求收集用户任务和现场证据。",
                    "expected_evidence": [
                        {"kind": "text", "event_kind": "terminal.text.input.v1", "description": "用户任务说明"}
                    ],
                    "source_file": "SKILL.md",
                },
                {
                    "id": "verify_result",
                    "title": "验证结果",
                    "goal": "确认任务完成状态。",
                    "source_evidence": "SKILL.md 要求最终验证完成标准。",
                    "expected_evidence": [
                        {"kind": "image", "event_kind": "terminal.image.input.v1", "description": "完成状态截图"}
                    ],
                    "source_file": "SKILL.md",
                },
            ],
            "safety_constraints": ["证据不足时暂停。"],
            "completion_criteria": ["两个阶段均完成。"],
            "recovery_paths": [{"when": "evidence_insufficient", "action": "request_more_evidence"}],
            "source_map": [
                {
                    "target": "runtime_contract.workflow_steps[collect_context]",
                    "source_excerpt": "SKILL.md 要求收集用户任务和现场证据。",
                },
                {
                    "target": "nodes[instruct_verify_result],nodes[evaluate_verify_result]",
                    "evidence": "由验证结果阶段机械展开为指令节点和证据评估节点。",
                },
            ],
            "include_full_candidate": True,
        },
        context,
    )

    assert result["status"] == "success"
    assert result["artifact_ref"] == "sandbox://workspace/compiler-scaffold-artifact.json"
    assert result["candidate_ref"] == "sandbox://workspace/compiler-scaffold-candidate.json"
    assert result["full_candidate_omitted"] is True
    assert "artifact" not in result
    assert "candidate" not in result
    assert "source_map" not in result
    candidate = json.loads(sandbox.read_text("/mnt/psop/workspace/compiler-scaffold-candidate.json"))
    validate_compiler_candidate(candidate)
    assert candidate["source_map"][0]["source_file"] == "SKILL.md"
    assert candidate["source_map"][1]["source_summary"] == "由验证结果阶段机械展开为指令节点和证据评估节点。"
    assert all(item.get("source_file") for item in candidate["source_map"])
    assert all(item.get("source_excerpt") or item.get("source_summary") for item in candidate["source_map"])
    validation = validate_and_normalize_artifact(candidate["artifact"])
    assert validation.artifact is not None
    assert not validation.has_errors
    assert len(candidate["artifact"]["runtime_contract"]["workflow_steps"]) == 2
    node_ids = {node["id"] for node in candidate["artifact"]["nodes"]}
    assert "instruct_collect_context" in node_ids
    assert "evaluate_verify_result" in node_ids
    evaluate_collect_context = next(node for node in candidate["artifact"]["nodes"] if node["id"] == "evaluate_collect_context")
    final_verify = next(node for node in candidate["artifact"]["nodes"] if node["id"] == "final_verify")
    assert evaluate_collect_context["interaction"]["transitions"] == {
        "proceed": "instruct_verify_result",
        "complete": "terminal",
        "abort": "terminal",
    }
    assert final_verify["interaction"]["transitions"] == {
        "proceed": "terminal",
        "complete": "terminal",
        "abort": "terminal",
    }
    assert not any(
        operation.get("path") == "phase" and operation.get("from") == "observation.next_phase"
        for node in candidate["artifact"]["nodes"]
        for operation in node.get("merge", [])
        if isinstance(operation, dict)
    )

    validation_result = registry.execute(
        "psop.compiler.validate_formal_v5",
        {"artifact_ref": result["artifact_ref"]},
        context,
    )
    assert validation_result["valid"] is True

    submit_result = registry.execute(
        "psop.compiler.submit_candidate",
        {"candidate_ref": result["candidate_ref"]},
        context,
    )
    assert submit_result["status"] == "success"
    assert sandbox.resolve_virtual_path("/mnt/psop/outputs/compiler-result.json").exists()


def test_compiler_submit_candidate_rejects_invalid_payload(tmp_path) -> None:
    settings = Settings(
        database_url="sqlite+pysqlite:///:memory:",
        agent_harness_sandbox_root=str(tmp_path / "agent-runs"),
    )
    sandbox = LocalAgentSandboxProvider(settings).acquire(input_payload={})
    registry = ToolRegistry()
    register_compiler_tools(registry)
    context = ToolExecutionContext(
        sandbox=sandbox,
        memory_store=FileMemoryStore(sandbox.memory_path),
        memory_scope="psop.compiler",
        event_writer=AgentEventWriter(sandbox.events_path),
        invocation_context={},
        invocation_input={},
    )

    result = registry.execute("psop.compiler.submit_candidate", {"artifact": {}}, context)

    assert result["status"] == "error"
    assert result["type"] == "validation_failed"
    assert not sandbox.resolve_virtual_path("/mnt/psop/outputs").joinpath("compiler-result.json").exists()


def _minimal_artifact() -> dict:
    from app.agent_harness.models.scripted_compiler_chat_model import _artifact

    return _artifact()
