from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.agents.schemas import AgentRunResponse
from app.compiler.schemas import CompileRequestResponse


class PSkillVersionSummaryResponse(BaseModel):
    id: str
    version_no: int
    status: str
    source_ref: str
    source_commit_sha: str | None = None
    manifest_snapshot: dict[str, Any] | None = None
    runtime_policy_snapshot: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime


class PSkillPublishRecordResponse(BaseModel):
    id: str
    pskill_version_id: str
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
    current_draft_version: PSkillVersionSummaryResponse | None = None
    latest_published_version: PSkillVersionSummaryResponse | None = None
    recent_publish_records: list[PSkillPublishRecordResponse] = Field(default_factory=list)


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
    publish_record: PSkillPublishRecordResponse
    published_version: PSkillVersionSummaryResponse
    published_commit_sha: str
    compile_request: CompileRequestResponse | None = None


class PSkillMaterialDerivedAssetResponse(BaseModel):
    id: str
    material_id: str
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


class PSkillMaterialResponse(BaseModel):
    id: str
    pskill_definition_id: str
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


class PSkillMaterialDetailResponse(PSkillMaterialResponse):
    analysis_result: dict[str, Any] = Field(default_factory=dict)
    derived_assets: list[PSkillMaterialDerivedAssetResponse] = Field(default_factory=list)


class DeletePSkillMaterialResponse(BaseModel):
    deleted: bool
    material_id: str


class GenerateSkillDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_description: str = Field(min_length=1, max_length=10000)
    base_commit_sha: str | None = Field(default=None, min_length=1)


class GeneratePSkillDraftPatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_description: str = Field(min_length=1, max_length=10000)
    material_ids: list[str] = Field(default_factory=list)
    base_commit_sha: str | None = Field(default=None, min_length=1)
    proposed_files: dict[str, str] = Field(default_factory=dict)


class PSkillDraftGenerateResponse(BaseModel):
    status: str
    agent_run: AgentRunResponse
    base_commit_sha: str
    material_ids: list[str] = Field(default_factory=list)
    patch: dict[str, Any]


class ApplyPSkillDraftPatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_commit_sha: str = Field(min_length=1)
    files: dict[str, str] = Field(default_factory=dict)
    commit_message: str = Field(default="Apply PSkill draft patch via PSOP WEB IDE", max_length=500)


class PSkillDraftApplyPatchResponse(BaseModel):
    applied: bool
    changed_files: list[str]
    committed_commit_sha: str
    source: SkillSourceResponse


class PSkillMaterialGenerationResponse(BaseModel):
    id: str
    job_id: str | None = None
    pskill_definition_id: str
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


class PSkillMaterialAnalysisResponse(BaseModel):
    id: str
    material_id: str
    status: str
    analysis_result: dict[str, Any]
    error_message: str
    error_details: dict[str, Any] = Field(default_factory=dict)
    derived_assets: list[PSkillMaterialDerivedAssetResponse] = Field(default_factory=list)
    started_at: datetime | None = None
    ended_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class BatchAnalyzeMaterialsRequest(BaseModel):
    material_ids: list[str] = Field(default_factory=list)
    force: bool = False


class BatchAnalyzeMaterialsResponse(BaseModel):
    pskill_definition_id: str
    requested_count: int
    analyzed_count: int
    skipped_count: int
    analyses: list[PSkillMaterialAnalysisResponse] = Field(default_factory=list)
    skipped_material_ids: list[str] = Field(default_factory=list)
