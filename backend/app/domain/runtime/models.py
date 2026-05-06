from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.skills.models import generate_uuid, now_utc
from app.infra.database import Base


class SkillInvocation(Base):
    __tablename__ = "skill_invocation"
    __table_args__ = (
        Index("idx_skill_invocation_status_created_at", "status", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    skill_definition_id: Mapped[str] = mapped_column(
        ForeignKey("skill_definition.id", ondelete="CASCADE"),
        nullable=False,
    )
    skill_version_id: Mapped[str] = mapped_column(
        ForeignKey("skill_version.id", ondelete="CASCADE"),
        nullable=False,
    )
    compile_artifact_id: Mapped[str] = mapped_column(
        ForeignKey("eg_compile_artifact.id", ondelete="RESTRICT"),
        nullable=False,
    )
    gateway_type: Mapped[str] = mapped_column(String(64), default="web", nullable=False)
    input_envelope: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="accepted", nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=now_utc,
        onupdate=now_utc,
        nullable=False,
    )


class Run(Base):
    __tablename__ = "run"
    __table_args__ = (
        Index("idx_run_status_updated_at", "status", "updated_at"),
        Index("idx_run_skill_definition_created_at", "skill_definition_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    invocation_id: Mapped[str] = mapped_column(
        ForeignKey("skill_invocation.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    skill_definition_id: Mapped[str] = mapped_column(
        ForeignKey("skill_definition.id", ondelete="CASCADE"),
        nullable=False,
    )
    skill_version_id: Mapped[str] = mapped_column(
        ForeignKey("skill_version.id", ondelete="CASCADE"),
        nullable=False,
    )
    compile_artifact_id: Mapped[str] = mapped_column(
        ForeignKey("eg_compile_artifact.id", ondelete="RESTRICT"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(32), default="queued", nullable=False)
    runtime_phase: Mapped[str] = mapped_column(String(64), default="queued", nullable=False)
    latest_snapshot_seq: Mapped[int] = mapped_column(default=0, nullable=False)
    final_output: Mapped[str] = mapped_column(Text, default="", nullable=False)
    exit_reason: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=now_utc,
        onupdate=now_utc,
        nullable=False,
    )


class SessionTokenSnapshot(Base):
    __tablename__ = "session_token_snapshot"
    __table_args__ = (
        Index("idx_session_token_snapshot_run_seq", "run_id", "seq_no"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    run_id: Mapped[str] = mapped_column(ForeignKey("run.id", ondelete="CASCADE"), nullable=False)
    seq_no: Mapped[int] = mapped_column(nullable=False)
    token_payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    enabled_set: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    selection_summary: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    snapshot_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)


class TraceEvent(Base):
    __tablename__ = "trace_event"
    __table_args__ = (
        Index("idx_trace_event_run_phase_seq", "run_id", "phase", "seq_no"),
        Index("idx_trace_event_span_id", "span_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    run_id: Mapped[str] = mapped_column(ForeignKey("run.id", ondelete="CASCADE"), nullable=False)
    seq_no: Mapped[int] = mapped_column(nullable=False)
    phase: Mapped[str] = mapped_column(String(64), nullable=False)
    event_type: Mapped[str] = mapped_column(String(120), nullable=False)
    span_id: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    parent_span_id: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)
