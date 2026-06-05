from __future__ import annotations

from tests.test_skills_api import create_test_client


def test_tools_api_lists_seeded_tools_and_exposes_policy_metadata() -> None:
    client, _, _ = create_test_client()

    with client:
        list_response = client.get("/api/v1/tools")
        high_write_response = client.get(
            "/api/v1/tools",
            params={"side_effect_level": "high_write", "requires_authorization": "true"},
        )
        detail_response = client.get("/api/v1/tools/psop.agent_version.activate")
        memory_detail_response = client.get("/api/v1/tools/psop.memory.write_candidate")

    tools = {item["name"]: item for item in list_response.json()}
    assert list_response.status_code == 200
    assert {
        "psop.pskills.read",
        "psop.compiler.validate_formal_v5",
        "psop.memory.search",
        "psop.memory.write_candidate",
        "psop.agent_version.activate",
        "psop.skill_version.activate",
    } <= set(tools)

    assert tools["psop.pskills.read"]["side_effect_level"] == "read"
    assert tools["psop.pskills.read"]["requires_authorization"] is False
    assert tools["psop.agent_version.activate"]["side_effect_level"] == "high_write"
    assert tools["psop.agent_version.activate"]["requires_authorization"] is True

    high_write_names = {item["name"] for item in high_write_response.json()}
    assert high_write_response.status_code == 200
    assert high_write_names == {
        "psop.agent_version.activate",
        "psop.repository.commit_patch",
        "psop.skill_version.activate",
    }

    detail = detail_response.json()
    assert detail_response.status_code == 200
    assert detail["name"] == "psop.agent_version.activate"
    assert detail["provider"] == "native"
    assert detail["requires_authorization"] is True
    assert detail["allowed_agent_keys"] == ["psop.governance"]
    assert detail["policy_summary"]["permission_rule"] == (
        "AgentSpec.allowed_tools ∩ SkillPackage.allowed_tools ∩ ToolPolicy.allowed_tools"
    )

    memory_detail = memory_detail_response.json()
    assert memory_detail_response.status_code == 200
    assert memory_detail["side_effect_level"] == "low_write"
    assert memory_detail["requires_authorization"] is False


def test_tools_api_reports_recent_tool_call_failure_stats() -> None:
    client, _, _ = create_test_client()

    with client:
        run_response = client.post(
            "/api/v1/agent-runs",
            json={
                "agent_key": "pskill.runner",
                "owner_type": "runtime",
                "owner_id": "tool-stats-runner",
                "input_payload": {
                    "agent_decision": {
                        "decision_type": "tool_call",
                        "tool_name": "psop.repository.commit_patch",
                        "side_effect_level": "high_write",
                        "arguments_summary": {"paths": ["SKILL.md"]},
                        "expected_effect_summary": "Attempt a repository patch from a runner that cannot use it.",
                    }
                },
            },
        )
        agent_run_id = run_response.json()["id"]
        run_once_response = client.post(f"/api/v1/agent-runs/{agent_run_id}/run-once")
        detail_response = client.get("/api/v1/tools/psop.repository.commit_patch")

    assert run_response.status_code == 201
    assert run_once_response.status_code == 200
    assert run_once_response.json()["status"] == "failed"
    assert run_once_response.json()["error_message"] == "tool_not_allowed_by_agent_or_skill"

    detail = detail_response.json()
    assert detail_response.status_code == 200
    assert detail["recent_call_count"] == 1
    assert detail["failed_call_count"] == 1
    assert detail["failure_rate"] == 1.0


def test_tools_api_rejects_invalid_side_effect_filter() -> None:
    client, _, _ = create_test_client()

    with client:
        response = client.get("/api/v1/tools", params={"side_effect_level": "unknown_write"})

    assert response.status_code == 422
    assert response.json()["code"] == "skill_validation_error"
