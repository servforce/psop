from __future__ import annotations

from app.agent_harness.guardrails import ToolGuardrail


def test_tool_guardrail_blocks_runner_runtime_state_mutation_intent() -> None:
    result = ToolGuardrail().check(
        agent_key="pskill.runner",
        tool_name="psop.runtime.read",
        arguments_summary={"target": "session_token_snapshot", "operation": "write token_payload"},
        expected_effect_summary="write run_event directly",
    )

    assert result.passed is False
    assert result.findings[0].code == "tool_runtime_state_sovereignty_violation"


def test_tool_guardrail_allows_runner_runtime_read_context() -> None:
    result = ToolGuardrail().check(
        agent_key="pskill.runner",
        tool_name="psop.runtime.read",
        arguments_summary={"snapshot_limit": 1, "run_event_limit": 5, "run_trace_limit": 5},
        expected_effect_summary="Read persisted Runtime facts as context.",
    )

    assert result.passed is True
    assert result.findings == []
