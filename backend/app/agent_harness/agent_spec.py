from __future__ import annotations

from copy import deepcopy
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator


AGENT_SPEC_FIELDS = (
    "key",
    "name",
    "role",
    "goal",
    "instructions",
    "model_policy",
    "runtime_policy",
    "allowed_tools",
    "allowed_skill_names",
    "memory_policy",
    "planner_policy",
    "sandbox_policy",
    "guardrail_policy",
    "output_schema",
)


class AgentSpec(BaseModel):
    model_config = ConfigDict(extra="allow")

    key: str
    name: str
    role: str
    goal: str
    instructions: dict[str, Any] = Field(default_factory=dict)
    model_policy: dict[str, Any] = Field(default_factory=dict)
    runtime_policy: dict[str, Any] = Field(default_factory=dict)
    allowed_tools: list[str] = Field(default_factory=list)
    allowed_skill_names: list[str] = Field(default_factory=list)
    memory_policy: dict[str, Any] = Field(default_factory=dict)
    planner_policy: dict[str, Any] = Field(default_factory=dict)
    sandbox_policy: dict[str, Any] = Field(default_factory=dict)
    guardrail_policy: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any]
    prompt_usage_key: str | None = None
    prompt_fallback_ref: str | None = None

    @field_validator("key", "name", "role", "goal")
    @classmethod
    def _non_empty_string(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("required")
        return value.strip()

    @field_validator(
        "instructions",
        "model_policy",
        "runtime_policy",
        "memory_policy",
        "planner_policy",
        "sandbox_policy",
        "guardrail_policy",
        "output_schema",
        mode="before",
    )
    @classmethod
    def _dict_field(cls, value: Any) -> dict[str, Any]:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ValueError("must be an object")
        return dict(value)

    @field_validator("allowed_tools", "allowed_skill_names", mode="before")
    @classmethod
    def _string_list(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("must be a list of strings")
        result: list[str] = []
        for item in value:
            if not isinstance(item, str) or not item.strip():
                raise ValueError("must be a list of strings")
            result.append(item.strip())
        return result

    @field_validator("prompt_usage_key", "prompt_fallback_ref")
    @classmethod
    def _optional_non_empty_string(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip():
            raise ValueError("must be a non-empty string")
        return value.strip()

    @model_validator(mode="after")
    def _requires_named_output_schema(self) -> AgentSpec:
        if not str(self.output_schema.get("name") or "").strip():
            raise ValueError("output_schema.name is required")
        return self


def normalize_agent_spec(spec: dict[str, Any], *, defaults: dict[str, Any] | None = None) -> dict[str, Any]:
    candidate: dict[str, Any] = {}
    if defaults:
        candidate.update(deepcopy(defaults))
    candidate.update(deepcopy(spec))
    return AgentSpec.model_validate(candidate).model_dump(mode="json", exclude_none=True)


def agent_spec_validation_errors(error: ValidationError) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    for item in error.errors():
        loc = item.get("loc") or ("spec",)
        field = ".".join(str(part) for part in loc) if isinstance(loc, tuple) else str(loc)
        message = str(item.get("msg") or item.get("type") or "invalid")
        errors.append({"field": field, "message": message})
    return errors
