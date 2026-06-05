from __future__ import annotations

from datetime import timedelta

from app.jobs.models import RuntimeJob
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
                job_type="runtime",
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
                job_type="compile",
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
                job_type="runtime",
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
        assert stats["by_type"]["runtime"] == 1
        assert stats["by_type"]["compile"] == 1
