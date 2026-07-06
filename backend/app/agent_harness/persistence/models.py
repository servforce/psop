from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.skills.models import generate_uuid, now_utc
from app.infra.database import Base


class AgentRunRecord(Base):
    __tablename__ = "agent_run"
    __table_args__ = (
        Index("idx_agent_run_key_status_created_at", "agent_key", "status", "created_at"),
        Index("idx_agent_run_related_generation", "related_generation_id"),
        Index("idx_agent_run_related_runtime_run", "related_runtime_run_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    agent_key: Mapped[str] = mapped_column(String(160), nullable=False)
    agent_version: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="running", nullable=False)
    related_skill_definition_id: Mapped[str] = mapped_column(String(36), default="", nullable=False)
    related_generation_id: Mapped[str] = mapped_column(String(36), default="", nullable=False)
    related_job_id: Mapped[str] = mapped_column(String(36), default="", nullable=False)
    related_runtime_run_id: Mapped[str] = mapped_column(String(36), default="", nullable=False)
    input_summary: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    sandbox_path: Mapped[str] = mapped_column(Text, default="", nullable=False)
    model_info: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    error_message: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=now_utc,
        onupdate=now_utc,
        nullable=False,
    )


class AgentEventRecord(Base):
    __tablename__ = "agent_event"
    __table_args__ = (
        Index("idx_agent_event_run_seq", "agent_run_id", "seq_no", unique=True),
        Index("idx_agent_event_type", "event_type"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    agent_run_id: Mapped[str] = mapped_column(ForeignKey("agent_run.id", ondelete="CASCADE"), nullable=False)
    seq_no: Mapped[int] = mapped_column(nullable=False)
    event_type: Mapped[str] = mapped_column(String(160), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AgentArtifactRecord(Base):
    __tablename__ = "agent_artifact"
    __table_args__ = (
        Index("idx_agent_artifact_run_type", "agent_run_id", "artifact_type"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    agent_run_id: Mapped[str] = mapped_column(ForeignKey("agent_run.id", ondelete="CASCADE"), nullable=False)
    artifact_type: Mapped[str] = mapped_column(String(120), nullable=False)
    path: Mapped[str] = mapped_column(Text, default="", nullable=False)
    content_hash: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    provenance: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="draft", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)
