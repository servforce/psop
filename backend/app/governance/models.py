from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.database import Base
from app.pskills.models import generate_uuid, now_utc


class PsopImprovementProposal(Base):
    __tablename__ = "psop_improvement_proposal"
    __table_args__ = (
        Index("idx_psop_improvement_proposal_status_created_at", "status", "created_at"),
        Index("idx_psop_improvement_proposal_agent_run", "agent_run_id"),
        Index("idx_psop_improvement_proposal_source_run", "source_run_id"),
        Index("idx_psop_improvement_proposal_source_evaluation", "source_evaluation_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    agent_run_id: Mapped[str] = mapped_column(ForeignKey("agent_run.id", ondelete="RESTRICT"), nullable=False)
    source_finding_ids: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    source_evaluation_id: Mapped[str | None] = mapped_column(
        ForeignKey("run_evaluation.id", ondelete="SET NULL"),
        nullable=True,
    )
    source_run_id: Mapped[str | None] = mapped_column(ForeignKey("run.id", ondelete="SET NULL"), nullable=True)
    proposal_type: Mapped[str] = mapped_column(String(80), nullable=False)
    target_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    problem_statement: Mapped[str] = mapped_column(Text, default="", nullable=False)
    evidence_refs: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    proposed_changes: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    risk_assessment: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    required_tests: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    activation_plan: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="draft", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=now_utc,
        onupdate=now_utc,
        nullable=False,
    )


class PsopImprovementExperiment(Base):
    __tablename__ = "psop_improvement_experiment"
    __table_args__ = (
        Index("idx_psop_improvement_experiment_proposal_created_at", "proposal_id", "created_at"),
        Index("idx_psop_improvement_experiment_status", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    proposal_id: Mapped[str] = mapped_column(
        ForeignKey("psop_improvement_proposal.id", ondelete="CASCADE"),
        nullable=False,
    )
    experiment_type: Mapped[str] = mapped_column(String(60), nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="planned", nullable=False)
    summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    before_metrics: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    after_metrics: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    result_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)
