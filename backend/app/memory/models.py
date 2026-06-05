from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.database import Base
from app.pskills.models import generate_uuid, now_utc


class AgentMemoryEntry(Base):
    __tablename__ = "agent_memory_entry"
    __table_args__ = (
        Index("idx_agent_memory_entry_namespace_type_status", "namespace", "memory_type", "status"),
        Index("idx_agent_memory_entry_agent_status", "agent_key", "status"),
        Index("idx_agent_memory_entry_created_by_run", "created_by_agent_run_id"),
        Index("idx_agent_memory_entry_created_at", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    namespace: Mapped[str] = mapped_column(String(160), default="default", nullable=False)
    memory_type: Mapped[str] = mapped_column(String(40), nullable=False)
    agent_key: Mapped[str] = mapped_column(String(160), default="", nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="pending_review", nullable=False)
    confidence: Mapped[int] = mapped_column(Integer, default=50, nullable=False)
    title: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    content: Mapped[str] = mapped_column(Text, default="", nullable=False)
    source_refs: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    tags: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_by_agent_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_run.id", ondelete="SET NULL"),
        nullable=True,
    )
    reviewed_by_agent_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_run.id", ondelete="SET NULL"),
        nullable=True,
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=now_utc,
        onupdate=now_utc,
        nullable=False,
    )
