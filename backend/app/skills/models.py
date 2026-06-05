from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infra.database import Base
from app.pskills.models import generate_uuid, now_utc


class SkillPackage(Base):
    __tablename__ = "skill_package"
    __table_args__ = (
        Index("uk_skill_package_name", "name", unique=True),
        Index("idx_skill_package_scope_status", "scope", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    scope: Mapped[str] = mapped_column(String(40), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    source_uri: Mapped[str] = mapped_column(Text, default="", nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="active", nullable=False)
    active_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=now_utc,
        onupdate=now_utc,
        nullable=False,
    )

    versions: Mapped[list["SkillVersion"]] = relationship(
        back_populates="package",
        cascade="all, delete-orphan",
    )


class SkillVersion(Base):
    __tablename__ = "skill_version"
    __table_args__ = (
        Index("idx_skill_version_package_status", "package_id", "status"),
        Index("uk_skill_version_package_hash", "package_id", "content_hash", unique=True),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    package_id: Mapped[str] = mapped_column(ForeignKey("skill_package.id", ondelete="CASCADE"), nullable=False)
    version_label: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="active", nullable=False)
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    manifest_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    body_object_key: Mapped[str] = mapped_column(Text, default="", nullable=False)
    resource_index: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    allowed_tools: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    validation_status: Mapped[str] = mapped_column(String(40), default="valid", nullable=False)
    validation_diagnostics: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=now_utc,
        onupdate=now_utc,
        nullable=False,
    )

    package: Mapped["SkillPackage"] = relationship(back_populates="versions")
    resources: Mapped[list["SkillResource"]] = relationship(
        back_populates="version",
        cascade="all, delete-orphan",
    )


class SkillResource(Base):
    __tablename__ = "skill_resource"
    __table_args__ = (
        Index("idx_skill_resource_version_path", "version_id", "resource_path"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    version_id: Mapped[str] = mapped_column(ForeignKey("skill_version.id", ondelete="CASCADE"), nullable=False)
    resource_path: Mapped[str] = mapped_column(Text, nullable=False)
    resource_kind: Mapped[str] = mapped_column(String(40), default="file", nullable=False)
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    size_bytes: Mapped[int] = mapped_column(default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)

    version: Mapped["SkillVersion"] = relationship(back_populates="resources")


class SkillBinding(Base):
    __tablename__ = "skill_binding"
    __table_args__ = (
        Index("idx_skill_binding_agent_usage", "agent_key", "usage_key"),
        Index("idx_skill_binding_package", "package_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    agent_key: Mapped[str] = mapped_column(String(160), nullable=False)
    usage_key: Mapped[str] = mapped_column(String(160), nullable=False)
    package_id: Mapped[str] = mapped_column(ForeignKey("skill_package.id", ondelete="CASCADE"), nullable=False)
    active_version_id: Mapped[str | None] = mapped_column(ForeignKey("skill_version.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=now_utc,
        onupdate=now_utc,
        nullable=False,
    )


class SkillActivation(Base):
    __tablename__ = "skill_activation"
    __table_args__ = (
        Index("idx_skill_activation_agent_run", "agent_run_id"),
        Index("idx_skill_activation_version", "version_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    agent_run_id: Mapped[str] = mapped_column(ForeignKey("agent_run.id", ondelete="CASCADE"), nullable=False)
    package_id: Mapped[str] = mapped_column(ForeignKey("skill_package.id", ondelete="CASCADE"), nullable=False)
    version_id: Mapped[str] = mapped_column(ForeignKey("skill_version.id", ondelete="CASCADE"), nullable=False)
    activation_context: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)
