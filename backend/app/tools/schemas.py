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
