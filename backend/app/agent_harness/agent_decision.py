from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class AgentDecision(BaseModel):
    decision_type: Literal["final_output", "tool_call", "fail"] = "final_output"
    output_payload: dict[str, Any] = Field(default_factory=dict)
    error_message: str = ""
    tool_name: str = ""
    tool_provider: str = "native"
    side_effect_level: str | None = None
    arguments_summary: dict[str, Any] = Field(default_factory=dict)
    expected_effect_summary: str = ""
    reversible: bool = False
    idempotency_key: str = ""
    authorization_reason: str = ""
