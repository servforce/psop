from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import socket
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Sequence

from sqlalchemy import event as sqlalchemy_event, select, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.agent_harness.service import AgentHarnessService
from app.core.config import Settings
from app.core.logging import log_context
from app.core.observability import (
    add_metric_counter,
    add_metric_up_down_counter,
    record_metric_histogram,
    record_span_exception,
    start_span,
)
from app.domain.compiler.service import CompilerService
from app.domain.jobs.models import RuntimeJob
from app.domain.jobs.progress import ensure_publish_progress_payload, mark_publish_stage
from app.domain.jobs.repository import JobLease, JobRepository
from app.domain.runtime.events import NoopRuntimeEventSink, RuntimeEventSink
from app.domain.runtime.service import RuntimeService, RuntimeStepTimeoutError
from app.domain.skill_tests.service import SkillTestService
from app.domain.skills.models import now_utc
from app.domain.skills.service import SKILL_RAW_MATERIAL_GENERATION_JOB_TYPE, SkillsService
from app.gateway.asr import AsrGateway
from app.gateway.gitlab import GitLabSkillSourceGateway
from app.gateway.inference import LlmInferenceGateway
from app.infra.database import DatabaseManager
from app.infra.object_store import ObjectStoreService


LOGGER = logging.getLogger(__name__)

RUNTIME_JOB_TYPES = ("runtime",)
BUILD_TEST_JOB_TYPES = ("compile", "skill_test_timeline_driver")
MATERIAL_JOB_TYPES = ("raw_material_analysis", SKILL_RAW_MATERIAL_GENERATION_JOB_TYPE)
ALL_JOB_TYPES = RUNTIME_JOB_TYPES + BUILD_TEST_JOB_TYPES + MATERIAL_JOB_TYPES


@dataclass(frozen=True, slots=True)
class WorkerPoolSpec:
    name: str
    job_types: tuple[str, ...]
    concurrency: int


def default_worker_pool_specs(settings: Settings) -> tuple[WorkerPoolSpec, ...]:
    return (
        WorkerPoolSpec(
            name="runtime-interactive",
            job_types=RUNTIME_JOB_TYPES,
            concurrency=max(0, int(getattr(settings, "runtime_worker_runtime_concurrency", 2))),
        ),
        WorkerPoolSpec(
            name="build-test",
            job_types=BUILD_TEST_JOB_TYPES,
            concurrency=max(0, int(getattr(settings, "runtime_worker_build_test_concurrency", 1))),
        ),
        WorkerPoolSpec(
            name="material",
            job_types=MATERIAL_JOB_TYPES,
            concurrency=max(0, int(getattr(settings, "runtime_worker_material_concurrency", 1))),
        ),
    )


def build_attempt_owner(pool_name: str, slot: int) -> str:
    prefix = f"{socket.gethostname()}:{os.getpid()}:{pool_name}:{slot}"
    # RuntimeJob.worker_name is VARCHAR(160); preserve the UUID attempt token.
    return f"{prefix[:123]}:{uuid.uuid4()}"


def advisory_lock_key(job_id: str) -> int:
    """Map a job id to a stable signed PostgreSQL bigint key."""

    digest = hashlib.sha256(job_id.encode("utf-8")).digest()[:8]
    return int.from_bytes(digest, byteorder="big", signed=True)


class JobAdvisoryLock:
    """Session advisory lock held on one dedicated PostgreSQL connection.

    SQLite and other test databases use a no-op lock. Row-level owner checks are
    still applied by the repository in those environments.
    """

    def __init__(self, engine: Engine, job_id: str) -> None:
        self.engine = engine
        self.job_id = job_id
        self.key = advisory_lock_key(job_id)
        self.connection: Connection | None = None
        self.acquired = False
        self._connection_lock = threading.RLock()

    def acquire(self) -> bool:
        if self.engine.dialect.name != "postgresql":
            self.acquired = True
            return True
        connection = self.engine.connect()
        try:
            acquired = bool(connection.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": self.key}))
        except Exception:
            connection.close()
            raise
        if not acquired:
            connection.close()
            return False
        # Session advisory locks survive transaction boundaries. End the
        # acquisition transaction so a slow job does not leave an
        # idle-in-transaction connection open for its whole attempt.
        connection.commit()
        self.connection = connection
        self.acquired = True
        return True

    def is_alive(self) -> bool:
        with self._connection_lock:
            if not self.acquired:
                return False
            if self.connection is None:
                return True
            try:
                self.connection.execute(text("SELECT 1"))
                self.connection.commit()
                return True
            except Exception:
                self.acquired = False
                return False

    def release(self) -> None:
        with self._connection_lock:
            connection = self.connection
            self.connection = None
            if connection is None:
                self.acquired = False
                return
            try:
                if self.acquired:
                    connection.scalar(text("SELECT pg_advisory_unlock(:key)"), {"key": self.key})
                    connection.commit()
            except Exception:
                LOGGER.exception("failed to release job advisory lock", extra={"job_id": self.job_id})
            finally:
                self.acquired = False
                connection.close()


class JobLeaseHeartbeat:
    def __init__(
        self,
        *,
        database_manager: DatabaseManager,
        lease: JobLease,
        advisory_lock: JobAdvisoryLock,
        lease_seconds: int,
        interval_seconds: float,
    ) -> None:
        self.database_manager = database_manager
        self.lease = lease
        self.advisory_lock = advisory_lock
        self.lease_seconds = lease_seconds
        self.interval_seconds = max(0.05, min(interval_seconds, max(0.05, lease_seconds / 3)))
        self.repository = JobRepository()
        self._stop = threading.Event()
        self._lost = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name=f"job-heartbeat-{lease.job_id}",
            daemon=True,
        )

    @property
    def lease_lost(self) -> bool:
        return self._lost.is_set()

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=max(1.0, self.interval_seconds + 1.0))

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            if not self.advisory_lock.is_alive():
                self._lost.set()
                LOGGER.error("job advisory lock lost", extra={"job_id": self.lease.job_id, "owner": self.lease.owner})
                return
            try:
                with self.database_manager.session() as session:
                    renewed = self.repository.renew_lease(
                        session,
                        self.lease,
                        lease_seconds=self.lease_seconds,
                    )
            except SQLAlchemyError:
                # The advisory lock prevents recovery while this connection is
                # alive. A transient heartbeat DB error is retried next tick.
                LOGGER.exception(
                    "job lease heartbeat failed",
                    extra={"job_id": self.lease.job_id, "owner": self.lease.owner},
                )
                continue
            if renewed is None:
                self._lost.set()
                LOGGER.error("job lease owner CAS failed", extra={"job_id": self.lease.job_id, "owner": self.lease.owner})
                return
            self.lease = renewed


class RuntimeJobWorker:
    """One database-backed worker slot for a bounded set of job types."""

    def __init__(
        self,
        *,
        settings: Settings,
        database_manager: DatabaseManager,
        gitlab_gateway: GitLabSkillSourceGateway,
        inference_gateway: LlmInferenceGateway,
        asr_gateway: AsrGateway,
        object_store: ObjectStoreService,
        agent_harness_service: AgentHarnessService,
        runtime_event_sink: RuntimeEventSink | None = None,
        poll_interval_seconds: float | None = None,
        job_types: Sequence[str] = ALL_JOB_TYPES,
        pool_name: str = "embedded",
        slot: int = 0,
    ) -> None:
        self.settings = settings
        self.database_manager = database_manager
        self.gitlab_gateway = gitlab_gateway
        self.inference_gateway = inference_gateway
        self.asr_gateway = asr_gateway
        self.object_store = object_store
        self.agent_harness_service = agent_harness_service
        self.runtime_event_sink = runtime_event_sink or NoopRuntimeEventSink()
        configured_poll = float(getattr(settings, "runtime_job_poll_interval_seconds", 0.5))
        self.poll_interval_seconds = configured_poll if poll_interval_seconds is None else poll_interval_seconds
        self.job_types = tuple(dict.fromkeys(job_types))
        if not self.job_types:
            raise ValueError("RuntimeJobWorker requires at least one job type.")
        self.pool_name = pool_name
        self.slot = slot
        self.job_repository = JobRepository()
        self._stop = threading.Event()

    def request_stop(self) -> None:
        self._stop.set()

    async def run_forever(self) -> None:
        LOGGER.info(
            "runtime job worker slot started",
            extra={"pool": self.pool_name, "slot": self.slot, "job_types": self.job_types},
        )
        try:
            while not self._stop.is_set():
                run_task = asyncio.create_task(asyncio.to_thread(self.run_once))
                try:
                    processed = await asyncio.shield(run_task)
                except asyncio.CancelledError:
                    self.request_stop()
                    await run_task
                    raise
                if not processed:
                    await asyncio.to_thread(self._stop.wait, self.poll_interval_seconds)
        except asyncio.CancelledError:
            self.request_stop()
            raise
        finally:
            LOGGER.info("runtime job worker slot stopped", extra={"pool": self.pool_name, "slot": self.slot})

    def run_once(self) -> bool:
        lease = self._claim_next_job()
        if lease is None:
            return False

        advisory_lock = JobAdvisoryLock(self.database_manager.engine, lease.job_id)
        try:
            acquired = advisory_lock.acquire()
        except Exception as exc:
            LOGGER.exception("job advisory lock acquisition failed", extra={"job_id": lease.job_id})
            self._abandon_claim(lease, f"advisory lock error: {exc}")
            return True
        if not acquired:
            LOGGER.warning(
                "job claim rejected because advisory lock is held",
                extra={"job_id": lease.job_id, "owner": lease.owner},
            )
            self._abandon_claim(lease, "job advisory lock is held by another attempt")
            return True

        heartbeat = JobLeaseHeartbeat(
            database_manager=self.database_manager,
            lease=lease,
            advisory_lock=advisory_lock,
            lease_seconds=max(1, int(self.settings.runtime_job_lease_seconds)),
            interval_seconds=float(getattr(self.settings, "runtime_job_heartbeat_seconds", 20)),
        )
        heartbeat.start()
        process_started_at = time.monotonic()
        outcome = "failed"
        add_metric_up_down_counter(
            "psop.jobs.in_flight",
            1,
            attributes={"pool": self.pool_name},
            description="Jobs currently executing in a worker pool",
        )
        try:
            lease_is_healthy = lambda: not heartbeat.lease_lost and advisory_lock.is_alive()
            self._process_job(lease, lease_is_healthy=lease_is_healthy)
            # Stop renewal before inspecting the terminal outcome. Otherwise a
            # heartbeat tick can race the handler's normal owner release and
            # misclassify a completed attempt as lease_lost.
            heartbeat.stop()
            if not lease_is_healthy():
                outcome = "lease_lost"
                LOGGER.error(
                    "job handler returned after losing its lease; terminal write must be fenced by the domain service",
                    extra={"job_id": lease.job_id, "owner": lease.owner},
                )
            else:
                outcome = "succeeded"
            return True
        except Exception as exc:
            if isinstance(exc, RuntimeStepTimeoutError):
                add_metric_counter(
                    "psop.jobs.step_timeout",
                    attributes={"pool": self.pool_name},
                    description="Runtime steps that exhausted their shared deadline",
                )
            LOGGER.exception("runtime job failed unexpectedly: %s", lease.job_id)
            if not heartbeat.lease_lost:
                try:
                    self._record_unhandled_failure(lease, str(exc))
                except Exception:
                    LOGGER.exception(
                        "failed to persist unhandled job failure; lease recovery will retry",
                        extra={"job_id": lease.job_id, "owner": lease.owner},
                    )
            return True
        finally:
            heartbeat.stop()
            advisory_lock.release()
            add_metric_up_down_counter(
                "psop.jobs.in_flight",
                -1,
                attributes={"pool": self.pool_name},
                description="Jobs currently executing in a worker pool",
            )
            record_metric_histogram(
                "psop.jobs.duration",
                max(0.0, time.monotonic() - process_started_at),
                attributes={"pool": self.pool_name, "job_type": lease.job_type, "outcome": outcome},
                unit="s",
                description="Worker job handler duration",
            )
            add_metric_counter(
                "psop.jobs.completed",
                attributes={"pool": self.pool_name, "job_type": lease.job_type, "outcome": outcome},
                description="Completed worker handler attempts by outcome",
            )

    def _claim_next_job(self) -> JobLease | None:
        owner = build_attempt_owner(self.pool_name, self.slot)
        try:
            with self.database_manager.session() as session:
                with start_span(
                    "job.claim",
                    pool=self.pool_name,
                    slot=self.slot,
                    job_types=",".join(self.job_types),
                ):
                    lease = self.job_repository.claim_next_job(
                        session,
                        job_types=self.job_types,
                        lease_seconds=max(1, int(self.settings.runtime_job_lease_seconds)),
                        worker_name=owner,
                    )
        except SQLAlchemyError as exc:
            LOGGER.warning("runtime job worker skipped polling because job store is not ready: %s", exc)
            return None
        if lease:
            add_metric_counter(
                "psop.jobs.claimed",
                attributes={"pool": self.pool_name, "job_type": lease.job_type},
                description="Jobs claimed by worker pool and bounded job type",
            )
            if lease.created_at is not None:
                record_metric_histogram(
                    "psop.jobs.queue_age",
                    _elapsed_seconds_since(lease.created_at),
                    attributes={"pool": self.pool_name, "job_type": lease.job_type},
                    unit="s",
                    description="Age of a durable job when claimed",
                )
            LOGGER.info(
                "runtime job claimed",
                extra={
                    "job_id": lease.job_id,
                    "job_type": lease.job_type,
                    "pool": self.pool_name,
                    "slot": self.slot,
                    "owner": lease.owner,
                    "attempt": lease.attempt_no,
                },
            )
        return lease

    def _process_job(self, lease: JobLease, *, lease_is_healthy: Callable[[], bool]) -> None:
        with self.database_manager.session() as session:
            lease_fence = (
                _install_session_lease_fence(session, lease, lease_is_healthy=lease_is_healthy)
                if lease.job_type != "runtime"
                else None
            )
            try:
                job = self.job_repository.get_runtime_job(session, lease.job_id)
                if not job or job.status != "running" or job.worker_name != lease.owner:
                    raise RuntimeError("Job lease ownership changed before processing started.")

                with log_context(job_id=lease.job_id), start_span(
                    "job.process",
                    job_id=lease.job_id,
                    job_type=lease.job_type,
                    pool=self.pool_name,
                    owner=lease.owner,
                    attempt=lease.attempt_no,
                ) as span:
                    try:
                        if lease.job_type == "compile":
                            CompilerService(
                                settings=self.settings,
                                gitlab_gateway=self.gitlab_gateway,
                                inference_gateway=self.inference_gateway,
                                agent_harness_service=self.agent_harness_service,
                                object_store=self.object_store,
                            ).process_compile_job(session, lease.job_id)
                        elif lease.job_type == "runtime":
                            if not job.run_id:
                                raise RuntimeError("Runtime job 缺少 run_id。")
                            self._runtime_service(lease_is_healthy=lease_is_healthy).process_run(
                                session,
                                job.run_id,
                                job_lease=lease,
                            )
                        elif lease.job_type == "skill_test_timeline_driver":
                            self._skill_test_service().process_driver_job(session, lease.job_id)
                        elif lease.job_type == "raw_material_analysis":
                            self._skills_service().process_raw_material_analysis_job(session, lease.job_id)
                        elif lease.job_type == SKILL_RAW_MATERIAL_GENERATION_JOB_TYPE:
                            self._skills_service().process_skill_raw_material_generation_job(session, lease.job_id)
                        else:
                            raise RuntimeError(f"Unsupported job_type={lease.job_type}.")
                    except Exception as exc:
                        record_span_exception(span, exc)
                        raise
            finally:
                if lease_fence is not None:
                    sqlalchemy_event.remove(session, "after_begin", lease_fence.after_begin)
                    sqlalchemy_event.remove(session, "before_commit", lease_fence.before_commit)
                    session.info.pop("runtime_job_external_lease_fence", None)

    def _skills_service(self) -> SkillsService:
        return SkillsService(
            settings=self.settings,
            gitlab_gateway=self.gitlab_gateway,
            inference_gateway=self.inference_gateway,
            asr_gateway=self.asr_gateway,
            object_store=self.object_store,
        )

    def _skill_test_service(self) -> SkillTestService:
        return SkillTestService(
            settings=self.settings,
            inference_gateway=self.inference_gateway,
            object_store=self.object_store,
            agent_harness_service=self.agent_harness_service,
        )

    def _runtime_service(self, *, lease_is_healthy: Callable[[], bool] | None = None) -> RuntimeService:
        return RuntimeService(
            settings=self.settings,
            inference_gateway=self.inference_gateway,
            object_store=self.object_store,
            agent_harness_service=self.agent_harness_service,
            runtime_event_sink=self.runtime_event_sink,
            lease_is_healthy=lease_is_healthy,
        )

    def _compiler_service(self) -> CompilerService:
        return CompilerService(
            settings=self.settings,
            gitlab_gateway=self.gitlab_gateway,
            inference_gateway=self.inference_gateway,
            agent_harness_service=self.agent_harness_service,
            object_store=self.object_store,
        )

    def _abandon_claim(self, lease: JobLease, error_message: str) -> None:
        try:
            with self.database_manager.session() as session:
                self.job_repository.abandon_claim(
                    session,
                    lease,
                    error_message=error_message,
                    retry_delay_seconds=1,
                )
        except SQLAlchemyError:
            LOGGER.exception("failed to return unlocked job claim", extra={"job_id": lease.job_id})

    def _record_unhandled_failure(self, lease: JobLease, error_message: str) -> bool:
        with self.database_manager.session() as session:
            job = session.scalar(
                select(RuntimeJob)
                .where(
                    RuntimeJob.id == lease.job_id,
                    RuntimeJob.status == "running",
                    RuntimeJob.worker_name == lease.owner,
                )
                .with_for_update()
            )
            if not job:
                session.rollback()
                return False

            with log_context(job_id=job.id, compile_request_id=job.compile_request_id):
                LOGGER.warning(
                    "recording unhandled runtime job failure",
                    extra={"error": error_message, "attempt": job.attempt_no, "max_attempts": job.max_attempts},
                )
            retryable = job.attempt_no < job.max_attempts
            job.last_error = error_message
            job.worker_name = ""
            job.lease_until = None
            if retryable:
                job.status = "retryable_failed"
                job.available_at = now_utc() + timedelta(seconds=_retry_delay_seconds(job.attempt_no))
                add_metric_counter(
                    "psop.jobs.retried",
                    attributes={"job_type": job.job_type},
                    description="Jobs scheduled for another attempt",
                )
            else:
                job.status = "failed"
                if job.job_type == "compile":
                    _mark_compile_job_exhausted(job, error_message)
                    self._compiler_service().finalize_exhausted_compile_job(
                        session,
                        job_id=job.id,
                        error_message=error_message,
                    )
                elif job.job_type == "runtime":
                    self._runtime_service().finalize_exhausted_job(
                        session,
                        job_id=job.id,
                        error_message=error_message,
                    )
                elif job.job_type == "skill_test_timeline_driver":
                    self._skill_test_service().finalize_exhausted_timeline_driver_job(
                        session,
                        job_id=job.id,
                        error_message=error_message,
                    )
                elif job.job_type == "raw_material_analysis":
                    self._skills_service().finalize_exhausted_raw_material_analysis_job(
                        session,
                        job_id=job.id,
                        error_message=error_message,
                    )
                elif job.job_type == SKILL_RAW_MATERIAL_GENERATION_JOB_TYPE:
                    self._skills_service().finalize_exhausted_raw_material_generation_job(
                        session,
                        job_id=job.id,
                        error_message=error_message,
                    )
            session.commit()
            return True


class RuntimeJobWorkerSupervisor:
    """Own the isolated worker pools and one expired-lease recovery loop."""

    def __init__(
        self,
        *,
        settings: Settings,
        database_manager: DatabaseManager,
        gitlab_gateway: GitLabSkillSourceGateway,
        inference_gateway: LlmInferenceGateway,
        asr_gateway: AsrGateway,
        object_store: ObjectStoreService,
        agent_harness_service: AgentHarnessService,
        runtime_event_sink: RuntimeEventSink | None = None,
        pool_specs: Sequence[WorkerPoolSpec] | None = None,
    ) -> None:
        self.settings = settings
        self.database_manager = database_manager
        self.gitlab_gateway = gitlab_gateway
        self.inference_gateway = inference_gateway
        self.asr_gateway = asr_gateway
        self.object_store = object_store
        self.agent_harness_service = agent_harness_service
        self.runtime_event_sink = runtime_event_sink or NoopRuntimeEventSink()
        self.repository = JobRepository()
        self._stop = threading.Event()
        self.workers: list[RuntimeJobWorker] = []
        for pool in pool_specs or default_worker_pool_specs(settings):
            for slot in range(pool.concurrency):
                self.workers.append(
                    RuntimeJobWorker(
                        settings=settings,
                        database_manager=database_manager,
                        gitlab_gateway=gitlab_gateway,
                        inference_gateway=inference_gateway,
                        asr_gateway=asr_gateway,
                        object_store=object_store,
                        agent_harness_service=agent_harness_service,
                        runtime_event_sink=self.runtime_event_sink,
                        job_types=pool.job_types,
                        pool_name=pool.name,
                        slot=slot,
                    )
                )

    def request_stop(self) -> None:
        self._stop.set()
        for worker in self.workers:
            worker.request_stop()

    async def run_forever(self) -> None:
        if not self.workers:
            raise RuntimeError("No runtime job worker slots are enabled.")
        LOGGER.info("runtime job worker supervisor started", extra={"slot_count": len(self.workers)})
        tasks = [asyncio.create_task(worker.run_forever()) for worker in self.workers]
        tasks.append(asyncio.create_task(self._run_recovery_loop()))
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            self.request_stop()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        except Exception:
            self.request_stop()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        finally:
            self.request_stop()
            LOGGER.info("runtime job worker supervisor stopped")

    def recover_expired_jobs_once(self) -> int:
        try:
            with self.database_manager.session() as session:
                expired = self.repository.list_expired_leases(session)
        except SQLAlchemyError as exc:
            LOGGER.warning("expired job scan skipped because job store is not ready: %s", exc)
            return 0

        recovered = 0
        for lease in expired:
            advisory_lock = JobAdvisoryLock(self.database_manager.engine, lease.job_id)
            try:
                if not advisory_lock.acquire():
                    continue
                with self.database_manager.session() as session:
                    result = self.repository.recover_expired_lease(
                        session,
                        lease,
                        retry_delay_seconds=_retry_delay_seconds(lease.attempt_no),
                        commit=False,
                    )
                    if result is None:
                        continue
                    if result.exhausted:
                        self._finalize_exhausted_job(session, result.job_id)
                    session.commit()
                    recovered += 1
                    add_metric_counter(
                        "psop.jobs.lease_recovered",
                        attributes={"job_type": result.job_type, "status": result.status},
                        description="Expired job leases recovered by the reaper",
                    )
                    LOGGER.warning(
                        "expired runtime job lease recovered",
                        extra={
                            "job_id": result.job_id,
                            "job_type": result.job_type,
                            "attempt": result.attempt_no,
                            "status": result.status,
                        },
                    )
            except Exception:
                LOGGER.exception("expired job recovery failed", extra={"job_id": lease.job_id})
            finally:
                advisory_lock.release()
        return recovered

    async def _run_recovery_loop(self) -> None:
        interval = max(0.1, float(getattr(self.settings, "runtime_job_recovery_scan_seconds", 10)))
        while not self._stop.is_set():
            await asyncio.to_thread(self.recover_expired_jobs_once)
            await asyncio.to_thread(self._stop.wait, interval)

    def _finalize_exhausted_job(self, session: Session, job_id: str) -> None:
        """Finalize queue-owned state without taking Runtime Kernel authority."""

        job = self.repository.get_runtime_job(session, job_id)
        if not job or job.status != "failed":
            return
        if job.job_type == "compile":
            _mark_compile_job_exhausted(job, job.last_error)
            self._compiler_service().finalize_exhausted_compile_job(
                session,
                job_id=job.id,
                error_message=job.last_error,
            )
            return
        if job.job_type == "runtime":
            self._runtime_service().finalize_exhausted_job(
                session,
                job_id=job.id,
                error_message=job.last_error,
            )
            return
        if job.job_type == "skill_test_timeline_driver":
            self._skill_test_service().finalize_exhausted_timeline_driver_job(
                session,
                job_id=job.id,
                error_message=job.last_error,
            )
            return
        if job.job_type == "raw_material_analysis":
            self._skills_service().finalize_exhausted_raw_material_analysis_job(
                session,
                job_id=job.id,
                error_message=job.last_error,
            )
            return
        if job.job_type == SKILL_RAW_MATERIAL_GENERATION_JOB_TYPE:
            self._skills_service().finalize_exhausted_raw_material_generation_job(
                session,
                job_id=job.id,
                error_message=job.last_error,
            )
            return
        LOGGER.error("job attempts exhausted without a domain finalizer", extra={"job_id": job.id, "job_type": job.job_type})

    def _skill_test_service(self) -> SkillTestService:
        return SkillTestService(
            settings=self.settings,
            inference_gateway=self.inference_gateway,
            object_store=self.object_store,
            agent_harness_service=self.agent_harness_service,
        )

    def _skills_service(self) -> SkillsService:
        return SkillsService(
            settings=self.settings,
            gitlab_gateway=self.gitlab_gateway,
            inference_gateway=self.inference_gateway,
            asr_gateway=self.asr_gateway,
            object_store=self.object_store,
            agent_harness_service=self.agent_harness_service,
        )

    def _runtime_service(self) -> RuntimeService:
        return RuntimeService(
            settings=self.settings,
            inference_gateway=self.inference_gateway,
            object_store=self.object_store,
            agent_harness_service=self.agent_harness_service,
            runtime_event_sink=self.runtime_event_sink,
        )

    def _compiler_service(self) -> CompilerService:
        return CompilerService(
            settings=self.settings,
            gitlab_gateway=self.gitlab_gateway,
            inference_gateway=self.inference_gateway,
            agent_harness_service=self.agent_harness_service,
            object_store=self.object_store,
        )


@dataclass(frozen=True, slots=True)
class SessionLeaseFence:
    after_begin: Callable[..., None]
    before_commit: Callable[..., None]


def _install_session_lease_fence(
    session: Session,
    lease: JobLease,
    *,
    lease_is_healthy: Callable[[], bool] = lambda: True,
) -> SessionLeaseFence:
    """Validate on transaction start and atomically fence every commit."""

    def ensure_attempt_is_healthy() -> None:
        if not lease_is_healthy():
            raise RuntimeError("Job lease health was lost; stale attempt write was fenced.")

    def fence_attempt_owner(_session: Session, transaction, connection) -> None:
        if transaction.nested:
            return
        ensure_attempt_is_healthy()
        owned_job_id = connection.scalar(
            select(RuntimeJob.id)
            .where(
                RuntimeJob.id == lease.job_id,
                RuntimeJob.status == "running",
                RuntimeJob.worker_name == lease.owner,
                RuntimeJob.lease_until.is_not(None),
                RuntimeJob.lease_until > now_utc(),
            )
        )
        if owned_job_id is None:
            raise RuntimeError("Job lease ownership changed; stale attempt write was fenced.")

    def fence_attempt_commit(active_session: Session) -> None:
        ensure_attempt_is_healthy()
        owned_job_id = active_session.connection().scalar(
            select(RuntimeJob.id)
            .where(
                RuntimeJob.id == lease.job_id,
                RuntimeJob.status == "running",
                RuntimeJob.worker_name == lease.owner,
                RuntimeJob.lease_until.is_not(None),
                RuntimeJob.lease_until > now_utc(),
            )
            .with_for_update()
        )
        if owned_job_id is None:
            raise RuntimeError("Job lease ownership changed; stale attempt write was fenced.")
        job = active_session.get(RuntimeJob, lease.job_id)
        if job is not None and job.status != "running":
            job.worker_name = ""
            job.lease_until = None

    sqlalchemy_event.listen(session, "after_begin", fence_attempt_owner)
    sqlalchemy_event.listen(session, "before_commit", fence_attempt_commit)
    session.info["runtime_job_external_lease_fence"] = lease.owner
    return SessionLeaseFence(after_begin=fence_attempt_owner, before_commit=fence_attempt_commit)


def _retry_delay_seconds(attempt_no: int) -> int:
    return min(300, 5 * (2 ** max(0, attempt_no - 1)))


def _elapsed_seconds_since(value: datetime) -> float:
    current = now_utc()
    if value.tzinfo is None:
        value = value.replace(tzinfo=current.tzinfo)
    return max(0.0, (current - value).total_seconds())


def _mark_compile_job_exhausted(job: RuntimeJob, error_message: str) -> None:
    payload = ensure_publish_progress_payload(job.payload)
    current_stage = payload.get("current_stage") or "source_loaded"
    job.payload = mark_publish_stage(
        payload,
        current_stage,
        "failed",
        error_message,
        terminal_status="failed",
        error_message=error_message,
    )
