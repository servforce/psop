from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class RuntimeJobProgressResponse(BaseModel):
    percent: int | None = None
    current_stage: str = ""
    label: str = ""
    detail: str = ""


class RuntimeJobTokenUsageResponse(BaseModel):
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    llm_calls: int | None = None


class RuntimeJobResponse(BaseModel):
    id: str
    job_type: str
    status: str
    payload: dict[str, Any]
    dedupe_key: str | None = None
    run_id: str | None = None
    compile_request_id: str | None = None
    worker_name: str = ""
    metrics: dict[str, Any] = Field(default_factory=dict)
    progress: RuntimeJobProgressResponse | None = None
    token_usage: RuntimeJobTokenUsageResponse | None = None
    duration_ms: int | None = None
    elapsed_ms: int | None = None
    lease_until: datetime | None = None
    available_at: datetime | None = None
    attempt_no: int
    max_attempts: int
    last_error: str = ""
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class RuntimeJobStatsResponse(BaseModel):
    window_hours: int
    total: int
    pending: int
    running: int
    succeeded: int
    failed: int
    cancelled: int
    success_rate: float | None = None
    avg_duration_ms: int | None = None
    p95_duration_ms: int | None = None
    max_duration_ms: int | None = None
    token_usage: RuntimeJobTokenUsageResponse | None = None
    by_status: dict[str, int]
    by_type: dict[str, int]
