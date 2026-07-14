from __future__ import annotations

import json
from pathlib import Path

import httpx

from app.agent_harness.events import AgentEventWriter
from app.agent_harness.agents.psop.builder.schemas import validate_builder_candidate
from app.agent_harness.agents.registry import default_agent_registry
from app.agent_harness.models.scripted_builder_chat_model import ScriptedBuilderChatModel
from app.agent_harness.schemas import AgentInvocation
from app.agent_harness.service import AgentHarnessService
from app.agent_harness.skills.loader import SkillLoader
from app.agent_harness.tools.builtin.builder import BUILDER_REFERENCE_ASSET_FILES_CONTEXT_KEY, register_builder_tools
from app.agent_harness.tools.builtin.standard import register_standard_tools
from app.agent_harness.tools.registry import ToolExecutionContext, ToolRegistry
from app.agent_harness.memory.file_store import FileMemoryStore
from app.agent_harness.sandbox.local import LocalAgentSandboxProvider
from app.core.config import Settings


FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "psop_builder" / "minimal.json"


def test_psop_builder_definition_and_skills_load() -> None:
    settings = Settings(database_url="sqlite+pysqlite:///:memory:")
    package = default_agent_registry(settings.backend_root).load("psop.builder")
    loader = SkillLoader(settings.repo_root / "skills")

    assert package.definition.description.startswith("根据用户目标")
    assert package.definition.memory_scope == "psop.builder"
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
    assert materialized_skill.index("### 阶段 1") < materialized_skill.index(f"]({reference_path})") < materialized_skill.index("### 阶段 2")


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


def test_builder_candidate_accepts_common_evidence_source_aliases() -> None:
    reference_path = "references/video-keyframes/material-1/000001.jpg"
    payload = _builder_candidate_payload(reference_path)
    payload["evidence_map"][0]["source_refs"] = [
        {"source_type": "material", "material_id": "material-1"},
        "875f7af3-8bad-40e1-a23b-56e7dcdc47d0#keyframe-10",
    ]

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


def test_builder_candidate_matches_workflow_step_with_normalized_spacing() -> None:
    reference_path = "references/video-keyframes/material-1/000001.jpg"
    payload = _builder_candidate_payload(reference_path)
    payload["files"]["SKILL.md"] += "\n### 首次点亮与 BIOS 配置\n确认 BIOS 设置。\n"
    payload["workflow_step_candidates"].append({"title": "首次点亮与BIOS配置"})

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
        "directory_tree": "README.md\nSKILL.md\nprompts/system.md\nreferences/README.md\nexamples/input.md\nexamples/expected-output.md\ntests/checklist.md",
        "files": {
            "README.md": "# 泵房进入前安全检查\n\n用于指导进入前检查。\n",
            "SKILL.md": (
                "# 泵房进入前安全检查\n\n"
                "## Workflow\n"
                "### 阶段 1\n"
                f"参考 `{reference_path}` 判断入口状态。\n\n"
                "### 阶段 2\n"
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
            }
        ],
        "evidence_map": [
            {
                "claim": "阶段 1 需要确认入口状态。",
                "support_level": "observed_fact",
                "source_refs": [{"source_type": "reference_asset", "asset_id": "asset-1"}],
                "used_in": ["阶段 1"],
            }
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
                "constraint": "PPE 不完整不得进入。",
                "applies_to": "阶段 1",
                "risk_type": "personal_safety",
                "required_action": "停止并要求补齐证据。",
            }
        ],
        "workflow_step_candidates": [
            {"step_id": "阶段 1", "title": "入口状态确认"},
            {"step_id": "阶段 2", "title": "压力表记录"},
        ],
        "expected_evidence_requirements": [
            {"stage_id": "阶段 1", "evidence_type": "photo", "completion_criteria": "入口状态清楚。"}
        ],
    }
