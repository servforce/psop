from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from app.agent_harness.events import AgentEventWriter
from app.agent_harness.errors import AgentBudgetExceededError
from app.agent_harness.middlewares.tool_calls import ToolCallMiddleware
from app.agent_harness.agents.psop.builder.schemas import BuilderCandidateValidationError, validate_builder_candidate
from app.agent_harness.agents.registry import default_agent_registry
from app.agent_harness.models.scripted_builder_chat_model import ScriptedBuilderChatModel
from app.agent_harness.schemas import AgentEvent, AgentInvocation, AgentResult
from app.agent_harness.service import AgentHarnessService
from app.agent_harness.skills.loader import SkillLoader
from app.agent_harness.tools.builtin.builder import BUILDER_REFERENCE_ASSET_FILES_CONTEXT_KEY, register_builder_tools
from app.agent_harness.tools.builtin.standard import register_standard_tools
from app.agent_harness.tools.registry import ToolExecutionContext, ToolRegistry
from app.agent_harness.memory.file_store import FileMemoryStore
from app.agent_harness.sandbox.local import LocalAgentSandboxProvider
from app.core.config import Settings
from app.domain.skills.service import SkillsService


FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "psop_builder" / "minimal.json"


def test_psop_builder_definition_and_skills_load() -> None:
    settings = Settings(database_url="sqlite+pysqlite:///:memory:")
    package = default_agent_registry(settings.backend_root).load("psop.builder")
    loader = SkillLoader(settings.repo_root / "skills")

    assert package.definition.description.startswith("根据用户目标")
    assert package.definition.version == "v2"
    assert package.definition.memory_scope == "psop.builder"
    middleware = {item.name: item.config for item in package.definition.middleware if not isinstance(item, str)}
    assert middleware["model_events"]["max_model_calls"] == 13
    assert middleware["tool_calls"]["max_error_counts"]["psop.builder.submit_candidate"] == 4
    for skill_name in package.definition.skills:
        skill = loader.load_metadata(skill_name)
        assert skill.description
        assert "psop" in skill.name


def test_psop_builder_scripted_run_creates_candidate_artifact(tmp_path) -> None:
    settings = Settings(
        database_url="sqlite+pysqlite:///:memory:",
        agent_harness_sandbox_root=str(tmp_path / "agent-runs"),
        standard_lightrag_base_url="",
        standard_lightrag_api_key="",
    )
    service = AgentHarnessService(
        settings=settings,
        chat_model_factory=lambda _definition: ScriptedBuilderChatModel(),
    )
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    result = service.invoke(
        AgentInvocation(
            agent_key="psop.builder",
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
    artifact_path = Path(result.sandbox_path or "") / "outputs" / "builder-result.json"
    files_root = Path(result.sandbox_path or "") / "outputs" / "skill-draft"
    candidate = json.loads(artifact_path.read_text(encoding="utf-8"))

    assert result.status == "succeeded"
    assert candidate["schema_version"] == "2.0"
    assert "agent.memory.read" in event_types
    assert {"psop.builder.read_current_source", "psop.builder.submit_candidate"}.issubset(completed_tools)
    assert any(artifact.artifact_type == "skill_draft_candidate" for artifact in result.artifacts)
    assert any(artifact.artifact_type == "skill_draft_files" for artifact in result.artifacts)
    assert (files_root / "README.md").read_text(encoding="utf-8") == candidate["files"]["README.md"]
    assert (files_root / "prompts" / "system.md").read_text(encoding="utf-8") == candidate["files"]["prompts/system.md"]
    assert (files_root / "tests" / "checklist.md").read_text(encoding="utf-8") == candidate["files"]["tests/checklist.md"]
    validate_builder_candidate(
        candidate,
        candidate_reference_assets=payload["context"]["candidate_reference_assets"],
        standard_search_results=[],
    )


def test_list_materials_filters_nested_video_material_kind(tmp_path) -> None:
    settings = Settings(
        database_url="sqlite+pysqlite:///:memory:",
        agent_harness_sandbox_root=str(tmp_path / "agent-runs"),
    )
    sandbox = LocalAgentSandboxProvider(settings).acquire(input_payload={})
    registry = ToolRegistry()
    register_builder_tools(registry)
    context = ToolExecutionContext(
        sandbox=sandbox,
        memory_store=FileMemoryStore(sandbox.memory_path),
        memory_scope="psop.builder",
        event_writer=AgentEventWriter(sandbox.events_path),
        invocation_input={"material_ids": ["material-1"]},
        invocation_context={
            "material_analysis_results": [
                {
                    "schema_version": "1.0",
                    "material_type": "video",
                    "source": {
                        "material_id": "material-1",
                        "material_kind": "video",
                        "filename": "install.mp4",
                        "mime_type": "video/mp4",
                    },
                    "status": "ready",
                    "summary": {"text": "装机视频解析摘要"},
                }
            ]
        },
    )

    result = registry.execute("psop.builder.list_materials", {"material_kinds": ["video"]}, context)

    assert result["status"] == "success"
    assert result["summary"] == "列出 1 个可用素材。"
    assert result["items"][0]["material_id"] == "material-1"
    assert result["items"][0]["kind"] == "video"
    assert result["items"][0]["analysis_status"] == "succeeded"


def test_submit_candidate_materializes_reference_images_at_usage_site(tmp_path) -> None:
    settings = Settings(
        database_url="sqlite+pysqlite:///:memory:",
        agent_harness_sandbox_root=str(tmp_path / "agent-runs"),
    )
    sandbox = LocalAgentSandboxProvider(settings).acquire(input_payload={})
    reference_path = "references/video-keyframes/material-1/000000000.jpg"
    registry = ToolRegistry()
    register_builder_tools(registry)
    context = ToolExecutionContext(
        sandbox=sandbox,
        memory_store=FileMemoryStore(sandbox.memory_path),
        memory_scope="psop.builder",
        event_writer=AgentEventWriter(sandbox.events_path),
        invocation_context={
            "candidate_reference_assets": [
                {
                    "id": "asset-1",
                    "asset_id": "asset-1",
                    "material_id": "material-1",
                    "asset_kind": "keyframe",
                    "reference_path": reference_path,
                }
            ],
            BUILDER_REFERENCE_ASSET_FILES_CONTEXT_KEY: [
                {
                    "asset_id": "asset-1",
                    "reference_path": reference_path,
                    "mime_type": "image/jpeg",
                    "content_base64": "ZmFrZS1pbWFnZQ==",
                }
            ],
        },
    )

    result = registry.execute(
        "psop.builder.submit_candidate",
        _builder_candidate_payload(reference_path),
        context,
    )

    materialized_skill = (sandbox.outputs_path / "skill-draft" / "SKILL.md").read_text(encoding="utf-8")
    builder_result = json.loads((sandbox.outputs_path / "builder-result.json").read_text(encoding="utf-8"))
    materialized_image = sandbox.outputs_path / "skill-draft" / reference_path

    assert result["status"] == "success"
    assert result["validation_summary"]["materialized_reference_image_count"] == 1
    assert builder_result["materialized_reference_image_count"] == 1
    assert reference_path in builder_result["files"]["SKILL.md"]
    assert reference_path in materialized_skill
    assert f"]({reference_path})" in materialized_skill
    assert "data:image/" not in materialized_skill
    assert materialized_image.read_bytes() == b"fake-image"
    assert "## 嵌入参考图片" not in materialized_skill
    assert (
        materialized_skill.index("### [stage_01_entry]")
        < materialized_skill.index(f"]({reference_path})")
        < materialized_skill.index("### [stage_02_pressure]")
    )


def test_submit_candidate_invalid_payload_returns_auditable_repair_hint(tmp_path) -> None:
    settings = Settings(
        database_url="sqlite+pysqlite:///:memory:",
        agent_harness_sandbox_root=str(tmp_path / "agent-runs"),
    )
    sandbox = LocalAgentSandboxProvider(settings).acquire(input_payload={})
    writer = AgentEventWriter(sandbox.events_path)
    registry = ToolRegistry()
    register_builder_tools(registry)
    context = ToolExecutionContext(
        sandbox=sandbox,
        memory_store=FileMemoryStore(sandbox.memory_path),
        memory_scope="psop.builder",
        event_writer=writer,
        invocation_context={"candidate_reference_assets": []},
    )

    result = registry.execute(
        "psop.builder.submit_candidate",
        {"evidence_map": [], "workflow_step_candidates": []},
        context,
    )

    assert result["status"] == "error"
    assert result["type"] == "invalid_arguments"
    assert result["retry_requires_argument_correction"] is True
    assert "files 对象必须包含所有必需 Markdown 文件" in result["correction_hint"]
    assert result["repair_checklist"]
    assert result["repair_checklist"][0]["field"] == "directory_tree"
    assert "README.md" in result["required_files"]
    assert writer.events[-1].event_type == "agent.validation.failed"
    assert not (sandbox.outputs_path / "builder-result.json").exists()


def test_read_material_analysis_clamps_out_of_range_max_chars(tmp_path) -> None:
    settings = Settings(
        database_url="sqlite+pysqlite:///:memory:",
        agent_harness_sandbox_root=str(tmp_path / "agent-runs"),
    )
    sandbox = LocalAgentSandboxProvider(settings).acquire(input_payload={})
    registry = ToolRegistry()
    register_builder_tools(registry)
    context = ToolExecutionContext(
        sandbox=sandbox,
        memory_store=FileMemoryStore(sandbox.memory_path),
        memory_scope="psop.builder",
        event_writer=AgentEventWriter(sandbox.events_path),
        invocation_input={"material_ids": ["material-1"]},
        invocation_context={
            "material_analysis_results": [
                {
                    "material_id": "material-1",
                    "summary": "安装前检查。",
                    "observed_actions": ["断电", "安装主板"],
                    "content": {"text": "x" * 2000},
                }
            ]
        },
    )

    result = registry.execute(
        "psop.builder.read_material_analysis",
        {"material_id": "material-1", "detail_level": "full", "max_chars": 10},
        context,
    )

    assert result["status"] == "success"
    assert "raw_analysis" in result
    assert len(result["raw_analysis"]) > 900
    assert result["truncated"] is True


def test_list_reference_assets_normalizes_video_keyframe_kind(tmp_path) -> None:
    settings = Settings(
        database_url="sqlite+pysqlite:///:memory:",
        agent_harness_sandbox_root=str(tmp_path / "agent-runs"),
    )
    sandbox = LocalAgentSandboxProvider(settings).acquire(input_payload={})
    registry = ToolRegistry()
    register_builder_tools(registry)
    context = ToolExecutionContext(
        sandbox=sandbox,
        memory_store=FileMemoryStore(sandbox.memory_path),
        memory_scope="psop.builder",
        event_writer=AgentEventWriter(sandbox.events_path),
        invocation_context={
            "candidate_reference_assets": [
                {
                    "id": "asset-1",
                    "material_id": "material-1",
                    "asset_kind": "video_keyframe",
                    "reference_path": "references/video-keyframes/material-1/000001.jpg",
                    "label": "CPU 安装关键帧。",
                }
            ]
        },
    )

    result = registry.execute(
        "psop.builder.list_reference_assets",
        {"material_id": "material-1", "asset_kinds": ["keyframe"], "max_items": 10},
        context,
    )

    assert result["status"] == "success"
    assert result["items"][0]["asset_id"] == "asset-1"
    assert result["items"][0]["asset_kind"] == "keyframe"
    assert result["items"][0]["source_asset_kind"] == "video_keyframe"


def test_submit_candidate_accepts_full_candidate_asset_after_truncated_list(tmp_path) -> None:
    settings = Settings(
        database_url="sqlite+pysqlite:///:memory:",
        agent_harness_sandbox_root=str(tmp_path / "agent-runs"),
    )
    sandbox = LocalAgentSandboxProvider(settings).acquire(input_payload={})
    registry = ToolRegistry()
    register_builder_tools(registry)
    first_path = "references/video-keyframes/material-1/000001.jpg"
    second_path = "references/video-keyframes/material-1/000002.jpg"
    context = ToolExecutionContext(
        sandbox=sandbox,
        memory_store=FileMemoryStore(sandbox.memory_path),
        memory_scope="psop.builder",
        event_writer=AgentEventWriter(sandbox.events_path),
        invocation_context={
            "candidate_reference_assets": [
                {
                    "id": "asset-1",
                    "material_id": "material-1",
                    "asset_kind": "video_keyframe",
                    "reference_path": first_path,
                    "label": "第一个候选帧。",
                },
                {
                    "id": "asset-2",
                    "material_id": "material-1",
                    "asset_kind": "video_keyframe",
                    "reference_path": second_path,
                    "label": "第二个候选帧。",
                },
            ],
            BUILDER_REFERENCE_ASSET_FILES_CONTEXT_KEY: [
                {
                    "asset_id": "asset-2",
                    "reference_path": second_path,
                    "mime_type": "image/jpeg",
                    "content_base64": "ZmFrZS1pbWFnZQ==",
                }
            ],
        },
    )
    registry.execute(
        "psop.builder.list_reference_assets",
        {"material_id": "material-1", "asset_kinds": ["keyframe"], "max_items": 1},
        context,
    )
    payload = _builder_candidate_payload(second_path)
    payload["selected_reference_assets"][0]["asset_id"] = "asset-2"
    payload["selected_reference_assets"][0]["reference_path"] = second_path
    payload["evidence_map"][0]["source_refs"][0]["asset_id"] = "asset-2"

    result = registry.execute("psop.builder.submit_candidate", payload, context)

    assert result["status"] == "success"
    assert result["validation_summary"]["reference_asset_count"] == 1


def test_builder_candidate_rejects_v1_evidence_source_aliases() -> None:
    reference_path = "references/video-keyframes/material-1/000001.jpg"
    payload = _builder_candidate_payload(reference_path)
    payload["evidence_map"][0]["source_refs"] = [
        {"source_type": "material", "material_id": "material-1"},
        "875f7af3-8bad-40e1-a23b-56e7dcdc47d0#keyframe-10",
    ]

    with pytest.raises(BuilderCandidateValidationError) as exc_info:
        validate_builder_candidate(payload)

    paths = {item["path"] for item in exc_info.value.diagnostics}
    assert "evidence_map.0.source_refs.0.source_type" in paths
    assert "evidence_map.0.source_refs.1" in paths


def test_builder_candidate_matches_workflow_step_with_normalized_spacing() -> None:
    reference_path = "references/video-keyframes/material-1/000001.jpg"
    payload = _builder_candidate_payload(reference_path)
    payload["files"]["SKILL.md"] += "\n### [stage_03_bios] 首次点亮与 BIOS 配置\n确认 BIOS 设置。\n"
    payload["workflow_step_candidates"].append({"stage_id": "stage_03_bios", "title": "首次点亮与BIOS配置"})
    payload["evidence_map"].append(
        {
            "claim": "首次点亮与 BIOS 配置需要人工确认。",
            "support_level": "observed_fact",
            "source_refs": [{"source_type": "material_analysis", "material_id": "material-1"}],
            "used_in": [{"target_type": "workflow_stage", "target_id": "stage_03_bios"}],
        }
    )

    validate_builder_candidate(
        payload,
        candidate_reference_assets=[
            {
                "id": "asset-1",
                "material_id": "material-1",
                "asset_kind": "video_keyframe",
                "reference_path": reference_path,
            }
        ],
        standard_search_results=[],
    )


def test_builder_candidate_accepts_structured_current_source_reference() -> None:
    payload = _builder_candidate_payload("references/video-keyframes/material-1/000001.jpg")
    payload["evidence_map"].append(
        {
            "claim": "当前 draft 需要修订。",
            "support_level": "current_source_fact",
            "source_refs": [{"source_type": "current_source", "ref": "SKILL.md"}],
            "used_in": [{"target_type": "review_notes", "target_id": "review_notes"}],
        }
    )

    candidate = validate_builder_candidate(
        payload,
        candidate_reference_assets=[{"id": "asset-1", "reference_path": "references/video-keyframes/material-1/000001.jpg"}],
    )

    assert candidate.evidence_map[-1].source_refs[0].source_type == "current_source"
    assert candidate.evidence_map[-1].source_refs[0].ref == "SKILL.md"


def test_builder_candidate_rejects_tool_timeout_as_evidence_source() -> None:
    payload = _builder_candidate_payload("references/video-keyframes/material-1/000001.jpg")
    payload["evidence_map"][0]["source_refs"] = [{"source_type": "psop.standard.search/timeout"}]

    with pytest.raises(BuilderCandidateValidationError) as exc_info:
        validate_builder_candidate(payload)

    diagnostics = exc_info.value.diagnostics
    diagnostic = next(item for item in diagnostics if item["path"].endswith("source_type"))
    assert "industry_standard" in diagnostic["allowed_values"]


def test_builder_candidate_rejects_unknown_support_level_with_repair_example() -> None:
    payload = _builder_candidate_payload("references/video-keyframes/material-1/000001.jpg")
    payload["evidence_map"][0]["support_level"] = "unverified"

    with pytest.raises(BuilderCandidateValidationError) as exc_info:
        validate_builder_candidate(payload)

    diagnostic = exc_info.value.diagnostics[0]
    assert diagnostic["path"] == "evidence_map.0.support_level"
    assert "observed_fact" in diagnostic["allowed_values"]
    assert diagnostic["example"] == "observed_fact"


def test_builder_candidate_v2_rejects_missing_version_and_v1_aliases_together() -> None:
    payload = _builder_candidate_payload("references/video-keyframes/material-1/000001.jpg")
    payload.pop("schema_version")
    payload["workflow_step_candidates"][0]["step_id"] = payload["workflow_step_candidates"][0].pop("stage_id")
    payload["safety_constraints"][0]["applies_to"] = "stage_01_entry"

    with pytest.raises(BuilderCandidateValidationError) as exc_info:
        validate_builder_candidate(payload)

    paths = {item["path"] for item in exc_info.value.diagnostics}
    assert {
        "schema_version",
        "workflow_step_candidates.0.stage_id",
        "workflow_step_candidates.0.step_id",
        "safety_constraints.0.applies_to",
    }.issubset(paths)


def test_builder_candidate_v2_rejects_v1_schema_version() -> None:
    payload = _builder_candidate_payload("references/video-keyframes/material-1/000001.jpg")
    payload["schema_version"] = "1.0"

    with pytest.raises(BuilderCandidateValidationError) as exc_info:
        validate_builder_candidate(payload)

    diagnostic = next(item for item in exc_info.value.diagnostics if item["path"] == "schema_version")
    assert diagnostic["allowed_values"] == ["2.0"]


def test_builder_candidate_v2_accepts_all_stages_scope_with_empty_stage_ids() -> None:
    payload = _builder_candidate_payload("references/video-keyframes/material-1/000001.jpg")
    payload["safety_constraints"][0]["scope"] = "all_stages"
    payload["safety_constraints"][0]["stage_ids"] = []

    candidate = validate_builder_candidate(payload)

    assert candidate.safety_constraints[0].scope == "all_stages"
    assert candidate.safety_constraints[0].stage_ids == []


def test_builder_candidate_v2_collects_id_scope_and_reference_errors() -> None:
    payload = _builder_candidate_payload("references/video-keyframes/material-1/000001.jpg")
    payload["workflow_step_candidates"].append({"stage_id": "stage_01_entry", "title": "重复阶段"})
    payload["safety_constraints"][0]["scope"] = "all_stages"
    payload["safety_constraints"].append(
        {
            "constraint_id": "safety_01_entry",
            "scope": "selected_stages",
            "stage_ids": [],
            "constraint": "重复约束。",
            "risk_type": "personal_safety",
            "required_action": "停止。",
        }
    )
    payload["expected_evidence_requirements"][0]["stage_id"] = "stage_99_unknown"
    payload["expected_evidence_requirements"].append(
        {
            "requirement_id": "evidence_01_entry",
            "stage_id": "stage_01_entry",
            "evidence_type": "photo",
            "completion_criteria": "重复证据要求。",
        }
    )
    payload["selected_reference_assets"][0]["stage_ids"] = ["stage_98_unknown"]
    payload["evidence_map"][0]["used_in"].extend(
        [
            {"target_type": "workflow_stage", "target_id": "stage_97_unknown"},
            {"target_type": "safety_constraint", "target_id": "safety_99_unknown"},
            {"target_type": "expected_evidence", "target_id": "evidence_99_unknown"},
        ]
    )

    with pytest.raises(BuilderCandidateValidationError) as exc_info:
        validate_builder_candidate(payload)

    diagnostics = {(item["path"], item["code"]) for item in exc_info.value.diagnostics}
    assert (
        "workflow_step_candidates.2.stage_id",
        "duplicate_id",
    ) in diagnostics
    assert ("safety_constraints.1.constraint_id", "duplicate_id") in diagnostics
    assert ("expected_evidence_requirements.1.requirement_id", "duplicate_id") in diagnostics
    assert ("safety_constraints.0.stage_ids", "invalid_scope_stage_ids") in diagnostics
    assert ("safety_constraints.1.stage_ids", "missing_stage_ids") in diagnostics
    assert ("expected_evidence_requirements.0.stage_id", "unknown_stage_id") in diagnostics
    assert ("selected_reference_assets.0.stage_ids", "unknown_stage_id") in diagnostics
    assert ("evidence_map.0.used_in.3.target_id", "unknown_evidence_target") in diagnostics
    assert ("evidence_map.0.used_in.4.target_id", "unknown_evidence_target") in diagnostics
    assert ("evidence_map.0.used_in.5.target_id", "unknown_evidence_target") in diagnostics


def test_builder_candidate_v2_rejects_invalid_stable_id() -> None:
    payload = _builder_candidate_payload("references/video-keyframes/material-1/000001.jpg")
    payload["workflow_step_candidates"][0]["stage_id"] = "Stage 1"

    with pytest.raises(BuilderCandidateValidationError) as exc_info:
        validate_builder_candidate(payload)

    diagnostic = next(
        item for item in exc_info.value.diagnostics if item["path"] == "workflow_step_candidates.0.stage_id"
    )
    assert diagnostic["code"] == "string_pattern_mismatch"


def test_builder_candidate_reports_all_missing_safety_evidence_coverage() -> None:
    payload = _builder_candidate_payload("references/video-keyframes/material-1/000001.jpg")
    payload["safety_constraints"].append(
        {
            "constraint_id": "safety_02_pressure",
            "scope": "selected_stages",
            "stage_ids": ["stage_02_pressure"],
            "constraint": "压力读数异常不得继续。",
            "risk_type": "equipment_pressure",
            "required_action": "停止并请求复核。",
        }
    )
    for evidence in payload["evidence_map"]:
        evidence["used_in"] = [target for target in evidence["used_in"] if target["target_type"] != "safety_constraint"]

    with pytest.raises(BuilderCandidateValidationError) as exc_info:
        validate_builder_candidate(payload)

    safety_paths = {
        item["path"]
        for item in exc_info.value.diagnostics
        if item["code"] == "missing_evidence_coverage" and item["path"].startswith("safety_constraints.")
    }
    assert safety_paths == {"safety_constraints.0", "safety_constraints.1"}


def test_builder_candidate_collects_errors_across_all_semantic_checks() -> None:
    reference_path = "references/video-keyframes/material-1/000001.jpg"
    payload = _builder_candidate_payload(reference_path)
    payload["files"].pop("README.md")
    payload["files"]["tests/checklist.md"] += "\n- TODO：补充场景。\n"
    payload["selected_reference_assets"][0].update(
        {
            "asset_id": "asset-missing",
            "reference_path": "references/video-keyframes/material-1/missing.jpg",
            "stage_ids": ["stage_99_unknown"],
        }
    )
    payload["workflow_step_candidates"].append({"stage_id": "stage_03_uncovered", "title": "未声明标题阶段"})
    payload["safety_constraints"].append(
        {
            "constraint_id": "safety_02_uncovered",
            "scope": "selected_stages",
            "stage_ids": ["stage_03_uncovered"],
            "constraint": "未确认状态时停止。",
            "risk_type": "unknown_state",
            "required_action": "停止并请求复核。",
        }
    )
    payload["expected_evidence_requirements"].append(
        {
            "requirement_id": "evidence_02_uncovered",
            "stage_id": "stage_03_uncovered",
            "evidence_type": "photo",
            "completion_criteria": "照片显示状态。",
        }
    )

    with pytest.raises(BuilderCandidateValidationError) as exc_info:
        validate_builder_candidate(
            payload,
            candidate_reference_assets=[
                {
                    "asset_id": "asset-known",
                    "reference_path": "references/video-keyframes/material-1/known.jpg",
                }
            ],
        )

    diagnostics = {(item["path"], item["code"]) for item in exc_info.value.diagnostics}
    assert ("files.README.md", "missing_required_file") in diagnostics
    assert ("files.tests/checklist.md", "placeholder_content") in diagnostics
    assert ("selected_reference_assets.0.asset_id", "unknown_reference_asset") in diagnostics
    assert ("selected_reference_assets.0.reference_path", "unknown_reference_path") in diagnostics
    assert ("selected_reference_assets.0.stage_ids", "unknown_stage_id") in diagnostics
    assert ("workflow_step_candidates.2.stage_id", "workflow_not_in_skill") in diagnostics
    assert ("workflow_step_candidates.2", "missing_evidence_coverage") in diagnostics
    assert ("safety_constraints.1", "missing_evidence_coverage") in diagnostics
    assert ("expected_evidence_requirements.1", "missing_evidence_coverage") in diagnostics


def test_submit_candidate_returns_grouped_repair_checklist_for_all_metadata_errors(tmp_path) -> None:
    settings = Settings(database_url="sqlite+pysqlite:///:memory:", agent_harness_sandbox_root=str(tmp_path / "agent-runs"))
    sandbox = LocalAgentSandboxProvider(settings).acquire(input_payload={})
    registry = ToolRegistry()
    register_builder_tools(registry)
    context = ToolExecutionContext(
        sandbox=sandbox,
        memory_store=FileMemoryStore(sandbox.memory_path),
        memory_scope="psop.builder",
        event_writer=AgentEventWriter(sandbox.events_path),
        invocation_context={"candidate_reference_assets": []},
    )
    payload = _builder_candidate_payload("references/video-keyframes/material-1/000001.jpg")
    payload["evidence_map"][0].pop("support_level")
    payload["missing_questions"][0].pop("reason")

    result = registry.execute("psop.builder.submit_candidate", payload, context)

    fields = {item["field"] for item in result["repair_checklist"]}
    assert {"evidence_map", "missing_questions"}.issubset(fields)


def test_submit_candidate_returns_all_diagnostics_without_eight_item_cap(tmp_path) -> None:
    settings = Settings(database_url="sqlite+pysqlite:///:memory:", agent_harness_sandbox_root=str(tmp_path / "agent-runs"))
    sandbox = LocalAgentSandboxProvider(settings).acquire(input_payload={})
    writer = AgentEventWriter(sandbox.events_path)
    registry = ToolRegistry()
    register_builder_tools(registry)
    context = ToolExecutionContext(
        sandbox=sandbox,
        memory_store=FileMemoryStore(sandbox.memory_path),
        memory_scope="psop.builder",
        event_writer=writer,
        invocation_context={"candidate_reference_assets": []},
    )
    payload = _builder_candidate_payload("references/video-keyframes/material-1/000001.jpg")
    for index in range(3, 14):
        stage_id = f"stage_{index:02d}_uncovered"
        payload["workflow_step_candidates"].append({"stage_id": stage_id, "title": f"未覆盖阶段 {index}"})
        payload["files"]["SKILL.md"] += f"\n### [{stage_id}] 未覆盖阶段 {index}\n执行检查。\n"

    result = registry.execute("psop.builder.submit_candidate", payload, context)

    checklist_items = [item for group in result["repair_checklist"] for item in group["items"]]
    event = writer.events[-1]
    assert result["status"] == "error"
    assert result["diagnostic_count"] == len(result["diagnostics"])
    assert result["diagnostic_count"] >= 11
    assert len(checklist_items) == result["diagnostic_count"]
    assert event.event_type == "agent.validation.failed"
    assert event.payload["diagnostic_count"] == result["diagnostic_count"]
    assert event.payload["diagnostics"] == result["diagnostics"]
    assert not (sandbox.outputs_path / "builder-result.json").exists()


def test_skills_service_preserves_all_builder_validation_diagnostics() -> None:
    diagnostics = [
        {
            "path": f"expected_evidence_requirements.{index}",
            "code": "missing_evidence_coverage",
            "message": f"缺少证据 {index}",
        }
        for index in range(12)
    ]
    result = AgentResult(
        agent_run_id="run-diagnostics",
        agent_key="psop.builder",
        status="failed",
        final_output="",
        events=[
            AgentEvent(
                seq_no=1,
                event_type="agent.validation.failed",
                payload={"diagnostics": diagnostics},
                occurred_at=datetime.now(timezone.utc),
            )
        ],
    )

    assert SkillsService._agent_validation_diagnostics(result) == diagnostics
    message = SkillsService._agent_validation_failure_message(result)
    assert "共 12 项" in message
    assert diagnostics[0]["path"] in message


def test_repeated_builder_validation_stops_before_fourth_submission(tmp_path) -> None:
    writer = AgentEventWriter(tmp_path / "events.jsonl")
    middleware = ToolCallMiddleware(writer, max_error_counts={"psop.builder.submit_candidate": 4})
    payload = {
        "tool_name": "psop.builder.submit_candidate",
        "result_status": "error",
        "validation_diagnostics": [{"path": "evidence_map.0.support_level", "code": "missing"}],
    }

    middleware._check_error_budget(payload)
    with pytest.raises(AgentBudgetExceededError, match="重复出现同一候选校验错误"):
        middleware._check_error_budget(payload)


def test_builder_candidate_rejects_current_source_section_path_as_legacy_alias() -> None:
    payload = _builder_candidate_payload("references/video-keyframes/material-1/000001.jpg")
    payload["evidence_map"].append(
        {
            "claim": "伪造路径。",
            "support_level": "current_source_fact",
            "source_refs": [{"source_type": "current_source/SKILL.md/安全约束"}],
            "used_in": [{"target_type": "review_notes", "target_id": "review_notes"}],
        }
    )

    with pytest.raises(BuilderCandidateValidationError) as exc_info:
        validate_builder_candidate(payload)

    assert any("source_type" in item["path"] for item in exc_info.value.diagnostics)


def test_builder_candidate_allows_checklist_assertion_but_rejects_real_todo() -> None:
    payload = _builder_candidate_payload("references/video-keyframes/material-1/000001.jpg")
    payload["files"]["tests/checklist.md"] = "# 检查\n\n- 断言：不得有未完成 TODO 项。\n"
    validate_builder_candidate(payload)

    payload["files"]["tests/checklist.md"] = "# 检查\n\n- TODO：补充场景。\n"
    with pytest.raises(BuilderCandidateValidationError):
        validate_builder_candidate(payload)


def test_builder_candidate_rejects_standard_reference_when_search_timed_out() -> None:
    payload = _builder_candidate_payload("references/video-keyframes/material-1/000001.jpg")
    payload["industry_standard_usage"] = [
        {
            "standard_ref": "GB 1",
            "clause_ref": "1.1",
            "usage": "mandatory",
            "used_in": [{"target_type": "workflow_stage", "target_id": "stage_01_entry"}],
        }
    ]

    with pytest.raises(BuilderCandidateValidationError) as exc_info:
        validate_builder_candidate(payload, standard_search_status="timeout")

    assert {item["code"] for item in exc_info.value.diagnostics} == {
        "standard_search_unavailable",
        "missing_standard_unavailable_note",
    }


def test_standard_search_uses_lightrag_query_endpoint_and_api_key(monkeypatch, tmp_path) -> None:
    settings = Settings(
        database_url="sqlite+pysqlite:///:memory:",
        agent_harness_sandbox_root=str(tmp_path / "agent-runs"),
        standard_lightrag_base_url="http://lightrag.local",
        standard_lightrag_api_key="servforce",
    )
    sandbox = LocalAgentSandboxProvider(settings).acquire(input_payload={})
    registry = ToolRegistry()
    register_standard_tools(registry)
    calls = {}

    def fake_post(url, *, headers, json, timeout):
        calls.update({"url": url, "headers": headers, "json": json, "timeout": timeout})
        return httpx.Response(
            200,
            json={
                "response": "依据 GB/T 1234 5.1 条，应进行进入前检查。",
                "references": [
                    {
                        "reference_id": "1",
                        "file_path": "/standards/GB_T_1234.md",
                        "content": ["GB/T 1234 5.1 条 进入前应检查 PPE 和设备状态。"],
                    }
                ],
            },
        )

    monkeypatch.setattr("app.agent_harness.tools.builtin.standard.httpx.post", fake_post)
    context = ToolExecutionContext(
        sandbox=sandbox,
        memory_store=FileMemoryStore(sandbox.memory_path),
        memory_scope="psop.builder",
        event_writer=AgentEventWriter(sandbox.events_path),
        invocation_context={},
        settings=settings,
    )

    result = registry.execute("psop.standard.search", {"query": "泵房 PPE 检查", "max_results": 99}, context)

    assert result["status"] == "success"
    assert calls["url"] == "http://lightrag.local/query"
    assert calls["headers"]["X-API-Key"] == "servforce"
    assert calls["json"]["mode"] == "mix"
    assert calls["json"]["top_k"] == settings.standard_lightrag_max_results
    assert result["items"][0]["citation_status"] == "complete"


def _builder_candidate_payload(reference_path: str) -> dict:
    return {
        "schema_version": "2.0",
        "directory_tree": "README.md\nSKILL.md\nprompts/system.md\nreferences/README.md\nexamples/input.md\nexamples/expected-output.md\ntests/checklist.md",
        "files": {
            "README.md": "# 泵房进入前安全检查\n\n用于指导进入前检查。\n",
            "SKILL.md": (
                "# 泵房进入前安全检查\n\n"
                "## Workflow\n"
                "### [stage_01_entry] 入口状态确认\n"
                f"参考 `{reference_path}` 判断入口状态。\n\n"
                "### [stage_02_pressure] 压力表记录\n"
                "记录压力表读数。\n\n"
                "## Safety Constraints\n"
                "PPE 不完整不得进入。\n"
            ),
            "prompts/system.md": "按 SKILL.md 推进。\n",
            "references/README.md": f"# 参考资料\n\n![入口关键帧]({reference_path})\n",
            "examples/input.md": "# 输入\n\n检查泵房。\n",
            "examples/expected-output.md": "# 输出\n\n阶段 1 后进入阶段 2。\n",
            "tests/checklist.md": "# 检查\n\n- 阶段 1 使用参考图。\n",
        },
        "generation_reason": "根据素材生成泵房检查 Skill。",
        "review_notes": ["行业标准需要人工确认。"],
        "material_usage": [{"material_id": "material-1", "usage": "识别阶段 1 和阶段 2。"}],
        "industry_standard_usage": [],
        "selected_reference_assets": [
            {
                "asset_id": "asset-1",
                "material_id": "material-1",
                "reference_path": reference_path,
                "reason": "用于判断入口状态。",
                "stage_ids": ["stage_01_entry"],
            }
        ],
        "evidence_map": [
            {
                "claim": "阶段 1 需要确认入口状态。",
                "support_level": "observed_fact",
                "source_refs": [{"source_type": "reference_asset", "asset_id": "asset-1"}],
                "used_in": [
                    {"target_type": "workflow_stage", "target_id": "stage_01_entry"},
                    {"target_type": "safety_constraint", "target_id": "safety_01_entry"},
                    {"target_type": "expected_evidence", "target_id": "evidence_01_entry"},
                ],
            },
            {
                "claim": "阶段 2 需要记录压力表读数。",
                "support_level": "observed_fact",
                "source_refs": [{"source_type": "material_analysis", "material_id": "material-1"}],
                "used_in": [{"target_type": "workflow_stage", "target_id": "stage_02_pressure"}],
            },
        ],
        "missing_questions": [
            {
                "question": "适用标准编号是什么？",
                "reason": "当前未确认。",
                "blocking_level": "non_blocking",
            }
        ],
        "safety_constraints": [
            {
                "constraint_id": "safety_01_entry",
                "scope": "selected_stages",
                "stage_ids": ["stage_01_entry"],
                "constraint": "PPE 不完整不得进入。",
                "risk_type": "personal_safety",
                "required_action": "停止并要求补齐证据。",
            }
        ],
        "workflow_step_candidates": [
            {"stage_id": "stage_01_entry", "title": "入口状态确认"},
            {"stage_id": "stage_02_pressure", "title": "压力表记录"},
        ],
        "expected_evidence_requirements": [
            {
                "requirement_id": "evidence_01_entry",
                "stage_id": "stage_01_entry",
                "evidence_type": "photo",
                "completion_criteria": "入口状态清楚。",
            }
        ],
    }
