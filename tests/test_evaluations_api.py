from __future__ import annotations

from app.gateway.inference import LlmCompletion
from app.jobs.types import GOVERNANCE_PROPOSAL_JOB_TYPE, RUN_EVALUATION_JOB_TYPE
from app.jobs.worker import RuntimeJobWorker
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
        replay_response = client.get(f"/api/v1/replay/runs/{run_id}")

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
    evaluator_facts = agent_run_response.json()["input_payload"]["facts"]
    assert "run_trace_event_types" in evaluator_facts
    assert "trace_event_types" not in evaluator_facts
    assert "last_run_trace" in evaluator_facts["evidence"]
    assert "last_trace" not in evaluator_facts["evidence"]
    assert agent_model_calls_response.json()[0]["provider"] == "deterministic"
    assert {
        "agent.run.created",
        "evaluation.run.started",
        "evaluation.agent.model_call.completed",
        "evaluation.run.completed",
    } <= {item["event_type"] for item in agent_events_response.json()}
    replay_payload = replay_response.json()
    assert evaluation["id"] in {item["id"] for item in replay_payload["run_evaluations"]}
    assert evaluation["agent_run_id"] in {item["id"] for item in replay_payload["agent_runs"]}
    assert "evaluation.run.completed" in {item["event_type"] for item in replay_payload["agent_events"]}
    assert any(item["agent_run_id"] == evaluation["agent_run_id"] for item in replay_payload["model_calls"])
    assert replay_payload["agent_model_calls"] == replay_payload["model_calls"]
    assert replay_payload["run_evaluation_findings"] == []


def test_evaluation_api_lists_reports_with_run_pskill_and_outcome_filters() -> None:
    client, _, failing_inference = create_test_client()

    with client:
        success_run_id = _publish_and_complete_successful_run(client, key="evaluation-list-success")
        failed_run_id, _, _ = _publish_and_complete_failed_run(
            client,
            key="evaluation-list-failed",
            restore_gateway=failing_inference,
        )

        success_evaluation = client.post(f"/api/v1/evaluations/runs/{success_run_id}").json()
        failed_evaluation = client.post(f"/api/v1/evaluations/runs/{failed_run_id}").json()

        all_reports_response = client.get("/api/v1/evaluations")
        run_filtered_response = client.get("/api/v1/evaluations", params={"run_id": success_run_id})
        pskill_filtered_response = client.get(
            "/api/v1/evaluations",
            params={"pskill_definition_id": failed_evaluation["pskill_definition_id"]},
        )
        outcome_filtered_response = client.get("/api/v1/evaluations", params={"overall_outcome": "failed"})

    assert all_reports_response.status_code == 200
    report_ids = {item["id"] for item in all_reports_response.json()}
    assert {success_evaluation["id"], failed_evaluation["id"]} <= report_ids

    assert run_filtered_response.status_code == 200
    assert [item["id"] for item in run_filtered_response.json()] == [success_evaluation["id"]]

    assert pskill_filtered_response.status_code == 200
    assert [item["id"] for item in pskill_filtered_response.json()] == [failed_evaluation["id"]]

    assert outcome_filtered_response.status_code == 200
    failed_reports = outcome_filtered_response.json()
    assert failed_evaluation["id"] in {item["id"] for item in failed_reports}
    assert success_evaluation["id"] not in {item["id"] for item in failed_reports}
    assert failed_reports[0]["findings"][0]["run_id"] == failed_run_id


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
            params={
                "status": "open",
                "category": "runner_issue",
                "run_id": run_id,
                "pskill_definition_id": evaluation["pskill_definition_id"],
            },
        )
        update_response = client.patch(
            f"/api/v1/evaluations/findings/{finding['id']}",
            json={"status": "accepted"},
        )
        replay_response = client.get(f"/api/v1/replay/runs/{run_id}")

    assert invocation_response.status_code == 201
    assert run_payload["status"] == "failed"
    assert evaluation_response.status_code == 201
    assert evaluation["overall_outcome"] == "failed"
    assert evaluation["quality_score"] < 42
    assert finding["category"] == "runner_issue"
    assert finding["severity"] == "high"
    assert finding["status"] == "open"
    assert finding["evidence_refs"][0]["kind"] == "run_trace"
    assert finding["run_id"] == run_id
    assert finding["pskill_definition_id"] == evaluation["pskill_definition_id"]
    assert finding["pskill_version_id"] == evaluation["pskill_version_id"]
    assert finding["overall_outcome"] == evaluation["overall_outcome"]
    assert finding["quality_score"] == evaluation["quality_score"]
    assert finding["evaluation_created_at"] == evaluation["created_at"]
    assert finding_list_response.json()[0]["id"] == finding["id"]
    assert finding_list_response.json()[0]["run_id"] == run_id
    assert finding_list_response.json()[0]["quality_score"] == evaluation["quality_score"]
    assert update_response.json()["status"] == "accepted"
    assert update_response.json()["run_id"] == run_id
    assert update_response.json()["quality_score"] == evaluation["quality_score"]
    replay_payload = replay_response.json()
    evidence_ref = finding["evidence_refs"][0]
    matching_timeline_items = [
        item
        for item in replay_payload["timeline"]
        if item["source_kind"] == evidence_ref["kind"] and item["source_id"] == evidence_ref["id"]
    ]
    assert matching_timeline_items
    assert matching_timeline_items[0]["event_type"] == evidence_ref["event_type"]
    assert replay_payload["run_evaluation_findings"][0]["evidence_refs"][0]["id"] == evidence_ref["id"]
    assert replay_payload["run_evaluation_findings"][0]["quality_score"] == evaluation["quality_score"]
    assert replay_payload["run_evaluations"][0]["findings"][0]["quality_score"] == evaluation["quality_score"]


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


def test_evaluation_api_queues_evaluation_and_governance_jobs_for_worker_processing() -> None:
    client, _, failing_inference = create_test_client()

    with client:
        run_id, _, _ = _publish_and_complete_failed_run(
            client,
            key="evaluation-job-producer",
            restore_gateway=failing_inference,
        )

        queue_evaluation_response = client.post(f"/api/v1/evaluations/runs/{run_id}/queue")
        duplicate_queue_response = client.post(f"/api/v1/evaluations/runs/{run_id}/queue")
        evaluation_job_id = queue_evaluation_response.json()["id"]

        worker = RuntimeJobWorker(
            settings=client.app.state.settings,
            database_manager=client.app.state.db_manager,
            gitlab_gateway=client.app.state.gitlab_gateway,
            inference_gateway=client.app.state.inference_gateway,
            asr_gateway=client.app.state.asr_gateway,
            object_store=client.app.state.object_store,
        )
        processed_evaluation = worker.run_once()
        evaluation_job_response = client.get(
            "/api/v1/runtime/jobs",
            params={"job_type": RUN_EVALUATION_JOB_TYPE, "q": evaluation_job_id},
        )
        evaluation_job = evaluation_job_response.json()[0]
        evaluation_response = client.get(f"/api/v1/evaluations/{evaluation_job['payload']['evaluation_id']}")
        finding = evaluation_response.json()["findings"][0]

        queue_proposal_response = client.post(f"/api/v1/evaluations/findings/{finding['id']}/queue-proposal")
        duplicate_proposal_queue_response = client.post(
            f"/api/v1/evaluations/findings/{finding['id']}/queue-proposal"
        )
        proposal_job_id = queue_proposal_response.json()["id"]
        processed_proposal = worker.run_once()
        proposal_job_response = client.get(
            "/api/v1/runtime/jobs",
            params={"job_type": GOVERNANCE_PROPOSAL_JOB_TYPE, "q": proposal_job_id},
        )
        proposal_job = proposal_job_response.json()[0]
        proposal_response = client.get(f"/api/v1/governance/proposals/{proposal_job['payload']['proposal_id']}")

    assert queue_evaluation_response.status_code == 202
    assert queue_evaluation_response.json()["job_type"] == RUN_EVALUATION_JOB_TYPE
    assert queue_evaluation_response.json()["status"] == "pending"
    assert queue_evaluation_response.json()["run_id"] == run_id
    assert duplicate_queue_response.status_code == 202
    assert duplicate_queue_response.json()["id"] == evaluation_job_id

    assert processed_evaluation is True
    assert evaluation_job_response.status_code == 200
    assert evaluation_job["status"] == "succeeded"
    assert evaluation_job["payload"]["operation"] == "run_evaluation"
    assert evaluation_job["payload"]["run_id"] == run_id
    assert evaluation_response.status_code == 200
    assert evaluation_response.json()["overall_outcome"] == "failed"
    assert finding["status"] == "open"

    assert queue_proposal_response.status_code == 202
    assert queue_proposal_response.json()["job_type"] == GOVERNANCE_PROPOSAL_JOB_TYPE
    assert queue_proposal_response.json()["status"] == "pending"
    assert duplicate_proposal_queue_response.status_code == 202
    assert duplicate_proposal_queue_response.json()["id"] == proposal_job_id

    assert processed_proposal is True
    assert proposal_job_response.status_code == 200
    assert proposal_job["status"] == "succeeded"
    assert proposal_job["payload"]["operation"] == "governance_proposal"
    assert proposal_job["payload"]["source_finding_ids"] == [finding["id"]]
    assert proposal_response.status_code == 200
    assert proposal_response.json()["status"] == "draft"
    assert proposal_response.json()["source_run_id"] == run_id
    assert proposal_response.json()["source_finding_ids"] == [finding["id"]]


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
