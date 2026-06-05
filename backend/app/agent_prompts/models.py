from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.pskills.models import generate_uuid, now_utc
from app.infra.database import Base


class AgentPromptDefinition(Base):
    __tablename__ = "agent_prompt_definition"
    __table_args__ = (
        Index("idx_agent_prompt_definition_scenario_status", "scenario", "status"),
        Index("uk_agent_prompt_definition_key", "key", unique=True),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    key: Mapped[str] = mapped_column(String(160), nullable=False)
    agent_id: Mapped[str] = mapped_column(String(255), nullable=False)
    scenario: Mapped[str] = mapped_column(String(120), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)
    active_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=now_utc,
        onupdate=now_utc,
        nullable=False,
    )

    versions: Mapped[list["AgentPromptVersion"]] = relationship(
        back_populates="definition",
        cascade="all, delete-orphan",
    )
    bindings: Mapped[list["AgentPromptBinding"]] = relationship(
        back_populates="definition",
        cascade="all, delete-orphan",
    )


class AgentPromptVersion(Base):
    __tablename__ = "agent_prompt_version"
    __table_args__ = (
        Index("idx_agent_prompt_version_definition_status", "definition_id", "status"),
        Index("uk_agent_prompt_version_definition_no", "definition_id", "version_no", unique=True),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    definition_id: Mapped[str] = mapped_column(
        ForeignKey("agent_prompt_definition.id", ondelete="CASCADE"),
        nullable=False,
    )
    version_no: Mapped[int] = mapped_column(nullable=False)
    version_label: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="draft", nullable=False)
    route_key: Mapped[str] = mapped_column(String(120), default="text", nullable=False)
    files: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    parent_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=now_utc,
        onupdate=now_utc,
        nullable=False,
    )

    definition: Mapped["AgentPromptDefinition"] = relationship(back_populates="versions")


class AgentPromptBinding(Base):
    __tablename__ = "agent_prompt_binding"
    __table_args__ = (
        Index("uk_agent_prompt_binding_usage_key", "usage_key", unique=True),
        Index("idx_agent_prompt_binding_definition", "definition_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    usage_key: Mapped[str] = mapped_column(String(160), nullable=False)
    definition_id: Mapped[str] = mapped_column(
        ForeignKey("agent_prompt_definition.id", ondelete="CASCADE"),
        nullable=False,
    )
    active_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_prompt_version.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=now_utc,
        onupdate=now_utc,
        nullable=False,
    )

    definition: Mapped["AgentPromptDefinition"] = relationship(back_populates="bindings")
