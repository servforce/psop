from __future__ import annotations

import pytest

from app.agent_harness.events import AgentEventWriter
from app.agent_harness.memory.file_store import FileMemoryStore
from app.agent_harness.sandbox.local import LocalAgentSandboxProvider
from app.agent_harness.skills.loader import SkillLoader
from app.agent_harness.skills.spec import AgentSkill
from app.agent_harness.tools.builtin import register_builtin_tools
from app.agent_harness.tools.framework import register_framework_tools
from app.agent_harness.tools.policy import filter_tools_by_skill_allowed_tools
from app.agent_harness.tools.registry import ToolExecutionContext, ToolRegistry
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
