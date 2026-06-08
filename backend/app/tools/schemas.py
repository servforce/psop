from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ToolDefinitionResponse(BaseModel):
    id: str
    name: str
    provider: str
    side_effect_level: str
    requires_authorization: bool
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    metadata: dict[str, Any]
    status: str
    allowed_agent_keys: list[str] = Field(default_factory=list)
    recent_call_count: int = 0
    failed_call_count: int = 0
    failure_rate: float = 0.0
    policy_summary: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class ToolTestRequest(BaseModel):
    arguments_summary: dict[str, Any] = Field(default_factory=dict)
    requested_side_effect_level: str | None = None
    agent_key: str | None = None


class ToolTestResponse(BaseModel):
    tool_name: str
    executable: bool
    dry_run: bool = True
    side_effect_level: str
    requires_authorization: bool
    policy_reason: str
    input_echo: dict[str, Any] = Field(default_factory=dict)
    output_preview: dict[str, Any] = Field(default_factory=dict)
    policy_decision: dict[str, Any] = Field(default_factory=dict)
