from __future__ import annotations

from app.agent_harness.events import AgentEventWriter
from app.agent_harness.memory.file_store import FileMemoryStore
from app.agent_harness.tools.builtin import register_builtin_tools
from app.agent_harness.tools.registry import ToolExecutionContext, ToolRegistry
from app.agent_harness.workspace.manager import WorkspaceManager
from app.core.config import Settings


def test_file_memory_store_and_tools_roundtrip(tmp_path) -> None:
    settings = Settings(
        database_url="sqlite+pysqlite:///:memory:",
        agent_harness_workspace_root=str(tmp_path / "agent-runs"),
    )
    workspace = WorkspaceManager(settings).create(input_payload={})
    writer = AgentEventWriter(workspace.events_path)
    store = FileMemoryStore(workspace.memory_path)
    registry = ToolRegistry()
    register_builtin_tools(registry)
    context = ToolExecutionContext(
        workspace=workspace,
        memory_store=store,
        memory_scope="demo.scope",
        event_writer=writer,
    )

    registry.execute("memory_put", {"key": "status", "value": "ok"}, context)
    result = registry.execute("memory_get", {"key": "status"}, context)

    assert result["value"] == "ok"
    assert store.read("demo.scope") == {"status": "ok"}
    assert "agent.memory.write" in [event.event_type for event in writer.events]
    assert "agent.memory.read" in [event.event_type for event in writer.events]
