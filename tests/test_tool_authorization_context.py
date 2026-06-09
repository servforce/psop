from __future__ import annotations

from app.agents.tool_authorization_context import merge_business_context


def test_business_context_uses_run_trace_id_without_legacy_trace_event_alias() -> None:
    assert merge_business_context({"run_trace_id": "run-trace-1"})["run_trace_id"] == "run-trace-1"
    assert "run_trace_id" not in merge_business_context({"trace_event_id": "trace-legacy"})
