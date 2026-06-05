from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.database import Base
from app.pskills.models import generate_uuid, now_utc


class RunEvaluation(Base):
    __tablename__ = "run_evaluation"
    __table_args__ = (
        Index("idx_run_evaluation_run_created_at", "run_id", "created_at"),
        Index("idx_run_evaluation_pskill_created_at", "pskill_definition_id", "created_at"),
        Index("idx_run_evaluation_agent_run", "agent_run_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    run_id: Mapped[str] = mapped_column(ForeignKey("run.id", ondelete="CASCADE"), nullable=False)
    pskill_definition_id: Mapped[str] = mapped_column(
        ForeignKey("pskill_definition.id", ondelete="CASCADE"),
        nullable=False,
    )
    pskill_version_id: Mapped[str] = mapped_column(
        ForeignKey("pskill_version.id", ondelete="CASCADE"),
        nullable=False,
    )
    artifact_id: Mapped[str] = mapped_column(ForeignKey("eg_compile_artifact.id", ondelete="RESTRICT"), nullable=False)
    agent_run_id: Mapped[str] = mapped_column(ForeignKey("agent_run.id", ondelete="RESTRICT"), nullable=False)
    overall_outcome: Mapped[str] = mapped_column(String(60), nullable=False)
    quality_score: Mapped[int] = mapped_column(Integer, nullable=False)
    summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    attribution_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)


class RunEvaluationFinding(Base):
    __tablename__ = "run_evaluation_finding"
    __table_args__ = (
        Index("idx_run_evaluation_finding_evaluation", "evaluation_id", "created_at"),
        Index("idx_run_evaluation_finding_status_severity", "status", "severity"),
        Index("idx_run_evaluation_finding_category_status", "category", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    evaluation_id: Mapped[str] = mapped_column(
        ForeignKey("run_evaluation.id", ondelete="CASCADE"),
        nullable=False,
    )
    category: Mapped[str] = mapped_column(String(80), nullable=False)
    severity: Mapped[str] = mapped_column(String(40), nullable=False)
    confidence: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    evidence_refs: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    recommended_action: Mapped[str] = mapped_column(Text, default="", nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="open", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)
