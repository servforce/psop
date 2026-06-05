from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, event, ForeignKey, Index, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.pskills.models import generate_uuid, now_utc
from app.infra.database import Base


TERMINAL_JOB_STATUSES = {"succeeded", "failed", "cancelled", "canceled", "deadletter", "dead_letter"}


class RuntimeJob(Base):
    __tablename__ = "runtime_job"
    __table_args__ = (
        Index("idx_runtime_job_status_available_at", "status", "available_at"),
        Index("idx_runtime_job_lease_until", "lease_until"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    job_type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("run.id", ondelete="CASCADE"), nullable=True)
    compile_request_id: Mapped[str | None] = mapped_column(
        ForeignKey("skill_compile_request.id", ondelete="CASCADE"),
        nullable=True,
    )
    worker_name: Mapped[str] = mapped_column(String(160), default="", nullable=False)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    attempt_no: Mapped[int] = mapped_column(default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(default=3, nullable=False)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    last_error: Mapped[str] = mapped_column(Text, default="", nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=now_utc,
        onupdate=now_utc,
        nullable=False,
    )


@event.listens_for(RuntimeJob.status, "set")
def _sync_runtime_job_observed_timestamps(
    target: RuntimeJob,
    value: str,
    oldvalue: str,
    initiator,
) -> None:
    if value == "running" and target.started_at is None:
        target.started_at = now_utc()
    if value in TERMINAL_JOB_STATUSES:
        if target.finished_at is None:
            target.finished_at = now_utc()
    elif oldvalue in TERMINAL_JOB_STATUSES:
        target.finished_at = None
