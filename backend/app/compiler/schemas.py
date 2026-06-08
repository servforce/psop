from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class CompileRequestResponse(BaseModel):
    id: str
    pskill_definition_id: str
    pskill_version_id: str
    agent_run_id: str | None = None
    trigger_type: str
    source_commit_sha: str
    status: str
    dedupe_key: str
    requested_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error_message: str = ""
    artifact_id: str | None = None
    created_at: datetime
    updated_at: datetime


class CompileDiagnosticResponse(BaseModel):
    id: str
    compile_request_id: str
    skill_compile_request_id: str | None = None
    pskill_version_id: str
    severity: str
    code: str
    message: str
    location: dict[str, Any] | None = None
    category: str
    created_at: datetime


class CompileArtifactResponse(BaseModel):
    id: str
    compile_request_id: str
    skill_compile_request_id: str | None = None
    pskill_version_id: str
    artifact_object_id: str
    formal_revision: str
    artifact_version: str
    graph_summary: dict[str, Any]
    capability_summary: dict[str, Any]
    status: str
    created_at: datetime
    artifact: dict[str, Any] | None = None


class CompileArtifactUpdateRequest(BaseModel):
    artifact: dict[str, Any]


class CompileArtifactValidationDiagnosticResponse(BaseModel):
    severity: str
    code: str
    message: str
    location: dict[str, Any] | None = None
    category: str


class CompileArtifactValidationResponse(BaseModel):
    artifact_id: str
    compile_request_id: str
    pskill_version_id: str
    valid: bool
    diagnostics: list[CompileArtifactValidationDiagnosticResponse]
    graph_summary: dict[str, Any] | None = None
    capability_summary: dict[str, Any] | None = None
    normalized_artifact: dict[str, Any] | None = None


class PublishProgressStageResponse(BaseModel):
    key: str
    label: str
    status: str
    message: str = ""
    started_at: datetime | None = None
    finished_at: datetime | None = None


class PublishProgressResponse(BaseModel):
    compile_request: CompileRequestResponse
    publish_record_id: str | None = None
    publish_status: str | None = None
    current_stage: str
    terminal: bool
    terminal_status: str | None = None
    error_message: str = ""
    updated_at: datetime | None = None
    stages: list[PublishProgressStageResponse]
