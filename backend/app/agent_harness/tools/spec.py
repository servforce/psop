from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ToolSpec(BaseModel):
    name: str
    description: str
    purpose: str | None = None
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] | None = None
    source: Literal["builtin", "skill", "mcp"] = "builtin"
    risk_class: str = "read_only"
    side_effect_class: str = "none"
    resource_scope: str = "agent_run"
    permission_policy: str = "allow"
    timeout_seconds: float | None = None
    max_result_chars: int | None = None
    retry_policy: dict[str, Any] = Field(default_factory=dict)
    audit_event: str | None = None
    error_types: list[str] = Field(default_factory=list)
