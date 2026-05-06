from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class CreateInvocationRequest(BaseModel):
    skill_key: str = Field(min_length=1, max_length=120)
    version_selector: str = Field(default="latest")
    input_envelope: dict[str, Any] = Field(default_factory=dict)
    gateway_type: str = Field(default="web", max_length=64)


class InvocationResponse(BaseModel):
    id: str
    skill_definition_id: str
    skill_version_id: str
    compile_artifact_id: str
    gateway_type: str
    input_envelope: dict[str, Any]
    status: str
    idempotency_key: str | None = None
    run_id: str | None = None
    created_at: datetime
    updated_at: datetime


class RunResponse(BaseModel):
    id: str
    invocation_id: str
    skill_definition_id: str
    skill_version_id: str
    compile_artifact_id: str
    status: str
    runtime_phase: str
    latest_snapshot_seq: int
    final_output: str = ""
    exit_reason: str = ""
    created_at: datetime
    started_at: datetime | None = None
    ended_at: datetime | None = None
    updated_at: datetime


class SessionTokenSnapshotResponse(BaseModel):
    id: str
    run_id: str
    seq_no: int
    token_payload: dict[str, Any]
    enabled_set: list[Any]
    selection_summary: dict[str, Any]
    snapshot_hash: str
    created_at: datetime


class TraceEventResponse(BaseModel):
    id: str
    run_id: str
    seq_no: int
    phase: str
    event_type: str
    span_id: str
    parent_span_id: str
    payload: dict[str, Any]
    occurred_at: datetime


class ReplayTimelineItem(BaseModel):
    seq_no: int
    phase: str
    event_type: str
    title: str
    summary: str
    payload: dict[str, Any]
    occurred_at: datetime


class ReplayDetailResponse(BaseModel):
    run: RunResponse
    timeline: list[ReplayTimelineItem]
    snapshots: list[SessionTokenSnapshotResponse]
    trace_events: list[TraceEventResponse]
