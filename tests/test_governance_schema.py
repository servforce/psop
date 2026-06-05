from __future__ import annotations

from app.infra.database import Base, DatabaseManager


def test_governance_tables_use_psop_improvement_naming() -> None:
    manager = DatabaseManager("sqlite+pysqlite:///:memory:")
    manager.create_schema()

    tables = Base.metadata.tables

    assert "psop_improvement_proposal" in tables
    assert "psop_improvement_experiment" in tables
    assert tables["psop_improvement_proposal"].c.agent_run_id is not None
    assert tables["psop_improvement_proposal"].c.source_finding_ids is not None
    assert tables["psop_improvement_experiment"].c.proposal_id is not None
