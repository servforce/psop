from __future__ import annotations

from app.infra.database import Base, DatabaseManager


def test_memory_table_uses_agent_memory_entry_naming() -> None:
    manager = DatabaseManager("sqlite+pysqlite:///:memory:")
    manager.create_schema()

    tables = Base.metadata.tables

    assert "agent_memory_entry" in tables
    assert tables["agent_memory_entry"].c.namespace is not None
    assert tables["agent_memory_entry"].c.memory_type is not None
    assert tables["agent_memory_entry"].c.created_by_agent_run_id is not None
