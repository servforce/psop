from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.domain.compiler.schemas import CompileRequestResponse


class SkillVersionSummaryResponse(BaseModel):
    id: str
    version_no: int
    status: str
    source_ref: str
    source_commit_sha: str | None = None
    manifest_snapshot: dict[str, Any] | None = None
    runtime_policy_snapshot: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime


class SkillPublishRecordResponse(BaseModel):
    id: str
    skill_version_id: str
    publish_reason: str
    publish_status: str
    published_commit_sha: str
    release_ref: str
    published_at: datetime
    created_at: datetime


class SkillSummaryResponse(BaseModel):
    id: str
    key: str
    name: str
    description: str
    status: str
    gitlab_group_path: str
    gitlab_project_id: str
    repository_url: str
    default_branch: str
    manifest_path: str
    latest_draft_head_sha: str | None = None
    latest_published_commit_sha: str | None = None
    latest_published_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class SkillDetailResponse(SkillSummaryResponse):
    current_draft_version: SkillVersionSummaryResponse | None = None
    latest_published_version: SkillVersionSummaryResponse | None = None
    recent_publish_records: list[SkillPublishRecordResponse] = Field(default_factory=list)


class SkillSourceResponse(BaseModel):
    readme_content: str
    skill_md_content: str
    skill_yaml_content: str
    source_ref: str
    head_commit_sha: str


class SkillRepositoryTreeEntryResponse(BaseModel):
    id: str
    name: str
    path: str
    type: str
    mode: str | None = None


class SkillRepositoryTreeResponse(BaseModel):
    path: str
    ref: str
    head_commit_sha: str
    entries: list[SkillRepositoryTreeEntryResponse]


class SkillRepositoryFileResponse(BaseModel):
    file_path: str
    file_name: str
    content: str
    ref: str
    head_commit_sha: str


class CreateSkillRequest(BaseModel):
    key: str = Field(min_length=2, max_length=120, pattern=r"^[a-z0-9][a-z0-9-]*$")
    name: str = Field(min_length=2, max_length=255)
    description: str = Field(default="", max_length=5000)


class UpdateSkillRequest(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=255)
    description: str | None = Field(default=None, max_length=5000)


class DeleteSkillRequest(BaseModel):
    confirmation_name: str = Field(min_length=1, max_length=255)


class SaveSkillSourceRequest(BaseModel):
    base_commit_sha: str = Field(min_length=1)
    readme_content: str
    skill_md_content: str
    skill_yaml_content: str = ""


class SaveSkillRepositoryFileRequest(BaseModel):
    path: str = Field(min_length=1, max_length=500)
    content: str = ""
    base_commit_sha: str = Field(min_length=1)


class CreateSkillRepositoryFileRequest(BaseModel):
    path: str = Field(min_length=1, max_length=500)
    content: str = ""


class CreateSkillRepositoryFolderRequest(BaseModel):
    path: str = Field(min_length=1, max_length=500)


class PublishSkillRequest(BaseModel):
    publish_reason: str = Field(min_length=1, max_length=5000)


class PublishSkillResponse(BaseModel):
    publish_record: SkillPublishRecordResponse
    published_version: SkillVersionSummaryResponse
    published_commit_sha: str
    compile_request: CompileRequestResponse | None = None
