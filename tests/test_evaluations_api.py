from __future__ import annotations

from app.gateway.inference import LlmCompletion
from tests.test_skills_api import create_test_client


class FailingRuntimeInferenceGateway:
    def complete(self, *, system_prompt: str, user_prompt: str, route_key: str = "text") -> LlmCompletion:
        raise RuntimeError("runtime provider failed during evaluation test")


def test_evaluation_api_creates_report_for_completed_run_and_records_evaluator_agent() -> None:
    client, _, _ = create_test_client()

    with client:
        run_id = _publish_and_complete_successful_run(client, key="evaluation-success")

        evaluation_response = client.post(f"/api/v1/evaluations/runs/{run_id}")
        evaluation = evaluation_response.json()
        detail_response = client.get(f"/api/v1/evaluations/{evaluation['id']}")
        findings_response = client.get(f"/api/v1/evaluations/{evaluation['id']}/findings")
        all_findings_response = client.get("/api/v1/evaluations/findings", params={"run_id": run_id})
        agent_run_response = client.get(f"/api/v1/agent-runs/{evaluation['agent_run_id']}")
        agent_events_response = client.get(f"/api/v1/agent-runs/{evaluation['agent_run_id']}/events")
        agent_model_calls_response = client.get(f"/api/v1/agent-runs/{evaluation['agent_run_id']}/model-calls")

    assert evaluation_response.status_code == 201
    assert evaluation["run_id"] == run_id
    assert evaluation["overall_outcome"] == "success"
    assert evaluation["quality_score"] == 94
    assert evaluation["findings"] == []
    assert evaluation["attribution"]["finding_count"] == 0
    assert detail_response.json()["id"] == evaluation["id"]
    assert findings_response.json() == []
    assert all_findings_response.json() == []
    assert agent_run_response.json()["agent_key"] == "pskill.evaluator"
    assert agent_run_response.json()["status"] == "succeeded"
    assert agent_run_response.json()["owner_type"] == "run_evaluation"
    assert agent_run_response.json()["owner_id"] == evaluation["id"]
    assert agent_run_response.json()["output_payload"]["schema"] == "RunEvaluationResult"
    assert agent_model_calls_response.json()[0]["provider"] == "deterministic"
    assert {
        "agent.run.created",
        "evaluation.run.started",
        "evaluation.agent.model_call.completed",
        "evaluation.run.completed",
    } <= {item["event_type"] for item in agent_events_response.json()}


def test_evaluation_api_generates_findings_for_failed_run_and_updates_status() -> None:
    client, _, failing_inference = create_test_client()

    with client:
        run_id, invocation_response, run_payload = _publish_and_complete_failed_run(
            client,
            key="evaluation-failed",
            restore_gateway=failing_inference,
        )

        evaluation_response = client.post(f"/api/v1/evaluations/runs/{run_id}")
        evaluation = evaluation_response.json()
        finding = evaluation["findings"][0]
        finding_list_response = client.get(
            "/api/v1/evaluations/findings",
            params={"status": "open", "category": "runner_issue", "run_id": run_id},
        )
        update_response = client.patch(
            f"/api/v1/evaluations/findings/{finding['id']}",
            json={"status": "accepted"},
        )

    assert invocation_response.status_code == 201
    assert run_payload["status"] == "failed"
    assert evaluation_response.status_code == 201
    assert evaluation["overall_outcome"] == "failed"
    assert evaluation["quality_score"] < 42
    assert finding["category"] == "runner_issue"
    assert finding["severity"] == "high"
    assert finding["status"] == "open"
    assert finding["evidence_refs"][0]["kind"] == "run_trace"
    assert finding_list_response.json()[0]["id"] == finding["id"]
    assert update_response.json()["status"] == "accepted"


def test_evaluation_activity_websocket_streams_report_snapshot() -> None:
    client, _, failing_inference = create_test_client()

    with client:
        run_id, _, _ = _publish_and_complete_failed_run(
            client,
            key="evaluation-activity",
            restore_gateway=failing_inference,
        )
        evaluation_response = client.post(f"/api/v1/evaluations/runs/{run_id}")
        evaluation = evaluation_response.json()
        finding = evaluation["findings"][0]

        with client.websocket_connect(f"/ws/evaluations/{evaluation['id']}") as websocket:
            connected = websocket.receive_json()
            initial_snapshot = websocket.receive_json()
            update_response = client.patch(
                f"/api/v1/evaluations/findings/{finding['id']}",
                json={"status": "accepted"},
            )
            updated_snapshot = websocket.receive_json()

    assert evaluation_response.status_code == 201
    assert connected["event_type"] == "ws.connected"
    assert connected["evaluation_id"] == evaluation["id"]

    assert initial_snapshot["event_type"] == "evaluation.activity.snapshot"
    initial_payload = initial_snapshot["payload"]
    assert initial_payload["evaluation"]["id"] == evaluation["id"]
    assert initial_payload["agent_run"]["id"] == evaluation["agent_run_id"]
    assert initial_payload["agent_run"]["agent_key"] == "pskill.evaluator"
    assert initial_payload["active"] is False
    assert initial_payload["terminal"] is True
    assert initial_payload["findings"][0]["id"] == finding["id"]
    assert initial_payload["findings"][0]["status"] == "open"
    assert {
        "agent.run.created",
        "evaluation.run.started",
        "evaluation.agent.model_call.completed",
        "evaluation.run.completed",
    } <= {item["event_type"] for item in initial_payload["agent_events"]}
    assert initial_payload["model_calls"][0]["provider"] == "deterministic"

    assert update_response.status_code == 200
    updated_payload = updated_snapshot["payload"]
    assert updated_payload["findings"][0]["id"] == finding["id"]
    assert updated_payload["findings"][0]["status"] == "accepted"
    assert updated_payload["evaluation"]["findings"][0]["status"] == "accepted"


def _publish_and_complete_successful_run(client, *, key: str) -> str:
    created = client.post(
        "/api/v1/pskills",
        json={
            "key": key,
            "name": "Evaluation Success",
            "description": "Validate run evaluation.",
        },
    ).json()
    publish_payload = client.post(
        f"/api/v1/pskills/{created['id']}/publish",
        json={"publish_reason": "Evaluation acceptance publish"},
    ).json()
    compile_request_id = publish_payload["compile_request"]["id"]
    client.post(f"/api/v1/compiler/requests/{compile_request_id}/retry")
    invocation_payload = client.post(
        "/api/v1/gateway/invocations",
        json={
            "skill_key": key,
            "input_envelope": {"user_input": "请处理现场任务"},
            "gateway_type": "web",
        },
    ).json()
    run_id = invocation_payload["run_id"]
    client.post(
        f"/api/v1/runs/{run_id}/events",
        json={
            "direction": "input",
            "event_kind": "terminal.text.input.v1",
            "mime_type": "text/plain",
            "payload_inline": "现场步骤已完成，请核验。",
            "external_event_id": f"{key}-evidence-001",
        },
    )
    return str(run_id)


def _publish_and_complete_failed_run(client, *, key: str, restore_gateway) -> tuple[str, object, dict]:
    created = client.post(
        "/api/v1/pskills",
        json={
            "key": key,
            "name": "Evaluation Failed",
            "description": "Validate failed run evaluation findings.",
        },
    ).json()
    publish_payload = client.post(
        f"/api/v1/pskills/{created['id']}/publish",
        json={"publish_reason": "Create artifact before failing runtime"},
    ).json()
    compile_request_id = publish_payload["compile_request"]["id"]
    client.post(f"/api/v1/compiler/requests/{compile_request_id}/retry")

    client.app.state.inference_gateway = FailingRuntimeInferenceGateway()
    try:
        invocation_response = client.post(
            "/api/v1/gateway/invocations",
            json={
                "skill_key": key,
                "input_envelope": {"user_input": "触发运行失败"},
                "gateway_type": "web",
            },
        )
        run_id = invocation_response.json()["run_id"]
        run_payload = client.get(f"/api/v1/runs/{run_id}").json()
    finally:
        client.app.state.inference_gateway = restore_gateway
    return str(run_id), invocation_response, run_payload
