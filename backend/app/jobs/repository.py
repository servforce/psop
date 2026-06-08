from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.jobs.models import RuntimeJob
from app.jobs.types import job_type_filter_values
from app.pskills.models import now_utc


class JobRepository:
    """Database access for the shared runtime_job queue."""

    def get_runtime_job(self, session: Session, job_id: str) -> RuntimeJob | None:
        return session.get(RuntimeJob, job_id)

    def get_runtime_job_by_dedupe_key(self, session: Session, dedupe_key: str) -> RuntimeJob | None:
        return session.scalar(select(RuntimeJob).where(RuntimeJob.dedupe_key == dedupe_key))

    def get_compile_job(self, session: Session, compile_request_id: str) -> RuntimeJob | None:
        return session.scalar(select(RuntimeJob).where(RuntimeJob.compile_request_id == compile_request_id).limit(1))

    def recover_expired_leases(
        self,
        session: Session,
        *,
        retry_delay_base_seconds: int = 5,
        limit: int = 100,
    ) -> list[RuntimeJob]:
        now = now_utc()
        query = (
            select(RuntimeJob)
            .where(
                RuntimeJob.status == "running",
                RuntimeJob.lease_until.is_not(None),
                RuntimeJob.lease_until <= now,
            )
            .order_by(RuntimeJob.lease_until.asc(), RuntimeJob.created_at.asc())
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        jobs = list(session.scalars(query).all())
        for job in jobs:
            retryable = job.attempt_no < job.max_attempts
            metrics = dict(job.metrics or {})
            metrics["lease_recovery_count"] = int(metrics.get("lease_recovery_count") or 0) + 1
            metrics["last_lease_recovered_at"] = now.isoformat()
            job.metrics = metrics
            job.last_error = "runtime_job lease expired before worker completed the job."
            job.worker_name = ""
            job.lease_until = None
            if retryable:
                job.status = "pending"
                job.available_at = now + timedelta(seconds=retry_delay_base_seconds * max(1, job.attempt_no))
            else:
                job.status = "failed"
                job.available_at = now
        if jobs:
            session.commit()
            for job in jobs:
                session.refresh(job)
        return jobs

    def claim_next_job(
        self,
        session: Session,
        *,
        job_type: str,
        lease_seconds: int,
        worker_name: str = "",
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
        job.started_at = job.started_at or now
        job.worker_name = worker_name
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
        q: str | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[RuntimeJob]:
        query = select(RuntimeJob).order_by(RuntimeJob.created_at.desc())
        if status:
            query = query.where(RuntimeJob.status == status)
        if job_type:
            query = query.where(RuntimeJob.job_type.in_(job_type_filter_values(job_type)))
        if created_from:
            query = query.where(RuntimeJob.created_at >= created_from)
        if created_to:
            query = query.where(RuntimeJob.created_at <= created_to)
        if q:
            pattern = f"%{q.strip()}%"
            query = query.where(
                or_(
                    RuntimeJob.id.ilike(pattern),
                    RuntimeJob.job_type.ilike(pattern),
                    RuntimeJob.status.ilike(pattern),
                    RuntimeJob.dedupe_key.ilike(pattern),
                    RuntimeJob.run_id.ilike(pattern),
                    RuntimeJob.compile_request_id.ilike(pattern),
                )
            )
        if offset:
            query = query.offset(offset)
        if limit:
            query = query.limit(limit)
        return list(session.scalars(query).all())

    def count_runtime_jobs(
        self,
        session: Session,
        *,
        status: str | None = None,
        job_type: str | None = None,
        q: str | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
    ) -> int:
        query = select(func.count()).select_from(RuntimeJob)
        if status:
            query = query.where(RuntimeJob.status == status)
        if job_type:
            query = query.where(RuntimeJob.job_type.in_(job_type_filter_values(job_type)))
        if created_from:
            query = query.where(RuntimeJob.created_at >= created_from)
        if created_to:
            query = query.where(RuntimeJob.created_at <= created_to)
        if q:
            pattern = f"%{q.strip()}%"
            query = query.where(
                or_(
                    RuntimeJob.id.ilike(pattern),
                    RuntimeJob.job_type.ilike(pattern),
                    RuntimeJob.status.ilike(pattern),
                    RuntimeJob.dedupe_key.ilike(pattern),
                    RuntimeJob.run_id.ilike(pattern),
                    RuntimeJob.compile_request_id.ilike(pattern),
                )
            )
        return int(session.scalar(query) or 0)

    def accumulate_llm_usage(self, job: RuntimeJob | None, usage: dict[str, Any] | None) -> None:
        if not job or not isinstance(usage, dict) or not usage:
            return
        metrics = dict(job.metrics or {})
        metrics["llm_calls"] = int(metrics.get("llm_calls") or 0) + 1
        for key in ("input_tokens", "output_tokens", "total_tokens"):
            value = usage.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                metrics[key] = int(metrics.get(key) or 0) + value
        job.metrics = metrics

    def set_llm_usage_from_budgets(self, job: RuntimeJob | None, budgets: dict[str, Any] | None) -> None:
        if not job or not isinstance(budgets, dict):
            return
        metrics = dict(job.metrics or {})
        mapping = {
            "llm_calls": "llm_calls",
            "llm_input_tokens": "input_tokens",
            "llm_output_tokens": "output_tokens",
            "llm_total_tokens": "total_tokens",
        }
        changed = False
        for source_key, target_key in mapping.items():
            value = budgets.get(source_key)
            if isinstance(value, int) and not isinstance(value, bool):
                metrics[target_key] = value
                changed = True
        if changed:
            job.metrics = metrics
