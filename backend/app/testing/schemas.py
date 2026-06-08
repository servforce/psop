from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.agents.schemas import AgentRunResponse


class SkillTestScenarioRunSummary(BaseModel):
    id: str
    suite_id: str | None = None
    pskill_version_id: str | None = None
    artifact_id: str | None = None
    agent_run_id: str | None = None
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


class GenerateSkillTestScenariosRequest(BaseModel):
    pskill_version_id: str | None = None
    compile_artifact_id: str | None = None
    scenario_count: int = Field(default=1, ge=1, le=5)
    focus: str = Field(default="", max_length=1000)
    route_key: str = Field(default="text", max_length=80)


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
    pskill_definition_id: str
    suite_id: str | None = None
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


class GenerateSkillTestScenariosResponse(BaseModel):
    agent_run: AgentRunResponse
    scenarios: list[SkillTestScenarioResponse] = Field(default_factory=list)
    diagnostics: list[dict[str, Any]] = Field(default_factory=list)
    raw_generation_result: dict[str, Any] = Field(default_factory=dict)


class SkillTestAssetResponse(BaseModel):
    id: str
    pskill_definition_id: str
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


class CancelSkillTestScenarioRunRequest(BaseModel):
    reason: str = Field(default="cancelled by user", max_length=500)


class SkillTestScenarioRunResponse(BaseModel):
    id: str
    pskill_definition_id: str
    scenario_id: str
    suite_id: str | None = None
    pskill_version_id: str | None = None
    artifact_id: str | None = None
    agent_run_id: str | None = None
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


class SkillTestForkCursor(BaseModel):
    time_ms: int = Field(default=0, ge=0)
    terminal_seq: int = Field(default=0, ge=0)
    snapshot_seq: int = Field(default=0, ge=0)


class SkillTestStageActualOutputResponse(BaseModel):
    id: str
    terminal_event_id: str | None = None
    seq_no: int | None = None
    at_ms: int
    occurred_at: datetime | None = None
    event_kind: str = ""
    mime_type: str = ""
    payload_inline: Any | None = None


class SkillTestStageJudgeResultResponse(BaseModel):
    status: str = "pending"
    confidence: float = 0.0
    reason: str = ""
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)
    judge_provider: str = ""
    judge_model: str = ""
    prompt_hash: str = ""
    evaluation_id: str | None = None
    created_at: datetime | None = None


class SkillTestStageHumanReviewResponse(BaseModel):
    status: str = "pending"
    reviewer: str | None = None
    reason: str = ""
    updated_at: datetime | None = None


class SkillTestStageOutputResponse(BaseModel):
    stage_id: str
    event_id: str
    time_ms: int
    expectation: str
    actual_outputs: list[SkillTestStageActualOutputResponse] = Field(default_factory=list)
    judge_result: SkillTestStageJudgeResultResponse
    human_review: SkillTestStageHumanReviewResponse = Field(default_factory=SkillTestStageHumanReviewResponse)
    cursor: SkillTestForkCursor


class SkillTestScenarioReviewResponse(BaseModel):
    scenario: SkillTestScenarioResponse
    scenario_run: SkillTestScenarioRunResponse
    replay: dict[str, Any] | None = None
    scenario_timeline: dict[str, Any]
    replay_timeline: list[dict[str, Any]]
    cursor_anchors: list[dict[str, Any]]
    driver_events: list[dict[str, Any]]
    expectation_evaluations: list[SkillTestExpectationEvaluationResponse]
    stage_outputs: list[SkillTestStageOutputResponse] = Field(default_factory=list)


class ForkSkillTestScenarioRequest(BaseModel):
    cursor: SkillTestForkCursor
    name: str | None = Field(default=None, max_length=160)
    description: str | None = None


class ForkSkillDebugRequest(BaseModel):
    cursor: SkillTestForkCursor


class RunPublishGateRequest(BaseModel):
    pskill_id: str | None = None
    pskill_version_id: str | None = None
    compile_artifact_id: str | None = None


class PSkillPublishGateResponse(BaseModel):
    id: str
    pskill_definition_id: str
    pskill_version_id: str | None = None
    test_run_id: str | None = None
    status: str
    score: int
    result_json: dict[str, Any]
    created_at: datetime
    updated_at: datetime
