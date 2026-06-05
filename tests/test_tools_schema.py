from __future__ import annotations

from app.infra.database import Base, DatabaseManager


def test_tool_definition_table_is_registered() -> None:
    manager = DatabaseManager("sqlite+pysqlite:///:memory:")
    manager.create_schema()

    tables = Base.metadata.tables

    assert "tool_definition" in tables
    assert tables["tool_definition"].c.name is not None
    assert tables["tool_definition"].c.side_effect_level is not None
    assert tables["tool_definition"].c.requires_authorization is not None
