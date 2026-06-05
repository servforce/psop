from __future__ import annotations

from app.infra.database import Base, DatabaseManager


def test_testing_tables_use_pskill_test_naming() -> None:
    manager = DatabaseManager("sqlite+pysqlite:///:memory:")
    manager.create_schema()

    tables = set(Base.metadata.tables)

    assert "pskill_test_suite" in tables
    assert "pskill_test_scenario" in tables
    assert "pskill_test_run" in tables
    assert "pskill_test_asset" in tables
    assert "pskill_test_expectation_evaluation" in tables
    assert "pskill_publish_gate" in tables
    assert "skill_test_scenario" not in tables
    assert "skill_test_scenario_run" not in tables
    assert "skill_test_asset" not in tables
    assert "skill_test_expectation_evaluation" not in tables
    assert Base.metadata.tables["pskill_test_run"].c.agent_run_id is not None
