from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class SkillResourceResponse(BaseModel):
    id: str
    version_id: str
    resource_path: str
    resource_kind: str
    content_hash: str
    size_bytes: int
    created_at: datetime


class SkillActivationResponse(BaseModel):
    id: str
    agent_run_id: str
    package_id: str
    version_id: str
    activation_context: dict[str, Any]
    created_at: datetime


class SkillVersionResponse(BaseModel):
    id: str
    package_id: str
    version_label: str
    status: str
    content_hash: str
    manifest_json: dict[str, Any]
    body_object_key: str
    resource_index: list[dict[str, Any]]
    allowed_tools: list[str]
    validation_status: str
    validation_diagnostics: list[dict[str, Any]]
    activated_at: datetime | None = None
    resource_count: int = 0
    created_at: datetime
    updated_at: datetime


class SkillPackageSummaryResponse(BaseModel):
    id: str
    name: str
    scope: str
    description: str
    source_uri: str
    status: str
    active_version_id: str | None = None
    active_version_label: str | None = None
    active_content_hash: str | None = None
    version_count: int
    created_at: datetime
    updated_at: datetime


class SkillPackageDetailResponse(SkillPackageSummaryResponse):
    versions: list[SkillVersionResponse] = Field(default_factory=list)
    active_version: SkillVersionResponse | None = None
    resources: list[SkillResourceResponse] = Field(default_factory=list)


class SkillPackageSyncResponse(BaseModel):
    changed: bool
    scanned_count: int
    package_count: int
    version_count: int
    packages: list[SkillPackageSummaryResponse] = Field(default_factory=list)
