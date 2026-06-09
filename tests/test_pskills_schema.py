from __future__ import annotations

from app.infra.database import Base, DatabaseManager


def test_pskill_tables_track_builder_agent_runs() -> None:
    manager = DatabaseManager("sqlite+pysqlite:///:memory:")
    manager.create_schema()

    tables = Base.metadata.tables

    assert tables["pskill_version"].c.builder_agent_run_id is not None
    assert tables["pskill_material_generation"].c.agent_run_id is not None

