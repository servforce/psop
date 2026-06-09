from __future__ import annotations

from app.infra.database import Base, DatabaseManager
from app.memory.policy import FORMAL_FACT_SOURCE_KINDS, VALID_MEMORY_TYPES


def test_memory_table_uses_agent_memory_entry_naming() -> None:
    manager = DatabaseManager("sqlite+pysqlite:///:memory:")
    manager.create_schema()

    tables = Base.metadata.tables

    assert "agent_memory_entry" in tables
    assert tables["agent_memory_entry"].c.namespace is not None
    assert tables["agent_memory_entry"].c.memory_type is not None
    assert tables["agent_memory_entry"].c.created_by_agent_run_id is not None


def test_memory_policy_matches_closed_loop_memory_taxonomy() -> None:
    assert VALID_MEMORY_TYPES == {"short_term", "semantic", "episodic", "procedural", "artifact"}
    assert FORMAL_FACT_SOURCE_KINDS == {
        "git_source",
        "eg_compile_artifact",
        "session_token_snapshot",
        "run_event",
        "run_trace",
    }
