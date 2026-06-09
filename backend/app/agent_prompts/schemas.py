from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class AgentPromptVersionSummaryResponse(BaseModel):
    id: str
    definition_id: str
    version_no: int
    version_label: str
    status: str
    route_key: str
    content_hash: str
    parent_version_id: str | None = None
    published_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class AgentPromptBindingResponse(BaseModel):
    id: str
    usage_key: str
    definition_id: str
    definition_key: str
    active_version_id: str | None = None
    active_version_label: str | None = None
    active_content_hash: str | None = None
    created_at: datetime
    updated_at: datetime


class AgentPromptDefinitionSummaryResponse(BaseModel):
    id: str
    key: str
    agent_id: str
    agent_key: str = ""
    scenario: str
    name: str
    description: str
    status: str
    active_version_id: str | None = None
    active_version_label: str | None = None
    active_content_hash: str | None = None
    version_count: int
    bindings: list[AgentPromptBindingResponse] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class AgentPromptVersionDetailResponse(AgentPromptVersionSummaryResponse):
    files: dict[str, str]


class AgentPromptDefinitionDetailResponse(AgentPromptDefinitionSummaryResponse):
    versions: list[AgentPromptVersionSummaryResponse] = Field(default_factory=list)
    selected_version: AgentPromptVersionDetailResponse | None = None


class AgentPromptCreateRequest(BaseModel):
    key: str = Field(min_length=2, max_length=160, pattern=r"^[a-z0-9][a-z0-9_.-]*$")
    agent_id: str = Field(min_length=2, max_length=255)
    agent_key: str = Field(default="", max_length=160)
    scenario: str = Field(min_length=2, max_length=120)
    name: str = Field(min_length=2, max_length=255)
    description: str = ""
    route_key: str = Field(default="text", max_length=120)
    files: dict[str, str] = Field(default_factory=dict)


class AgentPromptVersionCreateRequest(BaseModel):
    version_label: str | None = Field(default=None, max_length=64)
    files: dict[str, str] | None = None
    parent_version_id: str | None = None


class AgentPromptVersionFilesUpdateRequest(BaseModel):
    files: dict[str, str]


class AgentPromptValidationResponse(BaseModel):
    valid: bool
    errors: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentPromptActivateRequest(BaseModel):
    usage_key: str | None = Field(default=None, max_length=160)


class AgentPromptBindingUpdateRequest(BaseModel):
    definition_id: str
    active_version_id: str
