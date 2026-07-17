from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

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
    is_published: bool
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


class SkillRawMaterialDerivedAssetResponse(BaseModel):
    id: str
    raw_material_id: str
    analysis_id: str
    artifact_object_id: str
    asset_kind: str
    timestamp_ms: int
    filename: str
    mime_type: str
    label: str
    observations: list[Any] = Field(default_factory=list)
    asset_metadata: dict[str, Any] = Field(default_factory=dict)
    reference_path: str
    created_at: datetime


class SkillRawMaterialResponse(BaseModel):
    id: str
    skill_definition_id: str
    artifact_object_id: str
    name: str
    description: str
    material_kind: str
    mime_type: str
    filename: str
    source_note: str
    status: str
    size_bytes: int
    checksum: str
    error_message: str
    analysis_status: str | None = None
    analysis_id: str | None = None
    analysis_result_summary: str = ""
    derived_asset_count: int = 0
    created_at: datetime
    updated_at: datetime


class SkillRawMaterialDetailResponse(SkillRawMaterialResponse):
    analysis_result: dict[str, Any] = Field(default_factory=dict)
    derived_assets: list[SkillRawMaterialDerivedAssetResponse] = Field(default_factory=list)


class DeleteSkillRawMaterialResponse(BaseModel):
    deleted: bool
    material_id: str


class GenerateSkillDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_description: str = Field(min_length=1, max_length=10000)
    base_commit_sha: str | None = Field(default=None, min_length=1)
    generation_intent: "GenerationIntentConfirmation | None" = None


class GenerationIntentPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_description: str = Field(min_length=1, max_length=10000)


class GenerationIntentOption(BaseModel):
    id: str
    label: str
    revision_instruction: str


class GenerationIntentPreviewResponse(BaseModel):
    status: str
    revision_mode: str
    summary: str
    preview_hash: str
    options: list[GenerationIntentOption] = Field(default_factory=list)


class GenerationIntentConfirmation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preview_hash: str = Field(min_length=16, max_length=128)
    confirmed_option_id: str = Field(min_length=1, max_length=64)


GenerateSkillDraftRequest.model_rebuild()


class SkillRawMaterialGenerationResponse(BaseModel):
    id: str
    job_id: str | None = None
    skill_definition_id: str
    material_ids: list[str]
    user_description: str
    status: str
    prompt_hash: str
    prompt_metadata: dict[str, Any]
    raw_response: dict[str, Any]
    generated_files: dict[str, str]
    generation_reason: str
    review_notes: list[str]
    material_usage: list[dict[str, Any]]
    committed_commit_sha: str
    error_message: str
    created_at: datetime


class SkillRawMaterialAnalysisResponse(BaseModel):
    id: str
    raw_material_id: str
    status: str
    analysis_result: dict[str, Any]
    error_message: str
    error_details: dict[str, Any] = Field(default_factory=dict)
    derived_assets: list[SkillRawMaterialDerivedAssetResponse] = Field(default_factory=list)
    started_at: datetime | None = None
    ended_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
