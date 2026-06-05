from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infra.database import Base
from app.pskills.models import generate_uuid, now_utc


class AgentDefinition(Base):
    __tablename__ = "agent_definition"
    __table_args__ = (
        Index("uk_agent_definition_key", "key", unique=True),
        Index("idx_agent_definition_status", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    key: Mapped[str] = mapped_column(String(160), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="active", nullable=False)
    active_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=now_utc,
        onupdate=now_utc,
        nullable=False,
    )

    versions: Mapped[list["AgentVersion"]] = relationship(
        back_populates="definition",
        cascade="all, delete-orphan",
    )
    bindings: Mapped[list["AgentBinding"]] = relationship(
        back_populates="definition",
        cascade="all, delete-orphan",
    )


class AgentVersion(Base):
    __tablename__ = "agent_version"
    __table_args__ = (
        Index("idx_agent_version_definition_status", "definition_id", "status"),
        Index("uk_agent_version_definition_no", "definition_id", "version_no", unique=True),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    definition_id: Mapped[str] = mapped_column(
        ForeignKey("agent_definition.id", ondelete="CASCADE"),
        nullable=False,
    )
    version_no: Mapped[int] = mapped_column(nullable=False)
    version_label: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="draft", nullable=False)
    spec_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=now_utc,
        onupdate=now_utc,
        nullable=False,
    )

    definition: Mapped["AgentDefinition"] = relationship(back_populates="versions")


class AgentBinding(Base):
    __tablename__ = "agent_binding"
    __table_args__ = (
        Index("uk_agent_binding_usage_key", "usage_key", unique=True),
        Index("idx_agent_binding_definition", "definition_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    usage_key: Mapped[str] = mapped_column(String(160), nullable=False)
    definition_id: Mapped[str] = mapped_column(
        ForeignKey("agent_definition.id", ondelete="CASCADE"),
        nullable=False,
    )
    active_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_version.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=now_utc,
        onupdate=now_utc,
        nullable=False,
    )

    definition: Mapped["AgentDefinition"] = relationship(back_populates="bindings")


class AgentRun(Base):
    __tablename__ = "agent_run"
    __table_args__ = (
        Index("idx_agent_run_definition_status_updated_at", "definition_id", "status", "updated_at"),
        Index("idx_agent_run_owner", "owner_type", "owner_id"),
        Index("idx_agent_run_status_created_at", "status", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    definition_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_definition.id", ondelete="SET NULL"),
        nullable=True,
    )
    agent_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_version.id", ondelete="SET NULL"),
        nullable=True,
    )
    agent_key: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="queued", nullable=False)
    owner_type: Mapped[str] = mapped_column(String(80), default="", nullable=False)
    owner_id: Mapped[str] = mapped_column(String(80), default="", nullable=False)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("run.id", ondelete="SET NULL"), nullable=True)
    input_payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    output_payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    error_message: Mapped[str] = mapped_column(Text, default="", nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=now_utc,
        onupdate=now_utc,
        nullable=False,
    )


class AgentEvent(Base):
    __tablename__ = "agent_event"
    __table_args__ = (
        Index("idx_agent_event_run_seq", "agent_run_id", "seq_no"),
        Index("idx_agent_event_type", "event_type"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    agent_run_id: Mapped[str] = mapped_column(
        ForeignKey("agent_run.id", ondelete="CASCADE"),
        nullable=False,
    )
    seq_no: Mapped[int] = mapped_column(nullable=False)
    event_type: Mapped[str] = mapped_column(String(160), nullable=False)
    phase: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)


class AgentToolCall(Base):
    __tablename__ = "agent_tool_call"
    __table_args__ = (
        Index("idx_agent_tool_call_run_status", "agent_run_id", "status"),
        Index("idx_agent_tool_call_tool_name", "tool_name"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    agent_run_id: Mapped[str] = mapped_column(
        ForeignKey("agent_run.id", ondelete="CASCADE"),
        nullable=False,
    )
    tool_name: Mapped[str] = mapped_column(String(160), nullable=False)
    tool_provider: Mapped[str] = mapped_column(String(60), default="native", nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="planned", nullable=False)
    arguments_summary: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    result_summary: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    side_effect_level: Mapped[str] = mapped_column(String(60), default="read", nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=now_utc,
        onupdate=now_utc,
        nullable=False,
    )


class AgentToolAuthorization(Base):
    __tablename__ = "agent_tool_authorization"
    __table_args__ = (
        Index("idx_agent_tool_authorization_run_status", "agent_run_id", "status"),
        Index("idx_agent_tool_authorization_status_created_at", "status", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    agent_run_id: Mapped[str] = mapped_column(
        ForeignKey("agent_run.id", ondelete="CASCADE"),
        nullable=False,
    )
    agent_tool_call_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_tool_call.id", ondelete="SET NULL"),
        nullable=True,
    )
    run_id: Mapped[str | None] = mapped_column(ForeignKey("run.id", ondelete="SET NULL"), nullable=True)
    run_event_id: Mapped[str | None] = mapped_column(ForeignKey("run_event.id", ondelete="SET NULL"), nullable=True)
    tool_name: Mapped[str] = mapped_column(String(160), nullable=False)
    tool_provider: Mapped[str] = mapped_column(String(60), default="native", nullable=False)
    mcp_server_name: Mapped[str] = mapped_column(String(160), default="", nullable=False)
    side_effect_level: Mapped[str] = mapped_column(String(60), nullable=False)
    risk_level: Mapped[str] = mapped_column(String(40), default="medium", nullable=False)
    authorization_reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    tool_arguments_summary: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    expected_effect_summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    reversible: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="pending", nullable=False)
    request_payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    response_payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
