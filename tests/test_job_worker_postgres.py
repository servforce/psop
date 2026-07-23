from __future__ import annotations

import threading
import time
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url

from app.agent_harness.models.scripted_runner_chat_model import ScriptedRunnerChatModel
from app.agent_harness.service import AgentHarnessService
from app.core.config import get_settings
from app.domain.compiler.service import CompilerService
from app.domain.jobs.models import RuntimeJob
from app.domain.jobs.repository import JobLease, JobRepository
from app.domain.jobs.worker import JobAdvisoryLock, RuntimeJobWorkerSupervisor
from app.domain.runtime.events import PostgresRuntimeEventListener, PostgresRuntimeEventSink
from app.domain.runtime.schemas import AppendTerminalEventRequest, CreateInvocationRequest
from app.domain.runtime.service import RuntimeService, RuntimeStepTimeoutError
from app.domain.skills.models import now_utc
from app.domain.skills.schemas import CreateSkillRequest, PublishSkillRequest
from app.domain.skills.service import SkillsService
from app.infra.database import DatabaseManager
from tests.test_skills_api import FakeGitLabGateway, FakeInferenceGateway


class RaisingRuntimeHarness:
    def __init__(self, *, started: threading.Event | None = None, release: threading.Event | None = None) -> None:
        self.started = started
        self.release = release

    def invoke(self, *args, **kwargs):
        if self.started is not None:
            self.started.set()
        if self.release is not None and not self.release.wait(5):
            raise RuntimeError("Timed out waiting to release PostgreSQL runtime harness.")
        raise RuntimeStepTimeoutError("Runtime 节点执行超过总时限。")


@pytest.fixture(scope="module")
def postgres_job_store(tmp_path_factory):
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
            "agent_harness_sandbox_root": str(tmp_path_factory.mktemp("postgres-agent-runs")),
            "gitlab_skills_group_path": "skills",
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


def test_postgres_runtime_recovery_and_terminal_append_never_lose_wakeup(postgres_job_store) -> None:
    settings, manager = postgres_job_store
    inference_gateway = FakeInferenceGateway()
    compiler_service = CompilerService(
        settings=settings,
        gitlab_gateway=FakeGitLabGateway(),
        inference_gateway=inference_gateway,
    )
    skills_service = SkillsService(
        settings=settings,
        gitlab_gateway=compiler_service.gitlab_gateway,
        compiler_service=compiler_service,
    )
    runtime_service = RuntimeService(
        settings=settings,
        inference_gateway=inference_gateway,
        agent_harness_service=AgentHarnessService(
            settings=settings,
            chat_model_factory=lambda _definition: ScriptedRunnerChatModel(),
        ),
    )

    with manager.session() as session:
        skill = skills_service.create_skill(
            session,
            CreateSkillRequest(
                name="PostgreSQL Runtime Lost Wakeup",
                description="Validate runtime recovery lock ordering.",
            ),
        )
        published = skills_service.publish_skill(
            session,
            skill_id=skill.id,
            payload=PublishSkillRequest(publish_reason="PostgreSQL runtime lost wakeup publish"),
        )
        compile_job = JobRepository().get_compile_job(session, published.compile_request.id)
        assert compile_job is not None
        compile_job.status = "running"
        compile_job.attempt_no += 1
        session.commit()
        compiler_service.process_compile_job(session, compile_job.id)

    def create_waiting_run(suffix: str) -> tuple[str, int]:
        with manager.session() as session:
            invocation = runtime_service.create_invocation(
                session,
                CreateInvocationRequest(skill_key=skill.key, terminal_context={"terminal_kind": "web"}),
            )
            run_id = invocation.run_id or ""
            runtime_service.process_run(session, run_id)
            failed_input = runtime_service.append_terminal_event(
                session,
                run_id,
                AppendTerminalEventRequest(
                    direction="input",
                    event_kind="terminal.text.input.v1",
                    mime_type="text/plain",
                    payload_inline=f"触发 {suffix} 恢复",
                    external_event_id=f"postgres-runtime-failed-input-{suffix}",
                ),
            )
            return run_id, failed_input.seq_no

    def claim_runtime_job(run_id: str, owner: str) -> JobLease:
        with manager.session() as session:
            lease = JobRepository().claim_next_job(
                session,
                job_types=("runtime",),
                lease_seconds=60,
                worker_name=owner,
            )
            assert lease is not None
            assert lease.run_id == run_id
            return lease

    def process_in_thread(
        service: RuntimeService,
        run_id: str,
        lease: JobLease,
    ) -> tuple[threading.Thread, list[BaseException]]:
        errors: list[BaseException] = []

        def process() -> None:
            try:
                with manager.session() as session:
                    service.process_run(session, run_id, job_lease=lease)
            except BaseException as exc:  # pragma: no cover - asserted below
                errors.append(exc)

        thread = threading.Thread(target=process)
        thread.start()
        return thread, errors

    input_first_run_id, input_first_cursor = create_waiting_run("input-first")
    actor_started = threading.Event()
    release_actor = threading.Event()
    input_first_lease = claim_runtime_job(input_first_run_id, "postgres-runtime-input-first")
    input_first_runtime = RuntimeService(
        settings=settings,
        inference_gateway=inference_gateway,
        agent_harness_service=RaisingRuntimeHarness(started=actor_started, release=release_actor),
        lease_is_healthy=lambda: True,
    )
    input_first_thread, input_first_errors = process_in_thread(
        input_first_runtime,
        input_first_run_id,
        input_first_lease,
    )
    assert actor_started.wait(5)
    with manager.session() as session:
        concurrent_input = runtime_service.append_terminal_event(
            session,
            input_first_run_id,
            AppendTerminalEventRequest(
                direction="input",
                event_kind="terminal.text.input.v1",
                mime_type="text/plain",
                payload_inline="恢复事务锁定前到达的输入",
                external_event_id="postgres-runtime-input-first-concurrent",
            ),
        )
    release_actor.set()
    input_first_thread.join(timeout=5)
    assert not input_first_thread.is_alive()
    assert input_first_errors == []
    with manager.session() as session:
        snapshot = input_first_runtime.repository.list_snapshots(session, input_first_run_id)[-1]
        job = JobRepository().get_runtime_job_by_dedupe_key(session, f"job:runtime:{input_first_run_id}")
        assert snapshot.token_payload["metadata"]["terminal_cursor"] == input_first_cursor
        assert concurrent_input.seq_no > input_first_cursor
        assert job is not None
        assert job.status == "pending"
        assert job.attempt_no == 0
        job.status = "succeeded"
        session.commit()

    recovery_first_run_id, _ = create_waiting_run("recovery-first")
    recovery_locked = threading.Event()
    release_recovery = threading.Event()
    recovery_first_lease = claim_runtime_job(recovery_first_run_id, "postgres-runtime-recovery-first")
    recovery_first_runtime = RuntimeService(
        settings=settings,
        inference_gateway=inference_gateway,
        agent_harness_service=RaisingRuntimeHarness(),
        lease_is_healthy=lambda: True,
    )
    append_recovery_output = recovery_first_runtime._append_runtime_recoverable_failure_terminal_event

    def gated_recovery_output(session, *, run, trace_event):
        recovery_locked.set()
        if not release_recovery.wait(5):
            raise RuntimeError("Timed out waiting to release PostgreSQL recovery transaction.")
        return append_recovery_output(session, run=run, trace_event=trace_event)

    recovery_first_runtime._append_runtime_recoverable_failure_terminal_event = gated_recovery_output  # type: ignore[method-assign]
    recovery_thread, recovery_errors = process_in_thread(
        recovery_first_runtime,
        recovery_first_run_id,
        recovery_first_lease,
    )
    assert recovery_locked.wait(5)
    append_started = threading.Event()
    append_errors: list[BaseException] = []

    def append_after_recovery_lock() -> None:
        append_started.set()
        try:
            with manager.session() as session:
                runtime_service.append_terminal_event(
                    session,
                    recovery_first_run_id,
                    AppendTerminalEventRequest(
                        direction="input",
                        event_kind="terminal.text.input.v1",
                        mime_type="text/plain",
                        payload_inline="恢复事务锁定后到达的输入",
                        external_event_id="postgres-runtime-recovery-first-concurrent",
                    ),
                )
        except BaseException as exc:  # pragma: no cover - asserted below
            append_errors.append(exc)

    append_thread = threading.Thread(target=append_after_recovery_lock)
    append_thread.start()
    assert append_started.wait(5)
    time.sleep(0.1)
    release_recovery.set()
    recovery_thread.join(timeout=5)
    append_thread.join(timeout=5)
    assert not recovery_thread.is_alive()
    assert not append_thread.is_alive()
    assert recovery_errors == []
    assert append_errors == []
    with manager.session() as session:
        job = JobRepository().get_runtime_job_by_dedupe_key(session, f"job:runtime:{recovery_first_run_id}")
        assert job is not None
        assert job.status == "pending"
        assert job.attempt_no == 0


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

        delivered.clear()
        sink.publish({"event_type": "run.task_status.updated", "run_id": "run-1", "seq_no": 0})
        assert delivered.wait(5)
        assert received[-1]["event_type"] == "run.task_status.updated"
        assert received[-1]["run_id"] == "run-1"
        assert received[-1]["snapshot_seq"] == 0
        assert "payload" not in received[-1]
    finally:
        listener.close()
        sink.close()
