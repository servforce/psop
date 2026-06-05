from __future__ import annotations

from tests.test_skills_api import create_test_client


def test_agents_seed_agent_runs_events_and_tool_authorizations() -> None:
    client, _, _ = create_test_client()

    with client:
        agents_response = client.get("/api/v1/agents")
        agent_detail_response = client.get("/api/v1/agents/pskill.runner")
        versions_response = client.get("/api/v1/agents/pskill.runner/versions")
        run_response = client.post(
            "/api/v1/agent-runs",
            json={
                "agent_key": "pskill.runner",
                "owner_type": "runtime",
                "owner_id": "run-owner",
                "input_payload": {"node_id": "inspect"},
            },
        )
        agent_run = run_response.json()
        event_response = client.post(
            f"/api/v1/agent-runs/{agent_run['id']}/events",
            json={
                "event_type": "agent.test.event",
                "phase": "test",
                "payload": {"ok": True},
            },
        )
        events_response = client.get(f"/api/v1/agent-runs/{agent_run['id']}/events")
        authorization_response = client.post(
            "/api/v1/tool-authorizations",
            json={
                "agent_run_id": agent_run["id"],
                "tool_name": "psop.repository.commit_patch",
                "side_effect_level": "high_write",
                "risk_level": "high",
                "authorization_reason": "需要写入 Git 仓库。",
                "tool_arguments_summary": {"path_count": 2},
                "expected_effect_summary": "提交 PSkill 源码 patch。",
                "reversible": True,
            },
        )
        authorization = authorization_response.json()
        waiting_run_response = client.get(f"/api/v1/agent-runs/{agent_run['id']}")
        approve_response = client.post(
            f"/api/v1/tool-authorizations/{authorization['id']}/approve",
            json={"response_payload": {"approved_by": "tester"}},
        )
        approved_run_response = client.get(f"/api/v1/agent-runs/{agent_run['id']}")

        reject_run_response = client.post(
            "/api/v1/agent-runs",
            json={"agent_key": "psop.governance", "owner_type": "governance", "owner_id": "proposal-1"},
        )
        reject_run = reject_run_response.json()
        reject_authorization_response = client.post(
            "/api/v1/tool-authorizations",
            json={
                "agent_run_id": reject_run["id"],
                "tool_name": "psop.agent_version.activate",
                "side_effect_level": "high_write",
                "authorization_reason": "激活 AgentVersion 属于高副作用写操作。",
            },
        )
        reject_authorization = reject_authorization_response.json()
        reject_response = client.post(
            f"/api/v1/tool-authorizations/{reject_authorization['id']}/reject",
            json={"response_payload": {"reason": "needs review"}},
        )
        rejected_run_response = client.get(f"/api/v1/agent-runs/{reject_run['id']}")
        pending_authorizations_response = client.get("/api/v1/tool-authorizations", params={"status": "pending"})

    agent_keys = {item["key"] for item in agents_response.json()}
    assert agents_response.status_code == 200
    assert agent_keys == {
        "pskill.builder",
        "pskill.compiler",
        "pskill.tester",
        "pskill.runner",
        "pskill.evaluator",
        "psop.governance",
    }
    assert agent_detail_response.status_code == 200
    assert agent_detail_response.json()["active_version"]["spec_json"]["output_schema"]["name"] == "RuntimeAgentObservation"
    assert versions_response.status_code == 200
    assert versions_response.json()[0]["status"] == "published"

    assert run_response.status_code == 201
    assert agent_run["agent_key"] == "pskill.runner"
    assert agent_run["status"] == "queued"
    assert event_response.status_code == 201
    assert [event["event_type"] for event in events_response.json()] == ["agent.run.created", "agent.test.event"]

    assert authorization_response.status_code == 201
    assert authorization["status"] == "pending"
    assert waiting_run_response.json()["status"] == "waiting_tool_authorization"
    assert approve_response.status_code == 200
    assert approve_response.json()["status"] == "approved"
    assert approved_run_response.json()["status"] == "queued"

    assert reject_response.status_code == 200
    assert reject_response.json()["status"] == "rejected"
    assert rejected_run_response.json()["status"] == "failed"
    assert rejected_run_response.json()["error_message"] == "tool_authorization_denied"
    assert pending_authorizations_response.json() == []
