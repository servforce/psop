from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class AgentVersionSummaryResponse(BaseModel):
    id: str
    definition_id: str
    version_no: int
    version_label: str
    status: str
    spec_json: dict[str, Any]
    content_hash: str
    published_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class AgentBindingResponse(BaseModel):
    id: str
    usage_key: str
    definition_id: str
    active_version_id: str | None = None
    created_at: datetime
    updated_at: datetime


class AgentDefinitionSummaryResponse(BaseModel):
    id: str
    key: str
    name: str
    role: str
    description: str
    status: str
    active_version_id: str | None = None
    active_version_label: str | None = None
    version_count: int
    bindings: list[AgentBindingResponse] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class AgentDefinitionDetailResponse(AgentDefinitionSummaryResponse):
    versions: list[AgentVersionSummaryResponse] = Field(default_factory=list)
    active_version: AgentVersionSummaryResponse | None = None


class AgentSessionResponse(BaseModel):
    id: str
    definition_id: str | None = None
    agent_key: str
    owner_type: str
    owner_id: str
    status: str
    summary_json: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class CreateAgentRunRequest(BaseModel):
    agent_key: str = Field(min_length=2, max_length=160)
    owner_type: str = Field(default="", max_length=80)
    owner_id: str = Field(default="", max_length=80)
    run_id: str | None = None
    input_payload: dict[str, Any] = Field(default_factory=dict)


class AgentRunResponse(BaseModel):
    id: str
    definition_id: str | None = None
    agent_version_id: str | None = None
    agent_session_id: str | None = None
    agent_key: str
    status: str
    owner_type: str
    owner_id: str
    run_id: str | None = None
    input_payload: dict[str, Any]
    output_payload: dict[str, Any]
    error_message: str
    started_at: datetime | None = None
    ended_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class AgentEventResponse(BaseModel):
    id: str
    agent_run_id: str
    seq_no: int
    event_type: str
    phase: str
    payload: dict[str, Any]
    occurred_at: datetime


class AppendAgentEventRequest(BaseModel):
    event_type: str = Field(min_length=2, max_length=160)
    phase: str = Field(default="", max_length=120)
    payload: dict[str, Any] = Field(default_factory=dict)


class AgentModelCallResponse(BaseModel):
    id: str
    agent_run_id: str
    provider: str
    route_key: str
    model_name: str
    status: str
    request_payload: dict[str, Any]
    response_payload: dict[str, Any]
    usage_json: dict[str, Any]
    error_message: str
    started_at: datetime | None = None
    ended_at: datetime | None = None
    created_at: datetime


class CreateAgentToolCallRequest(BaseModel):
    tool_name: str = Field(min_length=2, max_length=160)
    tool_provider: str = Field(default="native", max_length=60)
    arguments_summary: dict[str, Any] = Field(default_factory=dict)
    side_effect_level: str = Field(default="read", max_length=60)
    idempotency_key: str = Field(default="", max_length=255)


class AgentToolCallResponse(BaseModel):
    id: str
    agent_run_id: str
    tool_name: str
    tool_provider: str
    status: str
    arguments_summary: dict[str, Any]
    result_summary: dict[str, Any]
    side_effect_level: str
    idempotency_key: str
    created_at: datetime
    updated_at: datetime


class CreateToolAuthorizationRequest(BaseModel):
    agent_run_id: str
    agent_tool_call_id: str | None = None
    run_id: str | None = None
    run_event_id: str | None = None
    tool_name: str = Field(min_length=2, max_length=160)
    tool_provider: str = Field(default="native", max_length=60)
    mcp_server_name: str = Field(default="", max_length=160)
    side_effect_level: str = Field(min_length=2, max_length=60)
    risk_level: str = Field(default="medium", max_length=40)
    authorization_reason: str = ""
    tool_arguments_summary: dict[str, Any] = Field(default_factory=dict)
    expected_effect_summary: str = ""
    reversible: bool = False
    idempotency_key: str = Field(default="", max_length=255)
    request_payload: dict[str, Any] = Field(default_factory=dict)


class ToolAuthorizationDecisionRequest(BaseModel):
    response_payload: dict[str, Any] = Field(default_factory=dict)


class AgentToolAuthorizationResponse(BaseModel):
    id: str
    agent_run_id: str
    agent_tool_call_id: str | None = None
    run_id: str | None = None
    run_event_id: str | None = None
    tool_name: str
    tool_provider: str
    mcp_server_name: str
    side_effect_level: str
    risk_level: str
    authorization_reason: str
    tool_arguments_summary: dict[str, Any]
    expected_effect_summary: str
    reversible: bool
    idempotency_key: str
    status: str
    request_payload: dict[str, Any]
    response_payload: dict[str, Any]
    created_at: datetime
    responded_at: datetime | None = None
    executed_at: datetime | None = None
