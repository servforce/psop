from __future__ import annotations

from datetime import timedelta

from app.jobs.models import RuntimeJob
from app.jobs.repository import JobRepository
from app.jobs.types import LEGACY_RUNTIME_JOB_TYPE, PSKILL_COMPILE_JOB_TYPE, RUNTIME_STEP_JOB_TYPE
from app.jobs.worker import RuntimeJobWorker
from app.pskills.models import now_utc
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

    assert exhausted_state["status"] == "failed"
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
