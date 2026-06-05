from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.pskills.models import generate_uuid, now_utc
from app.infra.database import Base


class SkillCompileRequest(Base):
    __tablename__ = "skill_compile_request"
    __table_args__ = (
        Index("idx_skill_compile_request_status_requested_at", "status", "requested_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    pskill_definition_id: Mapped[str] = mapped_column(
        ForeignKey("pskill_definition.id", ondelete="CASCADE"),
        nullable=False,
    )
    pskill_version_id: Mapped[str] = mapped_column(
        ForeignKey("pskill_version.id", ondelete="CASCADE"),
        nullable=False,
    )
    trigger_type: Mapped[str] = mapped_column(String(32), default="publish", nullable=False)
    source_commit_sha: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=now_utc,
        onupdate=now_utc,
        nullable=False,
    )


class ArtifactObject(Base):
    __tablename__ = "artifact_object"
    __table_args__ = (
        Index("idx_artifact_object_media_type_created_at", "media_type", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    bucket: Mapped[str] = mapped_column(String(255), nullable=False)
    object_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    media_type: Mapped[str] = mapped_column(String(255), default="application/json", nullable=False)
    size_bytes: Mapped[int] = mapped_column(default=0, nullable=False)
    checksum: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    content_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)


class EgCompileArtifact(Base):
    __tablename__ = "eg_compile_artifact"
    __table_args__ = (
        Index("idx_eg_compile_artifact_version_status", "pskill_version_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    skill_compile_request_id: Mapped[str] = mapped_column(
        ForeignKey("skill_compile_request.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    pskill_version_id: Mapped[str] = mapped_column(
        ForeignKey("pskill_version.id", ondelete="CASCADE"),
        nullable=False,
    )
    artifact_object_id: Mapped[str] = mapped_column(
        ForeignKey("artifact_object.id", ondelete="RESTRICT"),
        nullable=False,
    )
    formal_revision: Mapped[str] = mapped_column(String(120), nullable=False)
    artifact_version: Mapped[str] = mapped_column(String(120), default="psop-eg-mvp/v1", nullable=False)
    graph_summary: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    capability_summary: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="ready", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)


class CompileDiagnostic(Base):
    __tablename__ = "compile_diagnostic"
    __table_args__ = (
        Index("idx_compile_diagnostic_request_severity", "skill_compile_request_id", "severity"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    skill_compile_request_id: Mapped[str] = mapped_column(
        ForeignKey("skill_compile_request.id", ondelete="CASCADE"),
        nullable=False,
    )
    pskill_version_id: Mapped[str] = mapped_column(
        ForeignKey("pskill_version.id", ondelete="CASCADE"),
        nullable=False,
    )
    severity: Mapped[str] = mapped_column(String(32), default="info", nullable=False)
    code: Mapped[str] = mapped_column(String(120), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    location: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    category: Mapped[str] = mapped_column(String(64), default="compiler", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)

