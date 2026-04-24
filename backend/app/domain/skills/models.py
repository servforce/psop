from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Index, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infra.database import Base


def generate_uuid() -> str:
    return str(uuid4())


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


class SkillDefinition(Base):
    __tablename__ = "skill_definition"
    __table_args__ = (
        Index("idx_skill_definition_status_updated_at", "status", "updated_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    key: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)
    gitlab_group_path: Mapped[str] = mapped_column(String(255), default="skills", nullable=False)
    gitlab_project_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    repository_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    default_branch: Mapped[str] = mapped_column(String(255), default="main", nullable=False)
    manifest_path: Mapped[str] = mapped_column(String(255), default="skill.yaml", nullable=False)
    latest_draft_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    latest_published_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=now_utc,
        onupdate=now_utc,
        nullable=False,
    )

    versions: Mapped[list["SkillVersion"]] = relationship(
        back_populates="skill_definition",
        cascade="all, delete-orphan",
    )
    publish_records: Mapped[list["SkillPublishRecord"]] = relationship(
        back_populates="skill_definition",
        cascade="all, delete-orphan",
    )


class SkillVersion(Base):
    __tablename__ = "skill_version"
    __table_args__ = (
        Index("idx_skill_version_definition_status", "skill_definition_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    skill_definition_id: Mapped[str] = mapped_column(
        ForeignKey("skill_definition.id", ondelete="CASCADE"),
        nullable=False,
    )
    version_no: Mapped[int] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="draft", nullable=False)
    source_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    source_commit_sha: Mapped[str | None] = mapped_column(String(255), nullable=True)
    manifest_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    runtime_policy_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=now_utc,
        onupdate=now_utc,
        nullable=False,
    )

    skill_definition: Mapped["SkillDefinition"] = relationship(back_populates="versions")


class SkillPublishRecord(Base):
    __tablename__ = "skill_publish_record"
    __table_args__ = (
        Index("idx_skill_publish_record_definition_published_at", "skill_definition_id", "published_at"),
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
    publish_reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    publish_status: Mapped[str] = mapped_column(String(32), default="requested", nullable=False)
    published_commit_sha: Mapped[str] = mapped_column(String(255), nullable=False)
    release_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)

    skill_definition: Mapped["SkillDefinition"] = relationship(back_populates="publish_records")
