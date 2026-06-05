from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.pskills.models import generate_uuid, now_utc
from app.infra.database import Base


class SkillTestScenario(Base):
    __tablename__ = "skill_test_scenario"
    __table_args__ = (
        Index("idx_skill_test_scenario_skill_status_updated_at", "pskill_definition_id", "status", "updated_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    pskill_definition_id: Mapped[str] = mapped_column(
        ForeignKey("pskill_definition.id", ondelete="CASCADE"),
        nullable=False,
    )
    target_compile_artifact_id: Mapped[str | None] = mapped_column(
        ForeignKey("eg_compile_artifact.id", ondelete="SET NULL"),
        nullable=True,
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    target_version_selector: Mapped[str] = mapped_column(String(120), default="latest", nullable=False)
    duration_ms: Mapped[int] = mapped_column(default=1_800_000, nullable=False)
    timeline: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    judge_policy: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    fork_seed: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=now_utc,
        onupdate=now_utc,
        nullable=False,
    )


class SkillTestAsset(Base):
    __tablename__ = "skill_test_asset"
    __table_args__ = (
        Index("idx_skill_test_asset_scenario_created_at", "scenario_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    pskill_definition_id: Mapped[str] = mapped_column(
        ForeignKey("pskill_definition.id", ondelete="CASCADE"),
        nullable=False,
    )
    scenario_id: Mapped[str] = mapped_column(
        ForeignKey("skill_test_scenario.id", ondelete="CASCADE"),
        nullable=False,
    )
    artifact_object_id: Mapped[str] = mapped_column(
        ForeignKey("artifact_object.id", ondelete="RESTRICT"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    lane_id: Mapped[str] = mapped_column(String(120), default="input.file", nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(255), nullable=False)
    size_bytes: Mapped[int] = mapped_column(default=0, nullable=False)
    checksum: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)


class SkillTestScenarioRun(Base):
    __tablename__ = "skill_test_scenario_run"
    __table_args__ = (
        Index("idx_skill_test_scenario_run_scenario_created_at", "scenario_id", "created_at"),
        Index("idx_skill_test_scenario_run_status_created_at", "status", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    pskill_definition_id: Mapped[str] = mapped_column(
        ForeignKey("pskill_definition.id", ondelete="CASCADE"),
        nullable=False,
    )
    scenario_id: Mapped[str] = mapped_column(
        ForeignKey("skill_test_scenario.id", ondelete="CASCADE"),
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
    driver_status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    driver_cursor: Mapped[int] = mapped_column(default=0, nullable=False)
    driver_events: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    timeline: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    result_summary: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    time_origin: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=now_utc,
        onupdate=now_utc,
        nullable=False,
    )


class SkillTestExpectationEvaluation(Base):
    __tablename__ = "skill_test_expectation_evaluation"
    __table_args__ = (
        Index("idx_skill_test_expectation_eval_run_created_at", "scenario_run_id", "created_at"),
        Index("idx_skill_test_expectation_eval_run_expectation", "scenario_run_id", "expectation_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    scenario_run_id: Mapped[str] = mapped_column(
        ForeignKey("skill_test_scenario_run.id", ondelete="CASCADE"),
        nullable=False,
    )
    expectation_id: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence: Mapped[float] = mapped_column(default=0.0, nullable=False)
    reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    evidence_refs: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    judge_provider: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    judge_model: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    prompt_hash: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    raw_response: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)
