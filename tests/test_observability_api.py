from __future__ import annotations

from datetime import timedelta

from app.agents.models import AgentEvent, AgentModelCall, AgentRun, AgentToolAuthorization, AgentToolCall
from app.evaluations.models import RunEvaluation, RunEvaluationFinding
from app.governance.models import PsopImprovementProposal
from app.pskills.models import PSkillDefinition, PSkillVersion, now_utc
from app.runtime.models import Run, RunTrace
from app.testing.models import PSkillPublishGate
from tests.test_skills_api import create_test_client


def test_observability_dashboard_metrics_aggregate_system_health() -> None:
    client, _, _ = create_test_client()

    with client:
        now = now_utc()
        db_manager = client.app.state.db_manager
        with db_manager.session() as session:
            pskill = PSkillDefinition(
                id="pskill-dashboard-1",
                key="dashboard-demo",
                name="Dashboard Demo",
                gitlab_project_id="dashboard-demo-project",
                repository_url="https://gitlab.example.local/skills/dashboard-demo",
            )
            version = PSkillVersion(
                id="pskill-version-dashboard-1",
                pskill_definition_id=pskill.id,
                version_no=1,
                status="draft",
                source_ref="main",
            )
            pskill.latest_draft_version_id = version.id
            pskill.latest_published_version_id = version.id
            agent_run = AgentRun(
                id="agent-run-dashboard-1",
                agent_key="pskill.runner",
                status="succeeded",
                owner_type="runtime",
                owner_id="run-dashboard-1",
                input_payload={},
                output_payload={},
                started_at=now - timedelta(seconds=4),
                ended_at=now - timedelta(seconds=1),
                created_at=now - timedelta(minutes=1),
                updated_at=now,
            )
            runtime_run = Run(
                id="run-dashboard-1",
                invocation_id="invocation-dashboard-1",
                pskill_definition_id=pskill.id,
                pskill_version_id=version.id,
                compile_artifact_id="artifact-dashboard-1",
                status="succeeded",
                started_at=now - timedelta(seconds=5),
                ended_at=now,
                created_at=now - timedelta(minutes=1),
            )
            session.add_all(
                [
                    pskill,
                    version,
                    PSkillPublishGate(
                        pskill_definition_id=pskill.id,
                        pskill_version_id=version.id,
                        status="passed",
                        score=94,
                        created_at=now - timedelta(minutes=1),
                    ),
                    runtime_run,
                    RunEvaluation(
                        id="evaluation-dashboard-1",
                        run_id=runtime_run.id,
                        pskill_definition_id=pskill.id,
                        pskill_version_id=version.id,
                        artifact_id="artifact-dashboard-1",
                        agent_run_id=agent_run.id,
                        overall_outcome="success",
                        quality_score=94,
                        created_at=now - timedelta(minutes=1),
                    ),
                    RunEvaluationFinding(
                        evaluation_id="evaluation-dashboard-1",
                        category="runtime",
                        severity="high",
                        confidence=80,
                        description="needs review",
                        recommended_action="improve",
                        status="open",
                        created_at=now - timedelta(minutes=1),
                    ),
                    PsopImprovementProposal(
                        id="proposal-dashboard-1",
                        agent_run_id=agent_run.id,
                        proposal_type="test_suite_update",
                        target_json={"kind": "test_suite"},
                        problem_statement="add coverage",
                        status="canary",
                        created_at=now - timedelta(minutes=1),
                    ),
                    agent_run,
                    AgentToolCall(
                        agent_run_id=agent_run.id,
                        tool_name="psop.runtime.read",
                        tool_provider="native",
                        status="failed",
                        arguments_summary={},
                        result_summary={},
                        side_effect_level="read",
                        created_at=now - timedelta(minutes=1),
                    ),
                    AgentModelCall(
                        agent_run_id=agent_run.id,
                        provider="deterministic",
                        route_key="json",
                        model_name="test-model",
                        status="succeeded",
                        request_payload={},
                        response_payload={},
                        usage_json={"total_tokens": 10},
                        created_at=now - timedelta(minutes=1),
                    ),
                    AgentEvent(
                        agent_run_id=agent_run.id,
                        seq_no=1,
                        event_type="agent.run.created",
                        phase="created",
                        payload={},
                        occurred_at=now - timedelta(minutes=1),
                    ),
                    AgentToolAuthorization(
                        agent_run_id=agent_run.id,
                        tool_name="psop.repository.commit_patch",
                        tool_provider="native",
                        side_effect_level="high_write",
                        risk_level="medium",
                        status="pending",
                        request_payload={},
                        response_payload={},
                        created_at=now - timedelta(minutes=1),
                    ),
                    RunTrace(
                        run_id=runtime_run.id,
                        agent_run_id=agent_run.id,
                        seq_no=1,
                        phase="runtime",
                        event_type="runtime.started",
                        payload={},
                        occurred_at=now - timedelta(minutes=1),
                    ),
                ]
            )
            session.commit()

        response = client.get("/api/v1/observability/dashboard", params={"window_hours": 24})

    assert response.status_code == 200
    payload = response.json()
    assert payload["pskills"]["total_count"] == 1
    assert payload["pskills"]["draft_count"] == 1
    assert payload["pskills"]["published_count"] == 1
    assert payload["pskills"]["publish_gate_pass_rate"] == 1.0
    assert payload["runtime"]["recent_run_count"] == 1
    assert payload["runtime"]["success_rate"] == 1.0
    assert payload["evaluations"]["average_quality_score"] == 94.0
    assert payload["evaluations"]["high_severity_finding_count"] == 1
    assert payload["governance"]["canary_proposal_count"] == 1
    runner_metrics = next(item for item in payload["agents"] if item["agent_key"] == "pskill.runner")
    assert runner_metrics["recent_run_count"] == 1
    assert runner_metrics["tool_failure_rate"] == 1.0
    assert payload["observability"]["run_trace_count"] == 1
    assert payload["observability"]["pending_tool_authorization_count"] == 1
