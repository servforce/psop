from __future__ import annotations

from app.gateway.inference import LlmCompletion
from app.pskills import service as skills_service_module
from tests.test_skills_api import _fake_video_analysis_result, create_test_client


class FailingRuntimeInferenceGateway:
    def complete(self, *, system_prompt: str, user_prompt: str, route_key: str = "text") -> LlmCompletion:
        raise RuntimeError("runtime provider failed during closed-loop test")


def test_materials_to_governance_closed_loop(monkeypatch) -> None:
    monkeypatch.setattr(
        skills_service_module,
        "analyze_video_material",
        lambda **_: _fake_video_analysis_result(),
    )
    client, fake_gateway, original_inference = create_test_client()

    with client:
        created = client.post(
            "/api/v1/pskills",
            json={
                "key": "closed-loop-materials",
                "name": "Closed Loop Materials",
                "description": "Validate materials to governance acceptance loop.",
            },
        ).json()
        skill_id = created["id"]
        material_response = client.post(
            f"/api/v1/pskills/{skill_id}/materials",
            data={"name": "现场流程视频", "material_kind": "video"},
            files={"file": ("workflow.mp4", b"fake mp4", "video/mp4")},
        )
        material_id = material_response.json()["id"]
        generate_response = client.post(
            f"/api/v1/pskills/{skill_id}/draft/generate",
            json={
                "user_description": "请基于素材生成一个现场支持 PSkill。",
                "material_ids": [material_id],
                "base_commit_sha": created["latest_draft_head_sha"],
            },
        )
        generated = generate_response.json()
        file_changes = generated["patch"]["file_changes"]
        apply_response = client.post(
            f"/api/v1/pskills/{skill_id}/draft/apply-patch",
            json={
                "base_commit_sha": generated["base_commit_sha"],
                "files": {item["path"]: item["proposed_content"] for item in file_changes},
                "commit_message": "Apply builder material draft for closed-loop acceptance",
            },
        )
        applied = apply_response.json()
        source_response = client.get(f"/api/v1/pskills/{skill_id}/source")

        publish_response = client.post(
            f"/api/v1/pskills/{skill_id}/publish",
            json={"publish_reason": "Closed-loop acceptance publish"},
        )
        publish_payload = publish_response.json()
        compile_response = client.post(f"/api/v1/compiler/requests/{publish_payload['compile_request']['id']}/retry")
        compile_payload = compile_response.json()
        artifact_response = client.get(f"/api/v1/compiler/artifacts/{compile_payload['artifact_id']}")
        publishes_response = client.get(f"/api/v1/pskills/{skill_id}/publishes")

        client.app.state.inference_gateway = FailingRuntimeInferenceGateway()
        try:
            invocation_response = client.post(
                "/api/v1/gateway/invocations",
                json={
                    "skill_key": "closed-loop-materials",
                    "input_envelope": {"user_input": "触发一次失败运行以生成改进闭环。"},
                    "gateway_type": "web",
                },
            )
            run_id = invocation_response.json()["run_id"]
            run_response = client.get(f"/api/v1/runs/{run_id}")
        finally:
            client.app.state.inference_gateway = original_inference

        replay_response = client.get(f"/api/v1/replay/runs/{run_id}")
        evaluation_response = client.post(f"/api/v1/evaluations/runs/{run_id}")
        evaluation = evaluation_response.json()
        finding = evaluation["findings"][0]
        proposal_response = client.post(f"/api/v1/evaluations/findings/{finding['id']}/create-proposal")
        proposal = proposal_response.json()
        run_tests_response = client.post(f"/api/v1/governance/proposals/{proposal['id']}/run-tests")
        replay_after_governance_response = client.get(f"/api/v1/replay/runs/{run_id}")
        governance_agent_run_response = client.get(f"/api/v1/agent-runs/{proposal['agent_run_id']}")
        governance_authorizations_response = client.get(
            f"/api/v1/agent-runs/{proposal['agent_run_id']}/tool-authorizations"
        )

    assert material_response.status_code == 201
    assert material_response.json()["status"] == "ready"
    assert generate_response.status_code == 201
    assert generated["status"] == "patch_proposed"
    assert generated["agent_run"]["agent_key"] == "pskill.builder"
    assert generated["agent_run"]["status"] == "succeeded"
    assert generated["material_ids"] == [material_id]
    assert generated["patch"]["committed"] is False
    assert generated["patch"]["requires_human_apply"] is True
    assert apply_response.status_code == 200
    assert applied["changed_files"] == ["SKILL.md"]
    assert source_response.json()["head_commit_sha"] == applied["committed_commit_sha"]
    assert "## Builder Draft Proposal" in fake_gateway.projects[created["gitlab_project_id"]].files["SKILL.md"]

    assert publish_response.status_code == 202
    assert publish_payload["publish_record"]["publish_status"] == "compiling"
    assert compile_response.status_code == 200
    assert compile_payload["status"] == "succeeded"
    assert artifact_response.json()["artifact"]["formal_revision"] == "psop-eg-formal/v5"
    assert publishes_response.json()[0]["publish_status"] == "published"

    assert invocation_response.status_code == 201
    assert run_response.json()["status"] == "failed"
    replay_payload = replay_response.json()
    assert replay_payload["run"]["id"] == run_id
    assert len(replay_payload["run_traces"]) >= 1
    assert "runtime.failed" in {item["event_type"] for item in replay_payload["timeline"]}

    assert evaluation_response.status_code == 201
    assert evaluation["run_id"] == run_id
    assert evaluation["overall_outcome"] == "failed"
    assert finding["severity"] == "high"
    assert finding["evidence_refs"][0]["kind"] == "run_trace"

    assert proposal_response.status_code == 201
    assert proposal["status"] == "draft"
    assert proposal["source_run_id"] == run_id
    assert proposal["source_evaluation_id"] == evaluation["id"]
    assert proposal["source_finding_ids"] == [finding["id"]]
    assert any(ref["kind"] == "run_evaluation_finding" and ref["id"] == finding["id"] for ref in proposal["evidence_refs"])
    assert any(ref["kind"] == "run_evaluation" and ref["id"] == evaluation["id"] for ref in proposal["evidence_refs"])
    assert any(ref["kind"] == "run_replay" and ref["run_id"] == run_id for ref in proposal["evidence_refs"])
    assert governance_agent_run_response.json()["agent_key"] == "psop.governance"
    assert governance_agent_run_response.json()["status"] == "succeeded"
    assert governance_authorizations_response.json() == []

    assert run_tests_response.status_code == 200
    replay_after_governance = replay_after_governance_response.json()
    replay_proposals = replay_after_governance["governance_proposals"]
    replay_experiments = replay_after_governance["governance_experiments"]
    assert [item["id"] for item in replay_proposals] == [proposal["id"]]
    assert replay_proposals[0]["source_run_id"] == run_id
    assert replay_proposals[0]["source_evaluation_id"] == evaluation["id"]
    assert replay_proposals[0]["source_findings"][0]["id"] == finding["id"]
    assert replay_proposals[0]["experiments"][0]["experiment_type"] == "regression"
    assert [item["proposal_id"] for item in replay_experiments] == [proposal["id"]]
    assert replay_experiments[0]["source_run_id"] == run_id
    assert replay_experiments[0]["result"]["direct_activation_performed"] is False
