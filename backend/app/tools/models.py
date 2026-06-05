from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.database import Base
from app.pskills.models import generate_uuid, now_utc


class ToolDefinition(Base):
    __tablename__ = "tool_definition"
    __table_args__ = (
        Index("uk_tool_definition_name", "name", unique=True),
        Index("idx_tool_definition_side_effect", "side_effect_level"),
        Index("idx_tool_definition_status", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    provider: Mapped[str] = mapped_column(String(60), default="native", nullable=False)
    side_effect_level: Mapped[str] = mapped_column(String(60), nullable=False)
    requires_authorization: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    input_schema_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    output_schema_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="active", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=now_utc,
        onupdate=now_utc,
        nullable=False,
    )
