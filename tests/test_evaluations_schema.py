from __future__ import annotations

from app.infra.database import Base, DatabaseManager


def test_evaluation_tables_use_run_evaluation_naming() -> None:
    manager = DatabaseManager("sqlite+pysqlite:///:memory:")
    manager.create_schema()

    tables = set(Base.metadata.tables)

    assert "run_evaluation" in tables
    assert "run_evaluation_finding" in tables
    assert "skill_run_evaluation" not in tables
    assert "skill_run_evaluation_finding" not in tables
    assert Base.metadata.tables["run_evaluation"].c.agent_run_id is not None
    assert Base.metadata.tables["run_evaluation_finding"].c.evaluation_id is not None
