from __future__ import annotations

from app.infra.database import Base, DatabaseManager


def test_runtime_facts_link_to_agent_runs() -> None:
    manager = DatabaseManager("sqlite+pysqlite:///:memory:")
    manager.create_schema()

    tables = Base.metadata.tables

    assert "run_event" in tables
    assert "run_event_part" in tables
    assert "run_trace" in tables
    assert tables["run_event"].c.agent_run_id is not None
    assert tables["run_trace"].c.agent_run_id is not None
