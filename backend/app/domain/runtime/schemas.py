from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


RunStatus = Literal[
    "queued",
    "waiting_runtime",
    "running",
    "waiting_input",
    "succeeded",
    "failed",
    "aborted",
    "cancelled",
]


class CreateInvocationRequest(BaseModel):
    skill_key: str = Field(min_length=1, max_length=120)
    version_selector: str = Field(default="latest")
    compile_artifact_id: str | None = None
    input_envelope: dict[str, Any] = Field(default_factory=dict)
    gateway_type: str = Field(default="terminal", max_length=64)
    terminal_context: dict[str, Any] = Field(default_factory=dict)
    binding_preferences: list[dict[str, Any]] = Field(default_factory=list)


class InvocationResponse(BaseModel):
    id: str
    skill_definition_id: str
    skill_version_id: str
    compile_artifact_id: str
    gateway_type: str
    input_envelope: dict[str, Any]
    terminal_context: dict[str, Any]
    binding_preferences: list[dict[str, Any]]
    status: str
    idempotency_key: str | None = None
    run_id: str | None = None
    terminal_session_id: str | None = None
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
    latest_terminal_seq: int
    latest_trace_seq: int
    terminal_session_id: str | None = None
    binding_summary: list[dict[str, Any]] = Field(default_factory=list)
    current_step: str = ""
    wait_reason: str = ""
    expected_inputs: list[dict[str, Any]] = Field(default_factory=list)
    checkpoint_id: str = ""
    resume_phase: str = ""
    latest_evaluation: dict[str, Any] = Field(default_factory=dict)
    final_output: str = ""
    exit_reason: str = ""
    created_at: datetime
    started_at: datetime | None = None
    ended_at: datetime | None = None
    updated_at: datetime


class RunTaskIdentityResponse(BaseModel):
    skill_key: str = ""
    skill_name: str = ""
    version_no: int | None = None
    execution_goal: str = ""


class RunTaskProgressResponse(BaseModel):
    completed: int = 0
    total: int = 0
    percent: int = 0


class RunTaskStageResponse(BaseModel):
    id: str
    index: int
    title: str
    goal: str = ""
    status: str
    status_reason: str = ""


class RunTaskEvidenceRequirementResponse(BaseModel):
    requirement_key: str
    description: str
    kind: str = ""
    status: str
    reason: str = ""


class RunTaskCheckpointResponse(BaseModel):
    checkpoint_id: str
    reason: str = ""
    expected_inputs: list[dict[str, Any]] = Field(default_factory=list)
    accepted_requirements: int = 0
    total_requirements: int = 0
    requirements: list[RunTaskEvidenceRequirementResponse] = Field(default_factory=list)


class RunTaskStatusResponse(BaseModel):
    run_id: str
    snapshot_seq: int
    updated_at: datetime
    run_status: str
    activity_status: str
    task: RunTaskIdentityResponse
    progress: RunTaskProgressResponse
    current_stage_id: str = ""
    stages: list[RunTaskStageResponse] = Field(default_factory=list)
    current_checkpoint: RunTaskCheckpointResponse | None = None


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


class TerminalSessionResponse(BaseModel):
    id: str
    run_id: str
    mode: str
    status: str
    opened_at: datetime
    closed_at: datetime | None = None
    created_at: datetime


class TerminalTranscriptSummary(BaseModel):
    latest_seq: int
    event_count: int


class TerminalSessionDetailResponse(BaseModel):
    terminal_session: TerminalSessionResponse
    transcript_summary: TerminalTranscriptSummary


class TerminalEventSource(BaseModel):
    kind: str = Field(default="web", max_length=64)
    device_id: str | None = None
    connection_id: str | None = None


class AppendTerminalEventRequest(BaseModel):
    direction: str = Field(max_length=32)
    event_kind: str = Field(default="terminal.multimodal.input.v1", max_length=120)
    mime_type: str = Field(default="multipart/mixed", max_length=255)
    text: str | None = None
    payload_inline: Any | None = None
    artifact_object_id: str | None = None
    parts: list["TerminalEventPartInput"] = Field(default_factory=list)
    binding_id: str | None = None
    source: TerminalEventSource = Field(default_factory=TerminalEventSource)
    external_event_id: str | None = Field(default=None, max_length=255)
    occurred_at: datetime | None = None


class TerminalEventPartInput(BaseModel):
    part_id: str | None = Field(default=None, max_length=120)
    kind: str = Field(default="text", max_length=32)
    mime_type: str = Field(default="text/plain", max_length=255)
    text: str | None = None
    artifact_object_id: str | None = None
    size_bytes: int | None = None
    checksum: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class TerminalEventPartResponse(BaseModel):
    id: str
    terminal_event_id: str
    run_id: str
    artifact_object_id: str | None = None
    part_id: str
    order_index: int
    kind: str
    mime_type: str
    text: str = ""
    size_bytes: int = 0
    checksum: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class TerminalEventResponse(BaseModel):
    id: str
    terminal_session_id: str
    run_id: str
    trace_event_id: str | None = None
    artifact_object_id: str | None = None
    run_capability_binding_id: str | None = None
    direction: str
    event_kind: str
    mime_type: str
    payload_inline: Any | None = None
    seq_no: int
    external_event_id: str | None = None
    source_ref: dict[str, Any]
    parts: list[TerminalEventPartResponse] = Field(default_factory=list)
    occurred_at: datetime
    created_at: datetime


class TerminalEventAppendResponse(BaseModel):
    accepted: bool
    event_id: str
    seq_no: int
    event: TerminalEventResponse


class RunCapabilityBindingResponse(BaseModel):
    id: str
    run_id: str
    compile_artifact_id: str
    source_capability_binding_id: str | None = None
    requirement_key: str
    binding_type: str
    capability: str
    target_kind: str
    target_ref: str
    channel: str
    schema_ref: str
    manifest_hash: str
    policy_snapshot: dict[str, Any]
    status: str
    created_at: datetime
    updated_at: datetime


class BindingRequirementResponse(BaseModel):
    requirement_key: str
    binding_type: str
    capability: str
    direction: str
    required: bool = True
    schema_ref: str = ""


class ResolveRunBindingItem(BaseModel):
    requirement_key: str = Field(min_length=1, max_length=120)
    target_kind: str = Field(default="web_terminal", max_length=64)
    target_ref: str | None = Field(default=None, max_length=255)
    channel: str = Field(default="", max_length=120)


class ResolveRunBindingsRequest(BaseModel):
    bindings: list[ResolveRunBindingItem] = Field(default_factory=list)


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
    terminal_events: list[TerminalEventResponse] = Field(default_factory=list)
    bindings: list[RunCapabilityBindingResponse] = Field(default_factory=list)
