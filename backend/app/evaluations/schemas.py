from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class RunEvaluationFindingResponse(BaseModel):
    id: str
    evaluation_id: str
    run_id: str = ""
    pskill_definition_id: str = ""
    pskill_version_id: str = ""
    category: str
    severity: str
    confidence: int
    description: str
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)
    recommended_action: str
    status: str
    created_at: datetime


class RunEvaluationResponse(BaseModel):
    id: str
    run_id: str
    pskill_definition_id: str
    pskill_version_id: str
    artifact_id: str
    agent_run_id: str
    overall_outcome: str
    quality_score: int
    summary: str
    attribution: dict[str, Any]
    findings: list[RunEvaluationFindingResponse] = Field(default_factory=list)
    created_at: datetime


class UpdateRunEvaluationFindingRequest(BaseModel):
    status: str = Field(min_length=2, max_length=40)
