from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
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
    gateway_type: Mapped[str] = mapped_column(String(64), default="terminal", nullable=False)
    input_envelope: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    terminal_context: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    binding_preferences: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
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
    terminal_session_id: Mapped[str | None] = mapped_column(
        ForeignKey("terminal_session.id", ondelete="SET NULL"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(String(32), default="queued", nullable=False)
    runtime_phase: Mapped[str] = mapped_column(String(64), default="queued", nullable=False)
    latest_snapshot_seq: Mapped[int] = mapped_column(default=0, nullable=False)
    latest_terminal_seq: Mapped[int] = mapped_column(default=0, nullable=False)
    latest_trace_seq: Mapped[int] = mapped_column(default=0, nullable=False)
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
        UniqueConstraint("run_id", "seq_no", name="uk_session_token_snapshot_run_seq"),
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
        UniqueConstraint("run_id", "seq_no", name="uk_trace_event_run_seq"),
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


class TerminalSession(Base):
    __tablename__ = "terminal_session"
    __table_args__ = (
        Index("idx_terminal_session_status_opened_at", "status", "opened_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("run.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    mode: Mapped[str] = mapped_column(String(64), default="web", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="open", nullable=False)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)


class RunCapabilityBinding(Base):
    __tablename__ = "run_capability_binding"
    __table_args__ = (
        UniqueConstraint("run_id", "requirement_key", name="uk_run_capability_binding_run_requirement"),
        Index("idx_run_capability_binding_run_status", "run_id", "status"),
        Index("idx_run_capability_binding_target", "target_kind", "target_ref"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    run_id: Mapped[str] = mapped_column(ForeignKey("run.id", ondelete="CASCADE"), nullable=False)
    compile_artifact_id: Mapped[str] = mapped_column(
        ForeignKey("eg_compile_artifact.id", ondelete="RESTRICT"),
        nullable=False,
    )
    source_capability_binding_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    requirement_key: Mapped[str] = mapped_column(String(120), nullable=False)
    binding_type: Mapped[str] = mapped_column(String(64), nullable=False)
    capability: Mapped[str] = mapped_column(String(120), nullable=False)
    target_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    target_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    channel: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    schema_ref: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    manifest_hash: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    policy_snapshot: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=now_utc,
        onupdate=now_utc,
        nullable=False,
    )


class TerminalEvent(Base):
    __tablename__ = "terminal_event"
    __table_args__ = (
        UniqueConstraint("terminal_session_id", "seq_no", name="uk_terminal_event_session_seq"),
        UniqueConstraint("run_id", "external_event_id", name="uk_terminal_event_run_external"),
        Index("idx_terminal_event_run_seq", "run_id", "seq_no"),
        Index("idx_terminal_event_binding_seq", "run_capability_binding_id", "seq_no"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    terminal_session_id: Mapped[str] = mapped_column(
        ForeignKey("terminal_session.id", ondelete="CASCADE"),
        nullable=False,
    )
    run_id: Mapped[str] = mapped_column(ForeignKey("run.id", ondelete="CASCADE"), nullable=False)
    trace_event_id: Mapped[str | None] = mapped_column(ForeignKey("trace_event.id", ondelete="SET NULL"), nullable=True)
    artifact_object_id: Mapped[str | None] = mapped_column(
        ForeignKey("artifact_object.id", ondelete="SET NULL"),
        nullable=True,
    )
    run_capability_binding_id: Mapped[str | None] = mapped_column(
        ForeignKey("run_capability_binding.id", ondelete="SET NULL"),
        nullable=True,
    )
    direction: Mapped[str] = mapped_column(String(32), nullable=False)
    event_kind: Mapped[str] = mapped_column(String(120), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(255), default="text/plain", nullable=False)
    payload_inline: Mapped[Any | None] = mapped_column(JSON, nullable=True)
    seq_no: Mapped[int] = mapped_column(nullable=False)
    external_event_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_ref: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)


class TerminalEventPart(Base):
    __tablename__ = "terminal_event_part"
    __table_args__ = (
        UniqueConstraint("terminal_event_id", "part_id", name="uk_terminal_event_part_event_part"),
        UniqueConstraint("terminal_event_id", "order_index", name="uk_terminal_event_part_event_order"),
        Index("idx_terminal_event_part_event_order", "terminal_event_id", "order_index"),
        Index("idx_terminal_event_part_run", "run_id", "order_index"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    terminal_event_id: Mapped[str] = mapped_column(
        ForeignKey("terminal_event.id", ondelete="CASCADE"),
        nullable=False,
    )
    run_id: Mapped[str] = mapped_column(ForeignKey("run.id", ondelete="CASCADE"), nullable=False)
    artifact_object_id: Mapped[str | None] = mapped_column(
        ForeignKey("artifact_object.id", ondelete="SET NULL"),
        nullable=True,
    )
    part_id: Mapped[str] = mapped_column(String(120), nullable=False)
    order_index: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(255), default="text/plain", nullable=False)
    text_inline: Mapped[str] = mapped_column(Text, default="", nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    checksum: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    part_metadata: Mapped[dict] = mapped_column("metadata", JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)
