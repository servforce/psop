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
        created = client.post(
            "/api/v1/pskills",
            json={
                "key": "evaluation-failed",
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
        invocation_response = client.post(
            "/api/v1/gateway/invocations",
            json={
                "skill_key": "evaluation-failed",
                "input_envelope": {"user_input": "触发运行失败"},
                "gateway_type": "web",
            },
        )
        run_id = invocation_response.json()["run_id"]
        run_payload = client.get(f"/api/v1/runs/{run_id}").json()
        client.app.state.inference_gateway = failing_inference

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
