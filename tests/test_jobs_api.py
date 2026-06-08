from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select

from app.agents.models import AgentRun
from app.compiler.models import ArtifactObject, EgCompileArtifact, PSkillCompileRequest
from app.evaluations.models import RunEvaluation, RunEvaluationFinding
from app.jobs.models import RuntimeJob
from app.jobs.repository import JobRepository
from app.jobs.types import (
    DEAD_LETTER_JOB_STATUS,
    GOVERNANCE_PROPOSAL_JOB_TYPE,
    LEGACY_RUNTIME_JOB_TYPE,
    MEMORY_COMPACTION_JOB_TYPE,
    PSKILL_COMPILE_JOB_TYPE,
    RUN_EVALUATION_JOB_TYPE,
    RUNTIME_STEP_JOB_TYPE,
    SKILL_SYNC_JOB_TYPE,
)
from app.jobs.worker import RuntimeJobWorker
from app.memory.models import AgentMemoryEntry
from app.pskills.models import PSkillDefinition, PSkillVersion, now_utc
from app.runtime.models import Run, RunTrace, SkillInvocation
from app.skills.models import SkillPackage
from tests.test_skills_api import create_test_client


def test_runtime_jobs_api_exposes_observability_fields_and_filters() -> None:
    client, _, _ = create_test_client()
    now = now_utc()

    with client:
        with client.app.state.db_manager.session() as session:
            unknown_job = RuntimeJob(
                job_type="custom_future_job",
                status="pending",
                payload={"operation": "future", "entity_id": "entity-1"},
                dedupe_key="job:custom-future",
                metrics={"llm_calls": 1, "input_tokens": 20, "output_tokens": 30, "total_tokens": 50},
            )
            unknown_job.created_at = now - timedelta(minutes=10)
            unknown_job.started_at = now - timedelta(minutes=9)
            unknown_job.finished_at = now - timedelta(minutes=8)
            unknown_job.status = "succeeded"
            session.add(unknown_job)

            pending_job = RuntimeJob(
                job_type="no_token_job",
                status="pending",
                payload={"operation": "no-token"},
                dedupe_key="job:no-token",
            )
            pending_job.created_at = now - timedelta(minutes=5)
            session.add(pending_job)

            old_job = RuntimeJob(
                job_type="old_compile",
                status="pending",
                payload={},
                dedupe_key="job:old-compile",
                metrics={"llm_calls": 1, "total_tokens": 999},
            )
            old_job.created_at = now - timedelta(hours=30)
            old_job.started_at = now - timedelta(hours=30)
            old_job.finished_at = old_job.started_at + timedelta(seconds=2)
            old_job.status = "succeeded"
            session.add(old_job)
            session.commit()

        response = client.get(
            "/api/v1/runtime/jobs",
            params={"job_type": "custom_future_job", "status": "succeeded", "q": "future"},
        )
        assert response.status_code == 200
        payload = response.json()
        assert len(payload) == 1
        job = payload[0]
        assert job["job_type"] == "custom_future_job"
        assert job["progress"]["percent"] == 100
        assert job["duration_ms"] is not None
        assert job["token_usage"]["total_tokens"] == 50
        assert job["metrics"]["llm_calls"] == 1

        no_token_response = client.get("/api/v1/runtime/jobs", params={"job_type": "no_token_job"})
        assert no_token_response.status_code == 200
        assert no_token_response.json()[0]["token_usage"] is None


def test_runtime_jobs_stats_uses_window_and_summarizes_tokens() -> None:
    client, _, _ = create_test_client()
    now = now_utc()

    with client:
        with client.app.state.db_manager.session() as session:
            succeeded = RuntimeJob(
                job_type=RUNTIME_STEP_JOB_TYPE,
                status="pending",
                payload={"run_id": "run-1"},
                dedupe_key="job:runtime-stats",
                metrics={"llm_calls": 2, "input_tokens": 40, "output_tokens": 10, "total_tokens": 50},
            )
            succeeded.created_at = now - timedelta(hours=1)
            succeeded.started_at = now - timedelta(hours=1)
            succeeded.finished_at = now - timedelta(hours=1) + timedelta(seconds=4)
            succeeded.status = "succeeded"
            session.add(succeeded)

            failed = RuntimeJob(
                job_type=PSKILL_COMPILE_JOB_TYPE,
                status="pending",
                payload={},
                dedupe_key="job:compile-stats",
            )
            failed.created_at = now - timedelta(hours=2)
            failed.started_at = now - timedelta(hours=2)
            failed.finished_at = now - timedelta(hours=2) + timedelta(seconds=2)
            failed.status = "failed"
            session.add(failed)

            old = RuntimeJob(
                job_type=LEGACY_RUNTIME_JOB_TYPE,
                status="pending",
                payload={},
                dedupe_key="job:old-stats",
                metrics={"llm_calls": 1, "total_tokens": 500},
            )
            old.created_at = now - timedelta(hours=25)
            old.started_at = now - timedelta(hours=25)
            old.finished_at = now - timedelta(hours=25) + timedelta(seconds=1)
            old.status = "succeeded"
            session.add(old)
            session.commit()

        response = client.get("/api/v1/runtime/jobs/stats")
        assert response.status_code == 200
        stats = response.json()
        assert stats["window_hours"] == 24
        assert stats["total"] == 2
        assert stats["succeeded"] == 1
        assert stats["failed"] == 1
        assert stats["success_rate"] == 0.5
        assert stats["avg_duration_ms"] == 3000
        assert stats["max_duration_ms"] == 4000
        assert stats["token_usage"]["total_tokens"] == 50
        assert stats["by_type"][RUNTIME_STEP_JOB_TYPE] == 1
        assert stats["by_type"][PSKILL_COMPILE_JOB_TYPE] == 1

        legacy_filter = client.get(
            "/api/v1/runtime/jobs",
            params={"job_type": LEGACY_RUNTIME_JOB_TYPE, "q": "runtime-stats"},
        )
        assert legacy_filter.status_code == 200
        assert [item["job_type"] for item in legacy_filter.json()] == [RUNTIME_STEP_JOB_TYPE]


def test_runtime_job_repository_recovers_expired_leases_with_retry_and_failure() -> None:
    client, _, _ = create_test_client()
    now = now_utc()

    with client:
        with client.app.state.db_manager.session() as session:
            retryable = RuntimeJob(
                job_type=RUNTIME_STEP_JOB_TYPE,
                status="running",
                payload={"run_id": "retry-run"},
                dedupe_key="job:lease-retry",
                attempt_no=1,
                max_attempts=3,
                worker_name="stale-worker",
                lease_until=now - timedelta(seconds=1),
                available_at=now - timedelta(minutes=1),
            )
            exhausted = RuntimeJob(
                job_type=PSKILL_COMPILE_JOB_TYPE,
                status="running",
                payload={"compile_request_id": "compile-exhausted"},
                dedupe_key="job:lease-exhausted",
                attempt_no=3,
                max_attempts=3,
                worker_name="stale-worker",
                lease_until=now - timedelta(seconds=1),
                available_at=now - timedelta(minutes=1),
            )
            active = RuntimeJob(
                job_type=RUNTIME_STEP_JOB_TYPE,
                status="running",
                payload={"run_id": "active-run"},
                dedupe_key="job:lease-active",
                attempt_no=1,
                max_attempts=3,
                worker_name="active-worker",
                lease_until=now + timedelta(minutes=1),
                available_at=now - timedelta(minutes=1),
            )
            session.add_all([retryable, exhausted, active])
            session.commit()

            recovered = JobRepository().recover_expired_leases(session)
            recovered_ids = {job.id for job in recovered}
            retryable_id = retryable.id
            exhausted_id = exhausted.id
            active_id = active.id

            refreshed_retryable = session.get(RuntimeJob, retryable_id)
            refreshed_exhausted = session.get(RuntimeJob, exhausted_id)
            refreshed_active = session.get(RuntimeJob, active_id)

            retryable_state = {
                "status": refreshed_retryable.status,
                "worker_name": refreshed_retryable.worker_name,
                "lease_until": refreshed_retryable.lease_until,
                "available_at": refreshed_retryable.available_at,
                "last_error": refreshed_retryable.last_error,
                "metrics": dict(refreshed_retryable.metrics or {}),
            }
            exhausted_state = {
                "status": refreshed_exhausted.status,
                "worker_name": refreshed_exhausted.worker_name,
                "lease_until": refreshed_exhausted.lease_until,
                "finished_at": refreshed_exhausted.finished_at,
                "metrics": dict(refreshed_exhausted.metrics or {}),
            }
            active_state = {
                "status": refreshed_active.status,
                "worker_name": refreshed_active.worker_name,
                "lease_until": refreshed_active.lease_until,
            }

    assert recovered_ids == {retryable_id, exhausted_id}
    assert retryable_state["status"] == "pending"
    assert retryable_state["worker_name"] == ""
    assert retryable_state["lease_until"] is None
    assert retryable_state["available_at"] is not None
    assert retryable_state["last_error"] == "runtime_job lease expired before worker completed the job."
    assert retryable_state["metrics"]["lease_recovery_count"] == 1

    assert exhausted_state["status"] == DEAD_LETTER_JOB_STATUS
    assert exhausted_state["worker_name"] == ""
    assert exhausted_state["lease_until"] is None
    assert exhausted_state["finished_at"] is not None
    assert exhausted_state["metrics"]["lease_recovery_count"] == 1

    assert active_state["status"] == "running"
    assert active_state["worker_name"] == "active-worker"
    assert active_state["lease_until"] is not None


def test_runtime_job_worker_recovers_expired_leases_before_claiming() -> None:
    client, _, _ = create_test_client()
    now = now_utc()

    with client:
        with client.app.state.db_manager.session() as session:
            expired = RuntimeJob(
                job_type=RUNTIME_STEP_JOB_TYPE,
                status="running",
                payload={"run_id": "worker-recovery-run"},
                dedupe_key="job:worker-lease-recovery",
                attempt_no=1,
                max_attempts=3,
                worker_name="interrupted-worker",
                lease_until=now - timedelta(seconds=1),
                available_at=now - timedelta(minutes=1),
            )
            session.add(expired)
            session.commit()
            expired_id = expired.id

        worker = RuntimeJobWorker(
            settings=client.app.state.settings,
            database_manager=client.app.state.db_manager,
            gitlab_gateway=client.app.state.gitlab_gateway,
            inference_gateway=client.app.state.inference_gateway,
            asr_gateway=client.app.state.asr_gateway,
            object_store=client.app.state.object_store,
        )
        processed = worker.run_once()

        with client.app.state.db_manager.session() as session:
            recovered = session.get(RuntimeJob, expired_id)
            recovered_state = {
                "status": recovered.status,
                "worker_name": recovered.worker_name,
                "lease_until": recovered.lease_until,
                "metrics": dict(recovered.metrics or {}),
            }

    assert processed is True
    assert recovered_state["status"] == "pending"
    assert recovered_state["worker_name"] == ""
    assert recovered_state["lease_until"] is None
    assert recovered_state["metrics"]["lease_recovery_count"] == 1


def test_runtime_job_worker_moves_exhausted_unhandled_failures_to_dead_letter() -> None:
    client, _, _ = create_test_client()

    with client:
        with client.app.state.db_manager.session() as session:
            job = RuntimeJob(
                job_type=PSKILL_COMPILE_JOB_TYPE,
                status="pending",
                payload={},
                dedupe_key="job:dead-letter-compile",
                attempt_no=2,
                max_attempts=3,
            )
            session.add(job)
            session.commit()
            job_id = job.id

        worker = RuntimeJobWorker(
            settings=client.app.state.settings,
            database_manager=client.app.state.db_manager,
            gitlab_gateway=client.app.state.gitlab_gateway,
            inference_gateway=client.app.state.inference_gateway,
            asr_gateway=client.app.state.asr_gateway,
            object_store=client.app.state.object_store,
        )
        processed = worker.run_once()

        with client.app.state.db_manager.session() as session:
            refreshed = session.get(RuntimeJob, job_id)
            job_state = {
                "status": refreshed.status,
                "attempt_no": refreshed.attempt_no,
                "last_error": refreshed.last_error,
                "finished_at": refreshed.finished_at,
                "payload": dict(refreshed.payload or {}),
            }

    assert processed is True
    assert job_state["attempt_no"] == 3
    assert job_state["status"] == DEAD_LETTER_JOB_STATUS
    assert job_state["last_error"]
    assert job_state["finished_at"] is not None
    assert job_state["payload"]["terminal_status"] == "failed"


def test_runtime_job_worker_processes_skill_sync_job_and_exposes_progress() -> None:
    client, _, _ = create_test_client()

    with client:
        with client.app.state.db_manager.session() as session:
            job = RuntimeJob(
                job_type=SKILL_SYNC_JOB_TYPE,
                status="pending",
                payload={"operation": "sync skill packages"},
                dedupe_key="job:skill-sync",
            )
            session.add(job)
            session.commit()
            job_id = job.id

        worker = RuntimeJobWorker(
            settings=client.app.state.settings,
            database_manager=client.app.state.db_manager,
            gitlab_gateway=client.app.state.gitlab_gateway,
            inference_gateway=client.app.state.inference_gateway,
            asr_gateway=client.app.state.asr_gateway,
            object_store=client.app.state.object_store,
        )
        processed = worker.run_once()

        with client.app.state.db_manager.session() as session:
            refreshed = session.get(RuntimeJob, job_id)
            package_count = session.scalar(select(func.count()).select_from(SkillPackage))
            job_state = {
                "status": refreshed.status,
                "payload": dict(refreshed.payload or {}),
                "metrics": dict(refreshed.metrics or {}),
                "lease_until": refreshed.lease_until,
                "last_error": refreshed.last_error,
            }
        jobs_response = client.get("/api/v1/runtime/jobs", params={"job_type": SKILL_SYNC_JOB_TYPE})

    assert processed is True
    assert job_state["status"] == "succeeded"
    assert job_state["lease_until"] is None
    assert job_state["last_error"] == ""
    assert package_count == 8
    assert job_state["metrics"]["scanned_count"] == 8
    assert job_state["metrics"]["package_count"] == 8
    assert job_state["metrics"]["version_count"] == 8
    assert job_state["metrics"]["changed"] is True
    assert job_state["payload"]["sync_result"]["package_count"] == 8
    assert job_state["payload"]["sync_result"]["changed"] is True

    assert jobs_response.status_code == 200
    synced_job = jobs_response.json()[0]
    assert synced_job["id"] == job_id
    assert synced_job["status"] == "succeeded"
    assert synced_job["progress"]["percent"] == 100
    assert synced_job["progress"]["label"] == "Skill 包同步完成"
    assert synced_job["progress"]["detail"] == "scanned=8 / packages=8 / versions=8 / changed=True"


def test_runtime_job_worker_processes_run_evaluation_job_and_exposes_progress() -> None:
    client, _, _ = create_test_client()
    now = now_utc()

    with client:
        with client.app.state.db_manager.session() as session:
            pskill = PSkillDefinition(
                id="pskill-job-evaluation",
                key="job-evaluation",
                name="Job Evaluation",
                gitlab_project_id="job-evaluation-project",
                repository_url="https://gitlab.example.local/skills/job-evaluation",
            )
            version = PSkillVersion(
                id="pskill-version-job-evaluation",
                pskill_definition_id=pskill.id,
                version_no=1,
                status="published",
                source_ref="main",
            )
            pskill.latest_published_version_id = version.id
            compile_request = PSkillCompileRequest(
                id="compile-job-evaluation",
                pskill_definition_id=pskill.id,
                pskill_version_id=version.id,
                source_commit_sha="commit-job-evaluation",
                status="succeeded",
                dedupe_key="compile:job-evaluation",
            )
            artifact_object = ArtifactObject(
                id="artifact-object-job-evaluation",
                bucket="compile-artifacts",
                object_key="job-evaluation/artifact.json",
                media_type="application/json",
                content_json={"formal_revision": "psop-eg-formal/v5"},
            )
            artifact = EgCompileArtifact(
                id="artifact-job-evaluation",
                compile_request_id=compile_request.id,
                pskill_version_id=version.id,
                artifact_object_id=artifact_object.id,
                formal_revision="psop-eg-formal/v5",
                graph_summary={"template": "job evaluation"},
                capability_summary={},
                status="ready",
            )
            invocation = SkillInvocation(
                id="invocation-job-evaluation",
                pskill_definition_id=pskill.id,
                pskill_version_id=version.id,
                compile_artifact_id=artifact.id,
                gateway_type="web",
                status="accepted",
            )
            run = Run(
                id="run-job-evaluation",
                invocation_id=invocation.id,
                pskill_definition_id=pskill.id,
                pskill_version_id=version.id,
                compile_artifact_id=artifact.id,
                status="failed",
                runtime_phase="failed",
                latest_trace_seq=1,
                exit_reason="runtime provider failed",
                created_at=now - timedelta(minutes=1),
                started_at=now - timedelta(minutes=1),
                ended_at=now,
            )
            trace = RunTrace(
                id="trace-job-evaluation",
                run_id=run.id,
                seq_no=1,
                phase="runtime",
                event_type="runtime.failed",
                payload={"error": "runtime provider failed"},
                occurred_at=now,
            )
            job = RuntimeJob(
                job_type=RUN_EVALUATION_JOB_TYPE,
                status="pending",
                payload={"run_id": run.id},
                run_id=run.id,
                dedupe_key="job:run-evaluation",
            )
            session.add_all([pskill, version, compile_request, artifact_object, artifact, invocation, run, trace, job])
            session.commit()
            job_id = job.id

        worker = RuntimeJobWorker(
            settings=client.app.state.settings,
            database_manager=client.app.state.db_manager,
            gitlab_gateway=client.app.state.gitlab_gateway,
            inference_gateway=client.app.state.inference_gateway,
            asr_gateway=client.app.state.asr_gateway,
            object_store=client.app.state.object_store,
        )
        processed = worker.run_once()

        with client.app.state.db_manager.session() as session:
            refreshed = session.get(RuntimeJob, job_id)
            evaluation = session.scalar(select(RunEvaluation).where(RunEvaluation.run_id == "run-job-evaluation"))
            job_state = {
                "status": refreshed.status,
                "payload": dict(refreshed.payload or {}),
                "metrics": dict(refreshed.metrics or {}),
                "lease_until": refreshed.lease_until,
                "last_error": refreshed.last_error,
            }
            evaluation_id = evaluation.id
            agent_run_id = evaluation.agent_run_id

        evaluation_response = client.get(f"/api/v1/evaluations/{evaluation_id}")
        agent_run_response = client.get(f"/api/v1/agent-runs/{agent_run_id}")
        jobs_response = client.get("/api/v1/runtime/jobs", params={"job_type": RUN_EVALUATION_JOB_TYPE})

    assert processed is True
    assert job_state["status"] == "succeeded"
    assert job_state["lease_until"] is None
    assert job_state["last_error"] == ""
    assert job_state["payload"]["operation"] == "run_evaluation"
    assert job_state["payload"]["evaluation_id"] == evaluation_id
    assert job_state["payload"]["overall_outcome"] == "failed"
    assert job_state["payload"]["finding_count"] == 2
    assert job_state["metrics"]["evaluation_id"] == evaluation_id
    assert job_state["metrics"]["finding_count"] == 2
    assert job_state["metrics"]["quality_score"] == 13

    assert evaluation_response.status_code == 200
    evaluation_payload = evaluation_response.json()
    assert evaluation_payload["run_id"] == "run-job-evaluation"
    assert evaluation_payload["overall_outcome"] == "failed"
    assert {finding["category"] for finding in evaluation_payload["findings"]} == {
        "runner_issue",
        "evidence_quality_issue",
    }
    assert agent_run_response.json()["agent_key"] == "pskill.evaluator"
    assert agent_run_response.json()["status"] == "succeeded"

    assert jobs_response.status_code == 200
    evaluation_job = jobs_response.json()[0]
    assert evaluation_job["id"] == job_id
    assert evaluation_job["progress"]["percent"] == 100
    assert evaluation_job["progress"]["label"] == "Run 评估完成"
    assert f"evaluation={evaluation_id}" in evaluation_job["progress"]["detail"]
    assert "outcome=failed" in evaluation_job["progress"]["detail"]
    assert "score=13" in evaluation_job["progress"]["detail"]
    assert "findings=2" in evaluation_job["progress"]["detail"]


def test_runtime_job_worker_processes_governance_proposal_job_and_exposes_progress() -> None:
    client, _, _ = create_test_client()
    now = now_utc()

    with client:
        with client.app.state.db_manager.session() as session:
            pskill = PSkillDefinition(
                id="pskill-job-governance",
                key="job-governance",
                name="Job Governance",
                gitlab_project_id="job-governance-project",
                repository_url="https://gitlab.example.local/skills/job-governance",
            )
            version = PSkillVersion(
                id="pskill-version-job-governance",
                pskill_definition_id=pskill.id,
                version_no=1,
                status="published",
                source_ref="main",
            )
            pskill.latest_published_version_id = version.id
            compile_request = PSkillCompileRequest(
                id="compile-job-governance",
                pskill_definition_id=pskill.id,
                pskill_version_id=version.id,
                source_commit_sha="commit-job-governance",
                status="succeeded",
                dedupe_key="compile:job-governance",
            )
            artifact_object = ArtifactObject(
                id="artifact-object-job-governance",
                bucket="compile-artifacts",
                object_key="job-governance/artifact.json",
                media_type="application/json",
                content_json={"formal_revision": "psop-eg-formal/v5"},
            )
            artifact = EgCompileArtifact(
                id="artifact-job-governance",
                compile_request_id=compile_request.id,
                pskill_version_id=version.id,
                artifact_object_id=artifact_object.id,
                formal_revision="psop-eg-formal/v5",
                graph_summary={"template": "job governance"},
                capability_summary={},
                status="ready",
            )
            invocation = SkillInvocation(
                id="invocation-job-governance",
                pskill_definition_id=pskill.id,
                pskill_version_id=version.id,
                compile_artifact_id=artifact.id,
                gateway_type="web",
                status="accepted",
            )
            run = Run(
                id="run-job-governance",
                invocation_id=invocation.id,
                pskill_definition_id=pskill.id,
                pskill_version_id=version.id,
                compile_artifact_id=artifact.id,
                status="failed",
                runtime_phase="failed",
                exit_reason="runtime provider failed",
                created_at=now - timedelta(minutes=2),
                started_at=now - timedelta(minutes=2),
                ended_at=now - timedelta(minutes=1),
            )
            evaluator_run = AgentRun(
                id="agent-run-job-governance-evaluator",
                agent_key="pskill.evaluator",
                status="succeeded",
                owner_type="run_evaluation",
                owner_id="evaluation-job-governance",
                run_id=run.id,
                input_payload={},
                output_payload={},
                started_at=now - timedelta(minutes=1),
                ended_at=now,
            )
            evaluation = RunEvaluation(
                id="evaluation-job-governance",
                run_id=run.id,
                pskill_definition_id=pskill.id,
                pskill_version_id=version.id,
                artifact_id=artifact.id,
                agent_run_id=evaluator_run.id,
                overall_outcome="failed",
                quality_score=13,
                summary="Run 失败，需要治理提案。",
                attribution_json={"finding_count": 1},
            )
            finding = RunEvaluationFinding(
                id="finding-job-governance",
                evaluation_id=evaluation.id,
                category="runner_issue",
                severity="high",
                confidence=90,
                description="Runtime provider failure should be replayed before changing runner skills.",
                evidence_refs=[{"kind": "run_trace", "id": "trace-job-governance", "event_type": "runtime.failed"}],
                recommended_action="补充回归测试并更新 runner skill 说明。",
                status="open",
            )
            job = RuntimeJob(
                job_type=GOVERNANCE_PROPOSAL_JOB_TYPE,
                status="pending",
                payload={"finding_id": finding.id},
                run_id=run.id,
                dedupe_key="job:governance-proposal",
            )
            session.add_all(
                [
                    pskill,
                    version,
                    compile_request,
                    artifact_object,
                    artifact,
                    invocation,
                    run,
                    evaluator_run,
                    evaluation,
                    finding,
                    job,
                ]
            )
            session.commit()
            job_id = job.id

        worker = RuntimeJobWorker(
            settings=client.app.state.settings,
            database_manager=client.app.state.db_manager,
            gitlab_gateway=client.app.state.gitlab_gateway,
            inference_gateway=client.app.state.inference_gateway,
            asr_gateway=client.app.state.asr_gateway,
            object_store=client.app.state.object_store,
        )
        processed = worker.run_once()

        with client.app.state.db_manager.session() as session:
            refreshed = session.get(RuntimeJob, job_id)
            refreshed_finding = session.get(RunEvaluationFinding, "finding-job-governance")
            job_state = {
                "status": refreshed.status,
                "payload": dict(refreshed.payload or {}),
                "metrics": dict(refreshed.metrics or {}),
                "lease_until": refreshed.lease_until,
                "last_error": refreshed.last_error,
            }
            proposal_id = job_state["payload"]["proposal_id"]
            finding_status = refreshed_finding.status

        proposal_response = client.get(f"/api/v1/governance/proposals/{proposal_id}")
        agent_run_response = client.get(f"/api/v1/agent-runs/{proposal_response.json()['agent_run_id']}")
        authorizations_response = client.get(
            f"/api/v1/agent-runs/{proposal_response.json()['agent_run_id']}/tool-authorizations"
        )
        jobs_response = client.get("/api/v1/runtime/jobs", params={"job_type": GOVERNANCE_PROPOSAL_JOB_TYPE})

    assert processed is True
    assert job_state["status"] == "succeeded"
    assert job_state["lease_until"] is None
    assert job_state["last_error"] == ""
    assert job_state["payload"]["operation"] == "governance_proposal"
    assert job_state["payload"]["proposal_type"] == "agent_skill_update"
    assert job_state["payload"]["proposal_status"] == "draft"
    assert job_state["payload"]["source_finding_ids"] == ["finding-job-governance"]
    assert job_state["metrics"]["proposal_id"] == proposal_id
    assert job_state["metrics"]["source_finding_count"] == 1
    assert finding_status == "converted_to_proposal"

    assert proposal_response.status_code == 200
    proposal = proposal_response.json()
    assert proposal["id"] == proposal_id
    assert proposal["proposal_type"] == "agent_skill_update"
    assert proposal["source_run_id"] == "run-job-governance"
    assert proposal["source_evaluation_id"] == "evaluation-job-governance"
    assert proposal["source_finding_ids"] == ["finding-job-governance"]
    assert proposal["activation_plan"]["direct_activation_allowed"] is False
    assert agent_run_response.json()["agent_key"] == "psop.governance"
    assert agent_run_response.json()["status"] == "succeeded"
    assert authorizations_response.json() == []

    assert jobs_response.status_code == 200
    proposal_job = jobs_response.json()[0]
    assert proposal_job["id"] == job_id
    assert proposal_job["progress"]["percent"] == 100
    assert proposal_job["progress"]["label"] == "治理提案已生成"
    assert f"proposal={proposal_id}" in proposal_job["progress"]["detail"]
    assert "type=agent_skill_update" in proposal_job["progress"]["detail"]
    assert "status=draft" in proposal_job["progress"]["detail"]
    assert "findings=1" in proposal_job["progress"]["detail"]


def test_runtime_job_worker_processes_memory_compaction_job_and_exposes_progress() -> None:
    client, _, _ = create_test_client()

    with client:
        with client.app.state.db_manager.session() as session:
            first = AgentMemoryEntry(
                id="memory-source-1",
                namespace="evaluation",
                memory_type="episodic",
                agent_key="pskill.evaluator",
                status="active",
                confidence=80,
                title="Runtime provider failure replay",
                content="Provider failures should be evaluated with replay and run_trace evidence.",
                source_refs=[{"kind": "run_trace", "id": "trace-memory-1"}],
                tags=["runtime", "replay"],
            )
            second = AgentMemoryEntry(
                id="memory-source-2",
                namespace="evaluation",
                memory_type="episodic",
                agent_key="pskill.evaluator",
                status="active",
                confidence=90,
                title="Governance proposal boundary",
                content="Governance proposals stay in review and must not directly activate high-write changes.",
                source_refs=[{"kind": "run_evaluation_finding", "id": "finding-memory-1"}],
                tags=["governance"],
            )
            job = RuntimeJob(
                job_type=MEMORY_COMPACTION_JOB_TYPE,
                status="pending",
                payload={
                    "namespace": "evaluation",
                    "memory_type": "episodic",
                    "status": "active",
                    "agent_key": "pskill.evaluator",
                    "target_namespace": "evaluation",
                    "target_memory_type": "artifact",
                    "title": "Evaluation governance compacted memory",
                    "archive_source_entries": True,
                },
                dedupe_key="job:memory-compaction",
            )
            session.add_all([first, second, job])
            session.commit()
            job_id = job.id

        worker = RuntimeJobWorker(
            settings=client.app.state.settings,
            database_manager=client.app.state.db_manager,
            gitlab_gateway=client.app.state.gitlab_gateway,
            inference_gateway=client.app.state.inference_gateway,
            asr_gateway=client.app.state.asr_gateway,
            object_store=client.app.state.object_store,
        )
        processed = worker.run_once()

        with client.app.state.db_manager.session() as session:
            refreshed = session.get(RuntimeJob, job_id)
            compacted_id = refreshed.payload["compacted_memory_id"]
            compacted = session.get(AgentMemoryEntry, compacted_id)
            source_statuses = {
                item.id: item.status
                for item in session.scalars(
                    select(AgentMemoryEntry).where(AgentMemoryEntry.id.in_(["memory-source-1", "memory-source-2"]))
                )
            }
            job_state = {
                "status": refreshed.status,
                "payload": dict(refreshed.payload or {}),
                "metrics": dict(refreshed.metrics or {}),
                "lease_until": refreshed.lease_until,
                "last_error": refreshed.last_error,
            }
            compacted_state = {
                "namespace": compacted.namespace,
                "memory_type": compacted.memory_type,
                "agent_key": compacted.agent_key,
                "status": compacted.status,
                "confidence": compacted.confidence,
                "title": compacted.title,
                "content": compacted.content,
                "source_refs": list(compacted.source_refs or []),
                "tags": list(compacted.tags or []),
                "metadata": dict(compacted.metadata_json or {}),
            }
        jobs_response = client.get("/api/v1/runtime/jobs", params={"job_type": MEMORY_COMPACTION_JOB_TYPE})

    assert processed is True
    assert job_state["status"] == "succeeded"
    assert job_state["lease_until"] is None
    assert job_state["last_error"] == ""
    assert job_state["payload"]["operation"] == "memory_compaction"
    assert job_state["payload"]["target_namespace"] == "evaluation"
    assert job_state["payload"]["target_memory_type"] == "artifact"
    assert job_state["payload"]["source_memory_count"] == 2
    assert job_state["metrics"]["compacted_memory_id"] == job_state["payload"]["compacted_memory_id"]
    assert job_state["metrics"]["source_memory_count"] == 2

    assert compacted_state["namespace"] == "evaluation"
    assert compacted_state["memory_type"] == "artifact"
    assert compacted_state["agent_key"] == "pskill.evaluator"
    assert compacted_state["status"] == "pending_review"
    assert compacted_state["confidence"] == 85
    assert compacted_state["title"] == "Evaluation governance compacted memory"
    assert "Runtime provider failure replay" in compacted_state["content"]
    assert "Governance proposal boundary" in compacted_state["content"]
    assert [ref["id"] for ref in compacted_state["source_refs"]] == ["memory-source-2", "memory-source-1"]
    assert {"compacted", "governance", "replay", "runtime"} <= set(compacted_state["tags"])
    assert compacted_state["metadata"]["schema"] == "psop-memory-compaction/v1"
    assert compacted_state["metadata"]["source_memory_count"] == 2
    assert source_statuses == {"memory-source-1": "archived", "memory-source-2": "archived"}

    assert jobs_response.status_code == 200
    compaction_job = jobs_response.json()[0]
    assert compaction_job["id"] == job_id
    assert compaction_job["progress"]["percent"] == 100
    assert compaction_job["progress"]["label"] == "记忆压缩完成"
    assert f"memory={job_state['payload']['compacted_memory_id']}" in compaction_job["progress"]["detail"]
    assert "namespace=evaluation" in compaction_job["progress"]["detail"]
    assert "type=artifact" in compaction_job["progress"]["detail"]
    assert "sources=2" in compaction_job["progress"]["detail"]
