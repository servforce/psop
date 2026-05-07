from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.skills.models import generate_uuid, now_utc
from app.infra.database import Base


class SkillTestCase(Base):
    __tablename__ = "skill_test_case"
    __table_args__ = (
        Index("idx_skill_test_case_skill_status_updated_at", "skill_definition_id", "status", "updated_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    skill_definition_id: Mapped[str] = mapped_column(
        ForeignKey("skill_definition.id", ondelete="CASCADE"),
        nullable=False,
    )
    target_compile_artifact_id: Mapped[str | None] = mapped_column(
        ForeignKey("eg_compile_artifact.id", ondelete="SET NULL"),
        nullable=True,
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    target_version_selector: Mapped[str] = mapped_column(String(120), default="latest", nullable=False)
    input_envelope: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    terminal_context: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    assertions: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=now_utc,
        onupdate=now_utc,
        nullable=False,
    )


class SkillTestDataObject(Base):
    __tablename__ = "skill_test_data_object"
    __table_args__ = (
        Index("idx_skill_test_data_case_created_at", "test_case_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    skill_definition_id: Mapped[str] = mapped_column(
        ForeignKey("skill_definition.id", ondelete="CASCADE"),
        nullable=False,
    )
    test_case_id: Mapped[str] = mapped_column(
        ForeignKey("skill_test_case.id", ondelete="CASCADE"),
        nullable=False,
    )
    artifact_object_id: Mapped[str] = mapped_column(
        ForeignKey("artifact_object.id", ondelete="RESTRICT"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    role: Mapped[str] = mapped_column(String(80), default="input", nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(255), nullable=False)
    size_bytes: Mapped[int] = mapped_column(default=0, nullable=False)
    checksum: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)


class SkillTestRun(Base):
    __tablename__ = "skill_test_run"
    __table_args__ = (
        Index("idx_skill_test_run_case_created_at", "test_case_id", "created_at"),
        Index("idx_skill_test_run_status_created_at", "status", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    skill_definition_id: Mapped[str] = mapped_column(
        ForeignKey("skill_definition.id", ondelete="CASCADE"),
        nullable=False,
    )
    test_case_id: Mapped[str] = mapped_column(
        ForeignKey("skill_test_case.id", ondelete="CASCADE"),
        nullable=False,
    )
    invocation_id: Mapped[str | None] = mapped_column(
        ForeignKey("skill_invocation.id", ondelete="SET NULL"),
        nullable=True,
    )
    run_id: Mapped[str | None] = mapped_column(
        ForeignKey("run.id", ondelete="SET NULL"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    selected_data_object_ids: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    input_envelope: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    assertion_results: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    assertion_summary: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=now_utc,
        onupdate=now_utc,
        nullable=False,
    )
