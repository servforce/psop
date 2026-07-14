from __future__ import annotations

import asyncio
import threading
from dataclasses import FrozenInstanceError, replace
from datetime import timedelta, timezone

import pytest
from sqlalchemy import event as sqlalchemy_event

from app.core.config import Settings
from app.domain.compiler.models import SkillCompileRequest
from app.domain.compiler.service import CompilerService
from app.domain.jobs.models import RuntimeJob
from app.domain.jobs.repository import JobLease, JobRepository
from app.domain.jobs.worker import (
    BUILD_TEST_JOB_TYPES,
    MATERIAL_JOB_TYPES,
    RUNTIME_JOB_TYPES,
    JobAdvisoryLock,
    RuntimeJobWorker,
    RuntimeJobWorkerSupervisor,
    advisory_lock_key,
    build_attempt_owner,
    default_worker_pool_specs,
    _install_session_lease_fence,
)
from app.domain.runtime.events import NoopRuntimeEventSink
from app.domain.runtime.models import Run, SkillInvocation, TerminalEvent, TerminalSession, TraceEvent
from app.domain.runtime.service import RuntimeService
from app.domain.skill_tests.models import SkillTestScenarioRun
from app.domain.skills.models import (
    SkillDefinition,
    SkillPublishRecord,
    SkillRawMaterial,
    SkillRawMaterialAnalysis,
    SkillRawMaterialGeneration,
    SkillVersion,
    now_utc,
)
from app.infra.database import DatabaseManager


@pytest.fixture
def job_store() -> tuple[Settings, DatabaseManager]:
    settings = Settings(
        database_url="sqlite+pysqlite:///:memory:",
        database_auto_create_schema=False,
        database_check_on_startup=False,
        runtime_worker_enabled=False,
        runtime_worker_runtime_concurrency=0,
        runtime_worker_build_test_concurrency=0,
        runtime_worker_material_concurrency=0,
        otel_enabled=False,
    )
    manager = DatabaseManager(settings.sqlalchemy_database_url)
    manager.create_schema()
    try:
        yield settings, manager
    finally:
        manager.dispose()


def _seed_exhausted_domain_jobs(session, *, status: str = "running") -> dict[str, str]:
    scenario_run = SkillTestScenarioRun(
        skill_definition_id="skill-domain-finalizers",
        scenario_id="scenario-domain-finalizers",
        status="running",
        driver_status="waiting_time",
        result_summary={"status": "running"},
    )
    material = SkillRawMaterial(
        skill_definition_id="skill-domain-finalizers",
        artifact_object_id="artifact-domain-finalizers",
        name="source.mp4",
        material_kind="video",
        mime_type="video/mp4",
        filename="source.mp4",
        status="processing",
    )
    generation = SkillRawMaterialGeneration(
        skill_definition_id="skill-domain-finalizers",
        status="running",
        raw_response={"request": {"kind": "test"}},
    )
    session.add_all([scenario_run, material, generation])
    session.flush()
    analysis = SkillRawMaterialAnalysis(
        skill_definition_id="skill-domain-finalizers",
        raw_material_id=material.id,
        status="running",
    )
    session.add(analysis)
    session.flush()

    expired_at = now_utc() - timedelta(seconds=5) if status == "running" else None
    worker_name = "dead-domain-owner" if status == "running" else ""
    jobs = [
        RuntimeJob(
            job_type="skill_test_timeline_driver",
            status=status,
            payload={"scenario_run_id": scenario_run.id},
            dedupe_key=f"job:test-driver-finalizer:{scenario_run.id}",
            worker_name=worker_name,
            lease_until=expired_at,
            attempt_no=1,
            max_attempts=1,
        ),
        RuntimeJob(
            job_type="raw_material_analysis",
            status=status,
            payload={"analysis_id": analysis.id, "material_id": material.id},
            dedupe_key=f"job:material-analysis-finalizer:{analysis.id}",
            worker_name=worker_name,
            lease_until=expired_at,
            attempt_no=1,
            max_attempts=1,
        ),
        RuntimeJob(
            job_type="skill_raw_material_generation",
            status=status,
            payload={"generation_id": generation.id},
            dedupe_key=f"job:material-generation-finalizer:{generation.id}",
            worker_name=worker_name,
            lease_until=expired_at,
            attempt_no=1,
            max_attempts=1,
        ),
    ]
    session.add_all(jobs)
    session.flush()
    return {
        "scenario_run_id": scenario_run.id,
        "material_id": material.id,
        "analysis_id": analysis.id,
        "generation_id": generation.id,
        "driver_job_id": jobs[0].id,
        "analysis_job_id": jobs[1].id,
        "generation_job_id": jobs[2].id,
    }


def _domain_finalizer_supervisor(settings: Settings, manager: DatabaseManager) -> RuntimeJobWorkerSupervisor:
    return RuntimeJobWorkerSupervisor(
        settings=settings,
        database_manager=manager,
        gitlab_gateway=object(),  # type: ignore[arg-type]
        inference_gateway=object(),  # type: ignore[arg-type]
        asr_gateway=object(),  # type: ignore[arg-type]
        object_store=object(),  # type: ignore[arg-type]
        agent_harness_service=object(),  # type: ignore[arg-type]
    )


def test_pool_claim_is_fifo_across_job_types_and_returns_immutable_lease(job_store) -> None:
    _, manager = job_store
    repository = JobRepository()
    now = now_utc()
    with manager.session() as session:
        excluded = RuntimeJob(
            job_type="runtime",
            status="pending",
            payload={},
            dedupe_key="job:excluded-runtime",
            available_at=now - timedelta(minutes=5),
            created_at=now - timedelta(minutes=5),
        )
        oldest_in_pool = RuntimeJob(
            job_type="skill_test_timeline_driver",
            status="pending",
            payload={},
            dedupe_key="job:oldest-build-test",
            available_at=now - timedelta(minutes=3),
            created_at=now - timedelta(minutes=3),
        )
        newer_in_pool = RuntimeJob(
            job_type="compile",
            status="pending",
            payload={},
            dedupe_key="job:newer-build-test",
            available_at=now - timedelta(minutes=2),
            created_at=now - timedelta(minutes=2),
        )
        session.add_all([excluded, oldest_in_pool, newer_in_pool])
        session.commit()

        lease = repository.claim_next_job(
            session,
            job_types=BUILD_TEST_JOB_TYPES,
            lease_seconds=60,
            worker_name="host:123:build-test:0:attempt-a",
        )

        assert lease is not None
        assert lease.job_id == oldest_in_pool.id
        assert lease.job_type == "skill_test_timeline_driver"
        assert lease.attempt_no == 1
        assert lease.owner == "host:123:build-test:0:attempt-a"
        assert session.get(RuntimeJob, excluded.id).status == "pending"
        with pytest.raises(FrozenInstanceError):
            lease.owner = "changed"  # type: ignore[misc]

        second = repository.claim_next_job(
            session,
            job_type="compile",
            lease_seconds=60,
            worker_name="host:123:build-test:0:attempt-b",
        )
        assert second is not None
        assert second.job_id == newer_in_pool.id


def test_heartbeat_uses_owner_cas_and_stale_recovery_candidate_is_rejected(job_store) -> None:
    _, manager = job_store
    repository = JobRepository()
    with manager.session() as session:
        job = RuntimeJob(
            job_type="runtime",
            status="pending",
            payload={},
            dedupe_key="job:heartbeat",
        )
        session.add(job)
        session.commit()
        lease = repository.claim_next_job(
            session,
            job_types=RUNTIME_JOB_TYPES,
            lease_seconds=1,
            worker_name="owner-a",
        )
        assert lease is not None

        wrong_owner = replace(lease, owner="owner-b")
        assert repository.renew_lease(session, wrong_owner, lease_seconds=60) is None
        renewed = repository.renew_lease(session, lease, lease_seconds=60)
        assert renewed is not None
        assert renewed.lease_until > lease.lease_until

        stale_candidate = replace(lease, lease_until=now_utc() - timedelta(seconds=1))
        assert repository.recover_expired_lease(session, stale_candidate, retry_delay_seconds=5) is None
        stored = session.get(RuntimeJob, job.id)
        assert stored.status == "running"
        assert stored.worker_name == "owner-a"


def test_non_runtime_session_fence_rejects_stale_attempt_owner(job_store) -> None:
    _, manager = job_store
    with manager.session() as session:
        job = RuntimeJob(
            job_type="compile",
            status="running",
            payload={},
            dedupe_key="job:stale-compile-owner",
            worker_name="owner-b",
            lease_until=now_utc() + timedelta(seconds=60),
            attempt_no=2,
            max_attempts=3,
        )
        session.add(job)
        session.commit()
        lease = JobLease(
            job_id=job.id,
            job_type=job.job_type,
            owner="owner-a",
            attempt_no=1,
            max_attempts=3,
            lease_until=now_utc() + timedelta(seconds=60),
        )

    with manager.session() as session:
        listener = _install_session_lease_fence(session, lease)
        try:
            with pytest.raises(RuntimeError, match="stale attempt write was fenced"):
                session.get(RuntimeJob, lease.job_id)
        finally:
            sqlalchemy_event.remove(session, "after_begin", listener.after_begin)
            sqlalchemy_event.remove(session, "before_commit", listener.before_commit)


def test_non_runtime_session_fence_rejects_lost_health_before_commit(job_store) -> None:
    _, manager = job_store
    with manager.session() as session:
        job = RuntimeJob(
            job_type="compile",
            status="running",
            payload={"value": "before"},
            dedupe_key="job:lost-health-compile",
            worker_name="owner-a",
            lease_until=now_utc() + timedelta(seconds=60),
            attempt_no=1,
            max_attempts=3,
        )
        session.add(job)
        session.commit()
        lease = JobLease.from_job(job)

    healthy = True
    with manager.session() as session:
        listener = _install_session_lease_fence(
            session,
            lease,
            lease_is_healthy=lambda: healthy,
        )
        try:
            stored = session.get(RuntimeJob, lease.job_id)
            assert stored is not None
            stored.payload = {"value": "stale"}
            healthy = False
            with pytest.raises(RuntimeError, match="lease health was lost"):
                session.commit()
            session.rollback()
        finally:
            sqlalchemy_event.remove(session, "after_begin", listener.after_begin)
            sqlalchemy_event.remove(session, "before_commit", listener.before_commit)

    with manager.session() as session:
        assert session.get(RuntimeJob, lease.job_id).payload == {"value": "before"}


def test_claim_skips_jobs_that_exhausted_attempt_budget(job_store) -> None:
    _, manager = job_store
    with manager.session() as session:
        session.add(
            RuntimeJob(
                job_type="runtime",
                status="retryable_failed",
                payload={},
                dedupe_key="job:attempts-exhausted",
                attempt_no=3,
                max_attempts=3,
            )
        )
        session.commit()

        assert JobRepository().claim_next_job(
            session,
            job_types=RUNTIME_JOB_TYPES,
            lease_seconds=60,
            worker_name="owner-never",
        ) is None


def test_reaper_retries_expired_jobs_and_runs_exhaustion_finalizers(job_store, monkeypatch) -> None:
    settings, manager = job_store
    expired_at = now_utc() - timedelta(seconds=5)
    with manager.session() as session:
        retryable = RuntimeJob(
            job_type="runtime",
            status="running",
            payload={"run_id": "run-retry"},
            dedupe_key="job:expired-retry",
            worker_name="dead-owner-a",
            lease_until=expired_at,
            attempt_no=1,
            max_attempts=3,
        )
        exhausted = RuntimeJob(
            job_type="compile",
            status="running",
            payload={"current_stage": "agent_compiling"},
            dedupe_key="job:expired-exhausted",
            worker_name="dead-owner-b",
            lease_until=expired_at,
            attempt_no=3,
            max_attempts=3,
        )
        exhausted_runtime = RuntimeJob(
            job_type="runtime",
            status="running",
            payload={"run_id": "run-exhausted"},
            run_id=None,
            dedupe_key="job:expired-runtime-exhausted",
            worker_name="dead-owner-c",
            lease_until=expired_at,
            attempt_no=3,
            max_attempts=3,
        )
        session.add_all([retryable, exhausted, exhausted_runtime])
        session.commit()
        retryable_id = retryable.id
        exhausted_id = exhausted.id
        exhausted_runtime_id = exhausted_runtime.id

    supervisor = RuntimeJobWorkerSupervisor(
        settings=settings,
        database_manager=manager,
        gitlab_gateway=object(),  # type: ignore[arg-type]
        inference_gateway=object(),  # type: ignore[arg-type]
        asr_gateway=object(),  # type: ignore[arg-type]
        object_store=object(),  # type: ignore[arg-type]
        agent_harness_service=object(),  # type: ignore[arg-type]
    )
    assert supervisor.workers == []
    finalizer_calls: list[tuple[str, str]] = []

    class RuntimeFinalizer:
        def finalize_exhausted_job(self, _session, *, job_id: str, error_message: str) -> bool:
            finalizer_calls.append((job_id, error_message))
            return True

    monkeypatch.setattr(supervisor, "_runtime_service", lambda: RuntimeFinalizer())
    assert supervisor.recover_expired_jobs_once() == 3

    with manager.session() as session:
        retried = session.get(RuntimeJob, retryable_id)
        failed = session.get(RuntimeJob, exhausted_id)
        failed_runtime = session.get(RuntimeJob, exhausted_runtime_id)
        assert retried.status == "retryable_failed"
        assert retried.worker_name == ""
        assert retried.lease_until is None
        available_at = retried.available_at
        if available_at.tzinfo is None:
            available_at = available_at.replace(tzinfo=timezone.utc)
        assert available_at > now_utc()
        assert failed.status == "failed"
        assert failed.worker_name == ""
        assert failed.lease_until is None
        assert failed.payload["terminal"] is True
        assert failed.payload["terminal_status"] == "failed"
        assert failed_runtime.status == "failed"
        assert finalizer_calls == [
            (exhausted_runtime_id, "worker lease expired (attempt 3/3)"),
        ]


def test_domain_exhaustion_finalizers_leave_transaction_control_to_caller(job_store) -> None:
    settings, manager = job_store
    with manager.session() as session:
        ids = _seed_exhausted_domain_jobs(session, status="failed")
        session.commit()

    supervisor = _domain_finalizer_supervisor(settings, manager)
    with manager.session() as session:
        assert supervisor._skill_test_service().finalize_exhausted_timeline_driver_job(
            session,
            job_id=ids["driver_job_id"],
            error_message="driver attempts exhausted",
        ) is True
        skills_service = supervisor._skills_service()
        assert skills_service.finalize_exhausted_raw_material_analysis_job(
            session,
            job_id=ids["analysis_job_id"],
            error_message="analysis attempts exhausted",
        ) is True
        assert skills_service.finalize_exhausted_raw_material_generation_job(
            session,
            job_id=ids["generation_job_id"],
            error_message="generation attempts exhausted",
        ) is True
        session.rollback()

    with manager.session() as session:
        scenario_run = session.get(SkillTestScenarioRun, ids["scenario_run_id"])
        material = session.get(SkillRawMaterial, ids["material_id"])
        analysis = session.get(SkillRawMaterialAnalysis, ids["analysis_id"])
        generation = session.get(SkillRawMaterialGeneration, ids["generation_id"])
        assert scenario_run.status == "running"
        assert scenario_run.driver_status == "waiting_time"
        assert material.status == "processing"
        assert analysis.status == "running"
        assert generation.status == "running"


def test_reaper_finalizes_domain_records_and_finalizers_are_idempotent(job_store) -> None:
    settings, manager = job_store
    with manager.session() as session:
        ids = _seed_exhausted_domain_jobs(session)
        session.commit()

    supervisor = _domain_finalizer_supervisor(settings, manager)
    assert supervisor.recover_expired_jobs_once() == 3

    with manager.session() as session:
        scenario_run = session.get(SkillTestScenarioRun, ids["scenario_run_id"])
        material = session.get(SkillRawMaterial, ids["material_id"])
        analysis = session.get(SkillRawMaterialAnalysis, ids["analysis_id"])
        generation = session.get(SkillRawMaterialGeneration, ids["generation_id"])
        jobs = [
            session.get(RuntimeJob, ids["driver_job_id"]),
            session.get(RuntimeJob, ids["analysis_job_id"]),
            session.get(RuntimeJob, ids["generation_job_id"]),
        ]

        assert all(job.status == "failed" for job in jobs)
        assert scenario_run.status == "failed"
        assert scenario_run.driver_status == "failed"
        assert scenario_run.ended_at is not None
        assert scenario_run.result_summary["reason"] == "timeline_driver_job_attempts_exhausted"
        assert material.status == "failed"
        assert analysis.status == "failed"
        assert analysis.ended_at is not None
        assert analysis.error_details["error_type"] == "JobAttemptsExhausted"
        assert generation.status == "failed"
        assert generation.raw_response["error_type"] == "JobAttemptsExhausted"
        assert jobs[2].payload["current_stage"] == "failed"

        assert supervisor._skill_test_service().finalize_exhausted_timeline_driver_job(
            session,
            job_id=ids["driver_job_id"],
            error_message="second call",
        ) is False
        skills_service = supervisor._skills_service()
        assert skills_service.finalize_exhausted_raw_material_analysis_job(
            session,
            job_id=ids["analysis_job_id"],
            error_message="second call",
        ) is False
        assert skills_service.finalize_exhausted_raw_material_generation_job(
            session,
            job_id=ids["generation_job_id"],
            error_message="second call",
        ) is False


def test_worker_run_once_keeps_compatibility_and_uses_unique_owner(job_store, monkeypatch) -> None:
    settings, manager = job_store
    settings.runtime_job_heartbeat_seconds = 20
    with manager.session() as session:
        job = RuntimeJob(
            job_type="custom_test_job",
            status="pending",
            payload={},
            dedupe_key="job:run-once-compatibility",
        )
        session.add(job)
        session.commit()
        job_id = job.id

    worker = RuntimeJobWorker(
        settings=settings,
        database_manager=manager,
        gitlab_gateway=object(),  # type: ignore[arg-type]
        inference_gateway=object(),  # type: ignore[arg-type]
        asr_gateway=object(),  # type: ignore[arg-type]
        object_store=object(),  # type: ignore[arg-type]
        agent_harness_service=object(),  # type: ignore[arg-type]
        job_types=("custom_test_job",),
        pool_name="test-pool",
        slot=7,
    )
    captured: dict[str, JobLease] = {}

    def complete(lease: JobLease, *, lease_is_healthy) -> None:
        assert lease_is_healthy()
        captured["lease"] = lease
        with manager.session() as session:
            stored = session.get(RuntimeJob, lease.job_id)
            assert stored.worker_name == lease.owner
            stored.status = "succeeded"
            stored.lease_until = None
            session.commit()

    monkeypatch.setattr(worker, "_process_job", complete)

    assert worker.run_once() is True
    assert worker.run_once() is False
    lease = captured["lease"]
    assert lease.job_id == job_id
    assert ":test-pool:7:" in lease.owner
    assert len(lease.owner) <= 160


def test_reaper_rolls_back_exhaustion_when_domain_finalizer_fails(job_store, monkeypatch) -> None:
    settings, manager = job_store
    with manager.session() as session:
        job = RuntimeJob(
            job_type="runtime",
            status="running",
            payload={"run_id": "run-finalizer-failure"},
            dedupe_key="job:runtime-finalizer-failure",
            worker_name="dead-owner",
            lease_until=now_utc() - timedelta(seconds=5),
            attempt_no=1,
            max_attempts=1,
        )
        session.add(job)
        session.commit()
        job_id = job.id

    supervisor = RuntimeJobWorkerSupervisor(
        settings=settings,
        database_manager=manager,
        gitlab_gateway=object(),  # type: ignore[arg-type]
        inference_gateway=object(),  # type: ignore[arg-type]
        asr_gateway=object(),  # type: ignore[arg-type]
        object_store=object(),  # type: ignore[arg-type]
        agent_harness_service=object(),  # type: ignore[arg-type]
    )

    class BrokenFinalizer:
        def finalize_exhausted_job(self, _session, *, job_id: str, error_message: str) -> bool:
            raise RuntimeError(f"cannot finalize {job_id}: {error_message}")

    monkeypatch.setattr(supervisor, "_runtime_service", lambda: BrokenFinalizer())
    assert supervisor.recover_expired_jobs_once() == 0

    with manager.session() as session:
        stored = session.get(RuntimeJob, job_id)
        assert stored.status == "running"
        assert stored.worker_name == "dead-owner"
        assert stored.lease_until is not None


def test_worker_cancellation_waits_for_active_handler(job_store, monkeypatch) -> None:
    settings, manager = job_store
    worker = RuntimeJobWorker(
        settings=settings,
        database_manager=manager,
        gitlab_gateway=object(),  # type: ignore[arg-type]
        inference_gateway=object(),  # type: ignore[arg-type]
        asr_gateway=object(),  # type: ignore[arg-type]
        object_store=object(),  # type: ignore[arg-type]
        agent_harness_service=object(),  # type: ignore[arg-type]
        job_types=("custom_test_job",),
    )
    started = threading.Event()
    release = threading.Event()

    def active_run_once() -> bool:
        started.set()
        release.wait(timeout=2)
        return True

    monkeypatch.setattr(worker, "run_once", active_run_once)

    async def scenario() -> None:
        task = asyncio.create_task(worker.run_forever())
        assert await asyncio.to_thread(started.wait, 1)
        task.cancel()
        await asyncio.sleep(0.02)
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())


def test_runtime_exhaustion_finalizer_closes_domain_state_idempotently(job_store) -> None:
    settings, manager = job_store
    with manager.session() as session:
        invocation = SkillInvocation(
            skill_definition_id="skill-finalizer",
            skill_version_id="version-finalizer",
            compile_artifact_id="artifact-finalizer",
            status="running",
        )
        session.add(invocation)
        session.flush()
        run = Run(
            invocation_id=invocation.id,
            skill_definition_id=invocation.skill_definition_id,
            skill_version_id=invocation.skill_version_id,
            compile_artifact_id=invocation.compile_artifact_id,
            status="running",
            runtime_phase="evaluation",
        )
        session.add(run)
        session.flush()
        terminal_session = TerminalSession(run_id=run.id, status="open")
        session.add(terminal_session)
        session.flush()
        run.terminal_session_id = terminal_session.id
        job = RuntimeJob(
            job_type="runtime",
            status="failed",
            payload={"run_id": run.id},
            run_id=run.id,
            dedupe_key=f"job:runtime:{run.id}",
            attempt_no=3,
            max_attempts=3,
            last_error="runtime step timeout",
        )
        session.add(job)
        session.commit()
        run_id = run.id
        invocation_id = invocation.id
        terminal_session_id = terminal_session.id
        job_id = job.id

        service = RuntimeService(
            settings=settings,
            inference_gateway=object(),  # type: ignore[arg-type]
            object_store=None,
            agent_harness_service=object(),  # type: ignore[arg-type]
            runtime_event_sink=NoopRuntimeEventSink(),
        )
        assert service.finalize_exhausted_job(
            session,
            job_id=job_id,
            error_message="runtime step timeout",
        ) is True
        assert service.finalize_exhausted_job(
            session,
            job_id=job_id,
            error_message="runtime step timeout",
        ) is False

        stored_run = session.get(Run, run_id)
        stored_invocation = session.get(SkillInvocation, invocation_id)
        stored_terminal_session = session.get(TerminalSession, terminal_session_id)
        traces = session.query(TraceEvent).filter(TraceEvent.run_id == run_id).all()
        outputs = session.query(TerminalEvent).filter(TerminalEvent.run_id == run_id).all()
        assert stored_run.status == "failed"
        assert stored_run.runtime_phase == "failed"
        assert stored_run.ended_at is not None
        assert stored_invocation.status == "failed"
        assert stored_terminal_session.status == "closed"
        assert [trace.event_type for trace in traces] == ["runtime.job.attempts_exhausted"]
        assert len(outputs) == 1
        assert outputs[0].direction == "output"


def test_compile_exhaustion_finalizer_closes_request_and_publish_record(job_store) -> None:
    settings, manager = job_store
    with manager.session() as session:
        definition = SkillDefinition(
            key="compile-finalizer",
            name="Compile Finalizer",
            gitlab_project_id="compile-finalizer-project",
            repository_url="https://example.test/compile-finalizer.git",
        )
        session.add(definition)
        session.flush()
        version = SkillVersion(
            skill_definition_id=definition.id,
            version_no=1,
            status="published",
            source_ref="main",
            source_commit_sha="commit-finalizer",
        )
        session.add(version)
        session.flush()
        publish = SkillPublishRecord(
            skill_definition_id=definition.id,
            skill_version_id=version.id,
            publish_status="compiling",
            published_commit_sha="commit-finalizer",
            release_ref="release-finalizer",
        )
        request = SkillCompileRequest(
            skill_definition_id=definition.id,
            skill_version_id=version.id,
            trigger_type="publish",
            source_commit_sha="commit-finalizer",
            status="running",
            dedupe_key="compile-finalizer-request",
        )
        session.add_all([publish, request])
        session.flush()
        job = RuntimeJob(
            job_type="compile",
            status="failed",
            payload={"publish_record_id": publish.id},
            compile_request_id=request.id,
            dedupe_key="job:compile-finalizer-request",
            attempt_no=3,
            max_attempts=3,
        )
        session.add(job)
        session.commit()

        service = CompilerService(
            settings=settings,
            gitlab_gateway=object(),  # type: ignore[arg-type]
            inference_gateway=object(),  # type: ignore[arg-type]
            agent_harness_service=object(),  # type: ignore[arg-type]
        )
        assert service.finalize_exhausted_compile_job(
            session,
            job_id=job.id,
            error_message="compiler attempts exhausted",
        ) is True
        session.commit()
        assert service.finalize_exhausted_compile_job(
            session,
            job_id=job.id,
            error_message="compiler attempts exhausted",
        ) is False
        assert session.get(SkillCompileRequest, request.id).status == "failed"
        assert session.get(SkillCompileRequest, request.id).error_message == "compiler attempts exhausted"
        assert session.get(SkillPublishRecord, publish.id).publish_status == "failed"


def test_pool_topology_owner_and_advisory_key_are_stable(job_store) -> None:
    settings, manager = job_store
    settings.runtime_worker_runtime_concurrency = 2
    settings.runtime_worker_build_test_concurrency = 1
    settings.runtime_worker_material_concurrency = 1
    pools = default_worker_pool_specs(settings)
    assert [(pool.name, pool.job_types, pool.concurrency) for pool in pools] == [
        ("runtime-interactive", RUNTIME_JOB_TYPES, 2),
        ("build-test", BUILD_TEST_JOB_TYPES, 1),
        ("material", MATERIAL_JOB_TYPES, 1),
    ]

    owner_a = build_attempt_owner("runtime-interactive", 0)
    owner_b = build_attempt_owner("runtime-interactive", 0)
    assert owner_a != owner_b
    assert len(owner_a) <= 160
    assert advisory_lock_key("job-1") == advisory_lock_key("job-1")
    assert advisory_lock_key("job-1") != advisory_lock_key("job-2")

    lock = JobAdvisoryLock(manager.engine, "job-1")
    assert lock.acquire() is True
    assert lock.is_alive() is True
    lock.release()
    assert lock.is_alive() is False
