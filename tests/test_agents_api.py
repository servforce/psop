from __future__ import annotations

from sqlalchemy import select

from app.skills.models import SkillPackage, SkillVersion
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
                "run_id": "runtime-run-auth-1",
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
                "run_id": agent_run["run_id"],
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
        run_authorizations_response = client.get(f"/api/v1/runs/{agent_run['run_id']}/tool-authorizations")
        pending_run_authorizations_response = client.get(
            f"/api/v1/runs/{agent_run['run_id']}/tool-authorizations",
            params={"status": "pending"},
        )
        tool_run_authorizations_response = client.get(
            f"/api/v1/runs/{agent_run['run_id']}/tool-authorizations",
            params={"tool_name": "psop.repository.commit_patch"},
        )
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
        commit_patch_authorizations_response = client.get(
            "/api/v1/tool-authorizations",
            params={"tool_name": "psop.repository.commit_patch"},
        )
        activate_authorizations_response = client.get(
            "/api/v1/tool-authorizations",
            params={"tool_name": "psop.agent_version.activate"},
        )

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
    assert agent_detail_response.json()["active_version"]["spec_json"]["allowed_tools"] == ["psop.runtime.read"]
    assert versions_response.status_code == 200
    assert versions_response.json()[0]["status"] == "published"

    assert run_response.status_code == 201
    assert agent_run["agent_key"] == "pskill.runner"
    assert agent_run["status"] == "queued"
    assert event_response.status_code == 201
    assert [event["event_type"] for event in events_response.json()] == ["agent.run.created", "agent.test.event"]

    assert authorization_response.status_code == 201
    assert authorization["status"] == "pending"
    assert run_authorizations_response.status_code == 200
    assert [item["id"] for item in run_authorizations_response.json()] == [authorization["id"]]
    assert pending_run_authorizations_response.status_code == 200
    assert [item["id"] for item in pending_run_authorizations_response.json()] == [authorization["id"]]
    assert tool_run_authorizations_response.status_code == 200
    assert [item["id"] for item in tool_run_authorizations_response.json()] == [authorization["id"]]
    assert waiting_run_response.json()["status"] == "waiting_tool_authorization"
    assert approve_response.status_code == 200
    assert approve_response.json()["status"] == "approved"
    assert approved_run_response.json()["status"] == "queued"

    assert reject_response.status_code == 200
    assert reject_response.json()["status"] == "rejected"
    assert rejected_run_response.json()["status"] == "failed"
    assert rejected_run_response.json()["error_message"] == "tool_authorization_denied"
    assert pending_authorizations_response.json() == []
    assert [item["id"] for item in commit_patch_authorizations_response.json()] == [authorization["id"]]
    assert [item["id"] for item in activate_authorizations_response.json()] == [reject_authorization["id"]]


def test_agent_version_api_creates_publishes_and_activates_draft() -> None:
    client, _, _ = create_test_client()

    with client:
        before_response = client.get("/api/v1/agents/pskill.runner")
        before = before_response.json()
        spec = {
            **before["active_version"]["spec_json"],
            "goal": "在 RuntimeService 主权边界内为运行节点生成 observation，并携带 canary marker。",
            "runtime_policy": {"rollout": "canary"},
        }
        draft_response = client.post(
            "/api/v1/agents/pskill.runner/versions",
            json={"version_label": "runner-canary", "spec_json": spec},
        )
        draft = next(item for item in draft_response.json()["versions"] if item["version_label"] == "runner-canary")
        draft_activate_response = client.post(f"/api/v1/agents/pskill.runner/versions/{draft['id']}/activate")
        publish_response = client.post(f"/api/v1/agents/pskill.runner/versions/{draft['id']}/publish")
        activate_response = client.post(
            f"/api/v1/agents/pskill.runner/versions/{draft['id']}/activate",
            json={"update_bindings": True},
        )

    assert before_response.status_code == 200
    assert draft_response.status_code == 201
    assert draft["status"] == "draft"
    assert draft["content_hash"] != before["active_version"]["content_hash"]
    assert draft_activate_response.status_code == 422
    assert publish_response.status_code == 200
    assert publish_response.json()["status"] == "published"
    assert activate_response.status_code == 200
    assert activate_response.json()["active_version_id"] == draft["id"]
    assert activate_response.json()["active_version"]["spec_json"]["runtime_policy"] == {"rollout": "canary"}
    assert {item["active_version_id"] for item in activate_response.json()["bindings"]} == {draft["id"]}


def test_agent_runner_records_skills_model_tool_call_and_resumes_after_authorization() -> None:
    client, _, _ = create_test_client()

    with client:
        compiler_before = client.get("/api/v1/agents/pskill.compiler").json()
        compiler_spec = {
            **compiler_before["active_version"]["spec_json"],
            "goal": "将 PSkill 编译为 formal-v5 Execution Graph，并启用授权激活测试版本。",
            "runtime_policy": {"activation_test": True},
        }
        draft_response = client.post(
            "/api/v1/agents/pskill.compiler/versions",
            json={"version_label": "compiler-activation-test", "spec_json": compiler_spec},
        )
        draft_version = next(
            item for item in draft_response.json()["versions"] if item["version_label"] == "compiler-activation-test"
        )
        publish_response = client.post(f"/api/v1/agents/pskill.compiler/versions/{draft_version['id']}/publish")
        run_response = client.post(
            "/api/v1/agent-runs",
            json={
                "agent_key": "psop.governance",
                "owner_type": "governance",
                "owner_id": "proposal-activate-agent",
                "input_payload": {
                    "agent_decision": {
                        "decision_type": "tool_call",
                        "tool_name": "psop.agent_version.activate",
                        "side_effect_level": "high_write",
                        "arguments_summary": {"agent_key": "pskill.compiler", "version_id": draft_version["id"]},
                        "expected_effect_summary": "激活新的 compiler AgentVersion。",
                        "authorization_reason": "激活 AgentVersion 会改变生产智能体配置。",
                        "reversible": True,
                        "idempotency_key": "activate-compiler-agent-version-test",
                    }
                },
            },
        )
        agent_run_id = run_response.json()["id"]
        first_run_response = client.post(f"/api/v1/agent-runs/{agent_run_id}/run-once")
        tool_calls_response = client.get(f"/api/v1/agent-runs/{agent_run_id}/tool-calls")
        model_calls_response = client.get(f"/api/v1/agent-runs/{agent_run_id}/model-calls")
        skill_activations_response = client.get(f"/api/v1/agent-runs/{agent_run_id}/skill-activations")
        authorizations_response = client.get(f"/api/v1/agent-runs/{agent_run_id}/tool-authorizations")
        events_response = client.get(f"/api/v1/agent-runs/{agent_run_id}/events")

        authorization = authorizations_response.json()[0]
        approve_response = client.post(
            f"/api/v1/tool-authorizations/{authorization['id']}/approve",
            json={"response_payload": {"approved_by": "tester"}},
        )
        resumed_response = client.post(f"/api/v1/agent-runs/{agent_run_id}/run-once")
        executed_authorization_response = client.get(f"/api/v1/tool-authorizations/{authorization['id']}")
        resumed_tool_calls_response = client.get(f"/api/v1/agent-runs/{agent_run_id}/tool-calls")
        resumed_events_response = client.get(f"/api/v1/agent-runs/{agent_run_id}/events")
        compiler_after_response = client.get("/api/v1/agents/pskill.compiler")

    assert draft_response.status_code == 201
    assert publish_response.status_code == 200
    assert run_response.status_code == 201
    assert run_response.json()["agent_session_id"]
    assert first_run_response.status_code == 200
    assert first_run_response.json()["status"] == "waiting_tool_authorization"

    tool_calls = tool_calls_response.json()
    assert tool_calls_response.status_code == 200
    assert len(tool_calls) == 1
    assert tool_calls[0]["tool_name"] == "psop.agent_version.activate"
    assert tool_calls[0]["status"] == "waiting_authorization"
    assert tool_calls[0]["side_effect_level"] == "high_write"

    assert model_calls_response.status_code == 200
    assert model_calls_response.json()[0]["provider"] == "deterministic"
    assert model_calls_response.json()[0]["response_payload"]["decision_type"] == "tool_call"

    activation_names = {item["activation_context"]["package_name"] for item in skill_activations_response.json()}
    assert skill_activations_response.status_code == 200
    assert activation_names == {"psop-governance-manager"}

    assert authorizations_response.status_code == 200
    assert authorization["status"] == "pending"
    assert authorization["agent_tool_call_id"] == tool_calls[0]["id"]
    assert authorization["side_effect_level"] == "high_write"

    event_types = [item["event_type"] for item in events_response.json()]
    assert "agent.skills.activated" in event_types
    assert "agent.model_call.completed" in event_types
    assert "agent.waiting_tool_authorization" in event_types

    assert approve_response.status_code == 200
    assert approve_response.json()["status"] == "approved"
    assert resumed_response.status_code == 200
    assert resumed_response.json()["status"] == "succeeded"
    assert resumed_response.json()["output_payload"]["tool_result"]["tool_name"] == "psop.agent_version.activate"
    assert resumed_response.json()["output_payload"]["tool_result"]["result"]["version_id"] == draft_version["id"]
    assert executed_authorization_response.json()["status"] == "executed"
    assert resumed_tool_calls_response.json()[0]["status"] == "succeeded"
    assert resumed_tool_calls_response.json()[0]["result_summary"]["result"]["version_id"] == draft_version["id"]
    assert compiler_after_response.json()["active_version_id"] == draft_version["id"]
    assert compiler_after_response.json()["active_version"]["spec_json"]["runtime_policy"] == {"activation_test": True}
    assert {item["active_version_id"] for item in compiler_after_response.json()["bindings"]} == {draft_version["id"]}
    assert [item["status"] for item in resumed_tool_calls_response.json()] == ["succeeded"]
    resumed_event_types = [item["event_type"] for item in resumed_events_response.json()]
    assert "agent.runner.resumed_authorized_tool" in resumed_event_types
    assert "agent.tool_call.succeeded" in resumed_event_types


def test_agent_runner_executes_authorized_skill_version_activation_tool() -> None:
    client, _, _ = create_test_client()

    with client:
        sync_response = client.post("/api/v1/skills/sync")
        before_response = client.get("/api/v1/skills/pskill-builder")
        with client.app.state.db_manager.session() as session:
            package = session.scalar(select(SkillPackage).where(SkillPackage.name == "pskill-builder"))
            assert package is not None
            candidate = SkillVersion(
                package_id=package.id,
                version_label="tool-activation-test",
                status="candidate",
                content_hash="tool-activation-test-hash",
                manifest_json={"name": "pskill-builder", "description": "Tool activation candidate."},
                body_object_key="skills/psop/pskill-builder/SKILL.md",
                resource_index=[
                    {"path": "SKILL.md", "kind": "skill", "content_hash": "skill-md-hash", "size_bytes": 128},
                    {"path": "references/tool.md", "kind": "references", "content_hash": "ref-hash", "size_bytes": 64},
                ],
                allowed_tools=["psop.pskills.read", "psop.materials.read", "psop.run_events.write_low"],
                validation_status="valid",
                validation_diagnostics=[],
            )
            session.add(candidate)
            session.commit()
            candidate_version_id = candidate.id
        run_response = client.post(
            "/api/v1/agent-runs",
            json={
                "agent_key": "psop.governance",
                "owner_type": "governance",
                "owner_id": "proposal-activate-skill",
                "input_payload": {
                    "agent_decision": {
                        "decision_type": "tool_call",
                        "tool_name": "psop.skill_version.activate",
                        "side_effect_level": "high_write",
                        "arguments_summary": {"package_name": "pskill-builder", "version_id": candidate_version_id},
                        "expected_effect_summary": "激活新的 pskill-builder SkillVersion。",
                        "authorization_reason": "激活 SkillVersion 会改变生产 Skill package 配置。",
                        "reversible": True,
                        "idempotency_key": "activate-pskill-builder-version-test",
                    }
                },
            },
        )
        agent_run_id = run_response.json()["id"]
        first_run_response = client.post(f"/api/v1/agent-runs/{agent_run_id}/run-once")
        authorization = client.get(f"/api/v1/agent-runs/{agent_run_id}/tool-authorizations").json()[0]
        approve_response = client.post(
            f"/api/v1/tool-authorizations/{authorization['id']}/approve",
            json={"response_payload": {"approved_by": "tester"}},
        )
        resumed_response = client.post(f"/api/v1/agent-runs/{agent_run_id}/run-once")
        after_response = client.get("/api/v1/skills/pskill-builder")
        tool_calls_response = client.get(f"/api/v1/agent-runs/{agent_run_id}/tool-calls")
        executed_authorization_response = client.get(f"/api/v1/tool-authorizations/{authorization['id']}")

    assert sync_response.status_code == 200
    assert before_response.status_code == 200
    assert before_response.json()["active_version_id"] != candidate_version_id
    assert run_response.status_code == 201
    assert first_run_response.json()["status"] == "waiting_tool_authorization"
    assert authorization["tool_name"] == "psop.skill_version.activate"
    assert approve_response.status_code == 200
    assert resumed_response.status_code == 200
    assert resumed_response.json()["status"] == "succeeded"
    assert resumed_response.json()["output_payload"]["tool_result"]["result"]["version_id"] == candidate_version_id
    assert after_response.json()["active_version_id"] == candidate_version_id
    assert after_response.json()["active_version"]["allowed_tools"] == [
        "psop.pskills.read",
        "psop.materials.read",
        "psop.run_events.write_low",
    ]
    assert tool_calls_response.json()[0]["result_summary"]["result"]["package_name"] == "pskill-builder"
    assert executed_authorization_response.json()["status"] == "executed"


def test_agent_runner_output_guardrail_records_business_wait_as_non_hitl() -> None:
    client, _, _ = create_test_client()

    with client:
        run_response = client.post(
            "/api/v1/agent-runs",
            json={
                "agent_key": "pskill.builder",
                "owner_type": "pskill_draft",
                "owner_id": "draft-needs-input",
                "input_payload": {
                    "expected_output": {
                        "draft_summary": "需要用户补充设备铭牌照片。",
                        "clarifying_questions": ["请补充设备铭牌照片和额定电压。"],
                        "memory_candidates": [
                            {
                                "namespace": "builder",
                                "memory_type": "semantic",
                                "title": "铭牌照片是设备参数证据",
                                "content": "设备铭牌照片可作为型号、电压和安全约束的 source ref。",
                                "confidence": 88,
                                "source_refs": [{"kind": "pskill_material", "id": "material-nameplate-1"}],
                                "tags": ["evidence", "device"],
                            }
                        ],
                    }
                },
            },
        )
        agent_run_id = run_response.json()["id"]
        run_once_response = client.post(f"/api/v1/agent-runs/{agent_run_id}/run-once")
        events_response = client.get(f"/api/v1/agent-runs/{agent_run_id}/events")
        authorizations_response = client.get(f"/api/v1/agent-runs/{agent_run_id}/tool-authorizations")
        memory_response = client.get(f"/api/v1/agent-runs/{agent_run_id}/memory-entries")

    assert run_response.status_code == 201
    assert run_once_response.status_code == 200
    assert run_once_response.json()["status"] == "succeeded"
    assert authorizations_response.json() == []

    events = events_response.json()
    guardrail_event = next(item for item in events if item["event_type"] == "agent.output_guardrail.checked")
    final_event = next(item for item in events if item["event_type"] == "agent.final_output")
    assert guardrail_event["payload"]["passed"] is True
    assert guardrail_event["payload"]["business_wait_state"] == "clarifying_questions"
    assert guardrail_event["payload"]["non_hitl_business_state"] is True
    assert final_event["payload"]["business_wait_state"] == "clarifying_questions"
    assert final_event["payload"]["non_hitl_business_state"] is True

    memory_entries = memory_response.json()
    assert len(memory_entries) == 1
    assert memory_entries[0]["memory_type"] == "semantic"
    assert memory_entries[0]["source_refs"] == [{"kind": "pskill_material", "id": "material-nameplate-1"}]


def test_agent_runner_output_guardrail_rejects_memory_candidate_without_source_refs() -> None:
    client, _, _ = create_test_client()

    with client:
        run_response = client.post(
            "/api/v1/agent-runs",
            json={
                "agent_key": "pskill.evaluator",
                "owner_type": "run_evaluation",
                "owner_id": "evaluation-guardrail",
                "input_payload": {
                    "expected_output": {
                        "summary": "Evaluator attempted to persist an unsupported memory candidate.",
                        "memory_candidates": [
                            {
                                "namespace": "evaluation",
                                "memory_type": "episodic",
                                "title": "Unattributed runtime failure",
                                "content": "Runtime failures should be debugged through replay.",
                                "confidence": 84,
                            }
                        ],
                    }
                },
            },
        )
        agent_run_id = run_response.json()["id"]
        run_once_response = client.post(f"/api/v1/agent-runs/{agent_run_id}/run-once")
        events_response = client.get(f"/api/v1/agent-runs/{agent_run_id}/events")
        memory_response = client.get(f"/api/v1/agent-runs/{agent_run_id}/memory-entries")

    assert run_response.status_code == 201
    assert run_once_response.status_code == 200
    payload = run_once_response.json()
    assert payload["status"] == "failed"
    assert payload["error_message"] == "output_guardrail_failed"
    assert payload["output_payload"]["guardrail_findings"][0]["code"] == "memory_candidate_missing_source_refs"
    assert memory_response.json() == []

    guardrail_event = next(
        item for item in events_response.json() if item["event_type"] == "agent.output_guardrail.checked"
    )
    failed_event = next(
        item for item in events_response.json() if item["event_type"] == "agent.output_guardrail.failed"
    )
    assert guardrail_event["payload"]["passed"] is False
    assert guardrail_event["payload"]["findings"][0]["path"] == "memory_candidates[0].source_refs"
    assert failed_event["payload"]["findings"][0]["code"] == "memory_candidate_missing_source_refs"
