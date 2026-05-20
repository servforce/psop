from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.jobs.models import RuntimeJob
from app.domain.skills.models import now_utc


class JobRepository:
    """Database access for the shared runtime_job queue."""

    def get_runtime_job(self, session: Session, job_id: str) -> RuntimeJob | None:
        return session.get(RuntimeJob, job_id)

    def get_runtime_job_by_dedupe_key(self, session: Session, dedupe_key: str) -> RuntimeJob | None:
        return session.scalar(select(RuntimeJob).where(RuntimeJob.dedupe_key == dedupe_key))

    def get_compile_job(self, session: Session, compile_request_id: str) -> RuntimeJob | None:
        return session.scalar(select(RuntimeJob).where(RuntimeJob.compile_request_id == compile_request_id).limit(1))

    def claim_next_job(
        self,
        session: Session,
        *,
        job_type: str,
        lease_seconds: int,
    ) -> RuntimeJob | None:
        now = now_utc()
        query = (
            select(RuntimeJob)
            .where(
                RuntimeJob.job_type == job_type,
                RuntimeJob.status.in_(("pending", "retryable_failed")),
                RuntimeJob.available_at <= now,
            )
            .order_by(RuntimeJob.available_at.asc(), RuntimeJob.created_at.asc())
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        job = session.scalar(query)
        if not job:
            return None

        job.status = "running"
        job.attempt_no += 1
        job.lease_until = now + timedelta(seconds=lease_seconds)
        session.commit()
        session.refresh(job)
        return job

    def list_runtime_jobs(
        self,
        session: Session,
        *,
        status: str | None = None,
        job_type: str | None = None,
    ) -> list[RuntimeJob]:
        query = select(RuntimeJob).order_by(RuntimeJob.created_at.desc())
        if status:
            query = query.where(RuntimeJob.status == status)
        if job_type:
            query = query.where(RuntimeJob.job_type == job_type)
        return list(session.scalars(query).all())
