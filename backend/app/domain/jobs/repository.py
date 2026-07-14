from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Sequence

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.domain.jobs.models import RuntimeJob
from app.domain.skills.models import now_utc


@dataclass(frozen=True, slots=True)
class JobLease:
    """Immutable ownership token returned by an atomic queue claim."""

    job_id: str
    job_type: str
    owner: str
    attempt_no: int
    max_attempts: int
    lease_until: datetime
    run_id: str | None = None
    compile_request_id: str | None = None
    created_at: datetime | None = None

    @classmethod
    def from_job(cls, job: RuntimeJob) -> "JobLease":
        if job.lease_until is None:
            raise ValueError("Cannot build a JobLease without lease_until.")
        return cls(
            job_id=job.id,
            job_type=job.job_type,
            owner=job.worker_name,
            attempt_no=job.attempt_no,
            max_attempts=job.max_attempts,
            lease_until=job.lease_until,
            run_id=job.run_id,
            compile_request_id=job.compile_request_id,
            created_at=job.created_at,
        )


@dataclass(frozen=True, slots=True)
class LeaseRecoveryResult:
    job_id: str
    job_type: str
    status: str
    attempt_no: int
    exhausted: bool


class JobRepository:
    """Database access for the shared runtime_job queue."""

    def get_runtime_job(self, session: Session, job_id: str) -> RuntimeJob | None:
        return session.get(RuntimeJob, job_id)

    def get_runtime_job_for_update(self, session: Session, job_id: str) -> RuntimeJob | None:
        if session.info.get("runtime_job_external_lease_fence"):
            # The worker's commit fence and advisory lock provide ownership;
            # avoiding a long row lock lets the independent heartbeat renew.
            return session.get(RuntimeJob, job_id)
        return session.scalar(
            select(RuntimeJob)
            .where(RuntimeJob.id == job_id)
            .with_for_update()
        )

    def get_runtime_job_by_dedupe_key(self, session: Session, dedupe_key: str) -> RuntimeJob | None:
        return session.scalar(select(RuntimeJob).where(RuntimeJob.dedupe_key == dedupe_key))

    def get_runtime_job_by_dedupe_key_for_update(self, session: Session, dedupe_key: str) -> RuntimeJob | None:
        return session.scalar(
            select(RuntimeJob)
            .where(RuntimeJob.dedupe_key == dedupe_key)
            .with_for_update()
        )

    def get_compile_job(self, session: Session, compile_request_id: str) -> RuntimeJob | None:
        return session.scalar(select(RuntimeJob).where(RuntimeJob.compile_request_id == compile_request_id).limit(1))

    def claim_next_job(
        self,
        session: Session,
        *,
        job_types: Sequence[str] | None = None,
        job_type: str | None = None,
        lease_seconds: int,
        worker_name: str = "",
    ) -> JobLease | None:
        """Claim the oldest available job across one pool in a single transaction.

        ``job_type`` remains supported for callers that have not yet moved to pool
        claims. Production workers always pass ``job_types`` and a unique owner.
        """

        normalized_types = tuple(dict.fromkeys(job_types or (() if job_type is None else (job_type,))))
        if not normalized_types:
            raise ValueError("claim_next_job requires at least one job type.")
        now = now_utc()
        query = (
            select(RuntimeJob)
            .where(
                RuntimeJob.job_type.in_(normalized_types),
                RuntimeJob.status.in_(("pending", "retryable_failed")),
                RuntimeJob.available_at <= now,
                RuntimeJob.attempt_no < RuntimeJob.max_attempts,
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
        return JobLease.from_job(job)

    def renew_lease(
        self,
        session: Session,
        lease: JobLease,
        *,
        lease_seconds: int,
    ) -> JobLease | None:
        """Renew a lease only while the same attempt owner still owns the job."""

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
            return None
        job.lease_until = now_utc() + timedelta(seconds=lease_seconds)
        session.commit()
        session.refresh(job)
        return JobLease.from_job(job)

    def abandon_claim(
        self,
        session: Session,
        lease: JobLease,
        *,
        error_message: str,
        retry_delay_seconds: int = 1,
    ) -> bool:
        """Return a claim that could not establish its advisory lock."""

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
        job.status = "retryable_failed"
        job.available_at = now_utc() + timedelta(seconds=max(0, retry_delay_seconds))
        job.attempt_no = max(0, job.attempt_no - 1)
        job.worker_name = ""
        job.lease_until = None
        job.last_error = error_message
        session.commit()
        return True

    def list_expired_leases(self, session: Session, *, limit: int = 100) -> list[JobLease]:
        now = now_utc()
        jobs = session.scalars(
            select(RuntimeJob)
            .where(
                RuntimeJob.status == "running",
                RuntimeJob.lease_until.is_not(None),
                RuntimeJob.lease_until <= now,
            )
            .order_by(RuntimeJob.lease_until.asc(), RuntimeJob.created_at.asc())
            .limit(max(1, limit))
        ).all()
        return [JobLease.from_job(job) for job in jobs]

    def recover_expired_lease(
        self,
        session: Session,
        lease: JobLease,
        *,
        retry_delay_seconds: int,
        commit: bool = True,
    ) -> LeaseRecoveryResult | None:
        """Recover an expired attempt using owner and current-expiry fencing."""

        now = now_utc()
        job = session.scalar(
            select(RuntimeJob)
            .where(
                RuntimeJob.id == lease.job_id,
                RuntimeJob.status == "running",
                RuntimeJob.worker_name == lease.owner,
                RuntimeJob.lease_until.is_not(None),
                RuntimeJob.lease_until <= now,
            )
            .with_for_update(skip_locked=True)
        )
        if not job:
            session.rollback()
            return None

        exhausted = job.attempt_no >= job.max_attempts
        job.status = "failed" if exhausted else "retryable_failed"
        if not exhausted:
            job.available_at = now + timedelta(seconds=max(0, retry_delay_seconds))
        job.worker_name = ""
        job.lease_until = None
        job.last_error = f"worker lease expired (attempt {job.attempt_no}/{job.max_attempts})"
        metrics = dict(job.metrics or {})
        metrics["lease_recoveries"] = int(metrics.get("lease_recoveries") or 0) + 1
        job.metrics = metrics
        if commit:
            session.commit()
        else:
            session.flush()
        return LeaseRecoveryResult(
            job_id=job.id,
            job_type=job.job_type,
            status=job.status,
            attempt_no=job.attempt_no,
            exhausted=exhausted,
        )

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
            query = query.where(RuntimeJob.job_type == job_type)
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
            query = query.where(RuntimeJob.job_type == job_type)
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
