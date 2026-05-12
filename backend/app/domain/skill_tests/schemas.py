from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class SkillTestScenarioRunSummary(BaseModel):
    id: str
    status: str
    driver_status: str
    run_id: str | None = None
    result_summary: dict[str, Any]
    created_at: datetime
    ended_at: datetime | None = None


class SkillTestScenarioCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str = ""
    target_version_selector: str = Field(default="latest", max_length=120)
    target_compile_artifact_id: str | None = None
    duration_ms: int = Field(default=1_800_000, ge=1)
    timeline: dict[str, Any] = Field(default_factory=dict)
    judge_policy: dict[str, Any] = Field(default_factory=dict)
    fork_seed: dict[str, Any] = Field(default_factory=dict)


class SkillTestScenarioUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = None
    target_version_selector: str | None = Field(default=None, max_length=120)
    target_compile_artifact_id: str | None = None
    duration_ms: int | None = Field(default=None, ge=1)
    timeline: dict[str, Any] | None = None
    judge_policy: dict[str, Any] | None = None
    fork_seed: dict[str, Any] | None = None
    status: str | None = Field(default=None, max_length=32)


class SkillTestScenarioResponse(BaseModel):
    id: str
    skill_definition_id: str
    name: str
    description: str
    target_version_selector: str
    target_compile_artifact_id: str | None = None
    duration_ms: int
    timeline: dict[str, Any]
    judge_policy: dict[str, Any]
    fork_seed: dict[str, Any]
    status: str
    latest_run: SkillTestScenarioRunSummary | None = None
    created_at: datetime
    updated_at: datetime


class SkillTestAssetResponse(BaseModel):
    id: str
    skill_definition_id: str
    scenario_id: str
    artifact_object_id: str
    name: str
    description: str
    lane_id: str
    filename: str
    mime_type: str
    size_bytes: int
    checksum: str
    created_at: datetime


class StartSkillTestScenarioRunRequest(BaseModel):
    timeline_override: dict[str, Any] | None = None
    terminal_context_override: dict[str, Any] | None = None


class SkillTestScenarioRunResponse(BaseModel):
    id: str
    skill_definition_id: str
    scenario_id: str
    invocation_id: str | None = None
    run_id: str | None = None
    status: str
    driver_status: str
    driver_cursor: int
    driver_events: list[dict[str, Any]] = Field(default_factory=list)
    timeline: dict[str, Any]
    result_summary: dict[str, Any]
    time_origin: datetime | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class DeleteSkillTestAssetResponse(BaseModel):
    deleted: bool
    asset_id: str


class SkillTestExpectationEvaluationResponse(BaseModel):
    id: str
    scenario_run_id: str
    expectation_id: str
    status: str
    confidence: float
    reason: str
    evidence_refs: list[dict[str, Any]]
    judge_provider: str
    judge_model: str
    prompt_hash: str
    raw_response: dict[str, Any]
    created_at: datetime


class SkillTestScenarioReviewResponse(BaseModel):
    scenario: SkillTestScenarioResponse
    scenario_run: SkillTestScenarioRunResponse
    replay: dict[str, Any] | None = None
    scenario_timeline: dict[str, Any]
    replay_timeline: list[dict[str, Any]]
    cursor_anchors: list[dict[str, Any]]
    driver_events: list[dict[str, Any]]
    expectation_evaluations: list[SkillTestExpectationEvaluationResponse]


class SkillTestForkCursor(BaseModel):
    time_ms: int = Field(default=0, ge=0)
    terminal_seq: int = Field(default=0, ge=0)
    snapshot_seq: int = Field(default=0, ge=0)


class ForkSkillTestScenarioRequest(BaseModel):
    cursor: SkillTestForkCursor
    name: str | None = Field(default=None, max_length=160)
    description: str | None = None


class ForkSkillDebugRequest(BaseModel):
    cursor: SkillTestForkCursor
