from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class GovernanceProposalCreateRequest(BaseModel):
    proposal_type: str = Field(default="pskill_template_update", min_length=2, max_length=80)
    target: dict[str, Any] = Field(default_factory=dict)
    problem_statement: str = Field(min_length=2)
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)
    proposed_changes: list[dict[str, Any]] = Field(default_factory=list)
    risk_assessment: dict[str, Any] = Field(default_factory=dict)
    required_tests: list[dict[str, Any]] = Field(default_factory=list)
    activation_plan: dict[str, Any] = Field(default_factory=dict)
    source_finding_ids: list[str] = Field(default_factory=list)
    source_evaluation_id: str | None = None
    source_run_id: str | None = None


class GovernanceReviewRequest(BaseModel):
    decision: str | None = Field(default=None, max_length=40)
    review_notes: str = ""


class GovernanceExperimentResponse(BaseModel):
    id: str
    proposal_id: str
    experiment_type: str
    status: str
    summary: str
    before_metrics: dict[str, Any]
    after_metrics: dict[str, Any]
    result: dict[str, Any]
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime


class GovernanceProposalResponse(BaseModel):
    id: str
    agent_run_id: str
    source_finding_ids: list[str] = Field(default_factory=list)
    source_evaluation_id: str | None = None
    source_run_id: str | None = None
    proposal_type: str
    target: dict[str, Any]
    problem_statement: str
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)
    proposed_changes: list[dict[str, Any]] = Field(default_factory=list)
    risk_assessment: dict[str, Any]
    required_tests: list[dict[str, Any]] = Field(default_factory=list)
    activation_plan: dict[str, Any]
    status: str
    experiments: list[GovernanceExperimentResponse] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
