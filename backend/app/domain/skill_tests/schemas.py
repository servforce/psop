from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class SkillTestRunSummary(BaseModel):
    id: str
    status: str
    run_id: str | None = None
    assertion_summary: dict[str, Any]
    created_at: datetime
    ended_at: datetime | None = None


class SkillTestCaseCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str = ""
    target_version_selector: str = Field(default="latest", max_length=120)
    target_compile_artifact_id: str | None = None
    initial_terminal_events: list[dict[str, Any]] = Field(default_factory=list)
    input_envelope: dict[str, Any] = Field(default_factory=dict)
    terminal_context: dict[str, Any] = Field(default_factory=lambda: {"terminal_kind": "web"})
    assertions: list[dict[str, Any]] = Field(default_factory=list)


class SkillTestCaseUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = None
    target_version_selector: str | None = Field(default=None, max_length=120)
    target_compile_artifact_id: str | None = None
    initial_terminal_events: list[dict[str, Any]] | None = None
    input_envelope: dict[str, Any] | None = None
    terminal_context: dict[str, Any] | None = None
    assertions: list[dict[str, Any]] | None = None
    status: str | None = Field(default=None, max_length=32)


class SkillTestCaseResponse(BaseModel):
    id: str
    skill_definition_id: str
    name: str
    description: str
    target_version_selector: str
    target_compile_artifact_id: str | None = None
    initial_terminal_events: list[dict[str, Any]]
    input_envelope: dict[str, Any]
    terminal_context: dict[str, Any]
    assertions: list[dict[str, Any]]
    status: str
    latest_run: SkillTestRunSummary | None = None
    created_at: datetime
    updated_at: datetime


class SkillTestDataObjectResponse(BaseModel):
    id: str
    skill_definition_id: str
    test_case_id: str
    artifact_object_id: str
    name: str
    description: str
    role: str
    filename: str
    mime_type: str
    size_bytes: int
    checksum: str
    created_at: datetime


class StartSkillTestRunRequest(BaseModel):
    selected_data_object_ids: list[str] = Field(default_factory=list)
    send_case_initial_events: bool = False
    initial_terminal_events: list[dict[str, Any]] = Field(default_factory=list)
    terminal_context_override: dict[str, Any] | None = None
    input_override: dict[str, Any] | None = Field(default=None, description="Deprecated compatibility alias for one initial terminal event.")


class SkillTestRunResponse(BaseModel):
    id: str
    skill_definition_id: str
    test_case_id: str
    invocation_id: str | None = None
    run_id: str | None = None
    status: str
    selected_data_object_ids: list[str]
    initial_terminal_events: list[dict[str, Any]]
    input_envelope: dict[str, Any]
    assertion_results: list[dict[str, Any]]
    assertion_summary: dict[str, Any]
    started_at: datetime | None = None
    ended_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class SendSkillTestDataRequest(BaseModel):
    test_data_object_id: str
    event_kind: str | None = Field(default=None, max_length=120)
    payload_inline: dict[str, Any] | None = None


class SendSkillTestDataResponse(BaseModel):
    accepted: bool
    terminal_event: dict[str, Any]


class DeleteSkillTestDataResponse(BaseModel):
    deleted: bool
    data_id: str
