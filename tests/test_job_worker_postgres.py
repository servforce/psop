from __future__ import annotations

import threading
import time
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url

from app.core.config import get_settings
from app.domain.jobs.models import RuntimeJob
from app.domain.jobs.repository import JobRepository
from app.domain.jobs.worker import JobAdvisoryLock, RuntimeJobWorkerSupervisor
from app.domain.runtime.events import PostgresRuntimeEventListener, PostgresRuntimeEventSink
from app.domain.skills.models import now_utc
from app.infra.database import DatabaseManager


@pytest.fixture(scope="module")
def postgres_job_store():
    base_url = get_settings().sqlalchemy_database_url
    if not base_url.startswith("postgresql"):
        pytest.skip("PostgreSQL integration database is not configured.")
    admin = DatabaseManager(base_url)
    try:
        admin.check_connection()
    except Exception:
        admin.dispose()
        pytest.skip("PostgreSQL integration database is unavailable.")

    schema = f"psop_test_{uuid.uuid4().hex[:12]}"
    with admin.engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    parsed = make_url(base_url)
    query = dict(parsed.query)
    query["options"] = f"-csearch_path={schema}"
    scoped_url = parsed.set(query=query).render_as_string(hide_password=False)
    manager = DatabaseManager(scoped_url)
    manager.create_schema()
    settings = get_settings().model_copy(
        update={
            "database_url": scoped_url,
            "runtime_worker_runtime_concurrency": 0,
            "runtime_worker_build_test_concurrency": 0,
            "runtime_worker_material_concurrency": 0,
            "otel_enabled": False,
        }
    )
    try:
        yield settings, manager
    finally:
        manager.dispose()
        with admin.engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def test_postgres_skip_locked_never_claims_one_job_twice(postgres_job_store) -> None:
    _, manager = postgres_job_store
    with manager.session() as session:
        job = RuntimeJob(job_type="runtime", status="pending", payload={}, dedupe_key=f"job:{uuid.uuid4()}")
        session.add(job)
        session.commit()
        job_id = job.id

    barrier = threading.Barrier(2)
    claims = []

    def claim(owner: str) -> None:
        barrier.wait()
        with manager.session() as session:
            claims.append(
                JobRepository().claim_next_job(
                    session,
                    job_types=("runtime",),
                    lease_seconds=60,
                    worker_name=owner,
                )
            )

    threads = [threading.Thread(target=claim, args=(f"owner-{index}",)) for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    claimed = [lease for lease in claims if lease is not None]
    assert [lease.job_id for lease in claimed] == [job_id]


def test_postgres_reaper_respects_live_advisory_lock(postgres_job_store) -> None:
    settings, manager = postgres_job_store
    with manager.session() as session:
        job = RuntimeJob(
            job_type="runtime",
            status="running",
            payload={"run_id": "missing-run"},
            dedupe_key=f"job:{uuid.uuid4()}",
            worker_name="dead-owner",
            lease_until=now_utc(),
            attempt_no=1,
            max_attempts=3,
        )
        session.add(job)
        session.commit()
        job_id = job.id

    lock = JobAdvisoryLock(manager.engine, job_id)
    assert lock.acquire() is True
    supervisor = RuntimeJobWorkerSupervisor(
        settings=settings,
        database_manager=manager,
        gitlab_gateway=object(),  # type: ignore[arg-type]
        inference_gateway=object(),  # type: ignore[arg-type]
        asr_gateway=object(),  # type: ignore[arg-type]
        object_store=object(),  # type: ignore[arg-type]
        agent_harness_service=object(),  # type: ignore[arg-type]
    )
    assert supervisor.recover_expired_jobs_once() == 0
    lock.release()
    assert supervisor.recover_expired_jobs_once() == 1

    with manager.session() as session:
        recovered = session.get(RuntimeJob, job_id)
        assert recovered.status == "retryable_failed"
        assert recovered.worker_name == ""


def test_postgres_notify_delivers_runtime_hint(postgres_job_store) -> None:
    settings, _ = postgres_job_store
    channel = f"psop_test_{uuid.uuid4().hex[:10]}"
    delivered = threading.Event()
    received = []
    listener = PostgresRuntimeEventListener(
        database_url=settings.sqlalchemy_database_url,
        channel=channel,
        source_id="api-process",
        callback=lambda event: (received.append(event), delivered.set()),
    )
    sink = PostgresRuntimeEventSink(
        database_url=settings.sqlalchemy_database_url,
        channel=channel,
        source_id="worker-process",
    )
    listener.start()
    try:
        deadline = time.monotonic() + 5
        while not delivered.is_set() and time.monotonic() < deadline:
            sink.publish({"event_type": "terminal.event.appended", "run_id": "run-1", "seq_no": 12})
            delivered.wait(0.2)
        assert delivered.is_set()
        assert received[-1]["event_type"] == "terminal.event.appended"
        assert received[-1]["run_id"] == "run-1"
        assert received[-1]["seq_no"] == 12
    finally:
        listener.close()
        sink.close()
