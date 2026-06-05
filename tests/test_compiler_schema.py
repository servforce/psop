from __future__ import annotations

from app.infra.database import Base, DatabaseManager


def test_compiler_tables_use_pskill_compile_request_naming() -> None:
    manager = DatabaseManager("sqlite+pysqlite:///:memory:")
    manager.create_schema()

    tables = set(Base.metadata.tables)

    assert "pskill_compile_request" in tables
    assert "skill_compile_request" not in tables
    assert Base.metadata.tables["eg_compile_artifact"].c.compile_request_id is not None
    assert Base.metadata.tables["compile_diagnostic"].c.compile_request_id is not None
