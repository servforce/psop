from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from sqlalchemy.exc import SQLAlchemyError

from app.core.config import Settings
from app.core.logging import log_context
from app.core.observability import record_span_exception, start_span
from app.domain.compiler.service import CompilerService
from app.domain.jobs.progress import ensure_publish_progress_payload, mark_publish_stage
from app.domain.jobs.repository import JobRepository
from app.domain.skills.models import now_utc
from app.gateway.inference import LlmInferenceGateway
from app.gateway.gitlab import GitLabSkillSourceGateway
from app.infra.database import DatabaseManager


LOGGER = logging.getLogger(__name__)


class RuntimeJobWorker:
    """Database-backed worker for MVP compile jobs."""

    def __init__(
        self,
        *,
        settings: Settings,
        database_manager: DatabaseManager,
        gitlab_gateway: GitLabSkillSourceGateway,
        inference_gateway: LlmInferenceGateway,
        poll_interval_seconds: float = 0.5,
    ) -> None:
        self.settings = settings
        self.database_manager = database_manager
        self.gitlab_gateway = gitlab_gateway
        self.inference_gateway = inference_gateway
        self.poll_interval_seconds = poll_interval_seconds
        self.job_repository = JobRepository()

    async def run_forever(self) -> None:
        LOGGER.info("runtime job worker started")
        try:
            while True:
                processed = await asyncio.to_thread(self.run_once)
                await asyncio.sleep(0 if processed else self.poll_interval_seconds)
        except asyncio.CancelledError:
            LOGGER.info("runtime job worker stopped")
            raise

    def run_once(self) -> bool:
        try:
            with self.database_manager.session() as session:
                with start_span("job.claim", job_type="compile"):
                    job = self.job_repository.claim_next_job(
                        session,
                        job_type="compile",
                        lease_seconds=self.settings.runtime_job_lease_seconds,
                    )
                if not job:
                    return False
                job_id = job.id
                LOGGER.info(
                    "runtime job claimed",
                    extra={
                        "job_id": job.id,
                        "job_type": job.job_type,
                        "compile_request_id": job.compile_request_id,
                        "attempt": job.attempt_no,
                    },
                )
        except SQLAlchemyError as exc:
            LOGGER.warning("runtime job worker skipped polling because job store is not ready: %s", exc)
            return False

        try:
            with self.database_manager.session() as session:
                compiler_service = CompilerService(
                    settings=self.settings,
                    gitlab_gateway=self.gitlab_gateway,
                    inference_gateway=self.inference_gateway,
                )
                with log_context(job_id=job_id), start_span("job.process", job_id=job_id, job_type="compile") as span:
                    try:
                        compiler_service.process_compile_job(session, job_id)
                    except Exception as exc:
                        record_span_exception(span, exc)
                        raise
            return True
        except Exception as exc:
            LOGGER.exception("runtime job failed unexpectedly: %s", job_id)
            self._record_unhandled_failure(job_id, str(exc))
            return True

    def _record_unhandled_failure(self, job_id: str, error_message: str) -> None:
        with self.database_manager.session() as session:
            job = self.job_repository.get_runtime_job(session, job_id)
            if not job:
                return

            with log_context(job_id=job.id, compile_request_id=job.compile_request_id):
                LOGGER.warning(
                    "recording unhandled runtime job failure",
                    extra={"error": error_message, "attempt": job.attempt_no, "max_attempts": job.max_attempts},
                )
            retryable = job.attempt_no < job.max_attempts
            job.last_error = error_message
            if retryable:
                job.status = "pending"
                job.available_at = now_utc() + timedelta(seconds=5 * job.attempt_no)
            else:
                job.status = "failed"
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
            session.commit()
