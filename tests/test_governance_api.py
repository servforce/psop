from __future__ import annotations

from app.gateway.inference import LlmCompletion
from tests.test_skills_api import create_test_client


class FailingRuntimeInferenceGateway:
    def complete(self, *, system_prompt: str, user_prompt: str, route_key: str = "text") -> LlmCompletion:
        raise RuntimeError("runtime provider failed during governance test")


def test_governance_api_creates_proposal_from_finding_and_tracks_business_states() -> None:
    client, _, original_inference = create_test_client()

    with client:
        created = client.post(
            "/api/v1/pskills",
            json={
                "key": "governance-failed",
                "name": "Governance Failed",
                "description": "Validate governance proposal flow.",
            },
        ).json()
        publish_payload = client.post(
            f"/api/v1/pskills/{created['id']}/publish",
            json={"publish_reason": "Create artifact before failing runtime"},
        ).json()
        client.post(f"/api/v1/compiler/requests/{publish_payload['compile_request']['id']}/retry")

        client.app.state.inference_gateway = FailingRuntimeInferenceGateway()
        invocation_response = client.post(
            "/api/v1/gateway/invocations",
            json={
                "skill_key": "governance-failed",
                "input_envelope": {"user_input": "触发治理提案"},
                "gateway_type": "web",
            },
        )
        run_id = invocation_response.json()["run_id"]
        client.app.state.inference_gateway = original_inference

        evaluation = client.post(f"/api/v1/evaluations/runs/{run_id}").json()
        finding = evaluation["findings"][0]
        proposal_response = client.post(f"/api/v1/evaluations/findings/{finding['id']}/create-proposal")
        proposal = proposal_response.json()
        agent_run = client.get(f"/api/v1/agent-runs/{proposal['agent_run_id']}").json()
        agent_events = client.get(f"/api/v1/agent-runs/{proposal['agent_run_id']}/events").json()
        agent_model_calls = client.get(f"/api/v1/agent-runs/{proposal['agent_run_id']}/model-calls").json()
        agent_authorizations = client.get(f"/api/v1/agent-runs/{proposal['agent_run_id']}/tool-authorizations").json()
        converted_finding = client.get(
            "/api/v1/evaluations/findings",
            params={"run_id": run_id, "status": "converted_to_proposal"},
        ).json()[0]

        run_tests = client.post(f"/api/v1/governance/proposals/{proposal['id']}/run-tests").json()
        regression_experiment = run_tests["experiments"][0]
        experiment_detail = client.get(f"/api/v1/governance/experiments/{regression_experiment['id']}").json()
        approved = client.post(
            f"/api/v1/governance/proposals/{proposal['id']}/submit-review",
            json={"decision": "approved", "review_notes": "proposal verified"},
        ).json()
        canary = client.post(f"/api/v1/governance/proposals/{proposal['id']}/activate-canary").json()
        running_canaries = client.get(
            "/api/v1/governance/experiments",
            params={"proposal_id": proposal["id"], "status": "running", "experiment_type": "canary"},
        ).json()
        canary_detail = client.get(f"/api/v1/governance/experiments/{running_canaries[0]['id']}").json()
        rolled_back = client.post(f"/api/v1/governance/proposals/{proposal['id']}/rollback").json()
        proposal_experiments = client.get(f"/api/v1/governance/proposals/{proposal['id']}/experiments").json()
        rolled_back_canaries = client.get(
            "/api/v1/governance/experiments",
            params={"proposal_id": proposal["id"], "status": "rolled_back", "experiment_type": "canary"},
        ).json()
        listed = client.get("/api/v1/governance/proposals", params={"status": "rolled_back"}).json()

    assert invocation_response.status_code == 201
    assert proposal_response.status_code == 201
    assert proposal["status"] == "draft"
    assert proposal["proposal_type"] == "agent_skill_update"
    assert proposal["source_finding_ids"] == [finding["id"]]
    assert proposal["source_findings"][0]["id"] == finding["id"]
    assert proposal["source_findings"][0]["run_id"] == run_id
    assert proposal["source_findings"][0]["quality_score"] == evaluation["quality_score"]
    assert proposal["source_evaluation_id"] == evaluation["id"]
    assert proposal["source_run_id"] == run_id
    assert proposal["target"]["finding_id"] == finding["id"]
    assert proposal["risk_assessment"]["requires_human_review"] is True
    assert proposal["activation_plan"]["direct_activation_allowed"] is False
    assert converted_finding["id"] == finding["id"]
    assert converted_finding["status"] == "converted_to_proposal"

    assert agent_run["agent_key"] == "psop.governance"
    assert agent_run["status"] == "succeeded"
    assert agent_run["owner_type"] == "governance_proposal"
    assert agent_run["owner_id"] == proposal["id"]
    assert agent_run["run_id"] == run_id
    assert agent_run["output_payload"]["schema"] == "GovernanceProposalResult"
    assert agent_model_calls[0]["provider"] == "deterministic"
    assert agent_authorizations == []
    assert {
        "agent.run.created",
        "governance.proposal.started",
        "governance.agent.model_call.completed",
        "governance.proposal.created",
    } <= {item["event_type"] for item in agent_events}

    assert run_tests["status"] == "testing"
    assert regression_experiment["experiment_type"] == "regression"
    assert regression_experiment["status"] == "succeeded"
    assert experiment_detail["id"] == regression_experiment["id"]
    assert approved["status"] == "approved"
    assert canary["status"] == "canary"
    assert canary["experiments"][-1]["experiment_type"] == "canary"
    assert canary["experiments"][-1]["status"] == "running"
    assert [item["id"] for item in running_canaries] == [canary["experiments"][-1]["id"]]
    assert running_canaries[0]["proposal_status"] == "canary"
    assert running_canaries[0]["proposal_type"] == proposal["proposal_type"]
    assert running_canaries[0]["problem_statement"] == proposal["problem_statement"]
    assert running_canaries[0]["canary_scope"]["proposal_type"] == proposal["proposal_type"]
    assert "canary_metric_regression" in running_canaries[0]["rollback_conditions"]
    assert canary_detail["proposal_status"] == "canary"
    assert canary_detail["canary_scope"] == running_canaries[0]["canary_scope"]
    assert rolled_back["status"] == "rolled_back"
    assert rolled_back["experiments"][-1]["experiment_type"] == "rollback"
    assert rolled_back["experiments"][-1]["rollback_conditions"]
    assert {item["experiment_type"] for item in proposal_experiments} == {"regression", "canary", "rollback"}
    assert len(proposal_experiments) == 3
    assert [item["id"] for item in rolled_back_canaries] == [canary["experiments"][-1]["id"]]
    assert listed[0]["id"] == proposal["id"]


def test_governance_api_creates_manual_proposal_with_agent_run() -> None:
    client, _, _ = create_test_client()

    with client:
        proposal_response = client.post(
            "/api/v1/governance/proposals",
            json={
                "proposal_type": "test_suite_update",
                "target": {"kind": "regression_suite", "name": "runtime-llm-node"},
                "problem_statement": "补充 Runtime LLM node 回归测试覆盖。",
            },
        )
        proposal = proposal_response.json()
        update_response = client.patch(
            f"/api/v1/governance/proposals/{proposal['id']}",
            json={
                "problem_statement": "补充 Runtime LLM node 回归测试覆盖和 Replay 验证。",
                "proposed_changes": [
                    {
                        "kind": "patch",
                        "patch_diff": "--- a/tests/test_runtime.py\n+++ b/tests/test_runtime.py\n@@\n-old\n+new",
                    }
                ],
                "required_tests": [
                    {"kind": "regression", "description": "runtime regression"},
                    {"kind": "replay", "description": "replay verification"},
                ],
            },
        )
        updated = update_response.json()
        locked = client.post(f"/api/v1/governance/proposals/{proposal['id']}/run-tests").json()
        locked_update_response = client.patch(
            f"/api/v1/governance/proposals/{proposal['id']}",
            json={"problem_statement": "testing 状态不允许编辑。"},
        )
        agent_run = client.get(f"/api/v1/agent-runs/{proposal['agent_run_id']}").json()
        agent_events = client.get(f"/api/v1/agent-runs/{proposal['agent_run_id']}/events").json()

    assert proposal_response.status_code == 201
    assert update_response.status_code == 200
    assert updated["problem_statement"] == "补充 Runtime LLM node 回归测试覆盖和 Replay 验证。"
    assert updated["proposed_changes"][0]["patch_diff"].startswith("--- a/tests/test_runtime.py")
    assert [item["kind"] for item in updated["required_tests"]] == ["regression", "replay"]
    assert locked["status"] == "testing"
    assert locked_update_response.status_code == 422
    assert proposal["status"] == "draft"
    assert proposal["proposal_type"] == "test_suite_update"
    assert proposal["required_tests"][0]["kind"] == "regression"
    assert agent_run["agent_key"] == "psop.governance"
    assert agent_run["status"] == "succeeded"
    assert agent_run["owner_id"] == proposal["id"]
    assert "governance.proposal.updated" in {item["event_type"] for item in agent_events}


def test_governance_api_creates_manual_proposal_from_source_findings() -> None:
    client, _, original_inference = create_test_client()

    with client:
        created = client.post(
            "/api/v1/pskills",
            json={
                "key": "governance-manual-source",
                "name": "Governance Manual Source",
                "description": "Validate manual proposal source finding conversion.",
            },
        ).json()
        publish_payload = client.post(
            f"/api/v1/pskills/{created['id']}/publish",
            json={"publish_reason": "Create artifact before failing runtime"},
        ).json()
        client.post(f"/api/v1/compiler/requests/{publish_payload['compile_request']['id']}/retry")

        client.app.state.inference_gateway = FailingRuntimeInferenceGateway()
        invocation_response = client.post(
            "/api/v1/gateway/invocations",
            json={
                "skill_key": "governance-manual-source",
                "input_envelope": {"user_input": "触发手工治理提案"},
                "gateway_type": "web",
            },
        )
        run_id = invocation_response.json()["run_id"]
        client.app.state.inference_gateway = original_inference

        evaluation = client.post(f"/api/v1/evaluations/runs/{run_id}").json()
        finding = evaluation["findings"][0]
        proposal_response = client.post(
            "/api/v1/governance/proposals",
            json={
                "proposal_type": "agent_skill_update",
                "target": {"kind": "run_evaluation_findings", "finding_ids": [finding["id"]]},
                "problem_statement": "基于选中 findings 创建治理提案。",
                "source_finding_ids": [finding["id"], finding["id"]],
                "source_evaluation_id": evaluation["id"],
                "source_run_id": run_id,
            },
        )
        proposal = proposal_response.json()
        converted_finding = client.get(
            "/api/v1/evaluations/findings",
            params={"run_id": run_id, "status": "converted_to_proposal"},
        ).json()[0]

    assert invocation_response.status_code == 201
    assert proposal_response.status_code == 201
    assert proposal["source_finding_ids"] == [finding["id"]]
    assert proposal["source_findings"][0]["id"] == finding["id"]
    assert proposal["source_findings"][0]["quality_score"] == evaluation["quality_score"]
    assert proposal["source_evaluation_id"] == evaluation["id"]
    assert proposal["source_run_id"] == run_id
    assert converted_finding["id"] == finding["id"]
    assert converted_finding["status"] == "converted_to_proposal"


def test_governance_proposal_activity_websocket_streams_proposal_snapshot() -> None:
    client, _, _ = create_test_client()

    with client:
        proposal_response = client.post(
            "/api/v1/governance/proposals",
            json={
                "proposal_type": "test_suite_update",
                "target": {"kind": "regression_suite", "name": "governance-activity"},
                "problem_statement": "补充 governance proposal activity stream 覆盖。",
            },
        )
        proposal = proposal_response.json()

        with client.websocket_connect(f"/ws/governance/proposals/{proposal['id']}") as websocket:
            connected = websocket.receive_json()
            initial_snapshot = websocket.receive_json()
            run_tests_response = client.post(f"/api/v1/governance/proposals/{proposal['id']}/run-tests")
            updated_snapshot = websocket.receive_json()

    assert proposal_response.status_code == 201
    assert connected["event_type"] == "ws.connected"
    assert connected["proposal_id"] == proposal["id"]

    assert initial_snapshot["event_type"] == "governance_proposal.activity.snapshot"
    initial_payload = initial_snapshot["payload"]
    assert initial_payload["proposal"]["id"] == proposal["id"]
    assert initial_payload["proposal"]["status"] == "draft"
    assert initial_payload["agent_run"]["id"] == proposal["agent_run_id"]
    assert initial_payload["agent_run"]["agent_key"] == "psop.governance"
    assert initial_payload["active"] is False
    assert initial_payload["terminal"] is True
    assert initial_payload["tool_authorizations"] == []
    assert initial_payload["model_calls"][0]["provider"] == "deterministic"
    assert {
        "agent.run.created",
        "governance.proposal.started",
        "governance.agent.model_call.completed",
        "governance.proposal.created",
    } <= {item["event_type"] for item in initial_payload["agent_events"]}

    assert run_tests_response.status_code == 200
    updated_payload = updated_snapshot["payload"]
    assert updated_payload["proposal"]["status"] == "testing"
    assert updated_payload["proposal"]["experiments"][0]["experiment_type"] == "regression"
    assert updated_payload["proposal"]["experiments"][0]["status"] == "succeeded"
