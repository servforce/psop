from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

from app.agents.models import AgentEvent, AgentModelCall, AgentRun, AgentToolAuthorization, AgentToolCall
from app.evaluations.models import RunEvaluation, RunEvaluationFinding
from app.governance.models import PsopImprovementExperiment, PsopImprovementProposal
from app.pskills.models import PSkillDefinition, PSkillVersion, now_utc
from app.runtime.models import Run, RunEvent, RunEventPart, RunTrace, TerminalSession
from app.skills.models import SkillActivation, SkillPackage, SkillVersion
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
                        status="failed",
                        request_payload={},
                        response_payload={"error": "dashboard provider failed"},
                        usage_json={"total_tokens": 10},
                        error_message="dashboard provider failed",
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
    assert runner_metrics["model_call_count"] == 1
    assert runner_metrics["failed_model_call_count"] == 1
    assert runner_metrics["model_failure_rate"] == 1.0
    assert runner_metrics["tool_failure_rate"] == 1.0
    assert payload["observability"]["run_trace_count"] == 1
    assert payload["observability"]["model_call_count"] == 1
    assert payload["observability"]["pending_tool_authorization_count"] == 1


def test_observability_metrics_expose_runtime_agent_and_otel_status() -> None:
    client, _, _ = create_test_client()

    with client:
        client.app.state.observability = SimpleNamespace(enabled=True)
        expected_otel_service_name = client.app.state.settings.otel_service_name
        now = now_utc()
        db_manager = client.app.state.db_manager
        with db_manager.session() as session:
            pskill = PSkillDefinition(
                id="pskill-metrics-1",
                key="metrics-demo",
                name="Metrics Demo",
                gitlab_project_id="metrics-demo-project",
                repository_url="https://gitlab.example.local/skills/metrics-demo",
            )
            version = PSkillVersion(
                id="pskill-version-metrics-1",
                pskill_definition_id=pskill.id,
                version_no=1,
                status="published",
                source_ref="main",
            )
            pskill.latest_published_version_id = version.id
            runtime_run = Run(
                id="run-metrics-1",
                invocation_id="invocation-metrics-1",
                pskill_definition_id=pskill.id,
                pskill_version_id=version.id,
                compile_artifact_id="artifact-metrics-1",
                status="failed",
                runtime_phase="failed",
                started_at=now - timedelta(seconds=30),
                ended_at=now - timedelta(seconds=5),
                created_at=now - timedelta(minutes=1),
            )
            terminal_session = TerminalSession(
                id="terminal-session-metrics-1",
                run_id=runtime_run.id,
                mode="web",
                status="open",
                opened_at=now - timedelta(minutes=1),
                created_at=now - timedelta(minutes=1),
            )
            agent_run = AgentRun(
                id="agent-run-metrics-1",
                agent_key="pskill.runner",
                status="waiting_tool_authorization",
                owner_type="runtime",
                owner_id=runtime_run.id,
                run_id=runtime_run.id,
                input_payload={},
                output_payload={},
                started_at=now - timedelta(seconds=20),
                created_at=now - timedelta(minutes=1),
                updated_at=now,
            )
            session.add_all(
                [
                    pskill,
                    version,
                    runtime_run,
                    terminal_session,
                    RunEvent(
                        id="run-event-metrics-1",
                        terminal_session_id=terminal_session.id,
                        run_id=runtime_run.id,
                        direction="system",
                        event_kind="tool_authorization_request",
                        mime_type="application/json",
                        payload_inline={"tool_name": "psop.repository.commit_patch"},
                        seq_no=1,
                        external_event_id="metrics-event-1",
                        source_ref={"kind": "agent_tool_authorization"},
                        occurred_at=now - timedelta(minutes=1),
                        created_at=now - timedelta(minutes=1),
                    ),
                    RunTrace(
                        id="run-trace-metrics-1",
                        run_id=runtime_run.id,
                        agent_run_id=agent_run.id,
                        seq_no=1,
                        phase="runtime",
                        event_type="runtime.failed",
                        trace_id="trace-metrics-otel-1",
                        span_id="span-1",
                        payload={"error": "failed"},
                        occurred_at=now - timedelta(minutes=1),
                    ),
                    RunTrace(
                        id="run-trace-metrics-old",
                        run_id=runtime_run.id,
                        seq_no=2,
                        phase="runtime",
                        event_type="runtime.old",
                        payload={},
                        occurred_at=now - timedelta(days=3),
                    ),
                    agent_run,
                    AgentEvent(
                        id="agent-event-metrics-1",
                        agent_run_id=agent_run.id,
                        seq_no=1,
                        event_type="tool.authorization_requested",
                        phase="tool_authorization",
                        payload={},
                        occurred_at=now - timedelta(minutes=1),
                    ),
                    AgentModelCall(
                        id="agent-model-call-metrics-1",
                        agent_run_id=agent_run.id,
                        provider="deterministic",
                        route_key="json",
                        model_name="test-model",
                        status="succeeded",
                        request_payload={},
                        response_payload={},
                        usage_json={"total_tokens": 22},
                        created_at=now - timedelta(minutes=1),
                    ),
                    AgentToolCall(
                        id="agent-tool-call-metrics-1",
                        agent_run_id=agent_run.id,
                        tool_name="psop.repository.commit_patch",
                        tool_provider="native",
                        status="failed",
                        arguments_summary={},
                        result_summary={},
                        side_effect_level="high_write",
                        created_at=now - timedelta(minutes=1),
                    ),
                    SkillPackage(
                        id="skill-package-metrics-1",
                        name="pskill-runner-field-assistant-metrics",
                        scope="psop",
                        description="metrics package",
                        source_uri="skills/psop/pskill-runner-field-assistant",
                        created_at=now - timedelta(minutes=1),
                        updated_at=now - timedelta(minutes=1),
                    ),
                    SkillVersion(
                        id="skill-version-metrics-1",
                        package_id="skill-package-metrics-1",
                        version_label="v1",
                        content_hash="skill-version-metrics-hash",
                        manifest_json={},
                        body_object_key="",
                        resource_index=[],
                        allowed_tools=[],
                        created_at=now - timedelta(minutes=1),
                        updated_at=now - timedelta(minutes=1),
                    ),
                    SkillActivation(
                        id="skill-activation-metrics-1",
                        agent_run_id=agent_run.id,
                        package_id="skill-package-metrics-1",
                        version_id="skill-version-metrics-1",
                        activation_context={"usage": "runner"},
                        created_at=now - timedelta(minutes=1),
                    ),
                    AgentToolAuthorization(
                        id="agent-tool-auth-metrics-1",
                        agent_run_id=agent_run.id,
                        agent_tool_call_id="agent-tool-call-metrics-1",
                        run_id=runtime_run.id,
                        tool_name="psop.repository.commit_patch",
                        tool_provider="native",
                        side_effect_level="high_write",
                        risk_level="high",
                        status="pending",
                        request_payload={},
                        response_payload={},
                        created_at=now - timedelta(minutes=1),
                    ),
                    AgentToolAuthorization(
                        id="agent-tool-auth-metrics-executed-1",
                        agent_run_id=agent_run.id,
                        agent_tool_call_id="agent-tool-call-metrics-1",
                        run_id=runtime_run.id,
                        tool_name="psop.skill_version.activate",
                        tool_provider="native",
                        side_effect_level="high_write",
                        risk_level="high",
                        status="executed",
                        request_payload={},
                        response_payload={"decision": "approved"},
                        created_at=now - timedelta(minutes=1),
                        executed_at=now - timedelta(seconds=30),
                    ),
                    RunEvaluation(
                        id="evaluation-metrics-1",
                        run_id=runtime_run.id,
                        pskill_definition_id=pskill.id,
                        pskill_version_id=version.id,
                        artifact_id="artifact-metrics-1",
                        agent_run_id=agent_run.id,
                        overall_outcome="failed",
                        quality_score=38,
                        created_at=now - timedelta(minutes=1),
                    ),
                    RunEvaluationFinding(
                        id="evaluation-finding-metrics-1",
                        evaluation_id="evaluation-metrics-1",
                        category="runner_issue",
                        severity="high",
                        confidence=88,
                        description="runtime failure needs replay review",
                        evidence_refs=[{"kind": "run_trace", "id": "run-trace-metrics-1"}],
                        recommended_action="repair runner fallback",
                        status="open",
                        created_at=now - timedelta(minutes=1),
                    ),
                    PsopImprovementProposal(
                        id="proposal-metrics-1",
                        agent_run_id=agent_run.id,
                        source_finding_ids=["evaluation-finding-metrics-1"],
                        source_evaluation_id="evaluation-metrics-1",
                        source_run_id=runtime_run.id,
                        proposal_type="agent_skill_update",
                        target_json={"kind": "run_evaluation_finding"},
                        problem_statement="repair runtime runner failure",
                        status="canary",
                        created_at=now - timedelta(minutes=1),
                        updated_at=now - timedelta(minutes=1),
                    ),
                    PsopImprovementExperiment(
                        id="experiment-metrics-1",
                        proposal_id="proposal-metrics-1",
                        experiment_type="canary",
                        status="running",
                        summary="canary running",
                        before_metrics={},
                        after_metrics={},
                        result_json={},
                        created_at=now - timedelta(minutes=1),
                    ),
                ]
            )
            session.commit()

        response = client.get("/api/v1/observability/metrics", params={"window_hours": 24})

    assert response.status_code == 200
    payload = response.json()
    assert payload["runtime"]["run_count"] == 1
    assert payload["runtime"]["run_status_counts"]["failed"] == 1
    assert payload["runtime"]["run_event_kind_counts"]["tool_authorization_request"] == 1
    assert payload["runtime"]["run_trace_count"] == 1
    assert payload["runtime"]["run_trace_event_type_counts"]["runtime.failed"] == 1
    assert "runtime.old" not in payload["runtime"]["run_trace_event_type_counts"]
    assert payload["agents"]["agent_run_status_counts"]["waiting_tool_authorization"] == 1
    assert payload["agents"]["agent_run_key_counts"]["pskill.runner"] == 1
    assert payload["agents"]["agent_event_type_counts"]["tool.authorization_requested"] == 1
    assert payload["agents"]["model_call_provider_counts"]["deterministic"] == 1
    assert payload["agents"]["model_call_status_counts"]["succeeded"] == 1
    assert payload["agents"]["tool_call_status_counts"]["failed"] == 1
    assert payload["agents"]["tool_call_side_effect_counts"]["high_write"] == 1
    assert payload["agents"]["skill_activation_count"] == 1
    assert payload["agents"]["skill_activation_package_counts"]["skill-package-metrics-1"] == 1
    assert payload["agents"]["tool_authorization_status_counts"]["pending"] == 1
    assert payload["agents"]["tool_authorization_status_counts"]["executed"] == 1
    assert payload["agents"]["tool_authorization_risk_counts"]["high"] == 2
    assert payload["evaluations"]["evaluation_count"] == 1
    assert payload["evaluations"]["average_quality_score"] == 38.0
    assert payload["evaluations"]["outcome_counts"]["failed"] == 1
    assert payload["evaluations"]["finding_count"] == 1
    assert payload["evaluations"]["high_severity_finding_count"] == 1
    assert payload["evaluations"]["unresolved_finding_count"] == 1
    assert payload["evaluations"]["finding_status_counts"]["open"] == 1
    assert payload["evaluations"]["finding_category_counts"]["runner_issue"] == 1
    assert payload["evaluations"]["finding_severity_counts"]["high"] == 1
    assert payload["governance"]["proposal_count"] == 1
    assert payload["governance"]["open_proposal_count"] == 1
    assert payload["governance"]["canary_proposal_count"] == 1
    assert payload["governance"]["status_counts"]["canary"] == 1
    assert payload["governance"]["proposal_type_counts"]["agent_skill_update"] == 1
    assert payload["governance"]["source_run_linked_count"] == 1
    assert payload["governance"]["source_evaluation_linked_count"] == 1
    assert payload["governance"]["source_finding_linked_count"] == 1
    assert payload["governance"]["experiment_count"] == 1
    assert payload["governance"]["experiment_status_counts"]["running"] == 1
    assert payload["governance"]["experiment_type_counts"]["canary"] == 1
    assert payload["open_telemetry"]["enabled"] is True
    assert payload["open_telemetry"]["configured"] is True
    assert payload["open_telemetry"]["service_name"] == expected_otel_service_name


def test_observability_run_trace_query_filters_recent_runtime_traces() -> None:
    client, _, _ = create_test_client()

    with client:
        now = now_utc()
        db_manager = client.app.state.db_manager
        with db_manager.session() as session:
            pskill = PSkillDefinition(
                id="pskill-observe-traces-1",
                key="observe-traces-demo",
                name="Observe Traces Demo",
                gitlab_project_id="observe-traces-project",
                repository_url="https://gitlab.example.local/skills/observe-traces-demo",
            )
            version = PSkillVersion(
                id="pskill-version-observe-traces-1",
                pskill_definition_id=pskill.id,
                version_no=1,
                status="published",
                source_ref="main",
            )
            run = Run(
                id="run-observe-traces-1",
                invocation_id="invocation-observe-traces-1",
                pskill_definition_id=pskill.id,
                pskill_version_id=version.id,
                compile_artifact_id="artifact-observe-traces-1",
                status="failed",
                runtime_phase="failed",
                created_at=now - timedelta(minutes=5),
            )
            agent_run = AgentRun(
                id="agent-run-observe-traces-1",
                agent_key="pskill.runner",
                status="failed",
                owner_type="runtime",
                owner_id=run.id,
                run_id=run.id,
                input_payload={},
                output_payload={},
                created_at=now - timedelta(minutes=5),
                updated_at=now - timedelta(minutes=4),
            )
            session.add_all(
                [
                    pskill,
                    version,
                    run,
                    agent_run,
                    RunTrace(
                        id="run-trace-observe-newer",
                        run_id=run.id,
                        agent_run_id=agent_run.id,
                        seq_no=2,
                        phase="runtime",
                        event_type="runtime.failed",
                        trace_id="trace-observe-otel-newer",
                        span_id="span-newer",
                        payload={"error": "newer"},
                        occurred_at=now - timedelta(minutes=1),
                    ),
                    RunTrace(
                        id="run-trace-observe-older",
                        run_id=run.id,
                        agent_run_id=agent_run.id,
                        seq_no=1,
                        phase="runtime",
                        event_type="runtime.failed",
                        trace_id="trace-observe-otel-older",
                        span_id="span-older",
                        payload={"error": "older"},
                        occurred_at=now - timedelta(minutes=2),
                    ),
                    RunTrace(
                        id="run-trace-observe-completed",
                        run_id=run.id,
                        seq_no=3,
                        phase="runtime",
                        event_type="runtime.completed",
                        trace_id="trace-observe-otel-completed",
                        span_id="span-completed",
                        payload={},
                        occurred_at=now - timedelta(minutes=1),
                    ),
                    RunTrace(
                        id="run-trace-observe-old-window",
                        run_id=run.id,
                        seq_no=4,
                        phase="runtime",
                        event_type="runtime.failed",
                        trace_id="trace-observe-otel-old",
                        span_id="span-old-window",
                        payload={"error": "old"},
                        occurred_at=now - timedelta(days=2),
                    ),
                ]
            )
            session.commit()

        response = client.get(
            "/api/v1/observability/run-traces",
            params={"window_hours": 24, "run_trace_event_type": "runtime.failed", "limit": 10},
        )
        legacy_response = client.get(
            "/api/v1/observability/run-traces",
            params={"window_hours": 24, "event_type": "runtime.failed", "limit": 10},
        )
        run_response = client.get(
            "/api/v1/observability/run-traces",
            params={"window_hours": 24, "run_id": run.id, "agent_run_id": agent_run.id, "limit": 10},
        )
        runtime_trace_response = client.get(
            f"/api/v1/runs/{run.id}/traces",
            params={"run_trace_event_type": "runtime.failed"},
        )
        legacy_runtime_trace_response = client.get(
            f"/api/v1/runs/{run.id}/traces",
            params={"event_type": "runtime.failed"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert [item["id"] for item in payload] == ["run-trace-observe-newer", "run-trace-observe-older"]
    assert payload[0]["run_id"] == run.id
    assert payload[0]["agent_run_id"] == agent_run.id
    assert payload[0]["event_type"] == "runtime.failed"
    assert payload[0]["trace_id"] == "trace-observe-otel-newer"
    assert payload[0]["span_id"] == "span-newer"
    assert payload[0]["payload"] == {"error": "newer"}

    assert legacy_response.status_code == 200
    assert legacy_response.json() == payload

    assert run_response.status_code == 200
    run_payload = run_response.json()
    assert {item["id"] for item in run_payload} == {"run-trace-observe-newer", "run-trace-observe-older"}

    assert runtime_trace_response.status_code == 200
    runtime_trace_payload = runtime_trace_response.json()
    assert [item["id"] for item in runtime_trace_payload] == [
        "run-trace-observe-older",
        "run-trace-observe-newer",
        "run-trace-observe-old-window",
    ]
    assert legacy_runtime_trace_response.status_code == 200
    assert legacy_runtime_trace_response.json() == runtime_trace_payload


def test_replay_trace_lookup_accepts_otel_trace_id() -> None:
    client, _, _ = create_test_client()

    with client:
        now = now_utc()
        db_manager = client.app.state.db_manager
        with db_manager.session() as session:
            pskill = PSkillDefinition(
                id="pskill-replay-otel-1",
                key="replay-otel-demo",
                name="Replay OTel Demo",
                gitlab_project_id="replay-otel-demo-project",
                repository_url="https://gitlab.example.local/skills/replay-otel-demo",
            )
            version = PSkillVersion(
                id="pskill-version-replay-otel-1",
                pskill_definition_id=pskill.id,
                version_no=1,
                status="published",
                source_ref="main",
            )
            pskill.latest_published_version_id = version.id
            runtime_run = Run(
                id="run-replay-otel-1",
                invocation_id="invocation-replay-otel-1",
                pskill_definition_id=pskill.id,
                pskill_version_id=version.id,
                compile_artifact_id="artifact-replay-otel-1",
                status="waiting_input",
                runtime_phase="waiting_input",
                latest_trace_seq=1,
                created_at=now - timedelta(minutes=1),
            )
            session.add_all(
                [
                    pskill,
                    version,
                    runtime_run,
                    RunTrace(
                        id="run-trace-replay-otel-1",
                        run_id=runtime_run.id,
                        seq_no=1,
                        phase="runtime",
                        event_type="runtime.wait_checkpoint.entered",
                        trace_id="0123456789abcdef0123456789abcdef",
                        span_id="0123456789abcdef",
                        payload={"node_id": "instruct_collect_context"},
                        occurred_at=now - timedelta(seconds=30),
                    ),
                ]
            )
            session.commit()

        by_run_trace_id_response = client.get("/api/v1/replay/traces/run-trace-replay-otel-1")
        by_otel_trace_id_response = client.get("/api/v1/replay/traces/0123456789abcdef0123456789abcdef")

    assert by_run_trace_id_response.status_code == 200
    assert by_otel_trace_id_response.status_code == 200
    by_run_trace_id_payload = by_run_trace_id_response.json()
    by_otel_trace_id_payload = by_otel_trace_id_response.json()
    assert by_run_trace_id_payload["trace"]["id"] == "run-trace-replay-otel-1"
    assert by_otel_trace_id_payload["trace"]["id"] == "run-trace-replay-otel-1"
    assert by_otel_trace_id_payload["trace"]["trace_id"] == "0123456789abcdef0123456789abcdef"
    assert by_otel_trace_id_payload["run"]["id"] == "run-replay-otel-1"
    assert by_otel_trace_id_payload["timeline_item"]["source_kind"] == "run_trace"
    assert by_otel_trace_id_payload["timeline_item"]["source_id"] == "run-trace-replay-otel-1"


def test_observability_run_event_query_filters_recent_runtime_events() -> None:
    client, _, _ = create_test_client()

    with client:
        now = now_utc()
        db_manager = client.app.state.db_manager
        with db_manager.session() as session:
            pskill = PSkillDefinition(
                id="pskill-observe-events-1",
                key="observe-events-demo",
                name="Observe Events Demo",
                gitlab_project_id="observe-events-project",
                repository_url="https://gitlab.example.local/skills/observe-events-demo",
            )
            version = PSkillVersion(
                id="pskill-version-observe-events-1",
                pskill_definition_id=pskill.id,
                version_no=1,
                status="published",
                source_ref="main",
            )
            run = Run(
                id="run-observe-events-1",
                invocation_id="invocation-observe-events-1",
                pskill_definition_id=pskill.id,
                pskill_version_id=version.id,
                compile_artifact_id="artifact-observe-events-1",
                status="waiting_input",
                runtime_phase="wait",
                created_at=now - timedelta(minutes=5),
            )
            terminal_session = TerminalSession(
                id="terminal-session-observe-events-1",
                run_id=run.id,
                mode="web",
                status="open",
                opened_at=now - timedelta(minutes=5),
                created_at=now - timedelta(minutes=5),
            )
            agent_run = AgentRun(
                id="agent-run-observe-events-1",
                agent_key="pskill.runner",
                status="succeeded",
                owner_type="runtime",
                owner_id=run.id,
                run_id=run.id,
                input_payload={},
                output_payload={},
                created_at=now - timedelta(minutes=5),
                updated_at=now - timedelta(minutes=4),
            )
            session.add_all(
                [
                    pskill,
                    version,
                    run,
                    terminal_session,
                    agent_run,
                    RunEvent(
                        id="run-event-observe-newer",
                        terminal_session_id=terminal_session.id,
                        run_id=run.id,
                        agent_run_id=agent_run.id,
                        direction="system",
                        event_kind="tool_authorization_request",
                        mime_type="application/json",
                        payload_inline={"tool_name": "psop.repository.commit_patch"},
                        seq_no=2,
                        external_event_id="event-newer",
                        source_ref={"kind": "agent_tool_authorization"},
                        occurred_at=now - timedelta(minutes=1),
                        created_at=now - timedelta(minutes=1),
                    ),
                    RunEventPart(
                        id="run-event-part-observe-newer",
                        run_event_id="run-event-observe-newer",
                        run_id=run.id,
                        part_id="summary",
                        order_index=0,
                        kind="text",
                        mime_type="text/plain",
                        text_inline="Authorization summary",
                        part_metadata={"source": "test"},
                        created_at=now - timedelta(minutes=1),
                    ),
                    RunEvent(
                        id="run-event-observe-older",
                        terminal_session_id=terminal_session.id,
                        run_id=run.id,
                        agent_run_id=agent_run.id,
                        direction="system",
                        event_kind="tool_authorization_request",
                        mime_type="application/json",
                        payload_inline={"tool_name": "psop.repository.commit_patch"},
                        seq_no=1,
                        external_event_id="event-older",
                        source_ref={"kind": "agent_tool_authorization"},
                        occurred_at=now - timedelta(minutes=2),
                        created_at=now - timedelta(minutes=2),
                    ),
                    RunEvent(
                        id="run-event-observe-user",
                        terminal_session_id=terminal_session.id,
                        run_id=run.id,
                        direction="input",
                        event_kind="terminal.multimodal.input.v1",
                        mime_type="text/plain",
                        payload_inline={"text": "user input"},
                        seq_no=3,
                        external_event_id="event-user",
                        source_ref={"kind": "web"},
                        occurred_at=now - timedelta(minutes=1),
                        created_at=now - timedelta(minutes=1),
                    ),
                    RunEvent(
                        id="run-event-observe-old-window",
                        terminal_session_id=terminal_session.id,
                        run_id=run.id,
                        direction="system",
                        event_kind="tool_authorization_request",
                        mime_type="application/json",
                        payload_inline={},
                        seq_no=4,
                        external_event_id="event-old-window",
                        source_ref={"kind": "agent_tool_authorization"},
                        occurred_at=now - timedelta(days=2),
                        created_at=now - timedelta(days=2),
                    ),
                ]
            )
            session.commit()

        response = client.get(
            "/api/v1/observability/run-events",
            params={"window_hours": 24, "event_kind": "tool_authorization_request", "limit": 10},
        )
        run_response = client.get(
            "/api/v1/observability/run-events",
            params={"window_hours": 24, "run_id": run.id, "agent_run_id": agent_run.id, "limit": 10},
        )

    assert response.status_code == 200
    payload = response.json()
    assert [item["id"] for item in payload] == ["run-event-observe-newer", "run-event-observe-older"]
    assert payload[0]["run_id"] == run.id
    assert payload[0]["agent_run_id"] == agent_run.id
    assert payload[0]["event_kind"] == "tool_authorization_request"
    assert payload[0]["payload_inline"] == {"tool_name": "psop.repository.commit_patch"}
    assert payload[0]["parts"][0]["text"] == "Authorization summary"

    assert run_response.status_code == 200
    run_payload = run_response.json()
    assert {item["id"] for item in run_payload} == {"run-event-observe-newer", "run-event-observe-older"}


def test_observability_agent_event_query_filters_recent_agent_events() -> None:
    client, _, _ = create_test_client()

    with client:
        now = now_utc()
        db_manager = client.app.state.db_manager
        with db_manager.session() as session:
            pskill = PSkillDefinition(
                id="pskill-observe-agent-events-1",
                key="observe-agent-events-demo",
                name="Observe Agent Events Demo",
                gitlab_project_id="observe-agent-events-project",
                repository_url="https://gitlab.example.local/skills/observe-agent-events-demo",
            )
            version = PSkillVersion(
                id="pskill-version-observe-agent-events-1",
                pskill_definition_id=pskill.id,
                version_no=1,
                status="published",
                source_ref="main",
            )
            run = Run(
                id="run-observe-agent-events-1",
                invocation_id="invocation-observe-agent-events-1",
                pskill_definition_id=pskill.id,
                pskill_version_id=version.id,
                compile_artifact_id="artifact-observe-agent-events-1",
                status="failed",
                runtime_phase="failed",
                created_at=now - timedelta(minutes=5),
            )
            runner_run = AgentRun(
                id="agent-run-observe-agent-events-1",
                agent_key="pskill.runner",
                status="failed",
                owner_type="runtime",
                owner_id=run.id,
                run_id=run.id,
                input_payload={},
                output_payload={},
                created_at=now - timedelta(minutes=5),
                updated_at=now - timedelta(minutes=4),
            )
            builder_run = AgentRun(
                id="agent-run-observe-agent-events-2",
                agent_key="pskill.builder",
                status="succeeded",
                owner_type="pskill",
                owner_id=pskill.id,
                input_payload={},
                output_payload={},
                created_at=now - timedelta(minutes=5),
                updated_at=now - timedelta(minutes=4),
            )
            session.add_all(
                [
                    pskill,
                    version,
                    run,
                    runner_run,
                    builder_run,
                    AgentEvent(
                        id="agent-event-observe-newer",
                        agent_run_id=runner_run.id,
                        seq_no=3,
                        event_type="tool.authorization_requested",
                        phase="tool_authorization",
                        payload={"tool_name": "psop.repository.commit_patch"},
                        occurred_at=now - timedelta(minutes=1),
                    ),
                    AgentEvent(
                        id="agent-event-observe-executed",
                        agent_run_id=runner_run.id,
                        seq_no=2,
                        event_type="tool.authorization_executed",
                        phase="tool_authorization",
                        payload={
                            "authorization_id": "tool-auth-observe-executed",
                            "execution_status": "succeeded",
                        },
                        occurred_at=now - timedelta(minutes=1, seconds=30),
                    ),
                    AgentEvent(
                        id="agent-event-observe-older",
                        agent_run_id=runner_run.id,
                        seq_no=1,
                        event_type="tool.authorization_requested",
                        phase="tool_authorization",
                        payload={"tool_name": "psop.repository.commit_patch"},
                        occurred_at=now - timedelta(minutes=2),
                    ),
                    AgentEvent(
                        id="agent-event-observe-builder",
                        agent_run_id=builder_run.id,
                        seq_no=1,
                        event_type="agent.run.created",
                        phase="created",
                        payload={},
                        occurred_at=now - timedelta(minutes=1),
                    ),
                    AgentEvent(
                        id="agent-event-observe-old-window",
                        agent_run_id=runner_run.id,
                        seq_no=3,
                        event_type="tool.authorization_requested",
                        phase="tool_authorization",
                        payload={"old": True},
                        occurred_at=now - timedelta(days=2),
                    ),
                ]
            )
            session.commit()

        response = client.get(
            "/api/v1/observability/agent-events",
            params={"window_hours": 24, "event_type": "tool.authorization_requested", "limit": 10},
        )
        scoped_response = client.get(
            "/api/v1/observability/agent-events",
            params={"window_hours": 24, "agent_key": "pskill.runner", "run_id": run.id, "limit": 10},
        )
        executed_response = client.get(
            "/api/v1/observability/agent-events",
            params={"window_hours": 24, "event_type": "tool.authorization_executed", "limit": 10},
        )

    assert response.status_code == 200
    payload = response.json()
    assert [item["id"] for item in payload] == ["agent-event-observe-newer", "agent-event-observe-older"]
    assert payload[0]["agent_run_id"] == runner_run.id
    assert payload[0]["event_type"] == "tool.authorization_requested"
    assert payload[0]["payload"] == {"tool_name": "psop.repository.commit_patch"}

    assert scoped_response.status_code == 200
    scoped_payload = scoped_response.json()
    assert {item["id"] for item in scoped_payload} == {
        "agent-event-observe-newer",
        "agent-event-observe-executed",
        "agent-event-observe-older",
    }

    assert executed_response.status_code == 200
    executed_payload = executed_response.json()
    assert [item["id"] for item in executed_payload] == ["agent-event-observe-executed"]
    assert executed_payload[0]["phase"] == "tool_authorization"
    assert executed_payload[0]["payload"]["execution_status"] == "succeeded"


def test_observability_tool_call_query_filters_recent_agent_tool_calls() -> None:
    client, _, _ = create_test_client()

    with client:
        now = now_utc()
        db_manager = client.app.state.db_manager
        with db_manager.session() as session:
            pskill = PSkillDefinition(
                id="pskill-observe-tool-calls-1",
                key="observe-tool-calls-demo",
                name="Observe Tool Calls Demo",
                gitlab_project_id="observe-tool-calls-project",
                repository_url="https://gitlab.example.local/skills/observe-tool-calls-demo",
            )
            version = PSkillVersion(
                id="pskill-version-observe-tool-calls-1",
                pskill_definition_id=pskill.id,
                version_no=1,
                status="published",
                source_ref="main",
            )
            run = Run(
                id="run-observe-tool-calls-1",
                invocation_id="invocation-observe-tool-calls-1",
                pskill_definition_id=pskill.id,
                pskill_version_id=version.id,
                compile_artifact_id="artifact-observe-tool-calls-1",
                status="failed",
                runtime_phase="failed",
                created_at=now - timedelta(minutes=5),
            )
            runner_run = AgentRun(
                id="agent-run-observe-tool-calls-1",
                agent_key="pskill.runner",
                status="failed",
                owner_type="runtime",
                owner_id=run.id,
                run_id=run.id,
                input_payload={},
                output_payload={},
                created_at=now - timedelta(minutes=5),
                updated_at=now - timedelta(minutes=4),
            )
            builder_run = AgentRun(
                id="agent-run-observe-tool-calls-2",
                agent_key="pskill.builder",
                status="succeeded",
                owner_type="pskill",
                owner_id=pskill.id,
                input_payload={},
                output_payload={},
                created_at=now - timedelta(minutes=5),
                updated_at=now - timedelta(minutes=4),
            )
            session.add_all(
                [
                    pskill,
                    version,
                    run,
                    runner_run,
                    builder_run,
                    AgentToolCall(
                        id="tool-call-observe-newer",
                        agent_run_id=runner_run.id,
                        tool_name="psop.repository.commit_patch",
                        tool_provider="native",
                        status="failed",
                        arguments_summary={"path": "SKILL.md"},
                        result_summary={"error": "denied"},
                        side_effect_level="high_write",
                        idempotency_key="tool-call-newer",
                        created_at=now - timedelta(minutes=1),
                        updated_at=now - timedelta(seconds=30),
                    ),
                    AgentToolCall(
                        id="tool-call-observe-older",
                        agent_run_id=runner_run.id,
                        tool_name="psop.repository.commit_patch",
                        tool_provider="native",
                        status="failed",
                        arguments_summary={"path": "README.md"},
                        result_summary={"error": "denied"},
                        side_effect_level="high_write",
                        idempotency_key="tool-call-older",
                        created_at=now - timedelta(minutes=2),
                        updated_at=now - timedelta(minutes=2),
                    ),
                    AgentToolCall(
                        id="tool-call-observe-builder",
                        agent_run_id=builder_run.id,
                        tool_name="psop.memory.search",
                        tool_provider="native",
                        status="succeeded",
                        arguments_summary={"query": "materials"},
                        result_summary={},
                        side_effect_level="read",
                        created_at=now - timedelta(minutes=1),
                        updated_at=now - timedelta(minutes=1),
                    ),
                    AgentToolCall(
                        id="tool-call-observe-old-window",
                        agent_run_id=runner_run.id,
                        tool_name="psop.repository.commit_patch",
                        tool_provider="native",
                        status="failed",
                        arguments_summary={},
                        result_summary={},
                        side_effect_level="high_write",
                        created_at=now - timedelta(days=2),
                        updated_at=now - timedelta(days=2),
                    ),
                ]
            )
            session.commit()

        response = client.get(
            "/api/v1/observability/tool-calls",
            params={"window_hours": 24, "status": "failed", "limit": 10},
        )
        scoped_response = client.get(
            "/api/v1/observability/tool-calls",
            params={
                "window_hours": 24,
                "agent_key": "pskill.runner",
                "run_id": run.id,
                "tool_name": "psop.repository.commit_patch",
                "side_effect_level": "high_write",
                "limit": 10,
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert [item["id"] for item in payload] == ["tool-call-observe-newer", "tool-call-observe-older"]
    assert payload[0]["agent_run_id"] == runner_run.id
    assert payload[0]["tool_name"] == "psop.repository.commit_patch"
    assert payload[0]["arguments_summary"] == {"path": "SKILL.md"}
    assert payload[0]["status"] == "failed"

    assert scoped_response.status_code == 200
    scoped_payload = scoped_response.json()
    assert {item["id"] for item in scoped_payload} == {"tool-call-observe-newer", "tool-call-observe-older"}


def test_observability_model_call_query_filters_recent_agent_model_calls() -> None:
    client, _, _ = create_test_client()

    with client:
        now = now_utc()
        db_manager = client.app.state.db_manager
        with db_manager.session() as session:
            pskill = PSkillDefinition(
                id="pskill-observe-model-calls-1",
                key="observe-model-calls-demo",
                name="Observe Model Calls Demo",
                gitlab_project_id="observe-model-calls-project",
                repository_url="https://gitlab.example.local/skills/observe-model-calls-demo",
            )
            version = PSkillVersion(
                id="pskill-version-observe-model-calls-1",
                pskill_definition_id=pskill.id,
                version_no=1,
                status="published",
                source_ref="main",
            )
            run = Run(
                id="run-observe-model-calls-1",
                invocation_id="invocation-observe-model-calls-1",
                pskill_definition_id=pskill.id,
                pskill_version_id=version.id,
                compile_artifact_id="artifact-observe-model-calls-1",
                status="failed",
                runtime_phase="failed",
                created_at=now - timedelta(minutes=5),
            )
            runner_run = AgentRun(
                id="agent-run-observe-model-calls-1",
                agent_key="pskill.runner",
                status="failed",
                owner_type="runtime",
                owner_id=run.id,
                run_id=run.id,
                input_payload={},
                output_payload={},
                created_at=now - timedelta(minutes=5),
                updated_at=now - timedelta(minutes=4),
            )
            builder_run = AgentRun(
                id="agent-run-observe-model-calls-2",
                agent_key="pskill.builder",
                status="succeeded",
                owner_type="pskill",
                owner_id=pskill.id,
                input_payload={},
                output_payload={},
                created_at=now - timedelta(minutes=5),
                updated_at=now - timedelta(minutes=4),
            )
            session.add_all(
                [
                    pskill,
                    version,
                    run,
                    runner_run,
                    builder_run,
                    AgentModelCall(
                        id="model-call-observe-newer",
                        agent_run_id=runner_run.id,
                        provider="deterministic",
                        route_key="runner",
                        model_name="test-runner",
                        status="failed",
                        request_payload={"prompt": "newer"},
                        response_payload={},
                        usage_json={"total_tokens": 120},
                        error_message="provider failed",
                        started_at=now - timedelta(minutes=1, seconds=10),
                        ended_at=now - timedelta(minutes=1),
                        created_at=now - timedelta(minutes=1),
                    ),
                    AgentModelCall(
                        id="model-call-observe-older",
                        agent_run_id=runner_run.id,
                        provider="deterministic",
                        route_key="runner",
                        model_name="test-runner",
                        status="failed",
                        request_payload={"prompt": "older"},
                        response_payload={},
                        usage_json={"total_tokens": 80},
                        error_message="provider failed",
                        started_at=now - timedelta(minutes=2, seconds=10),
                        ended_at=now - timedelta(minutes=2),
                        created_at=now - timedelta(minutes=2),
                    ),
                    AgentModelCall(
                        id="model-call-observe-builder",
                        agent_run_id=builder_run.id,
                        provider="deterministic",
                        route_key="builder",
                        model_name="test-builder",
                        status="succeeded",
                        request_payload={},
                        response_payload={"ok": True},
                        usage_json={"total_tokens": 10},
                        created_at=now - timedelta(minutes=1),
                    ),
                    AgentModelCall(
                        id="model-call-observe-old-window",
                        agent_run_id=runner_run.id,
                        provider="deterministic",
                        route_key="runner",
                        model_name="test-runner",
                        status="failed",
                        request_payload={},
                        response_payload={},
                        usage_json={"total_tokens": 1},
                        error_message="old",
                        created_at=now - timedelta(days=2),
                    ),
                ]
            )
            session.commit()

        response = client.get(
            "/api/v1/observability/model-calls",
            params={"window_hours": 24, "provider": "deterministic", "status": "failed", "limit": 10},
        )
        scoped_response = client.get(
            "/api/v1/observability/model-calls",
            params={
                "window_hours": 24,
                "agent_key": "pskill.runner",
                "run_id": run.id,
                "route_key": "runner",
                "limit": 10,
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert [item["id"] for item in payload] == ["model-call-observe-newer", "model-call-observe-older"]
    assert payload[0]["agent_run_id"] == runner_run.id
    assert payload[0]["provider"] == "deterministic"
    assert payload[0]["usage_json"] == {"total_tokens": 120}
    assert payload[0]["error_message"] == "provider failed"

    assert scoped_response.status_code == 200
    scoped_payload = scoped_response.json()
    assert {item["id"] for item in scoped_payload} == {"model-call-observe-newer", "model-call-observe-older"}


def test_observability_tool_authorization_query_filters_recent_agent_authorizations() -> None:
    client, _, _ = create_test_client()

    with client:
        now = now_utc()
        db_manager = client.app.state.db_manager
        with db_manager.session() as session:
            pskill = PSkillDefinition(
                id="pskill-observe-tool-authorizations-1",
                key="observe-tool-authorizations-demo",
                name="Observe Tool Authorizations Demo",
                gitlab_project_id="observe-tool-authorizations-project",
                repository_url="https://gitlab.example.local/skills/observe-tool-authorizations-demo",
            )
            version = PSkillVersion(
                id="pskill-version-observe-tool-authorizations-1",
                pskill_definition_id=pskill.id,
                version_no=1,
                status="published",
                source_ref="main",
            )
            run = Run(
                id="run-observe-tool-authorizations-1",
                invocation_id="invocation-observe-tool-authorizations-1",
                pskill_definition_id=pskill.id,
                pskill_version_id=version.id,
                compile_artifact_id="artifact-observe-tool-authorizations-1",
                status="waiting_input",
                runtime_phase="wait",
                created_at=now - timedelta(minutes=5),
            )
            runner_run = AgentRun(
                id="agent-run-observe-tool-authorizations-1",
                agent_key="pskill.runner",
                status="waiting_tool_authorization",
                owner_type="runtime",
                owner_id=run.id,
                run_id=run.id,
                input_payload={},
                output_payload={},
                created_at=now - timedelta(minutes=5),
                updated_at=now - timedelta(minutes=4),
            )
            builder_run = AgentRun(
                id="agent-run-observe-tool-authorizations-2",
                agent_key="pskill.builder",
                status="succeeded",
                owner_type="pskill",
                owner_id=pskill.id,
                input_payload={},
                output_payload={},
                created_at=now - timedelta(minutes=5),
                updated_at=now - timedelta(minutes=4),
            )
            session.add_all(
                [
                    pskill,
                    version,
                    run,
                    runner_run,
                    builder_run,
                    AgentToolAuthorization(
                        id="tool-auth-observe-newer",
                        agent_run_id=runner_run.id,
                        run_id=run.id,
                        tool_name="psop.repository.commit_patch",
                        tool_provider="native",
                        side_effect_level="high_write",
                        risk_level="high",
                        authorization_reason="needs write approval",
                        tool_arguments_summary={"path": "SKILL.md"},
                        expected_effect_summary="update skill source",
                        reversible=False,
                        status="pending",
                        request_payload={"request": "newer"},
                        response_payload={},
                        created_at=now - timedelta(minutes=1),
                    ),
                    AgentToolAuthorization(
                        id="tool-auth-observe-older",
                        agent_run_id=runner_run.id,
                        run_id=run.id,
                        tool_name="psop.repository.commit_patch",
                        tool_provider="native",
                        side_effect_level="high_write",
                        risk_level="high",
                        authorization_reason="needs write approval",
                        tool_arguments_summary={"path": "README.md"},
                        expected_effect_summary="update docs",
                        reversible=False,
                        status="pending",
                        request_payload={"request": "older"},
                        response_payload={},
                        created_at=now - timedelta(minutes=2),
                    ),
                    AgentToolAuthorization(
                        id="tool-auth-observe-builder",
                        agent_run_id=builder_run.id,
                        tool_name="psop.memory.search",
                        tool_provider="native",
                        side_effect_level="read",
                        risk_level="medium",
                        tool_arguments_summary={"query": "materials"},
                        status="approved",
                        request_payload={},
                        response_payload={"decision": "approved"},
                        created_at=now - timedelta(minutes=1),
                    ),
                    AgentToolAuthorization(
                        id="tool-auth-observe-executed",
                        agent_run_id=runner_run.id,
                        run_id=run.id,
                        tool_name="psop.skill_version.activate",
                        tool_provider="native",
                        side_effect_level="high_write",
                        risk_level="high",
                        authorization_reason="activation completed after approval",
                        tool_arguments_summary={"package_name": "pskill-builder"},
                        expected_effect_summary="activate skill version",
                        reversible=True,
                        status="executed",
                        request_payload={"request": "executed"},
                        response_payload={"decision": "approved"},
                        created_at=now - timedelta(seconds=30),
                        executed_at=now - timedelta(seconds=10),
                    ),
                    AgentToolAuthorization(
                        id="tool-auth-observe-old-window",
                        agent_run_id=runner_run.id,
                        run_id=run.id,
                        tool_name="psop.repository.commit_patch",
                        tool_provider="native",
                        side_effect_level="high_write",
                        risk_level="high",
                        tool_arguments_summary={},
                        status="pending",
                        request_payload={},
                        response_payload={},
                        created_at=now - timedelta(days=2),
                    ),
                ]
            )
            session.commit()

        response = client.get(
            "/api/v1/observability/tool-authorizations",
            params={"window_hours": 24, "status": "pending", "limit": 10},
        )
        scoped_response = client.get(
            "/api/v1/observability/tool-authorizations",
            params={
                "window_hours": 24,
                "agent_key": "pskill.runner",
                "run_id": run.id,
                "risk_level": "high",
                "side_effect_level": "high_write",
                "tool_name": "psop.repository.commit_patch",
                "limit": 10,
            },
        )
        executed_response = client.get(
            "/api/v1/observability/tool-authorizations",
            params={"window_hours": 24, "status": "executed", "limit": 10},
        )

    assert response.status_code == 200
    payload = response.json()
    assert [item["id"] for item in payload] == ["tool-auth-observe-newer", "tool-auth-observe-older"]
    assert payload[0]["agent_run_id"] == runner_run.id
    assert payload[0]["tool_name"] == "psop.repository.commit_patch"
    assert payload[0]["status"] == "pending"
    assert payload[0]["risk_level"] == "high"
    assert payload[0]["side_effect_level"] == "high_write"
    assert payload[0]["tool_arguments_summary"] == {"path": "SKILL.md"}

    assert scoped_response.status_code == 200
    scoped_payload = scoped_response.json()
    assert {item["id"] for item in scoped_payload} == {"tool-auth-observe-newer", "tool-auth-observe-older"}

    assert executed_response.status_code == 200
    executed_payload = executed_response.json()
    assert [item["id"] for item in executed_payload] == ["tool-auth-observe-executed"]
    assert executed_payload[0]["status"] == "executed"
    assert executed_payload[0]["executed_at"]


def test_observability_skill_activation_query_filters_recent_agent_skill_activations() -> None:
    client, _, _ = create_test_client()

    with client:
        now = now_utc()
        db_manager = client.app.state.db_manager
        with db_manager.session() as session:
            pskill = PSkillDefinition(
                id="pskill-observe-skill-activations-1",
                key="observe-skill-activations-demo",
                name="Observe Skill Activations Demo",
                gitlab_project_id="observe-skill-activations-project",
                repository_url="https://gitlab.example.local/skills/observe-skill-activations-demo",
            )
            version = PSkillVersion(
                id="pskill-version-observe-skill-activations-1",
                pskill_definition_id=pskill.id,
                version_no=1,
                status="published",
                source_ref="main",
            )
            run = Run(
                id="run-observe-skill-activations-1",
                invocation_id="invocation-observe-skill-activations-1",
                pskill_definition_id=pskill.id,
                pskill_version_id=version.id,
                compile_artifact_id="artifact-observe-skill-activations-1",
                status="failed",
                runtime_phase="failed",
                created_at=now - timedelta(minutes=5),
            )
            runner_run = AgentRun(
                id="agent-run-observe-skill-activations-1",
                agent_key="pskill.runner",
                status="failed",
                owner_type="runtime",
                owner_id=run.id,
                run_id=run.id,
                input_payload={},
                output_payload={},
                created_at=now - timedelta(minutes=5),
                updated_at=now - timedelta(minutes=4),
            )
            builder_run = AgentRun(
                id="agent-run-observe-skill-activations-2",
                agent_key="pskill.builder",
                status="succeeded",
                owner_type="pskill",
                owner_id=pskill.id,
                input_payload={},
                output_payload={},
                created_at=now - timedelta(minutes=5),
                updated_at=now - timedelta(minutes=4),
            )
            runner_package = SkillPackage(
                id="skill-package-observe-runner",
                name="pskill-runner-field-assistant",
                scope="psop",
                description="runner package",
                source_uri="skills/psop/pskill-runner-field-assistant",
                created_at=now - timedelta(minutes=10),
                updated_at=now - timedelta(minutes=10),
            )
            runner_version = SkillVersion(
                id="skill-version-observe-runner",
                package_id=runner_package.id,
                version_label="v1",
                content_hash="hash-runner",
                manifest_json={},
                body_object_key="",
                resource_index=[],
                allowed_tools=[],
                created_at=now - timedelta(minutes=10),
                updated_at=now - timedelta(minutes=10),
            )
            builder_package = SkillPackage(
                id="skill-package-observe-builder",
                name="pskill-builder",
                scope="psop",
                description="builder package",
                source_uri="skills/psop/pskill-builder",
                created_at=now - timedelta(minutes=10),
                updated_at=now - timedelta(minutes=10),
            )
            builder_version = SkillVersion(
                id="skill-version-observe-builder",
                package_id=builder_package.id,
                version_label="v1",
                content_hash="hash-builder",
                manifest_json={},
                body_object_key="",
                resource_index=[],
                allowed_tools=[],
                created_at=now - timedelta(minutes=10),
                updated_at=now - timedelta(minutes=10),
            )
            session.add_all(
                [
                    pskill,
                    version,
                    run,
                    runner_run,
                    builder_run,
                    runner_package,
                    runner_version,
                    builder_package,
                    builder_version,
                    SkillActivation(
                        id="skill-activation-observe-newer",
                        agent_run_id=runner_run.id,
                        package_id=runner_package.id,
                        version_id=runner_version.id,
                        activation_context={"reason": "runtime field guidance"},
                        created_at=now - timedelta(minutes=1),
                    ),
                    SkillActivation(
                        id="skill-activation-observe-older",
                        agent_run_id=runner_run.id,
                        package_id=runner_package.id,
                        version_id=runner_version.id,
                        activation_context={"reason": "runtime evidence"},
                        created_at=now - timedelta(minutes=2),
                    ),
                    SkillActivation(
                        id="skill-activation-observe-builder",
                        agent_run_id=builder_run.id,
                        package_id=builder_package.id,
                        version_id=builder_version.id,
                        activation_context={"reason": "builder"},
                        created_at=now - timedelta(minutes=1),
                    ),
                    SkillActivation(
                        id="skill-activation-observe-old-window",
                        agent_run_id=runner_run.id,
                        package_id=runner_package.id,
                        version_id=runner_version.id,
                        activation_context={"old": True},
                        created_at=now - timedelta(days=2),
                    ),
                ]
            )
            session.commit()

        response = client.get(
            "/api/v1/observability/skill-activations",
            params={"window_hours": 24, "package_id": runner_package.id, "limit": 10},
        )
        scoped_response = client.get(
            "/api/v1/observability/skill-activations",
            params={
                "window_hours": 24,
                "agent_key": "pskill.runner",
                "run_id": run.id,
                "version_id": runner_version.id,
                "limit": 10,
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert [item["id"] for item in payload] == [
        "skill-activation-observe-newer",
        "skill-activation-observe-older",
    ]
    assert payload[0]["agent_run_id"] == runner_run.id
    assert payload[0]["package_id"] == runner_package.id
    assert payload[0]["version_id"] == runner_version.id
    assert payload[0]["activation_context"] == {"reason": "runtime field guidance"}

    assert scoped_response.status_code == 200
    scoped_payload = scoped_response.json()
    assert {item["id"] for item in scoped_payload} == {
        "skill-activation-observe-newer",
        "skill-activation-observe-older",
    }
