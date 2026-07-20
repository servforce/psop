from __future__ import annotations

import pytest
from langchain_core.utils.function_calling import convert_to_openai_tool

from app.agent_harness.events import AgentEventWriter
from app.agent_harness.memory.file_store import FileMemoryStore
from app.agent_harness.sandbox.local import LocalAgentSandboxProvider
from app.agent_harness.skills.loader import SkillLoader
from app.agent_harness.skills.spec import AgentSkill
from app.agent_harness.tools.builtin import register_builtin_tools
from app.agent_harness.tools.builtin.builder import register_builder_tools
from app.agent_harness.tools.builtin.workspace import register_workspace_tools
from app.agent_harness.tools.framework import register_framework_tools
from app.agent_harness.tools.langchain import to_langchain_tools
from app.agent_harness.tools.policy import filter_tools_by_skill_allowed_tools
from app.agent_harness.tools.registry import ToolExecutionContext, ToolRegistry
from app.agent_harness.tools.spec import ToolSpec
from app.core.config import Settings


def _context(tmp_path) -> tuple[ToolRegistry, ToolExecutionContext, AgentEventWriter]:
    settings = Settings(
        database_url="sqlite+pysqlite:///:memory:",
        agent_harness_sandbox_root=str(tmp_path / "agent-runs"),
    )
    sandbox = LocalAgentSandboxProvider(settings).acquire(input_payload={"text": "demo"})
    writer = AgentEventWriter(sandbox.events_path)
    registry = ToolRegistry()
    register_builtin_tools(registry)
    return registry, ToolExecutionContext(
        sandbox=sandbox,
        memory_store=FileMemoryStore(sandbox.memory_path),
        memory_scope="demo.psop_harness_agent",
        event_writer=writer,
        invocation_context={},
    ), writer


def test_tool_registry_executes_builtin_tool(tmp_path) -> None:
    registry, context, writer = _context(tmp_path)

    result = registry.execute(
        "demo_extract_check_items",
        {"text": "进入泵房前检查 PPE，确认阀门关闭，记录压力表读数。"},
        context,
    )

    assert result["item_count"] == 3
    assert writer.events == []


def test_tool_spec_has_governance_metadata_defaults() -> None:
    spec = ToolSpec(name="demo", description="测试工具。")

    assert spec.risk_class == "read_only"
    assert spec.side_effect_class == "none"
    assert spec.resource_scope == "agent_run"
    assert spec.permission_policy == "allow"
    assert spec.retry_policy == {}
    assert spec.error_types == []
    assert spec.input_schema_mode == "generated_model"


def test_langchain_tool_args_schema_rejects_string_for_array(tmp_path) -> None:
    registry, context, _ = _context(tmp_path)
    registry.register(
        ToolSpec(
            name="array_tool",
            description="测试数组参数工具。",
            input_schema={
                "type": "object",
                "required": ["items"],
                "properties": {
                    "items": {"type": "array", "items": {"type": "object"}},
                },
            },
        ),
        lambda arguments, _context: {"status": "success", "items": arguments["items"]},
    )
    tool = to_langchain_tools(tool_names=["array_tool"], registry=registry, context=context)[0]

    with pytest.raises(Exception):
        tool.args_schema.model_validate({"items": '[{"value": 1}]'})

    parsed = tool.args_schema.model_validate({"items": [{"value": 1}]})
    assert parsed.items == [{"value": 1}]


def test_builder_langchain_tool_exposes_complete_raw_schema(tmp_path) -> None:
    registry, context, _ = _context(tmp_path)
    register_builder_tools(registry)

    tool = to_langchain_tools(
        tool_names=["psop.builder.submit_candidate"],
        registry=registry,
        context=context,
    )[0]
    schema = tool.tool_call_schema

    assert isinstance(schema, dict)
    assert schema["additionalProperties"] is False
    assert all(
        definition.get("additionalProperties") is False
        for definition in schema["$defs"].values()
        if definition.get("type") == "object"
    )
    assert schema["properties"]["evidence_map"]["items"] == {"$ref": "#/$defs/EvidenceMapItem"}
    assert schema["properties"]["workflow_step_candidates"]["items"] == {
        "$ref": "#/$defs/WorkflowStepCandidate"
    }
    assert set(schema["$defs"]["ExpectedEvidenceRequirement"]["required"]) == {
        "requirement_id",
        "stage_id",
        "evidence_type",
        "completion_criteria",
    }
    target_type = schema["$defs"]["EvidenceUsageTarget"]["properties"]["target_type"]
    assert set(target_type["enum"]) == {
        "workflow_stage",
        "safety_constraint",
        "expected_evidence",
        "review_notes",
    }
    assert "schema_version" in schema["required"]

    provider_schema = convert_to_openai_tool(tool)["function"]["parameters"]
    evidence_item = provider_schema["properties"]["evidence_map"]["items"]
    assert evidence_item != {}
    assert set(evidence_item["required"]) == {"claim", "support_level", "source_refs", "used_in"}
    provider_target = evidence_item["properties"]["used_in"]["items"]
    assert set(provider_target["required"]) == {"target_type", "target_id"}
    assert set(provider_target["properties"]["target_type"]["enum"]) == {
        "workflow_stage",
        "safety_constraint",
        "expected_evidence",
        "review_notes",
    }
    provider_expected_evidence = provider_schema["properties"]["expected_evidence_requirements"]["items"]
    assert set(provider_expected_evidence["required"]) == {
        "requirement_id",
        "stage_id",
        "evidence_type",
        "completion_criteria",
    }


def test_tool_registry_raises_handler_error(tmp_path) -> None:
    registry, context, writer = _context(tmp_path)

    with pytest.raises(ValueError):
        registry.execute("demo_extract_check_items", {"text": ""}, context)

    assert writer.events == []


def test_score_tool_accepts_json_string_items_from_real_tool_calling_models(tmp_path) -> None:
    registry, context, writer = _context(tmp_path)

    result = registry.execute(
        "demo_score_checklist",
        {"items": '["进入泵房前检查 PPE", "确认阀门关闭", "记录压力表读数"]'},
        context,
    )

    assert result["item_count"] == 3
    assert result["risk_level"] == "medium"
    assert writer.events == []


def test_workspace_write_tool_rejects_path_escape(tmp_path) -> None:
    registry, context, _ = _context(tmp_path)

    with pytest.raises(ValueError):
        registry.execute("write_demo_report", {"filename": "../escape.md", "content": "bad"}, context)


def test_workspace_write_tool_writes_virtual_workspace_path(tmp_path) -> None:
    registry, context, writer = _context(tmp_path)

    result = registry.execute("write_demo_report", {"filename": "result.md", "content": "ok"}, context)

    assert result["path"] == "/mnt/psop/workspace/result.md"
    assert context.sandbox.read_text("/mnt/psop/workspace/result.md") == "ok"
    assert writer.events[-1].event_type == "agent.file.written"


def test_workspace_builtin_tools_reject_outputs_write(tmp_path) -> None:
    registry, context, _ = _context(tmp_path)
    register_workspace_tools(registry)

    result = registry.execute(
        "workspace.write_text",
        {"path": "/mnt/psop/outputs/builder-result.json", "content": "bad"},
        context,
    )

    assert result["status"] == "error"
    assert "outputs" in result["message"]


def test_tool_policy_intersects_agent_tools_with_skill_allowed_tools() -> None:
    result = filter_tools_by_skill_allowed_tools(
        ["demo_extract_check_items", "demo_score_checklist", "memory_get"],
        [
            AgentSkill(
                name="demo",
                description="demo",
                allowed_tools=["demo_extract_check_items", "demo_score_checklist"],
            )
        ],
    )

    assert result == ["demo_extract_check_items", "demo_score_checklist"]


def test_load_skill_framework_tool_loads_declared_skill(tmp_path) -> None:
    skill_dir = tmp_path / "skills" / "demo_skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        """---
name: demo_skill
description: Demo framework skill
allowed-tools:
  - demo_extract_check_items
---

# Demo Skill Body
""",
        encoding="utf-8",
    )
    registry, context, writer = _context(tmp_path)
    register_framework_tools(registry)
    context.skill_loader = SkillLoader(tmp_path / "skills")
    context.allowed_skill_names = {"demo_skill"}

    result = registry.execute("load_skill", {"skill_name": "demo_skill"}, context)

    assert result["name"] == "demo_skill"
    assert "# Demo Skill Body" in result["content"]
    assert writer.events[-1].event_type == "agent.skill.loaded"


def test_load_skill_resource_framework_tool_loads_declared_skill_resource(tmp_path) -> None:
    skill_dir = tmp_path / "skills" / "demo_skill" / "core"
    skill_dir.mkdir(parents=True)
    (tmp_path / "skills" / "demo_skill" / "SKILL.md").write_text(
        """---
name: demo_skill
description: Demo framework skill
allowed-tools:
  - demo_extract_check_items
---

# Demo Skill Body
""",
        encoding="utf-8",
    )
    (skill_dir / "SKILL.md").write_text("# Core Resource\n\n必须读取。", encoding="utf-8")
    registry, context, writer = _context(tmp_path)
    register_framework_tools(registry)
    context.skill_loader = SkillLoader(tmp_path / "skills")
    context.allowed_skill_names = {"demo_skill"}

    result = registry.execute(
        "load_skill_resource",
        {"skill_name": "demo_skill", "resource_path": "core/SKILL.md"},
        context,
    )

    assert result["skill_name"] == "demo_skill"
    assert result["resource_path"] == "core/SKILL.md"
    assert "# Core Resource" in result["content"]
    assert writer.events[-1].event_type == "agent.skill.resource.loaded"


def test_load_skill_resource_framework_tool_rejects_undeclared_skill(tmp_path) -> None:
    registry, context, _ = _context(tmp_path)
    register_framework_tools(registry)
    context.skill_loader = SkillLoader(tmp_path / "skills")
    context.allowed_skill_names = {"declared_skill"}

    with pytest.raises(ValueError, match="未声明"):
        registry.execute(
            "load_skill_resource",
            {"skill_name": "other_skill", "resource_path": "core/SKILL.md"},
            context,
        )
