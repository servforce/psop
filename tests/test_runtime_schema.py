from __future__ import annotations

import app.runtime.schemas as runtime_schemas
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


def test_runtime_schema_exports_use_run_event_and_run_trace_names() -> None:
    legacy_schema_aliases = {
        "TraceEventResponse",
        "TerminalEventSource",
        "AppendTerminalEventRequest",
        "TerminalEventPartInput",
        "TerminalEventPartResponse",
        "TerminalEventResponse",
        "TerminalEventAppendResponse",
    }

    assert {"RunTraceResponse", "RunEventResponse", "AppendRunEventRequest"} <= set(dir(runtime_schemas))
    assert legacy_schema_aliases.isdisjoint(dir(runtime_schemas))
