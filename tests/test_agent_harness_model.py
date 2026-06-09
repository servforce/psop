from __future__ import annotations

import pytest

from app.agent_harness.model import AgentModelClient
from app.pskills.exceptions import SkillValidationError


def test_agent_model_client_builds_deterministic_result_from_expected_output() -> None:
    result = AgentModelClient.deterministic_decision_result(
        agent_key="pskill.builder",
        spec={"model_policy": {"mode": "llm", "route_key": "text"}},
        input_payload={"expected_output": {"draft_summary": "ready"}},
        skill_context=[{"package_name": "pskill-builder"}],
        memory_context=[{"id": "memory-1"}],
        plan_payload={"steps": [{"id": "complete_model_decision"}]},
        allowed_tools=["psop.pskills.get"],
    )

    assert result.provider == "deterministic"
    assert result.route_key == "text"
    assert result.model_name == "agent-harness-deterministic"
    assert result.decision.decision_type == "final_output"
    assert result.response_payload["output_payload"] == {"draft_summary": "ready"}
    assert result.request_payload["mode"] == "deterministic"
    assert result.request_payload["agent_key"] == "pskill.builder"
    assert result.request_payload["skill_context"][0]["package_name"] == "pskill-builder"
    assert result.request_payload["memory_context"] == [{"id": "memory-1"}]
    assert result.request_payload["allowed_tools"] == ["psop.pskills.get"]
    assert result.usage_json == {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}


def test_agent_model_client_prefers_deterministic_input_over_llm_policy() -> None:
    client = AgentModelClient()

    assert client.should_use_llm(
        input_payload={"expected_output": {"summary": "done"}},
        spec={"model_policy": {"mode": "llm"}},
    ) is False
    assert client.should_use_llm(
        input_payload={"task": "build"},
        spec={"model_policy": {"mode": "llm"}},
    ) is True


def test_agent_model_client_rejects_invalid_deterministic_agent_decision() -> None:
    with pytest.raises(SkillValidationError):
        AgentModelClient.decision_from_input({"agent_decision": "not-an-object"})
