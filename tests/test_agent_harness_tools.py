from __future__ import annotations

import pytest

from app.agent_harness.events import AgentEventWriter
from app.agent_harness.memory.file_store import FileMemoryStore
from app.agent_harness.tools.builtin import register_builtin_tools
from app.agent_harness.tools.registry import ToolExecutionContext, ToolRegistry
from app.agent_harness.workspace.manager import WorkspaceManager
from app.core.config import Settings


def _context(tmp_path) -> tuple[ToolRegistry, ToolExecutionContext, AgentEventWriter]:
    settings = Settings(
        database_url="sqlite+pysqlite:///:memory:",
        agent_harness_workspace_root=str(tmp_path / "agent-runs"),
    )
    workspace = WorkspaceManager(settings).create(input_payload={"text": "demo"})
    writer = AgentEventWriter(workspace.events_path)
    registry = ToolRegistry()
    register_builtin_tools(registry)
    return registry, ToolExecutionContext(
        workspace=workspace,
        memory_store=FileMemoryStore(workspace.memory_path),
        memory_scope="demo.psop_harness_agent",
        event_writer=writer,
    ), writer


def test_tool_registry_records_success_events(tmp_path) -> None:
    registry, context, writer = _context(tmp_path)

    result = registry.execute(
        "demo_extract_check_items",
        {"text": "进入泵房前检查 PPE，确认阀门关闭，记录压力表读数。"},
        context,
    )

    assert result["item_count"] == 3
    assert [event.event_type for event in writer.events] == ["agent.tool.started", "agent.tool.completed"]
    assert writer.events[-1].payload["tool_name"] == "demo_extract_check_items"


def test_tool_registry_records_failed_events(tmp_path) -> None:
    registry, context, writer = _context(tmp_path)

    with pytest.raises(ValueError):
        registry.execute("demo_extract_check_items", {"text": ""}, context)

    assert [event.event_type for event in writer.events] == ["agent.tool.started", "agent.tool.failed"]
    assert writer.events[-1].payload["error_type"] == "ValueError"


def test_score_tool_accepts_json_string_items_from_real_tool_calling_models(tmp_path) -> None:
    registry, context, writer = _context(tmp_path)

    result = registry.execute(
        "demo_score_checklist",
        {"items": '["进入泵房前检查 PPE", "确认阀门关闭", "记录压力表读数"]'},
        context,
    )

    assert result["item_count"] == 3
    assert result["risk_level"] == "medium"
    assert writer.events[-1].event_type == "agent.tool.completed"


def test_workspace_write_tool_rejects_path_escape(tmp_path) -> None:
    registry, context, _ = _context(tmp_path)

    with pytest.raises(ValueError):
        registry.execute("write_demo_report", {"filename": "../escape.md", "content": "bad"}, context)
