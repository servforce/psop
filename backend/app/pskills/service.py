from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.agents.schemas import AppendAgentEventRequest, CreateAgentRunRequest
from app.agents.service import AgentService
from app.core.config import Settings
from app.core.logging import log_context
from app.core.observability import record_span_exception, start_span
from app.pskills.exceptions import (
    SkillsError,
    SkillConflictError,
    SkillNotFoundError,
    SkillSourceConflictError,
    SkillValidationError,
)
from app.pskills.manifest import (
    build_default_readme,
    build_default_skill_document,
    build_default_skill_markdown,
    document_with_prompt_material,
    document_from_manifest_snapshot,
    manifest_snapshot,
    parse_skill_yaml,
    render_skill_yaml,
    runtime_policy_snapshot,
    SkillDocument,
)
from app.agent_prompts.service import AgentPromptService
from app.compiler.models import ArtifactObject
from app.jobs.models import RuntimeJob
from app.jobs.repository import JobRepository
from app.jobs.types import MATERIAL_ANALYSIS_JOB_TYPE, PSKILL_BUILD_JOB_TYPE
from app.pskills.models import (
    PSkillDefinition,
    PSkillPublishRecord,
    PSkillMaterial,
    PSkillMaterialAnalysis,
    PSkillMaterialDerivedAsset,
    PSkillMaterialGeneration,
    PSkillVersion,
    now_utc,
)
from app.pskills.materials import (
    GeneratedSkillDraft,
    MaterialProcessor,
    infer_material_kind,
    parse_generated_skill_draft,
)
from app.pskills.repository import SkillsRepository
from app.pskills.schemas import (
    BatchAnalyzeMaterialsRequest,
    BatchAnalyzeMaterialsResponse,
    ApplyPSkillDraftPatchRequest,
    CreateSkillRepositoryFileRequest,
    CreateSkillRepositoryFolderRequest,
    CreateSkillRequest,
    DeleteSkillRequest,
    DeletePSkillMaterialResponse,
    GenerateSkillDraftRequest,
    PSkillDraftApplyPatchResponse,
    PublishSkillRequest,
    PublishSkillResponse,
    SaveSkillRepositoryFileRequest,
    SaveSkillSourceRequest,
    SkillDetailResponse,
    PSkillPublishRecordResponse,
    PSkillMaterialAnalysisResponse,
    PSkillMaterialDerivedAssetResponse,
    PSkillMaterialDetailResponse,
    PSkillMaterialGenerationResponse,
    PSkillMaterialResponse,
    SkillRepositoryFileResponse,
    SkillRepositoryTreeEntryResponse,
    SkillRepositoryTreeResponse,
    SkillSourceResponse,
    SkillSummaryResponse,
    PSkillVersionSummaryResponse,
    UpdateSkillRequest,
)
from app.pskills.video_analysis import MAX_ANALYZED_KEYFRAMES, MAX_SKILL_REFERENCE_ASSETS, VideoAnalysisResult, analyze_video_material
from app.compiler.service import CompilerService
from app.gateway.asr import AsrGateway, HttpAsrGateway
from app.gateway.inference import LlmInferenceGateway, OpenAICompatibleInferenceGateway
from app.gateway.gitlab import GitLabSkillSourceGateway
from app.infra.object_store import ObjectStoreService

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class MaterialContent:
    content: bytes
    mime_type: str
    filename: str


class SkillsService:
    """Application service for the Skills Management MVP."""

    def __init__(
        self,
        *,
        settings: Settings,
        gitlab_gateway: GitLabSkillSourceGateway,
        compiler_service: CompilerService | None = None,
        inference_gateway: LlmInferenceGateway | None = None,
        asr_gateway: AsrGateway | None = None,
        object_store: ObjectStoreService | None = None,
        agent_prompt_service: AgentPromptService | None = None,
        repository: SkillsRepository | None = None,
        job_repository: JobRepository | None = None,
        agent_service: AgentService | None = None,
    ) -> None:
        self.settings = settings
        self.gitlab_gateway = gitlab_gateway
        self.compiler_service = compiler_service
        self.inference_gateway = inference_gateway
        self.asr_gateway = asr_gateway
        self.object_store = object_store or ObjectStoreService.from_settings(settings)
        self.agent_prompt_service = agent_prompt_service or AgentPromptService()
        self.repository = repository or SkillsRepository()
        self.job_repository = job_repository or JobRepository()
        self.agent_service = agent_service or AgentService()

    def list_skills(
        self,
        session: Session,
        *,
        search: str | None = None,
        status: str | None = None,
        is_published: bool | None = None,
    ) -> list[SkillSummaryResponse]:
        definitions = self.repository.list_pskill_definitions(
            session,
            search=search,
            status=status,
            is_published=is_published,
        )
        return [self._build_skill_summary(session, definition) for definition in definitions]

    def create_skill(self, session: Session, payload: CreateSkillRequest) -> SkillDetailResponse:
        existing = self.repository.get_pskill_definition_by_key(session, payload.key)
        if existing:
            raise SkillConflictError("Skill key 已存在。", details={"key": payload.key})

        default_document = build_default_skill_document(payload.key, payload.name, payload.description)
        default_readme = build_default_readme(payload.name, payload.description)
        default_skill_md = build_default_skill_markdown(payload.name, payload.description)
        default_document = document_with_prompt_material(
            default_document,
            readme_content=default_readme,
            skill_md_content=default_skill_md,
        )
        default_skill_yaml = render_skill_yaml(default_document)

        project_info = self.gitlab_gateway.create_skill_project(
            group_path=self.settings.gitlab_skills_group_path,
            project_name=payload.name,
            project_path=payload.key,
            default_branch=self.settings.gitlab_default_branch,
            initial_readme=default_readme,
            initial_skill_md=default_skill_md,
            initial_skill_yaml=default_skill_yaml,
        )

        definition = PSkillDefinition(
            key=payload.key,
            name=payload.name,
            description=payload.description,
            status="active",
            gitlab_group_path=self.settings.gitlab_skills_group_path,
            gitlab_project_id=project_info.project_id,
            repository_url=project_info.repository_url,
            default_branch=project_info.default_branch,
            manifest_path="skill.yaml",
        )
        session.add(definition)
        session.flush()

        draft_version = PSkillVersion(
            pskill_definition_id=definition.id,
            version_no=0,
            status="draft",
            source_ref=project_info.default_branch,
            source_commit_sha=project_info.head_commit_sha,
            manifest_snapshot=manifest_snapshot(default_document),
            runtime_policy_snapshot=runtime_policy_snapshot(default_document),
        )
        session.add(draft_version)
        session.flush()

        definition.latest_draft_version_id = draft_version.id
        session.commit()
        session.refresh(definition)

        return self.get_skill_detail(session, definition.id)

    def get_skill_detail(self, session: Session, skill_id: str) -> SkillDetailResponse:
        definition = self._require_definition(session, skill_id)
        draft_version = self.repository.get_draft_version(session, definition)
        latest_published_version = self.repository.get_pskill_version(session, definition.latest_published_version_id)

        return SkillDetailResponse(
            **self._build_skill_summary(session, definition).model_dump(),
            current_draft_version=self._build_pskill_version_summary(draft_version),
            latest_published_version=self._build_pskill_version_summary(latest_published_version),
        )

    def list_skill_versions(self, session: Session, *, skill_id: str) -> list[PSkillVersionSummaryResponse]:
        definition = self._require_definition(session, skill_id)
        versions = self.repository.list_pskill_versions(session, definition.id)
        return [
            version_summary
            for version in versions
            if (version_summary := self._build_pskill_version_summary(version)) is not None
        ]

    def update_skill_metadata(
        self,
        session: Session,
        *,
        skill_id: str,
        payload: UpdateSkillRequest,
    ) -> SkillDetailResponse:
        definition = self._require_definition(session, skill_id)
        draft_version = self._require_draft_version(session, definition)

        if payload.name is None and payload.description is None:
            return self.get_skill_detail(session, skill_id)

        source_bundle = self.gitlab_gateway.get_skill_source(definition.gitlab_project_id, definition.default_branch)
        document = self._document_from_version_snapshot(draft_version, source_bundle.skill_yaml_content)

        if payload.name is not None:
            document.skill.identity.name = payload.name
        if payload.description is not None:
            document.skill.identity.description = payload.description

        document = document_with_prompt_material(
            document,
            readme_content=source_bundle.readme_content,
            skill_md_content=source_bundle.skill_md_content,
        )
        updated_skill_yaml = render_skill_yaml(document)
        new_commit_sha = self.gitlab_gateway.commit_skill_source(
            project_id=definition.gitlab_project_id,
            branch=definition.default_branch,
            readme_content=source_bundle.readme_content,
            skill_md_content=source_bundle.skill_md_content,
            skill_yaml_content=updated_skill_yaml,
            commit_message="Update skill metadata via PSOP WEB IDE",
        )

        if payload.name is not None and payload.name != definition.name:
            self.gitlab_gateway.update_project_name(definition.gitlab_project_id, payload.name)
            definition.name = payload.name
        if payload.description is not None:
            definition.description = payload.description

        draft_version.source_commit_sha = new_commit_sha
        draft_version.manifest_snapshot = manifest_snapshot(document)
        draft_version.runtime_policy_snapshot = runtime_policy_snapshot(document)
        definition.updated_at = now_utc()

        session.commit()
        return self.get_skill_detail(session, skill_id)

    def delete_skill(
        self,
        session: Session,
        *,
        skill_id: str,
        payload: DeleteSkillRequest,
    ) -> SkillSummaryResponse:
        definition = self._require_definition(session, skill_id)
        if payload.confirmation_name != definition.name:
            raise SkillValidationError(
                "确认名称与 Skill 名称不一致。",
                details={"expected": definition.name},
            )

        if definition.status != "archived":
            self.gitlab_gateway.archive_project(definition.gitlab_project_id)
            definition.status = "archived"
            session.commit()
            session.refresh(definition)

        return self._build_skill_summary(session, definition)

    def get_skill_source(self, session: Session, skill_id: str) -> SkillSourceResponse:
        definition = self._require_definition(session, skill_id)
        draft_version = self._require_draft_version(session, definition)
        source_bundle = self.gitlab_gateway.get_skill_source(definition.gitlab_project_id, draft_version.source_ref)
        document = self._document_from_version_snapshot(draft_version, source_bundle.skill_yaml_content)

        document = document_with_prompt_material(
            document,
            readme_content=source_bundle.readme_content,
            skill_md_content=source_bundle.skill_md_content,
        )
        current_manifest_snapshot = manifest_snapshot(document)
        if draft_version.source_commit_sha != source_bundle.head_commit_sha or draft_version.manifest_snapshot != current_manifest_snapshot:
            draft_version.source_commit_sha = source_bundle.head_commit_sha
            draft_version.manifest_snapshot = current_manifest_snapshot
            draft_version.runtime_policy_snapshot = runtime_policy_snapshot(document)
            session.commit()

        return SkillSourceResponse(
            readme_content=source_bundle.readme_content,
            skill_md_content=source_bundle.skill_md_content,
            skill_yaml_content=render_skill_yaml(document),
            source_ref=source_bundle.source_ref,
            head_commit_sha=source_bundle.head_commit_sha,
        )

    def save_skill_source(
        self,
        session: Session,
        *,
        skill_id: str,
        payload: SaveSkillSourceRequest,
    ) -> SkillSourceResponse:
        definition = self._require_definition(session, skill_id)
        draft_version = self._require_draft_version(session, definition)

        current_head = self.gitlab_gateway.get_branch_head(definition.gitlab_project_id, draft_version.source_ref)
        if current_head != payload.base_commit_sha:
            raise SkillSourceConflictError(
                "source 已变更，请刷新后重试。",
                details={"expected": payload.base_commit_sha, "actual": current_head},
            )

        document = self._document_from_version_snapshot(draft_version)
        document = document_with_prompt_material(
            document,
            readme_content=payload.readme_content,
            skill_md_content=payload.skill_md_content,
        )
        generated_skill_yaml = render_skill_yaml(document)

        new_commit_sha = self.gitlab_gateway.commit_skill_source(
            project_id=definition.gitlab_project_id,
            branch=draft_version.source_ref,
            readme_content=payload.readme_content,
            skill_md_content=payload.skill_md_content,
            skill_yaml_content=generated_skill_yaml,
            commit_message="Update skill source via PSOP WEB IDE",
        )

        draft_version.source_commit_sha = new_commit_sha
        draft_version.manifest_snapshot = manifest_snapshot(document)
        draft_version.runtime_policy_snapshot = runtime_policy_snapshot(document)
        definition.updated_at = now_utc()
        session.commit()

        return SkillSourceResponse(
            readme_content=payload.readme_content,
            skill_md_content=payload.skill_md_content,
            skill_yaml_content=generated_skill_yaml,
            source_ref=draft_version.source_ref,
            head_commit_sha=new_commit_sha,
        )

    def list_repository_tree(
        self,
        session: Session,
        *,
        skill_id: str,
        path: str | None = None,
    ) -> SkillRepositoryTreeResponse:
        definition = self._require_definition(session, skill_id)
        draft_version = self._require_draft_version(session, definition)
        normalized_path = self._normalize_repository_path(path or "", allow_empty=True, allow_trailing_slash=False)
        head_commit_sha = self.gitlab_gateway.get_branch_head(definition.gitlab_project_id, draft_version.source_ref)
        entries = self.gitlab_gateway.list_repository_tree(
            definition.gitlab_project_id,
            draft_version.source_ref,
            normalized_path or None,
        )
        sorted_entries = sorted(entries, key=lambda entry: (entry.type != "tree", entry.name.lower()))

        if draft_version.source_commit_sha != head_commit_sha:
            draft_version.source_commit_sha = head_commit_sha
            session.commit()

        return SkillRepositoryTreeResponse(
            path=normalized_path,
            ref=draft_version.source_ref,
            head_commit_sha=head_commit_sha,
            entries=[
                SkillRepositoryTreeEntryResponse(
                    id=entry.id,
                    name=entry.name,
                    path=entry.path,
                    type=entry.type,
                    mode=entry.mode,
                )
                for entry in sorted_entries
            ],
        )

    def get_repository_file(
        self,
        session: Session,
        *,
        skill_id: str,
        path: str,
    ) -> SkillRepositoryFileResponse:
        definition = self._require_definition(session, skill_id)
        draft_version = self._require_draft_version(session, definition)
        normalized_path = self._normalize_repository_path(path)
        if normalized_path == definition.manifest_path:
            head_commit_sha = self.gitlab_gateway.get_branch_head(definition.gitlab_project_id, draft_version.source_ref)
            source_bundle = self.gitlab_gateway.get_skill_source(definition.gitlab_project_id, draft_version.source_ref)
            document = self._document_from_version_snapshot(draft_version)
            document = document_with_prompt_material(
                document,
                readme_content=source_bundle.readme_content,
                skill_md_content=source_bundle.skill_md_content,
            )
            if draft_version.source_commit_sha != head_commit_sha:
                draft_version.source_commit_sha = head_commit_sha
                draft_version.manifest_snapshot = manifest_snapshot(document)
                draft_version.runtime_policy_snapshot = runtime_policy_snapshot(document)
                session.commit()
            return SkillRepositoryFileResponse(
                file_path=definition.manifest_path,
                file_name=definition.manifest_path.rsplit("/", 1)[-1],
                content=render_skill_yaml(document),
                ref=draft_version.source_ref,
                head_commit_sha=head_commit_sha,
            )

        repository_file = self.gitlab_gateway.get_repository_file(
            definition.gitlab_project_id,
            draft_version.source_ref,
            normalized_path,
        )

        if draft_version.source_commit_sha != repository_file.head_commit_sha:
            draft_version.source_commit_sha = repository_file.head_commit_sha
            session.commit()

        return SkillRepositoryFileResponse(
            file_path=repository_file.file_path,
            file_name=repository_file.file_name,
            content=repository_file.content,
            ref=repository_file.ref,
            head_commit_sha=repository_file.head_commit_sha,
        )

    def save_repository_file(
        self,
        session: Session,
        *,
        skill_id: str,
        payload: SaveSkillRepositoryFileRequest,
    ) -> SkillRepositoryFileResponse:
        definition = self._require_definition(session, skill_id)
        draft_version = self._require_draft_version(session, definition)
        file_path = self._normalize_repository_path(payload.path)

        current_head = self.gitlab_gateway.get_branch_head(definition.gitlab_project_id, draft_version.source_ref)
        if current_head != payload.base_commit_sha:
            raise SkillSourceConflictError(
                "source 已变更，请刷新后重试。",
                details={"expected": payload.base_commit_sha, "actual": current_head},
            )

        document = self._validate_repository_manifest_change(definition, file_path, payload.content)
        new_commit_sha = self.gitlab_gateway.commit_repository_file(
            project_id=definition.gitlab_project_id,
            branch=draft_version.source_ref,
            file_path=file_path,
            content=payload.content,
            action="update",
            commit_message=f"Update {file_path} via PSOP WEB IDE",
        )
        self._sync_draft_after_repository_commit(
            definition,
            draft_version,
            new_commit_sha,
            document=document,
            readme_content=payload.content if file_path == "README.md" else None,
            skill_md_content=payload.content if file_path == "SKILL.md" else None,
        )
        session.commit()

        return SkillRepositoryFileResponse(
            file_path=file_path,
            file_name=file_path.rsplit("/", 1)[-1],
            content=payload.content,
            ref=draft_version.source_ref,
            head_commit_sha=new_commit_sha,
        )

    def create_repository_file(
        self,
        session: Session,
        *,
        skill_id: str,
        payload: CreateSkillRepositoryFileRequest,
    ) -> SkillRepositoryFileResponse:
        definition = self._require_definition(session, skill_id)
        draft_version = self._require_draft_version(session, definition)
        file_path = self._normalize_repository_path(payload.path)
        document = self._validate_repository_manifest_change(definition, file_path, payload.content)

        new_commit_sha = self.gitlab_gateway.commit_repository_file(
            project_id=definition.gitlab_project_id,
            branch=draft_version.source_ref,
            file_path=file_path,
            content=payload.content,
            action="create",
            commit_message=f"Create {file_path} via PSOP WEB IDE",
        )
        self._sync_draft_after_repository_commit(
            definition,
            draft_version,
            new_commit_sha,
            document=document,
            readme_content=payload.content if file_path == "README.md" else None,
            skill_md_content=payload.content if file_path == "SKILL.md" else None,
        )
        session.commit()

        return SkillRepositoryFileResponse(
            file_path=file_path,
            file_name=file_path.rsplit("/", 1)[-1],
            content=payload.content,
            ref=draft_version.source_ref,
            head_commit_sha=new_commit_sha,
        )

    def create_repository_folder(
        self,
        session: Session,
        *,
        skill_id: str,
        payload: CreateSkillRepositoryFolderRequest,
    ) -> SkillRepositoryFileResponse:
        definition = self._require_definition(session, skill_id)
        draft_version = self._require_draft_version(session, definition)
        folder_path = self._normalize_repository_path(payload.path, allow_trailing_slash=True)
        placeholder_path = f"{folder_path.rstrip('/')}/.gitkeep"

        new_commit_sha = self.gitlab_gateway.commit_repository_file(
            project_id=definition.gitlab_project_id,
            branch=draft_version.source_ref,
            file_path=placeholder_path,
            content="",
            action="create",
            commit_message=f"Create folder {folder_path.rstrip('/')} via PSOP WEB IDE",
        )
        self._sync_draft_after_repository_commit(definition, draft_version, new_commit_sha)
        session.commit()

        return SkillRepositoryFileResponse(
            file_path=placeholder_path,
            file_name=".gitkeep",
            content="",
            ref=draft_version.source_ref,
            head_commit_sha=new_commit_sha,
        )

    def publish_skill(
        self,
        session: Session,
        *,
        skill_id: str,
        payload: PublishSkillRequest,
    ) -> PublishSkillResponse:
        definition = self._require_definition(session, skill_id)
        draft_version = self._require_draft_version(session, definition)

        publish_record = PSkillPublishRecord(
            pskill_definition_id=definition.id,
            pskill_version_id=draft_version.id,
            publish_reason=payload.publish_reason,
            publish_status="compiling",
            published_commit_sha=draft_version.source_commit_sha or "",
            release_ref=definition.default_branch,
        )
        session.add(publish_record)
        session.commit()
        LOGGER.info(
            "publish request accepted",
            extra={
                "skill_id": definition.id,
                "skill_key": definition.key,
                "pskill_version_id": draft_version.id,
                "publish_record_id": publish_record.id,
            },
        )

        try:
            with log_context(
                skill_id=definition.id,
                skill_key=definition.key,
                pskill_version_id=draft_version.id,
                publish_record_id=publish_record.id,
            ), start_span(
                "publish.source_freeze",
                skill_id=definition.id,
                skill_key=definition.key,
                pskill_version_id=draft_version.id,
                publish_record_id=publish_record.id,
            ) as span:
                try:
                    source_bundle = self.gitlab_gateway.get_skill_source(definition.gitlab_project_id, definition.default_branch)
                except Exception as exc:
                    record_span_exception(span, exc)
                    raise
            document = self._document_from_version_snapshot(draft_version, source_bundle.skill_yaml_content)
            document = document_with_prompt_material(
                document,
                readme_content=source_bundle.readme_content,
                skill_md_content=source_bundle.skill_md_content,
            )
            self._validate_manifest_identity(definition, document)

            draft_version.source_commit_sha = source_bundle.head_commit_sha
            draft_version.manifest_snapshot = manifest_snapshot(document)
            draft_version.runtime_policy_snapshot = runtime_policy_snapshot(document)

            next_version_no = self.repository.next_published_version_no(session, definition.id)
            published_version = PSkillVersion(
                pskill_definition_id=definition.id,
                version_no=next_version_no,
                status="published",
                source_ref=definition.default_branch,
                source_commit_sha=source_bundle.head_commit_sha,
                manifest_snapshot=manifest_snapshot(document),
                runtime_policy_snapshot=runtime_policy_snapshot(document),
                builder_agent_run_id=draft_version.builder_agent_run_id,
            )
            session.add(published_version)
            session.flush()

            publish_record.pskill_version_id = published_version.id
            publish_record.published_commit_sha = source_bundle.head_commit_sha

            compiler_service = self.compiler_service or CompilerService(
                settings=self.settings,
                gitlab_gateway=self.gitlab_gateway,
                inference_gateway=self.inference_gateway or OpenAICompatibleInferenceGateway.from_settings(self.settings),
            )
            compile_request = compiler_service.create_compile_request_for_publish(
                session,
                pskill_definition=definition,
                pskill_version=published_version,
                publish_record_id=publish_record.id,
            )
            session.commit()
            LOGGER.info(
                "publish compile request queued",
                extra={
                    "skill_id": definition.id,
                    "skill_key": definition.key,
                    "pskill_version_id": published_version.id,
                    "publish_record_id": publish_record.id,
                    "compile_request_id": compile_request.id,
                },
            )
        except Exception:
            session.rollback()
            failed_record = session.get(PSkillPublishRecord, publish_record.id)
            if failed_record:
                failed_record.publish_status = "failed"
                session.commit()
            LOGGER.exception(
                "publish request failed before compile job was queued",
                extra={
                    "skill_id": definition.id,
                    "skill_key": definition.key,
                    "pskill_version_id": draft_version.id,
                    "publish_record_id": publish_record.id,
                },
            )
            raise

        return PublishSkillResponse(
            publish_record=self._build_publish_record_summary(publish_record),
            published_version=self._build_pskill_version_summary(published_version),
            published_commit_sha=source_bundle.head_commit_sha,
            compile_request=compiler_service.get_compile_request(session, compile_request.id),
        )

    def list_publish_records(self, session: Session, *, skill_id: str) -> list[PSkillPublishRecordResponse]:
        definition = self._require_definition(session, skill_id)
        return [
            self._build_publish_record_summary(record)
            for record in self.repository.get_publish_records(session, definition.id)
        ]

    def upload_material(
        self,
        session: Session,
        *,
        skill_id: str,
        filename: str,
        content: bytes,
        mime_type: str,
        name: str | None = None,
        description: str = "",
        material_kind: str | None = None,
        source_note: str = "",
    ) -> PSkillMaterialDetailResponse:
        definition = self._require_definition(session, skill_id)
        safe_name = self._normalize_material_name(name or filename)
        resolved_kind = material_kind or infer_material_kind(filename, mime_type)
        processor = self._material_processor()
        stored_material = processor.store(
            skill_id=definition.id,
            filename=filename,
            content=content,
            mime_type=mime_type,
            name=safe_name,
            description=description or "",
            material_kind=resolved_kind,
            source_note=source_note or "",
        )
        artifact_object = ArtifactObject(
            bucket=stored_material.stored.bucket,
            object_key=stored_material.stored.object_key,
            media_type=stored_material.stored.media_type,
            size_bytes=stored_material.stored.size_bytes,
            checksum=stored_material.stored.checksum,
            content_json=stored_material.artifact_payload,
        )
        session.add(artifact_object)
        session.flush()
        material = PSkillMaterial(
            pskill_definition_id=definition.id,
            artifact_object_id=artifact_object.id,
            name=safe_name,
            description=description or "",
            material_kind=resolved_kind,
            mime_type=stored_material.stored.media_type,
            filename=filename.replace("\\", "/").split("/")[-1].strip() or "upload.bin",
            source_note=source_note or "",
            status="processing",
            size_bytes=stored_material.stored.size_bytes,
            checksum=stored_material.stored.checksum,
            error_message="",
        )
        session.add(material)
        session.commit()
        self._queue_material_analysis(session, material)
        return self._build_material_detail_response(session, material)

    def list_materials(self, session: Session, *, skill_id: str) -> list[PSkillMaterialResponse]:
        self._require_definition(session, skill_id)
        return [
            self._build_material_response(session, material)
            for material in self.repository.list_materials(session, skill_id)
        ]

    def get_material(
        self,
        session: Session,
        *,
        skill_id: str,
        material_id: str,
    ) -> PSkillMaterialDetailResponse:
        material = self._require_material(session, skill_id=skill_id, material_id=material_id)
        return self._build_material_detail_response(session, material)

    def get_material_content(
        self,
        session: Session,
        *,
        skill_id: str,
        material_id: str,
    ) -> MaterialContent:
        material = self._require_material(session, skill_id=skill_id, material_id=material_id)
        artifact_object = session.get(ArtifactObject, material.artifact_object_id)
        if not artifact_object:
            raise SkillNotFoundError("未找到素材对象。", details={"artifact_object_id": material.artifact_object_id})
        content = self.object_store.download_bytes(bucket=artifact_object.bucket, object_key=artifact_object.object_key)
        return MaterialContent(content=content, mime_type=material.mime_type, filename=material.filename)

    def get_material_derived_asset_content(
        self,
        session: Session,
        *,
        skill_id: str,
        material_id: str,
        asset_id: str,
    ) -> MaterialContent:
        material = self._require_material(session, skill_id=skill_id, material_id=material_id)
        asset = self.repository.get_derived_asset(session, asset_id)
        if not asset or asset.material_id != material.id:
            raise SkillNotFoundError(
                "未找到素材派生资产。",
                details={"material_id": material_id, "asset_id": asset_id},
            )
        artifact_object = session.get(ArtifactObject, asset.artifact_object_id)
        if not artifact_object:
            raise SkillNotFoundError("未找到派生资产对象。", details={"artifact_object_id": asset.artifact_object_id})
        content = self.object_store.download_bytes(bucket=artifact_object.bucket, object_key=artifact_object.object_key)
        return MaterialContent(content=content, mime_type=asset.mime_type, filename=asset.filename)

    def delete_material(
        self,
        session: Session,
        *,
        skill_id: str,
        material_id: str,
    ) -> DeletePSkillMaterialResponse:
        material = self._require_material(session, skill_id=skill_id, material_id=material_id)
        material.status = "archived"
        session.commit()
        return DeletePSkillMaterialResponse(deleted=True, material_id=material_id)

    def analyze_material(
        self,
        session: Session,
        *,
        skill_id: str,
        material_id: str,
    ) -> PSkillMaterialAnalysisResponse:
        material = self._require_material(session, skill_id=skill_id, material_id=material_id)
        existing = self.repository.get_latest_material_analysis(session, material.id)
        if material.status == "processing" or (existing and existing.status in {"pending", "running"}):
            raise SkillValidationError(
                "素材正在分析中，不能重复解析。",
                details={
                    "material_id": material_id,
                    "material_status": material.status,
                    "analysis_status": existing.status if existing else "",
                },
            )
        analysis = self._queue_material_analysis(session, material, force=True)
        return self._build_material_analysis_response(session, analysis)

    def batch_analyze_materials(
        self,
        session: Session,
        *,
        skill_id: str,
        payload: BatchAnalyzeMaterialsRequest,
    ) -> BatchAnalyzeMaterialsResponse:
        definition = self._require_definition(session, skill_id)
        if payload.material_ids:
            materials = self.repository.list_materials_by_ids(
                session,
                pskill_definition_id=definition.id,
                material_ids=payload.material_ids,
            )
            found_ids = {material.id for material in materials}
            missing_ids = [material_id for material_id in payload.material_ids if material_id not in found_ids]
            if missing_ids:
                raise SkillNotFoundError("部分素材不存在。", details={"material_ids": missing_ids})
        else:
            materials = self.repository.list_materials(session, definition.id)

        analyses: list[PSkillMaterialAnalysisResponse] = []
        skipped_material_ids: list[str] = []
        for material in materials:
            existing = self.repository.get_latest_material_analysis(session, material.id)
            if material.status == "processing" or (existing and existing.status in {"pending", "running"}):
                skipped_material_ids.append(material.id)
                if existing:
                    analyses.append(self._build_material_analysis_response(session, existing))
                continue
            analysis = self._queue_material_analysis(session, material, force=payload.force)
            analyses.append(self._build_material_analysis_response(session, analysis))
        return BatchAnalyzeMaterialsResponse(
            pskill_definition_id=definition.id,
            requested_count=len(payload.material_ids) if payload.material_ids else len(materials),
            analyzed_count=len(analyses),
            skipped_count=len(skipped_material_ids),
            analyses=analyses,
            skipped_material_ids=skipped_material_ids,
        )

    def get_material_analysis(
        self,
        session: Session,
        *,
        skill_id: str,
        material_id: str,
    ) -> PSkillMaterialAnalysisResponse:
        material = self._require_material(session, skill_id=skill_id, material_id=material_id)
        analysis = self.repository.get_latest_material_analysis(session, material.id)
        if not analysis:
            raise SkillNotFoundError("未找到素材分析记录。", details={"material_id": material_id})
        return self._build_material_analysis_response(session, analysis)

    def process_material_analysis_job(self, session: Session, job_id: str) -> PSkillMaterialAnalysis:
        job = self.job_repository.get_runtime_job(session, job_id)
        if not job:
            raise SkillNotFoundError("未找到素材分析任务。", details={"job_id": job_id})
        analysis_id = str((job.payload or {}).get("analysis_id") or "")
        analysis = self.repository.get_material_analysis(session, analysis_id)
        if not analysis:
            raise SkillNotFoundError("未找到素材分析记录。", details={"job_id": job_id, "analysis_id": analysis_id})
        material = self.repository.get_material(session, analysis.material_id)
        if not material or material.status == "archived":
            raise SkillNotFoundError("未找到原始素材。", details={"material_id": analysis.material_id})
        artifact_object = session.get(ArtifactObject, material.artifact_object_id)
        if not artifact_object:
            raise SkillNotFoundError("未找到素材对象。", details={"artifact_object_id": material.artifact_object_id})

        job.status = "running"
        analysis.status = "running"
        analysis.started_at = analysis.started_at or now_utc()
        material.status = "processing"
        material.error_message = ""
        analysis.error_message = ""
        analysis.error_details = {}
        session.commit()

        try:
            content = self.object_store.download_bytes(
                bucket=artifact_object.bucket,
                object_key=artifact_object.object_key,
            )
            if self._is_video_material(material):
                video_result = analyze_video_material(
                    filename=material.filename,
                    content=content,
                    asr_gateway=self._asr_gateway(),
                    inference_gateway=self._inference_gateway(),
                    max_keyframes=int(getattr(self.settings, "video_max_analyzed_frames", MAX_ANALYZED_KEYFRAMES)),
                )
                asset_payloads = self._persist_video_derived_assets(
                    session,
                    material=material,
                    analysis=analysis,
                    result=video_result,
                )
                analysis.analysis_result = self._build_video_material_analysis_result(
                    material=material,
                    result=video_result,
                    assets=asset_payloads,
                )
                analysis.status = "ready"
                analysis.error_message = ""
                analysis.error_details = {}
                material.status = "ready"
                material.error_message = ""
            else:
                result = self._material_processor().analyze(
                    material_id=material.id,
                    filename=material.filename,
                    content=content,
                    mime_type=material.mime_type,
                    name=material.name,
                    description=material.description,
                    material_kind=material.material_kind,
                    source_note=material.source_note,
                )
                analysis.status = result.status
                analysis.analysis_result = result.analysis_result
                analysis.error_message = result.error_message
                analysis.error_details = result.error_details
                material.status = result.status
                material.error_message = result.error_message
            analysis.ended_at = now_utc()
            job.status = "succeeded" if analysis.status == "ready" else "failed"
            job.last_error = analysis.error_message
            self._sync_material_analysis_job_metrics(job, analysis.analysis_result)
            session.commit()
            return analysis
        except Exception as exc:
            error_details = self._exception_details(exc)
            analysis.status = "failed"
            analysis.error_message = str(exc)
            analysis.error_details = error_details
            analysis.analysis_result = self._failed_material_analysis_result(material, error_details)
            analysis.ended_at = now_utc()
            job.status = "failed"
            job.last_error = str(exc)
            self._sync_material_analysis_job_metrics(job, analysis.analysis_result)
            material.status = "failed"
            material.error_message = str(exc)
            session.commit()
            LOGGER.exception("raw material analysis failed", extra={"material_id": material.id, "job_id": job_id})
            return analysis

    def generate_skill_draft_from_materials(
        self,
        session: Session,
        *,
        skill_id: str,
        payload: GenerateSkillDraftRequest,
    ) -> PSkillMaterialGenerationResponse:
        definition = self._require_definition(session, skill_id)
        materials = self.repository.list_materials(session, definition.id)
        material_ids = [material.id for material in materials]
        if not materials:
            raise SkillValidationError("生成 Skill 至少需要一个已分析完成的视频素材。")
        failed_materials = [material.id for material in materials if material.status != "ready"]
        if failed_materials:
            raise SkillValidationError("存在未就绪素材，不能用于生成 Skill。", details={"material_ids": failed_materials})
        if not any(self._is_video_material(material) for material in materials):
            raise SkillValidationError("生成 Skill 至少需要选择一个已分析完成的视频素材。")
        if payload.base_commit_sha:
            draft_version = self._require_draft_version(session, definition)
            current_head = self.gitlab_gateway.get_branch_head(definition.gitlab_project_id, draft_version.source_ref)
            if current_head != payload.base_commit_sha:
                raise SkillSourceConflictError(
                    "source 已变更，请刷新后重试。",
                    details={"expected": payload.base_commit_sha, "actual": current_head},
                )

        generation = PSkillMaterialGeneration(
            pskill_definition_id=definition.id,
            material_ids=material_ids,
            user_description=payload.user_description,
            status="pending",
            prompt_metadata={"reference_files": []},
            raw_response={
                "request": {
                    "material_ids": material_ids,
                    "user_description": payload.user_description,
                    "base_commit_sha": payload.base_commit_sha,
                }
            },
        )
        session.add(generation)
        session.flush()

        job = RuntimeJob(
            job_type=PSKILL_BUILD_JOB_TYPE,
            status="pending",
            payload=self._skill_generation_job_payload(
                pskill_definition_id=definition.id,
                generation_id=generation.id,
                material_ids=material_ids,
                base_commit_sha=payload.base_commit_sha,
                current_stage="queued",
            ),
            dedupe_key=f"pskill-build:{generation.id}",
            max_attempts=self.settings.runtime_job_max_attempts,
        )
        session.add(job)
        session.flush()
        agent_run_id = self._ensure_material_generation_agent_run(
            session,
            generation=generation,
            definition=definition,
            job=job,
            material_ids=material_ids,
            base_commit_sha=payload.base_commit_sha,
        )
        generation.prompt_metadata = {
            **(generation.prompt_metadata or {}),
            "job_id": job.id,
            "job_type": PSKILL_BUILD_JOB_TYPE,
            "agent_run_id": agent_run_id,
        }
        session.commit()

        if not self.settings.runtime_worker_enabled:
            self.process_pskill_build_job(session, job.id)
            refreshed = self.repository.get_material_generation(session, generation.id)
            return self._build_material_generation_response(session, refreshed or generation)

        return self._build_material_generation_response(session, generation)

    def apply_draft_patch(
        self,
        session: Session,
        *,
        skill_id: str,
        payload: ApplyPSkillDraftPatchRequest,
    ) -> PSkillDraftApplyPatchResponse:
        definition = self._require_definition(session, skill_id)
        draft_version = self._require_draft_version(session, definition)
        current_head = self.gitlab_gateway.get_branch_head(definition.gitlab_project_id, draft_version.source_ref)
        if current_head != payload.base_commit_sha:
            raise SkillSourceConflictError(
                "source 已变更，请刷新后重试。",
                details={"expected": payload.base_commit_sha, "actual": current_head},
            )
        if not payload.files:
            raise SkillValidationError("draft patch 至少需要包含一个文件。")

        files_to_commit: dict[str, str] = {}
        readme_content: str | None = None
        skill_md_content: str | None = None
        for raw_path, content in payload.files.items():
            file_path = self._normalize_repository_path(str(raw_path))
            self._validate_repository_manifest_change(definition, file_path, content)
            files_to_commit[file_path] = content
            if file_path == "README.md":
                readme_content = content
            if file_path == "SKILL.md":
                skill_md_content = content
        if not files_to_commit:
            raise SkillValidationError("draft patch 至少需要包含一个有效文件。")

        commit_message = payload.commit_message.strip() or "Apply PSkill draft patch via PSOP WEB IDE"
        new_commit_sha = self.gitlab_gateway.commit_repository_files(
            project_id=definition.gitlab_project_id,
            branch=draft_version.source_ref,
            files=files_to_commit,
            commit_message=commit_message,
        )
        self._sync_draft_after_repository_commit(
            definition,
            draft_version,
            new_commit_sha,
            readme_content=readme_content,
            skill_md_content=skill_md_content,
        )
        if payload.builder_agent_run_id:
            self._validate_builder_agent_run(
                session,
                payload.builder_agent_run_id,
                allowed_owner_ids={definition.id},
            )
            draft_version.builder_agent_run_id = payload.builder_agent_run_id
        session.commit()
        return PSkillDraftApplyPatchResponse(
            applied=True,
            changed_files=sorted(files_to_commit),
            committed_commit_sha=new_commit_sha,
            source=self.get_skill_source(session, skill_id),
        )

    def process_pskill_build_job(
        self,
        session: Session,
        job_id: str,
    ) -> PSkillMaterialGeneration:
        job = self.job_repository.get_runtime_job(session, job_id)
        if not job:
            raise SkillNotFoundError("未找到 Skill 生成任务。", details={"job_id": job_id})
        if job.job_type != PSKILL_BUILD_JOB_TYPE:
            raise SkillValidationError("当前 worker 仅支持 Skill 生成任务。", details={"job_type": job.job_type})
        generation_id = str((job.payload or {}).get("generation_id") or "")
        generation = self.repository.get_material_generation(session, generation_id)
        if not generation:
            raise SkillNotFoundError("未找到 Skill 生成记录。", details={"job_id": job_id, "generation_id": generation_id})
        if generation.status == "succeeded":
            job.status = "succeeded"
            job.lease_until = None
            session.commit()
            return generation

        definition = self._require_definition(session, generation.pskill_definition_id)
        material_ids = [str(item) for item in generation.material_ids]
        agent_run_id = self._ensure_material_generation_agent_run(
            session,
            generation=generation,
            definition=definition,
            job=job,
            material_ids=material_ids,
            base_commit_sha=str((job.payload or {}).get("base_commit_sha") or "") or None,
        )
        job.status = "running"
        job.payload = self._skill_generation_job_payload(
            pskill_definition_id=generation.pskill_definition_id,
            generation_id=generation.id,
            material_ids=material_ids,
            base_commit_sha=str((job.payload or {}).get("base_commit_sha") or "") or None,
            current_stage="loading_source",
        )
        generation.status = "running"
        generation.error_message = ""
        generation.prompt_metadata = {
            **(generation.prompt_metadata or {}),
            "job_id": job.id,
            "job_type": PSKILL_BUILD_JOB_TYPE,
            "agent_run_id": agent_run_id,
        }
        self._mark_material_generation_agent_started(session, generation=generation, job=job)
        session.commit()

        try:
            self._run_pskill_build(
                session,
                generation=generation,
                job=job,
                base_commit_sha=str((job.payload or {}).get("base_commit_sha") or "") or None,
            )
            return generation
        except Exception as exc:
            generation.status = "failed"
            generation.error_message = str(exc)
            generation.raw_response = {
                **(generation.raw_response or {}),
                "error": str(exc),
                "error_type": exc.__class__.__name__,
            }
            job.status = "failed"
            job.last_error = str(exc)
            job.lease_until = None
            job.payload = self._set_skill_generation_job_stage(job.payload, "failed", "failed", str(exc))
            self._mark_material_generation_agent_failed(session, generation=generation, error_message=str(exc))
            session.commit()
            LOGGER.exception(
                "skill raw material generation failed",
                extra={"generation_id": generation.id, "job_id": job_id},
            )
            return generation

    def _ensure_material_generation_agent_run(
        self,
        session: Session,
        *,
        generation: PSkillMaterialGeneration,
        definition: PSkillDefinition,
        job: RuntimeJob,
        material_ids: list[str],
        base_commit_sha: str | None,
    ) -> str:
        if generation.agent_run_id:
            return generation.agent_run_id
        agent_run = self.agent_service.create_run(
            session,
            CreateAgentRunRequest(
                agent_key="pskill.builder",
                owner_type="pskill_material_generation",
                owner_id=generation.id,
                input_payload={
                    "schema": "PSkillBuilderInput",
                    "source": "pskills.materials.generate_skill_draft",
                    "generation_id": generation.id,
                    "job_id": job.id,
                    "job_type": job.job_type,
                    "pskill_definition_id": definition.id,
                    "pskill": {
                        "id": definition.id,
                        "key": definition.key,
                        "name": definition.name,
                    },
                    "material_ids": material_ids,
                    "base_commit_sha": base_commit_sha,
                    "user_description": generation.user_description,
                },
            ),
            commit=False,
        )
        generation.agent_run_id = agent_run.id
        generation.prompt_metadata = {
            **(generation.prompt_metadata or {}),
            "agent_run_id": agent_run.id,
        }
        self.agent_service.append_event(
            session,
            agent_run.id,
            AppendAgentEventRequest(
                event_type="pskill.builder.generation.linked",
                phase="builder",
                payload={"generation_id": generation.id, "job_id": job.id, "material_count": len(material_ids)},
            ),
            commit=False,
        )
        return agent_run.id

    def _mark_material_generation_agent_started(
        self,
        session: Session,
        *,
        generation: PSkillMaterialGeneration,
        job: RuntimeJob,
    ) -> None:
        if not generation.agent_run_id:
            return
        agent_run = self.agent_service.get_run_model(session, generation.agent_run_id)
        agent_run.status = "running"
        agent_run.started_at = agent_run.started_at or now_utc()
        self.agent_service.append_event(
            session,
            agent_run.id,
            AppendAgentEventRequest(
                event_type="pskill.builder.generation.started",
                phase="builder",
                payload={"generation_id": generation.id, "job_id": job.id},
            ),
            commit=False,
        )

    def _record_material_generation_model_call(
        self,
        session: Session,
        *,
        generation: PSkillMaterialGeneration,
        prompt_payload: dict[str, Any],
        prompt_metadata: dict[str, Any],
        completion: Any,
    ) -> None:
        if not generation.agent_run_id:
            return
        self.agent_service.record_model_call(
            session,
            agent_run_id=generation.agent_run_id,
            provider=str(getattr(completion, "provider", "") or "llm_inference_gateway"),
            route_key=str(prompt_metadata.get("route_key") or "text"),
            model_name=str(getattr(completion, "model", "") or ""),
            status="succeeded",
            request_payload={
                "generation_id": generation.id,
                "prompt_payload": prompt_payload,
                "agent_prompt": prompt_metadata,
                "llm_request": getattr(completion, "request", {}) or {},
            },
            response_payload={
                "content": getattr(completion, "content", ""),
                "raw": getattr(completion, "raw_response", {}) or {},
            },
            usage_json=dict(getattr(completion, "usage", {}) or {}),
            commit=False,
        )
        self.agent_service.append_event(
            session,
            generation.agent_run_id,
            AppendAgentEventRequest(
                event_type="pskill.builder.model_call.completed",
                phase="builder",
                payload={"generation_id": generation.id, "status": "succeeded"},
            ),
            commit=False,
        )

    def _mark_material_generation_agent_succeeded(
        self,
        session: Session,
        *,
        generation: PSkillMaterialGeneration,
        draft_version: PSkillVersion,
        committed_commit_sha: str,
        generated: GeneratedSkillDraft,
        reference_files: list[str],
    ) -> None:
        if not generation.agent_run_id:
            return
        output_payload = {
            "schema": "PSkillBuilderResult",
            "generation_id": generation.id,
            "pskill_definition_id": generation.pskill_definition_id,
            "pskill_version_id": draft_version.id,
            "committed_commit_sha": committed_commit_sha,
            "material_ids": [str(item) for item in generation.material_ids],
            "generated_files": sorted(generated.files),
            "reference_files": reference_files,
            "generation_reason": generated.generation_reason,
            "review_notes": generated.review_notes,
            "material_usage": generated.material_usage,
        }
        agent_run = self.agent_service.get_run_model(session, generation.agent_run_id)
        agent_run.status = "succeeded"
        agent_run.output_payload = output_payload
        agent_run.error_message = ""
        agent_run.ended_at = now_utc()
        self.agent_service.append_event(
            session,
            agent_run.id,
            AppendAgentEventRequest(
                event_type="pskill.builder.generation.succeeded",
                phase="builder",
                payload=output_payload,
            ),
            commit=False,
        )

    def _mark_material_generation_agent_failed(
        self,
        session: Session,
        *,
        generation: PSkillMaterialGeneration,
        error_message: str,
    ) -> None:
        if not generation.agent_run_id:
            return
        agent_run = self.agent_service.get_run_model(session, generation.agent_run_id)
        agent_run.status = "failed"
        agent_run.error_message = error_message
        agent_run.ended_at = now_utc()
        self.agent_service.append_event(
            session,
            agent_run.id,
            AppendAgentEventRequest(
                event_type="pskill.builder.generation.failed",
                phase="builder",
                payload={"generation_id": generation.id, "error_message": error_message},
            ),
            commit=False,
        )

    def _validate_builder_agent_run(
        self,
        session: Session,
        agent_run_id: str,
        *,
        allowed_owner_ids: set[str] | None = None,
    ) -> None:
        agent_run = self.agent_service.get_run_model(session, agent_run_id)
        if agent_run.agent_key != "pskill.builder":
            raise SkillValidationError(
                "builder_agent_run_id 必须指向 pskill.builder AgentRun。",
                details={"agent_run_id": agent_run_id, "agent_key": agent_run.agent_key},
            )
        if agent_run.status != "succeeded":
            raise SkillValidationError(
                "builder_agent_run_id 必须指向已成功的 pskill.builder AgentRun。",
                details={"agent_run_id": agent_run_id, "status": agent_run.status},
            )
        if allowed_owner_ids and agent_run.owner_id and agent_run.owner_id not in allowed_owner_ids:
            raise SkillValidationError(
                "builder_agent_run_id 不属于当前 PSkill draft。",
                details={"agent_run_id": agent_run_id, "owner_id": agent_run.owner_id},
            )

    def _run_pskill_build(
        self,
        session: Session,
        *,
        generation: PSkillMaterialGeneration,
        job: RuntimeJob,
        base_commit_sha: str | None,
    ) -> None:
        definition = self._require_definition(session, generation.pskill_definition_id)
        draft_version = self._require_draft_version(session, definition)
        material_ids = [str(item) for item in generation.material_ids]
        materials = self.repository.list_materials_by_ids(
            session,
            pskill_definition_id=definition.id,
            material_ids=material_ids,
        )
        if len(materials) != len(material_ids):
            found_ids = {material.id for material in materials}
            raise SkillNotFoundError(
                "部分原始素材不存在。",
                details={"missing_material_ids": [item for item in material_ids if item not in found_ids]},
            )
        material_by_id = {material.id: material for material in materials}
        materials = [material_by_id[material_id] for material_id in material_ids]
        failed_materials = [material.id for material in materials if material.status != "ready"]
        if failed_materials:
            raise SkillValidationError("存在未就绪素材，不能用于生成 Skill。", details={"material_ids": failed_materials})

        material_generation_context = self._collect_generation_material_context(session, materials)
        source_bundle = self.gitlab_gateway.get_skill_source(definition.gitlab_project_id, draft_version.source_ref)
        if base_commit_sha and source_bundle.head_commit_sha != base_commit_sha:
            raise SkillSourceConflictError(
                "source 已变更，请刷新后重试。",
                details={"expected": base_commit_sha, "actual": source_bundle.head_commit_sha},
            )

        prompt_pack = self.agent_prompt_service.resolve_prompt_pack(
            session,
            usage_key="pskill.build.default",
            fallback_ref="skill_creation/conversational_draft/v1",
        )
        prompt_payload = self._build_skill_generation_prompt_payload(
            definition=definition,
            draft_version=draft_version,
            source_bundle=source_bundle,
            materials=materials,
            user_description=generation.user_description,
            material_generation_context=material_generation_context,
        )
        system_prompt = prompt_pack.system_prompt
        user_prompt = json.dumps(prompt_payload, ensure_ascii=False, sort_keys=True, indent=2)
        prompt_hash = hashlib.sha256(f"{system_prompt}\n{user_prompt}".encode("utf-8")).hexdigest()
        prompt_metadata = {
            **prompt_pack.metadata(),
            "job_id": job.id,
            "job_type": PSKILL_BUILD_JOB_TYPE,
            "agent_run_id": generation.agent_run_id,
            "candidate_reference_asset_count": len(material_generation_context["candidate_reference_assets"]),
            "reference_files": [],
        }
        generation.prompt_hash = prompt_hash
        generation.prompt_metadata = prompt_metadata
        generation.raw_response = {"request": {"prompt_payload": prompt_payload, "agent_prompt": prompt_metadata}}
        job.payload = self._set_skill_generation_job_stage(job.payload, "calling_model", "running")
        session.commit()

        completion = self.inference_gateway.complete(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            route_key=prompt_pack.route_key,
        )
        self.job_repository.accumulate_llm_usage(job, completion.usage)
        job.payload = self._set_skill_generation_job_stage(job.payload, "resolving_references", "running")
        self._record_material_generation_model_call(
            session,
            generation=generation,
            prompt_payload=prompt_payload,
            prompt_metadata=prompt_metadata,
            completion=completion,
        )
        session.commit()

        generated = parse_generated_skill_draft(completion.content)
        reference_binary_files, selected_reference_assets, reference_files = self._resolve_selected_reference_assets(
            session,
            selected_reference_assets=generated.selected_reference_assets,
            material_generation_context=material_generation_context,
        )
        prompt_metadata = {
            **prompt_metadata,
            "selected_reference_assets": selected_reference_assets,
            "reference_files": reference_files,
        }
        generation.prompt_metadata = prompt_metadata
        job.payload = self._set_skill_generation_job_stage(job.payload, "committing_source", "running")
        session.commit()

        current_head = self.gitlab_gateway.get_branch_head(definition.gitlab_project_id, draft_version.source_ref)
        if current_head != source_bundle.head_commit_sha:
            raise SkillSourceConflictError(
                "source 已变更，请刷新后重试。",
                details={"expected": source_bundle.head_commit_sha, "actual": current_head},
            )
        committed_commit_sha = self._commit_generated_skill_files(
            definition=definition,
            draft_version=draft_version,
            source_bundle=source_bundle,
            generated=generated,
            reference_binary_files=reference_binary_files,
        )
        generation.status = "succeeded"
        generation.raw_response = {
            "request": {"prompt_payload": prompt_payload, "agent_prompt": prompt_metadata},
            "content": completion.content,
            "parsed": generated.raw_parsed,
            "usage": completion.usage,
            "raw": completion.raw_response,
        }
        generation.generated_files = generated.files
        generation.prompt_metadata = prompt_metadata
        generation.generation_reason = generated.generation_reason
        generation.review_notes = generated.review_notes
        generation.material_usage = generated.material_usage
        generation.committed_commit_sha = committed_commit_sha
        generation.error_message = ""
        draft_version.builder_agent_run_id = generation.agent_run_id
        job.status = "succeeded"
        job.last_error = ""
        job.lease_until = None
        job.payload = self._set_skill_generation_job_stage(job.payload, "succeeded", "succeeded")
        self._mark_material_generation_agent_succeeded(
            session,
            generation=generation,
            draft_version=draft_version,
            committed_commit_sha=committed_commit_sha,
            generated=generated,
            reference_files=reference_files,
        )
        session.commit()

    def _commit_generated_skill_files(
        self,
        *,
        definition: PSkillDefinition,
        draft_version: PSkillVersion,
        source_bundle,
        generated: GeneratedSkillDraft,
        reference_binary_files: dict[str, bytes] | None = None,
    ) -> str:
        document = self._document_from_version_snapshot(draft_version, source_bundle.skill_yaml_content)
        document = document_with_prompt_material(
            document,
            readme_content=generated.files["README.md"],
            skill_md_content=generated.files["SKILL.md"],
        )
        skill_yaml_content = render_skill_yaml(document)
        files_to_commit = dict(generated.files)
        files_to_commit[definition.manifest_path] = skill_yaml_content
        new_commit_sha = self.gitlab_gateway.commit_repository_files(
            project_id=definition.gitlab_project_id,
            branch=draft_version.source_ref,
            files=files_to_commit,
            binary_files=reference_binary_files or {},
            commit_message="Generate PSkill draft from materials via PSOP WEB IDE",
        )
        draft_version.source_commit_sha = new_commit_sha
        draft_version.manifest_snapshot = manifest_snapshot(document)
        draft_version.runtime_policy_snapshot = runtime_policy_snapshot(document)
        definition.updated_at = now_utc()
        return new_commit_sha

    def _build_skill_generation_prompt_payload(
        self,
        *,
        definition: PSkillDefinition,
        draft_version: PSkillVersion,
        source_bundle,
        materials: list[PSkillMaterial],
        user_description: str,
        material_generation_context: dict,
    ) -> dict:
        return {
            "task": "generate_psop_skill_source_from_materials",
            "skill": {
                "id": definition.id,
                "key": definition.key,
                "name": definition.name,
                "description": definition.description,
                "draft_version_id": draft_version.id,
                "source_ref": draft_version.source_ref,
                "source_commit_sha": source_bundle.head_commit_sha,
            },
            "current_source": {
                "README.md": source_bundle.readme_content,
                "SKILL.md": source_bundle.skill_md_content,
            },
            "user_description": user_description,
            "psop_skill_form_definition": self._skill_creation_form_definition_context(),
            "physical_world_skill_guidance": self._physical_world_skill_guidance_context(),
            "publishable_document_skill_standard": self._publishable_document_skill_standard_context(),
            "material_analysis_results": material_generation_context["material_analysis_results"],
            "candidate_reference_assets": material_generation_context["candidate_reference_assets"],
            "output_contract": {
                "format": "json_object",
                "required_files": [
                    "README.md",
                    "SKILL.md",
                    "prompts/system.md",
                    "references/README.md",
                    "examples/input.md",
                    "examples/expected-output.md",
                    "tests/checklist.md",
                ],
                "forbidden_files": ["skill.yaml"],
                "required_top_level_fields": [
                    "directory_tree",
                    "files",
                    "review_notes",
                    "generation_reason",
                    "material_usage",
                    "selected_reference_assets",
                ],
                "draft_policy": "生成结果会提交到 GitLab draft 标准路径，但不会发布、不会编译。",
                "video_reference_policy": (
                    f"必须从 candidate_reference_assets 中选择 1 到 {MAX_SKILL_REFERENCE_ASSETS} 张最适合 Skill 运行时参考的关键帧，"
                    "输出到 selected_reference_assets。每一个 selected_reference_assets.reference_path 都必须至少被 "
                    "SKILL.md 或 references/README.md 引用一次；SKILL.md、references/README.md、examples/ 和 tests/ "
                    "不得引用未出现在 selected_reference_assets 中的 reference_path。"
                ),
                "material_analysis_policy": (
                    "material_analysis_results 是素材证据包，不是任务拆解；"
                    "必须由 Skill 构建智能体综合判断任务目标、步骤、安全风险和完成标准。"
                ),
                "reference_selection_policy": (
                    "优先选择能支撑关键步骤、状态变化、工具/对象识别、安全风险和完成标准的画面；"
                    "避开 Logo、片头、转场、纯水印、重复画面和低信息帧。"
                ),
            },
        }

    @staticmethod
    def _skill_creation_form_definition_context() -> dict:
        return {
            "definition": "PSOP Skill is a source-level contract for a real task. The platform compiles Skills into EG Compile Artifacts.",
            "formal_revision": "psop-eg-formal/v5",
            "core_constraints": [
                "WEB IDE users author Skills; the system compiles and executes EG.",
                "SKILL.md is the source contract for task workflow, evidence, safety, recovery, and completion criteria.",
                "A publishable document Skill must be self-contained enough for compilation from README.md and SKILL.md.",
                "Runtime execution must preserve explicit wait checkpoints and evidence requirements instead of silently advancing.",
            ],
            "minimum_contract_sections": [
                "goal",
                "applicability",
                "inputs",
                "outputs",
                "workflow_steps",
                "wait_checkpoints",
                "expected_evidence",
                "safety_constraints",
                "recovery_paths",
                "completion_criteria",
            ],
            "file_role_constraints": {
                "README.md": "review-facing overview",
                "SKILL.md": "canonical source contract",
                "prompts/system.md": "runtime behavior guidance only; must not contain core contract absent from SKILL.md",
                "references/README.md": "runtime reference knowledge and exact reference paths",
                "examples": "contract-aligned sample interactions",
                "tests/checklist.md": "release review and regression checklist",
            },
        }

    @staticmethod
    def _physical_world_skill_guidance_context() -> dict:
        return {
            "modeling_frame": "Physical-world skills should be modeled as state progression with evidence gates and safety stops.",
            "required_reasoning": [
                "Identify the real-world object state before and after each phase.",
                "Separate instructions, user evidence, completion judgment, and failure recovery.",
                "Make irreversible or hazardous actions explicit before the action is taken.",
                "Ask for photos or explicit confirmation at high-risk checkpoints.",
                "Stop or request evidence when the user skips prerequisites or reports an unsafe state.",
            ],
            "anti_patterns": [
                "turning a long video transcript into a generic article",
                "placing core runtime behavior only in prompts/system.md",
                "using placeholder image paths such as references/.../file.jpg",
                "mixing sponsor chatter, branding, or future predictions into the task contract",
                "advancing through multiple physical phases without evidence gates",
            ],
        }

    @staticmethod
    def _publishable_document_skill_standard_context() -> dict:
        return {
            "status_target": "draft suitable for human publish review",
            "must_pass": [
                "README.md describes purpose, scope, inputs, outputs, and maintenance notes without implementation leakage.",
                "SKILL.md includes a complete staged workflow with prerequisites, actions, evidence, wait points, safety constraints, recovery paths, and completion criteria.",
                "examples/expected-output.md uses the same stage numbering and behavior as SKILL.md.",
                "references/README.md and SKILL.md use exact reference_path values from selected_reference_assets.",
                "No generated text contains TODO, placeholder paths, ellipsis reference paths, or unsupported future-hardware claims.",
                "review_notes explicitly lists material gaps, uncertain assumptions, or items requiring human confirmation.",
                "Every selected_reference_assets/reference_files path is used by SKILL.md or references/README.md, and no document references an unselected reference_path.",
            ],
        }

    def _collect_generation_material_context(
        self,
        session: Session,
        materials: list[PSkillMaterial],
    ) -> dict:
        material_analysis_results: list[dict] = []
        candidate_reference_assets: list[dict] = []
        video_material_ids = [material.id for material in materials if self._is_video_material(material)]
        if not video_material_ids:
            raise SkillValidationError("生成 Skill 至少需要选择一个已分析完成的视频素材。")

        for material in materials:
            analysis = self.repository.get_latest_material_analysis(session, material.id)
            if not analysis or analysis.status != "ready":
                raise SkillValidationError(
                    "存在未完成分析的素材，不能用于生成 Skill。",
                    details={"material_id": material.id, "analysis_status": analysis.status if analysis else "missing"},
                )
            analysis_result = dict(analysis.analysis_result or {})
            analysis_result["analysis_id"] = analysis.id
            material_analysis_results.append(analysis_result)
            if not self._is_video_material(material):
                continue
            assets = self.repository.list_derived_assets(
                session,
                material_id=material.id,
                analysis_id=analysis.id,
            )
            for asset in assets:
                reference_path = asset.reference_path or self._keyframe_reference_path(asset.material_id, asset.timestamp_ms)
                asset_payload = {
                    "id": asset.id,
                    "material_id": material.id,
                    "analysis_id": analysis.id,
                    "asset_kind": asset.asset_kind,
                    "timestamp_ms": asset.timestamp_ms,
                    "label": asset.label,
                    "observations": asset.observations or [],
                    "asset_metadata": asset.asset_metadata or {},
                    "reference_path": reference_path,
                }
                candidate_reference_assets.append(asset_payload)

        if not material_analysis_results:
            raise SkillValidationError("生成 Skill 至少需要选择一个已分析完成的视频素材。")
        return {
            "material_analysis_results": material_analysis_results,
            "candidate_reference_assets": candidate_reference_assets,
        }

    def _resolve_selected_reference_assets(
        self,
        session: Session,
        *,
        selected_reference_assets: list[dict],
        material_generation_context: dict,
    ) -> tuple[dict[str, bytes], list[dict], list[str]]:
        candidate_assets = material_generation_context.get("candidate_reference_assets")
        if not isinstance(candidate_assets, list):
            candidate_assets = []
        candidate_by_id = {
            str(item.get("id")): item
            for item in candidate_assets
            if isinstance(item, dict) and item.get("id")
        }
        candidate_by_reference_path = {
            str(item.get("reference_path")): item
            for item in candidate_assets
            if isinstance(item, dict) and item.get("reference_path")
        }
        if candidate_by_id and not selected_reference_assets:
            raise SkillValidationError("Skill 创建智能体未选择任何参考帧。")
        if len(selected_reference_assets) > MAX_SKILL_REFERENCE_ASSETS:
            raise SkillValidationError(
                "Skill 创建智能体选择的参考帧数量超过限制。",
                details={"max_reference_assets": MAX_SKILL_REFERENCE_ASSETS, "actual": len(selected_reference_assets)},
            )

        binary_files: dict[str, bytes] = {}
        selected_payloads: list[dict] = []
        reference_files: list[str] = []
        seen_asset_ids: set[str] = set()
        for item in selected_reference_assets:
            asset_id = str(item.get("asset_id") or "").strip()
            reference_path_hint = str(item.get("reference_path") or "").strip()
            if not asset_id and reference_path_hint:
                candidate = candidate_by_reference_path.get(reference_path_hint)
                asset_id = str(candidate.get("id") or "").strip() if candidate else ""
            if not asset_id:
                raise SkillValidationError("Skill 创建智能体选择的参考帧缺少 asset_id。", details={"item": item})
            if asset_id in seen_asset_ids:
                continue
            candidate = candidate_by_id.get(asset_id)
            if not candidate:
                raise SkillValidationError(
                    "Skill 创建智能体选择了不属于本次素材的参考帧。",
                    details={"asset_id": asset_id},
                )
            asset = self.repository.get_derived_asset(session, asset_id)
            if not asset:
                raise SkillNotFoundError("未找到派生资产。", details={"asset_id": asset_id})
            artifact_object = session.get(ArtifactObject, asset.artifact_object_id)
            if not artifact_object:
                raise SkillNotFoundError(
                    "未找到派生资产对象。",
                    details={"artifact_object_id": asset.artifact_object_id, "asset_id": asset.id},
                )
            reference_path = str(candidate.get("reference_path") or asset.reference_path or self._keyframe_reference_path(asset.material_id, asset.timestamp_ms))
            binary_files[reference_path] = self.object_store.download_bytes(
                bucket=artifact_object.bucket,
                object_key=artifact_object.object_key,
            )
            selected_payload = {
                "asset_id": asset_id,
                "material_id": candidate.get("material_id", asset.material_id),
                "analysis_id": candidate.get("analysis_id", asset.analysis_id),
                "timestamp_ms": asset.timestamp_ms,
                "reference_path": reference_path,
                "reason": str(item.get("reason") or "").strip(),
            }
            selected_payloads.append(selected_payload)
            reference_files.append(reference_path)
            seen_asset_ids.add(asset_id)
        return binary_files, selected_payloads, reference_files

    @staticmethod
    def _truncate_prompt_text(value: str, limit: int = 40_000) -> str:
        if len(value) <= limit:
            return value
        return value[: limit - 20].rstrip() + "\n...[truncated]"

    @staticmethod
    def _skill_generation_job_payload(
        *,
        pskill_definition_id: str,
        generation_id: str,
        material_ids: list[str],
        base_commit_sha: str | None,
        current_stage: str,
    ) -> dict:
        stages = [
            {"key": "queued", "label": "等待生成", "status": "pending"},
            {"key": "loading_source", "label": "读取素材与源码", "status": "pending"},
            {"key": "calling_model", "label": "构建智能体生成中", "status": "pending"},
            {"key": "resolving_references", "label": "整理参考图片", "status": "pending"},
            {"key": "committing_source", "label": "提交源码草稿", "status": "pending"},
            {"key": "succeeded", "label": "生成完成", "status": "pending"},
        ]
        payload = {
            "operation": "generate_skill_draft_from_materials",
            "pskill_definition_id": pskill_definition_id,
            "generation_id": generation_id,
            "material_ids": material_ids,
            "base_commit_sha": base_commit_sha or "",
            "current_stage": current_stage,
            "progress_stages": stages,
        }
        return SkillsService._set_skill_generation_job_stage(payload, current_stage, "running" if current_stage != "queued" else "pending")

    @staticmethod
    def _set_skill_generation_job_stage(payload: dict | None, stage_key: str, status: str, message: str = "") -> dict:
        updated = dict(payload or {})
        updated["current_stage"] = stage_key
        if message:
            updated["error_message"] = message
        stages = []
        found = False
        stage_order = [stage_key]
        raw_stages = updated.get("progress_stages")
        if isinstance(raw_stages, list):
            stage_order = [str(stage.get("key") or "") for stage in raw_stages if isinstance(stage, dict)]
        completed_keys = set(stage_order[: stage_order.index(stage_key)]) if stage_key in stage_order else set()
        for stage in raw_stages if isinstance(raw_stages, list) else []:
            if not isinstance(stage, dict):
                continue
            item = dict(stage)
            key = str(item.get("key") or "")
            if key == stage_key:
                item["status"] = status
                item["message"] = message
                found = True
            elif status == "succeeded" or key in completed_keys:
                item["status"] = "succeeded"
            elif item.get("status") != "failed":
                item["status"] = "pending"
            stages.append(item)
        if not found and stage_key:
            stages.append({"key": stage_key, "label": stage_key, "status": status, "message": message})
        if stages:
            updated["progress_stages"] = stages
        return updated

    def _queue_material_analysis(
        self,
        session: Session,
        material: PSkillMaterial,
        *,
        force: bool = False,
    ) -> PSkillMaterialAnalysis:
        existing = self.repository.get_latest_material_analysis(session, material.id)
        if existing and (existing.status in {"pending", "running"} or (not force and existing.status == "ready")):
            return existing
        analysis = PSkillMaterialAnalysis(
            pskill_definition_id=material.pskill_definition_id,
            material_id=material.id,
            status="pending",
        )
        session.add(analysis)
        session.flush()
        material.status = "processing"
        material.error_message = ""
        job = RuntimeJob(
            job_type=MATERIAL_ANALYSIS_JOB_TYPE,
            status="pending",
            payload={
                "pskill_definition_id": material.pskill_definition_id,
                "material_id": material.id,
                "analysis_id": analysis.id,
            },
            dedupe_key=f"material-analysis:{analysis.id}",
            max_attempts=self.settings.runtime_job_max_attempts,
        )
        session.add(job)
        session.commit()
        if not self.settings.runtime_worker_enabled:
            self.process_material_analysis_job(session, job.id)
            refreshed = self.repository.get_material_analysis(session, analysis.id)
            return refreshed or analysis
        return analysis

    def _persist_video_derived_assets(
        self,
        session: Session,
        *,
        material: PSkillMaterial,
        analysis: PSkillMaterialAnalysis,
        result: VideoAnalysisResult,
    ) -> list[dict]:
        assets: list[dict] = []
        for keyframe in result.keyframes:
            reference_path = self._keyframe_reference_path(material.id, keyframe.timestamp_ms)
            object_key = "/".join(
                [
                    "pskill-material-derived-assets",
                    material.pskill_definition_id,
                    material.id,
                    analysis.id,
                    keyframe.filename,
                ]
            )
            stored = self.object_store.upload_bytes(
                object_key=object_key,
                content=keyframe.content,
                media_type="image/jpeg",
                metadata={
                    "skill_id": material.pskill_definition_id,
                    "material_id": material.id,
                    "analysis_id": analysis.id,
                    "timestamp_ms": str(keyframe.timestamp_ms),
                },
            )
            artifact_object = ArtifactObject(
                bucket=stored.bucket,
                object_key=stored.object_key,
                media_type=stored.media_type,
                size_bytes=stored.size_bytes,
                checksum=stored.checksum,
                content_json={
                    "kind": "pskill_material_derived_asset",
                    "asset_kind": "video_keyframe",
                    "material_id": material.id,
                    "analysis_id": analysis.id,
                    "timestamp_ms": keyframe.timestamp_ms,
                    "filename": keyframe.filename,
                    "reference_path": reference_path,
                    "asset_metadata": keyframe.metadata,
                },
            )
            session.add(artifact_object)
            session.flush()
            row = PSkillMaterialDerivedAsset(
                pskill_definition_id=material.pskill_definition_id,
                material_id=material.id,
                analysis_id=analysis.id,
                artifact_object_id=artifact_object.id,
                asset_kind="video_keyframe",
                timestamp_ms=keyframe.timestamp_ms,
                filename=keyframe.filename,
                mime_type="image/jpeg",
                label=keyframe.caption,
                observations=keyframe.observations,
                asset_metadata=keyframe.metadata,
                reference_path=reference_path,
            )
            session.add(row)
            session.flush()
            assets.append(
                {
                    "id": row.id,
                    "kind": row.asset_kind,
                    "timestamp_ms": row.timestamp_ms,
                    "filename": row.filename,
                    "mime_type": row.mime_type,
                    "label": row.label,
                    "observations": row.observations or [],
                    "asset_metadata": row.asset_metadata or {},
                    "reference_path": row.reference_path,
                    "artifact_object_id": row.artifact_object_id,
                }
            )
        return assets

    def _build_video_material_analysis_result(
        self,
        *,
        material: PSkillMaterial,
        result: VideoAnalysisResult,
        assets: list[dict],
    ) -> dict:
        first_caption = result.keyframes[0].caption if result.keyframes else ""
        summary = (
            f"视频分析完成：ASR 提取 {len(result.asr.text)} 个字符，"
            f"识别 {len(result.keyframes)} 个候选视频帧。"
            + (f" 首个候选画面：{first_caption}" if first_caption else "")
        )
        evidence_items = []
        if result.asr.text:
            evidence_items.append(
                {
                    "id": "asr-transcript",
                    "kind": "audio_transcript",
                    "content": self._truncate_prompt_text(result.asr.text),
                    "observations": [],
                }
            )
        asset_by_timestamp = {int(item["timestamp_ms"]): item for item in assets}
        for index, keyframe in enumerate(result.keyframes, start=1):
            asset = asset_by_timestamp.get(keyframe.timestamp_ms, {})
            evidence_items.append(
                {
                    "id": f"keyframe-{index}",
                    "kind": "video_keyframe",
                    "timestamp_ms": keyframe.timestamp_ms,
                    "content": keyframe.caption,
                    "observations": keyframe.observations or [],
                    "asset_id": asset.get("id", ""),
                    "reference_path": asset.get("reference_path", ""),
                    "asset_metadata": keyframe.metadata,
                }
            )
        return {
            "schema_version": "1.0",
            "material_type": "video",
            "source": {
                "material_id": material.id,
                "name": material.name,
                "description": material.description,
                "material_kind": material.material_kind,
                "filename": material.filename,
                "mime_type": material.mime_type,
                "source_note": material.source_note,
            },
            "summary": summary,
            "content": {
                "text": self._truncate_prompt_text(result.asr.text),
                "language": result.asr.language or "",
                "source_type": "asr",
            },
            "evidence_items": evidence_items,
            "assets": assets,
            "signals": [],
            "limitations": [*result.limitations, *(["ASR 未返回可用文本。"] if not result.asr.text else [])],
            "debug": {
                "processor": "video_analysis",
                "asr_language": result.asr.language or "",
                "video_duration_ms": result.duration_ms,
                "keyframe_count": len(result.keyframes),
                **(result.debug or {}),
            },
        }

    @staticmethod
    def _failed_material_analysis_result(material: PSkillMaterial, error_details: dict) -> dict:
        return {
            "schema_version": "1.0",
            "material_type": infer_material_kind(material.filename, material.mime_type),
            "source": {
                "material_id": material.id,
                "name": material.name,
                "description": material.description,
                "material_kind": material.material_kind,
                "filename": material.filename,
                "mime_type": material.mime_type,
                "source_note": material.source_note,
            },
            "summary": "素材解析失败。",
            "content": {"text": "", "language": "", "source_type": "error"},
            "evidence_items": [],
            "assets": [],
            "signals": [],
            "limitations": [str(error_details.get("message") or "素材解析失败。")],
            "debug": {"processor": "failed", "error_details": error_details},
        }

    @staticmethod
    def _is_video_material(material: PSkillMaterial) -> bool:
        return material.material_kind == "video" or material.mime_type.startswith("video/")

    def _asr_gateway(self) -> AsrGateway:
        return self.asr_gateway or HttpAsrGateway.from_settings(self.settings)

    def _inference_gateway(self) -> LlmInferenceGateway:
        return self.inference_gateway or OpenAICompatibleInferenceGateway.from_settings(self.settings)

    @staticmethod
    def _sync_material_analysis_job_metrics(job: RuntimeJob, analysis_result: dict) -> None:
        debug = analysis_result.get("debug") if isinstance(analysis_result, dict) else None
        usage = debug.get("usage") if isinstance(debug, dict) else None
        if not isinstance(usage, dict):
            return
        metrics = dict(job.metrics or {})
        changed = False
        for key in ("input_tokens", "output_tokens", "total_tokens"):
            value = usage.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                metrics[key] = value
                changed = True
        calls = usage.get("llm_calls")
        if isinstance(calls, int) and not isinstance(calls, bool):
            metrics["llm_calls"] = calls
            changed = True
        elif changed:
            metrics["llm_calls"] = 1
        if changed:
            job.metrics = metrics

    def _material_processor(self) -> MaterialProcessor:
        return MaterialProcessor(
            settings=self.settings,
            inference_gateway=self._inference_gateway(),
            object_store=self.object_store,
        )

    @staticmethod
    def _normalize_material_name(value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise SkillValidationError("素材名称不能为空。")
        if len(normalized) > 160:
            return normalized[:160]
        return normalized

    def _require_material(self, session: Session, *, skill_id: str, material_id: str) -> PSkillMaterial:
        material = self.repository.get_material(session, material_id)
        if not material or material.pskill_definition_id != skill_id or material.status == "archived":
            raise SkillNotFoundError("未找到原始素材。", details={"material_id": material_id, "skill_id": skill_id})
        return material

    @staticmethod
    def _normalize_repository_path(
        value: str,
        *,
        allow_empty: bool = False,
        allow_trailing_slash: bool = False,
    ) -> str:
        normalized = value.strip().replace("\\", "/")
        while "//" in normalized:
            normalized = normalized.replace("//", "/")
        normalized = normalized.lstrip("/")
        if allow_trailing_slash:
            normalized = normalized.rstrip("/")
        elif normalized.endswith("/"):
            normalized = normalized.rstrip("/")

        parts = [part for part in normalized.split("/") if part]
        if not parts and not allow_empty:
            raise SkillValidationError("仓库路径不能为空。")
        if any(part in {".", ".."} for part in parts):
            raise SkillValidationError("仓库路径不能包含 `.` 或 `..`。", details={"path": value})

        normalized = "/".join(parts)
        if not normalized and not allow_empty:
            raise SkillValidationError("仓库路径不能为空。")
        return normalized

    def _validate_repository_manifest_change(
        self,
        definition: PSkillDefinition,
        file_path: str,
        content: str,
    ):
        if file_path != definition.manifest_path:
            return None

        raise SkillValidationError("`skill.yaml` 是系统生成的 manifest 预览文件，请通过结构化配置表单修改。")

    def _document_from_version_snapshot(
        self,
        version: PSkillVersion,
        source_skill_yaml_content: str | None = None,
    ) -> SkillDocument:
        if not version.manifest_snapshot and source_skill_yaml_content:
            return parse_skill_yaml(source_skill_yaml_content)
        return document_from_manifest_snapshot(version.manifest_snapshot)

    @staticmethod
    def _validate_manifest_identity(definition: PSkillDefinition, document: SkillDocument) -> None:
        if document.skill.identity.key != definition.key:
            raise SkillValidationError(
                "manifest identity.key 与平台注册 key 不一致。",
                details={"expected": definition.key, "actual": document.skill.identity.key},
            )
        if document.skill.identity.name != definition.name:
            raise SkillValidationError(
                "manifest identity.name 需与 Skill 基本信息一致，请先通过基本信息面板修改名称。",
                details={"expected": definition.name, "actual": document.skill.identity.name},
            )
        if document.skill.identity.description != definition.description:
            raise SkillValidationError(
                "manifest identity.description 需与 Skill 基本信息一致，请先通过基本信息面板修改描述。",
                details={"expected": definition.description, "actual": document.skill.identity.description},
            )

    def _sync_draft_after_repository_commit(
        self,
        definition: PSkillDefinition,
        draft_version: PSkillVersion,
        commit_sha: str,
        document=None,
        readme_content: str | None = None,
        skill_md_content: str | None = None,
    ) -> None:
        draft_version.source_commit_sha = commit_sha
        definition.updated_at = now_utc()
        if document is not None or readme_content is not None or skill_md_content is not None:
            resolved_document = document or self._document_from_version_snapshot(draft_version)
            resolved_document = document_with_prompt_material(
                resolved_document,
                readme_content=readme_content,
                skill_md_content=skill_md_content,
            )
            draft_version.manifest_snapshot = manifest_snapshot(resolved_document)
            draft_version.runtime_policy_snapshot = runtime_policy_snapshot(resolved_document)

    def _require_definition(self, session: Session, skill_id: str) -> PSkillDefinition:
        definition = self.repository.get_pskill_definition(session, skill_id)
        if not definition:
            raise SkillNotFoundError("未找到对应的 Skill。", details={"skill_id": skill_id})
        return definition

    def _require_draft_version(self, session: Session, definition: PSkillDefinition) -> PSkillVersion:
        draft_version = self.repository.get_draft_version(session, definition)
        if not draft_version:
            raise SkillNotFoundError(
                "当前 Skill 不存在 draft version。",
                details={"skill_id": definition.id},
            )
        return draft_version

    def _build_skill_summary(self, session: Session, definition: PSkillDefinition) -> SkillSummaryResponse:
        draft_version = self.repository.get_draft_version(session, definition)
        latest_published_version = self.repository.get_pskill_version(session, definition.latest_published_version_id)
        return SkillSummaryResponse(
            id=definition.id,
            key=definition.key,
            name=definition.name,
            description=definition.description,
            status=definition.status,
            gitlab_group_path=definition.gitlab_group_path,
            gitlab_project_id=definition.gitlab_project_id,
            repository_url=definition.repository_url,
            default_branch=definition.default_branch,
            manifest_path=definition.manifest_path,
            is_published=latest_published_version is not None,
            latest_draft_head_sha=draft_version.source_commit_sha if draft_version else None,
            latest_published_commit_sha=(
                latest_published_version.source_commit_sha if latest_published_version else None
            ),
            latest_published_at=latest_published_version.created_at if latest_published_version else None,
            created_at=definition.created_at,
            updated_at=definition.updated_at,
        )

    @staticmethod
    def _build_pskill_version_summary(version: PSkillVersion | None) -> PSkillVersionSummaryResponse | None:
        if not version:
            return None
        return PSkillVersionSummaryResponse(
            id=version.id,
            version_no=version.version_no,
            status=version.status,
            source_ref=version.source_ref,
            source_commit_sha=version.source_commit_sha,
            manifest_snapshot=version.manifest_snapshot,
            runtime_policy_snapshot=version.runtime_policy_snapshot,
            builder_agent_run_id=version.builder_agent_run_id,
            created_at=version.created_at,
            updated_at=version.updated_at,
        )

    @staticmethod
    def _build_publish_record_summary(record: PSkillPublishRecord) -> PSkillPublishRecordResponse:
        return PSkillPublishRecordResponse(
            id=record.id,
            pskill_version_id=record.pskill_version_id,
            publish_reason=record.publish_reason,
            publish_status=record.publish_status,
            published_commit_sha=record.published_commit_sha,
            release_ref=record.release_ref,
            published_at=record.published_at,
            created_at=record.created_at,
        )

    def _build_material_response(self, session: Session, material: PSkillMaterial) -> PSkillMaterialResponse:
        analysis = self.repository.get_latest_material_analysis(session, material.id)
        derived_asset_count = 0
        if analysis:
            derived_asset_count = len(
                self.repository.list_derived_assets(session, material_id=material.id, analysis_id=analysis.id)
            )
        analysis_result = analysis.analysis_result if analysis else {}
        return PSkillMaterialResponse(
            id=material.id,
            pskill_definition_id=material.pskill_definition_id,
            artifact_object_id=material.artifact_object_id,
            name=material.name,
            description=material.description,
            material_kind=material.material_kind,
            mime_type=material.mime_type,
            filename=material.filename,
            source_note=material.source_note,
            status=material.status,
            size_bytes=material.size_bytes,
            checksum=material.checksum,
            error_message=material.error_message,
            analysis_status=analysis.status if analysis else None,
            analysis_id=analysis.id if analysis else None,
            analysis_result_summary=str((analysis_result or {}).get("summary") or ""),
            derived_asset_count=derived_asset_count,
            created_at=material.created_at,
            updated_at=material.updated_at,
        )

    def _build_material_detail_response(self, session: Session, material: PSkillMaterial) -> PSkillMaterialDetailResponse:
        analysis = self.repository.get_latest_material_analysis(session, material.id)
        derived_assets = (
            self.repository.list_derived_assets(session, material_id=material.id, analysis_id=analysis.id)
            if analysis
            else []
        )
        return PSkillMaterialDetailResponse(
            **self._build_material_response(session, material).model_dump(),
            analysis_result=analysis.analysis_result if analysis else {},
            derived_assets=[self._build_derived_asset_response(item) for item in derived_assets],
        )

    def _build_material_generation_response(
        self,
        session: Session,
        generation: PSkillMaterialGeneration,
    ) -> PSkillMaterialGenerationResponse:
        agent_run = None
        if generation.agent_run_id:
            agent_run = self.agent_service.get_run(session, generation.agent_run_id)
        return PSkillMaterialGenerationResponse(
            id=generation.id,
            job_id=str((generation.prompt_metadata or {}).get("job_id") or "") or None,
            agent_run=agent_run,
            pskill_definition_id=generation.pskill_definition_id,
            material_ids=[str(item) for item in generation.material_ids],
            user_description=generation.user_description,
            status=generation.status,
            prompt_hash=generation.prompt_hash,
            prompt_metadata=generation.prompt_metadata or {},
            raw_response=generation.raw_response or {},
            generated_files={str(key): str(value) for key, value in dict(generation.generated_files or {}).items()},
            generation_reason=generation.generation_reason,
            review_notes=[str(item) for item in (generation.review_notes or [])],
            material_usage=[item for item in (generation.material_usage or []) if isinstance(item, dict)],
            committed_commit_sha=generation.committed_commit_sha,
            error_message=generation.error_message,
            created_at=generation.created_at,
        )

    def _build_material_analysis_response(
        self,
        session: Session,
        analysis: PSkillMaterialAnalysis,
    ) -> PSkillMaterialAnalysisResponse:
        assets = self.repository.list_derived_assets(
            session,
            material_id=analysis.material_id,
            analysis_id=analysis.id,
        )
        return PSkillMaterialAnalysisResponse(
            id=analysis.id,
            material_id=analysis.material_id,
            status=analysis.status,
            analysis_result=analysis.analysis_result or {},
            error_message=analysis.error_message,
            error_details=analysis.error_details or {},
            derived_assets=[self._build_derived_asset_response(item) for item in assets],
            started_at=analysis.started_at,
            ended_at=analysis.ended_at,
            created_at=analysis.created_at,
            updated_at=analysis.updated_at,
        )

    @staticmethod
    def _build_derived_asset_response(asset: PSkillMaterialDerivedAsset) -> PSkillMaterialDerivedAssetResponse:
        return PSkillMaterialDerivedAssetResponse(
            id=asset.id,
            material_id=asset.material_id,
            analysis_id=asset.analysis_id,
            artifact_object_id=asset.artifact_object_id,
            asset_kind=asset.asset_kind,
            timestamp_ms=asset.timestamp_ms,
            filename=asset.filename,
            mime_type=asset.mime_type,
            label=asset.label,
            observations=asset.observations or [],
            asset_metadata=asset.asset_metadata or {},
            reference_path=asset.reference_path or SkillsService._keyframe_reference_path(asset.material_id, asset.timestamp_ms),
            created_at=asset.created_at,
        )

    @staticmethod
    def _keyframe_reference_path(material_id: str, timestamp_ms: int) -> str:
        return f"references/video-keyframes/{material_id}/{timestamp_ms:09d}.jpg"

    @staticmethod
    def _exception_details(exc: Exception) -> dict:
        details = dict(getattr(exc, "details", {}) or {}) if isinstance(exc, SkillsError) else {}
        body = details.get("body")
        if isinstance(body, str) and len(body) > 2000:
            details["body"] = body[:2000] + "\n...[truncated]"
        return {
            "error_type": exc.__class__.__name__,
            "message": str(exc),
            **details,
        }
