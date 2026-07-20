from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.domain.jobs.schemas import RuntimeJobProgressResponse, RuntimeJobTokenUsageResponse


class AgentRunStepResponse(BaseModel):
    key: str
    title: str
    status: Literal["pending", "running", "succeeded", "failed", "info"] = "info"
    detail: str = ""
    event_type: str = ""
    occurred_at: datetime | None = None
    duration_ms: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentRunFinalResponse(BaseModel):
    generation_reason: str = ""
    review_notes: list[str] = Field(default_factory=list)
    generated_file_paths: list[str] = Field(default_factory=list)
    reference_files: list[str] = Field(default_factory=list)
    committed_commit_sha: str = ""
    standard_search_summary: dict[str, Any] = Field(default_factory=dict)


class AgentRunTimelineResponse(BaseModel):
    agent_run_id: str
    agent_key: str = ""
    status: str
    user_description: str = ""
    related_skill_definition_id: str = ""
    related_generation_id: str = ""
    related_job_id: str = ""
    related_runtime_run_id: str = ""
    progress: RuntimeJobProgressResponse | None = None
    elapsed_ms: int | None = None
    token_usage: RuntimeJobTokenUsageResponse | None = None
    model_call_count: int = 0
    candidate_submission_attempts: int = 0
    candidate_correction_attempts: int = 0
    job_attempt_no: int = 0
    job_max_attempts: int = 0
    failure_kind: str = ""
    validation_diagnostic_count: int = 0
    validation_diagnostics: list[dict[str, Any]] = Field(default_factory=list)
    steps: list[AgentRunStepResponse] = Field(default_factory=list)
    final: AgentRunFinalResponse = Field(default_factory=AgentRunFinalResponse)
    error_message: str = ""
    created_at: datetime | None = None
    updated_at: datetime | None = None
