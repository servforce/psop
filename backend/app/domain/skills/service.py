from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.agent_harness.agents.psop.builder.schemas import parse_builder_candidate
from app.agent_harness.tools.builtin.builder import (
    BUILDER_REFERENCE_ASSET_FILES_CONTEXT_KEY,
    BUILDER_REVISION_BASELINE_CONTEXT_KEY,
)
from app.agent_harness.schemas import AgentInvocation, AgentResult
from app.agent_harness.service import AgentHarnessService, build_agent_harness_service
from app.core.config import Settings
from app.core.logging import log_context
from app.core.observability import record_span_exception, start_span
from app.domain.skills.exceptions import (
    SkillsError,
    SkillsGatewayError,
    SkillNotFoundError,
    SkillSourceConflictError,
    SkillValidationError,
)
from app.domain.skills.manifest import (
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
from app.domain.agent_prompts.service import AgentPromptService
from app.domain.compiler.models import ArtifactObject
from app.domain.jobs.models import RuntimeJob
from app.domain.jobs.repository import JobRepository
from app.domain.skills.models import (
    SkillDefinition,
    SkillPublishRecord,
    SkillRawMaterial,
    SkillRawMaterialAnalysis,
    SkillRawMaterialDerivedAsset,
    SkillRawMaterialGeneration,
    SkillVersion,
    now_utc,
)
from app.domain.skills.raw_materials import (
    GeneratedSkillDraft,
    RawMaterialProcessor,
    infer_material_kind,
    parse_generated_skill_draft,
)
from app.domain.skills.repository import SkillsRepository
from app.domain.skills.schemas import (
    CreateSkillRepositoryFileRequest,
    CreateSkillRepositoryFolderRequest,
    CreateSkillRequest,
    DeleteSkillRequest,
    DeleteSkillRawMaterialResponse,
    GenerationIntentConfirmation,
    GenerationIntentOption,
    GenerationIntentPreviewRequest,
    GenerationIntentPreviewResponse,
    GenerateSkillDraftRequest,
    PublishSkillRequest,
    PublishSkillResponse,
    SaveSkillRepositoryFileRequest,
    SaveSkillSourceRequest,
    SkillDetailResponse,
    SkillPublishRecordResponse,
    SkillRawMaterialAnalysisResponse,
    SkillRawMaterialDerivedAssetResponse,
    SkillRawMaterialDetailResponse,
    SkillRawMaterialGenerationResponse,
    SkillRawMaterialResponse,
    SkillRepositoryFileResponse,
    SkillRepositoryTreeEntryResponse,
    SkillRepositoryTreeResponse,
    SkillSourceResponse,
    SkillSummaryResponse,
    SkillVersionSummaryResponse,
    UpdateSkillRequest,
)
from app.domain.skills.video_analysis import MAX_ANALYZED_KEYFRAMES, MAX_SKILL_REFERENCE_ASSETS, VideoAnalysisResult, analyze_video_material
from app.domain.compiler.service import CompilerService
from app.gateway.asr import AsrGateway, HttpAsrGateway
from app.gateway.inference import LlmInferenceGateway, OpenAICompatibleInferenceGateway
from app.gateway.gitlab import GitLabSkillSourceGateway
from app.infra.object_store import ObjectStoreService

LOGGER = logging.getLogger(__name__)
SKILL_RAW_MATERIAL_GENERATION_JOB_TYPE = "skill_raw_material_generation"
SKILL_KEY_MAX_LENGTH = 120
SKILL_KEY_SUFFIX_LENGTH = 12
REPOSITORY_IMAGE_MEDIA_TYPES = {
    ".gif": "image/gif",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}


@dataclass(frozen=True, slots=True)
class RawMaterialContent:
    content: bytes
    mime_type: str
    filename: str


@dataclass(frozen=True, slots=True)
class RepositoryImageContent:
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
        agent_harness_service: AgentHarnessService | None = None,
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
        self.agent_harness_service = agent_harness_service

    def list_skills(
        self,
        session: Session,
        *,
        search: str | None = None,
        status: str | None = None,
        is_published: bool | None = None,
    ) -> list[SkillSummaryResponse]:
        definitions = self.repository.list_skill_definitions(
            session,
            search=search,
            status=status,
            is_published=is_published,
        )
        return [self._build_skill_summary(session, definition) for definition in definitions]

    def create_skill(self, session: Session, payload: CreateSkillRequest) -> SkillDetailResponse:
        skill_key = self._generate_unique_skill_key(session, payload.name)

        default_document = build_default_skill_document(skill_key, payload.name, payload.description)
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
            project_path=skill_key,
            default_branch=self.settings.gitlab_default_branch,
            initial_readme=default_readme,
            initial_skill_md=default_skill_md,
            initial_skill_yaml=default_skill_yaml,
        )

        definition = SkillDefinition(
            key=skill_key,
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

        draft_version = SkillVersion(
            skill_definition_id=definition.id,
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

    def _generate_unique_skill_key(self, session: Session, name: str) -> str:
        normalized_name = unicodedata.normalize("NFKD", name)
        ascii_name = normalized_name.encode("ascii", "ignore").decode("ascii").lower()
        base = re.sub(r"[^a-z0-9]+", "-", ascii_name).strip("-") or "skill"
        max_base_length = SKILL_KEY_MAX_LENGTH - SKILL_KEY_SUFFIX_LENGTH - 1
        base = base[:max_base_length].rstrip("-") or "skill"

        while True:
            candidate = f"{base}-{uuid4().hex[:SKILL_KEY_SUFFIX_LENGTH]}"
            if self.repository.get_skill_definition_by_key(session, candidate) is None:
                return candidate

    def get_skill_detail(self, session: Session, skill_id: str) -> SkillDetailResponse:
        definition = self._require_definition(session, skill_id)
        draft_version = self.repository.get_draft_version(session, definition)
        latest_published_version = self.repository.get_skill_version(session, definition.latest_published_version_id)

        return SkillDetailResponse(
            **self._build_skill_summary(session, definition).model_dump(),
            current_draft_version=self._build_skill_version_summary(draft_version),
            latest_published_version=self._build_skill_version_summary(latest_published_version),
        )

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

    def get_repository_image_content(
        self,
        session: Session,
        *,
        skill_id: str,
        path: str,
        ref: str,
    ) -> RepositoryImageContent:
        definition = self._require_definition(session, skill_id)
        normalized_path = self._normalize_repository_path(path)
        normalized_ref = ref.strip()
        if not normalized_ref:
            raise SkillValidationError("仓库 Commit 不能为空。")

        mime_type = REPOSITORY_IMAGE_MEDIA_TYPES.get(Path(normalized_path).suffix.lower())
        if not mime_type:
            raise SkillValidationError(
                "源码预览仅支持 JPG、PNG、GIF 和 WebP 图片。",
                details={"path": normalized_path},
            )

        try:
            content = self.gitlab_gateway.get_repository_file_bytes(
                definition.gitlab_project_id,
                normalized_ref,
                normalized_path,
            )
        except SkillsGatewayError as exc:
            if exc.details.get("status_code") == 404:
                raise SkillNotFoundError(
                    "未找到仓库图片。",
                    details={"path": normalized_path, "ref": normalized_ref},
                ) from exc
            raise

        return RepositoryImageContent(
            content=content,
            mime_type=mime_type,
            filename=Path(normalized_path).name,
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

        publish_record = SkillPublishRecord(
            skill_definition_id=definition.id,
            skill_version_id=draft_version.id,
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
                "skill_version_id": draft_version.id,
                "publish_record_id": publish_record.id,
            },
        )

        try:
            with log_context(
                skill_id=definition.id,
                skill_key=definition.key,
                skill_version_id=draft_version.id,
                publish_record_id=publish_record.id,
            ), start_span(
                "publish.source_freeze",
                skill_id=definition.id,
                skill_key=definition.key,
                skill_version_id=draft_version.id,
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
            published_version = SkillVersion(
                skill_definition_id=definition.id,
                version_no=next_version_no,
                status="published",
                source_ref=definition.default_branch,
                source_commit_sha=source_bundle.head_commit_sha,
                manifest_snapshot=manifest_snapshot(document),
                runtime_policy_snapshot=runtime_policy_snapshot(document),
            )
            session.add(published_version)
            session.flush()

            publish_record.skill_version_id = published_version.id
            publish_record.published_commit_sha = source_bundle.head_commit_sha

            compiler_service = self.compiler_service or CompilerService(
                settings=self.settings,
                gitlab_gateway=self.gitlab_gateway,
                inference_gateway=self.inference_gateway or OpenAICompatibleInferenceGateway.from_settings(self.settings),
                agent_harness_service=self.agent_harness_service,
                object_store=self.object_store,
            )
            compile_request = compiler_service.create_compile_request_for_publish(
                session,
                skill_definition=definition,
                skill_version=published_version,
                publish_record_id=publish_record.id,
            )
            session.commit()
            LOGGER.info(
                "publish compile request queued",
                extra={
                    "skill_id": definition.id,
                    "skill_key": definition.key,
                    "skill_version_id": published_version.id,
                    "publish_record_id": publish_record.id,
                    "compile_request_id": compile_request.id,
                },
            )
        except Exception:
            session.rollback()
            failed_record = session.get(SkillPublishRecord, publish_record.id)
            if failed_record:
                failed_record.publish_status = "failed"
                session.commit()
            LOGGER.exception(
                "publish request failed before compile job was queued",
                extra={
                    "skill_id": definition.id,
                    "skill_key": definition.key,
                    "skill_version_id": draft_version.id,
                    "publish_record_id": publish_record.id,
                },
            )
            raise

        return PublishSkillResponse(
            publish_record=self._build_publish_record_summary(publish_record),
            published_version=self._build_skill_version_summary(published_version),
            published_commit_sha=source_bundle.head_commit_sha,
            compile_request=compiler_service.get_compile_request(session, compile_request.id),
        )

    def list_publish_records(self, session: Session, *, skill_id: str) -> list[SkillPublishRecordResponse]:
        definition = self._require_definition(session, skill_id)
        return [
            self._build_publish_record_summary(record)
            for record in self.repository.get_publish_records(session, definition.id)
        ]

    def upload_raw_material(
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
    ) -> SkillRawMaterialDetailResponse:
        definition = self._require_definition(session, skill_id)
        safe_name = self._normalize_material_name(name or filename)
        resolved_kind = material_kind or infer_material_kind(filename, mime_type)
        processor = self._raw_material_processor()
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
        material = SkillRawMaterial(
            skill_definition_id=definition.id,
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
        self._queue_raw_material_analysis(session, material)
        return self._build_raw_material_detail_response(session, material)

    def list_raw_materials(self, session: Session, *, skill_id: str) -> list[SkillRawMaterialResponse]:
        self._require_definition(session, skill_id)
        return [
            self._build_raw_material_response(session, material)
            for material in self.repository.list_raw_materials(session, skill_id)
        ]

    def get_raw_material(
        self,
        session: Session,
        *,
        skill_id: str,
        material_id: str,
    ) -> SkillRawMaterialDetailResponse:
        material = self._require_raw_material(session, skill_id=skill_id, material_id=material_id)
        return self._build_raw_material_detail_response(session, material)

    def get_raw_material_content(
        self,
        session: Session,
        *,
        skill_id: str,
        material_id: str,
    ) -> RawMaterialContent:
        material = self._require_raw_material(session, skill_id=skill_id, material_id=material_id)
        artifact_object = session.get(ArtifactObject, material.artifact_object_id)
        if not artifact_object:
            raise SkillNotFoundError("未找到素材对象。", details={"artifact_object_id": material.artifact_object_id})
        content = self.object_store.download_bytes(bucket=artifact_object.bucket, object_key=artifact_object.object_key)
        return RawMaterialContent(content=content, mime_type=material.mime_type, filename=material.filename)

    def get_raw_material_derived_asset_content(
        self,
        session: Session,
        *,
        skill_id: str,
        material_id: str,
        asset_id: str,
    ) -> RawMaterialContent:
        material = self._require_raw_material(session, skill_id=skill_id, material_id=material_id)
        asset = self.repository.get_derived_asset(session, asset_id)
        if not asset or asset.raw_material_id != material.id:
            raise SkillNotFoundError(
                "未找到素材派生资产。",
                details={"material_id": material_id, "asset_id": asset_id},
            )
        artifact_object = session.get(ArtifactObject, asset.artifact_object_id)
        if not artifact_object:
            raise SkillNotFoundError("未找到派生资产对象。", details={"artifact_object_id": asset.artifact_object_id})
        content = self.object_store.download_bytes(bucket=artifact_object.bucket, object_key=artifact_object.object_key)
        return RawMaterialContent(content=content, mime_type=asset.mime_type, filename=asset.filename)

    def delete_raw_material(
        self,
        session: Session,
        *,
        skill_id: str,
        material_id: str,
    ) -> DeleteSkillRawMaterialResponse:
        material = self._require_raw_material(session, skill_id=skill_id, material_id=material_id)
        material.status = "archived"
        session.commit()
        return DeleteSkillRawMaterialResponse(deleted=True, material_id=material_id)

    def analyze_raw_material(
        self,
        session: Session,
        *,
        skill_id: str,
        material_id: str,
    ) -> SkillRawMaterialAnalysisResponse:
        material = self._require_raw_material(session, skill_id=skill_id, material_id=material_id)
        existing = self.repository.get_latest_raw_material_analysis(session, material.id)
        if material.status == "processing" or (existing and existing.status in {"pending", "running"}):
            raise SkillValidationError(
                "素材正在分析中，不能重复解析。",
                details={
                    "material_id": material_id,
                    "material_status": material.status,
                    "analysis_status": existing.status if existing else "",
                },
            )
        analysis = self._queue_raw_material_analysis(session, material, force=True)
        return self._build_raw_material_analysis_response(session, analysis)

    def get_raw_material_analysis(
        self,
        session: Session,
        *,
        skill_id: str,
        material_id: str,
    ) -> SkillRawMaterialAnalysisResponse:
        material = self._require_raw_material(session, skill_id=skill_id, material_id=material_id)
        analysis = self.repository.get_latest_raw_material_analysis(session, material.id)
        if not analysis:
            raise SkillNotFoundError("未找到素材分析记录。", details={"material_id": material_id})
        return self._build_raw_material_analysis_response(session, analysis)

    def process_raw_material_analysis_job(self, session: Session, job_id: str) -> SkillRawMaterialAnalysis:
        job = self.job_repository.get_runtime_job_for_update(session, job_id)
        if not job:
            raise SkillNotFoundError("未找到素材分析任务。", details={"job_id": job_id})
        analysis_id = str((job.payload or {}).get("analysis_id") or "")
        analysis = self.repository.get_raw_material_analysis(session, analysis_id)
        if not analysis:
            raise SkillNotFoundError("未找到素材分析记录。", details={"job_id": job_id, "analysis_id": analysis_id})
        material = self.repository.get_raw_material(session, analysis.raw_material_id)
        if not material or material.status == "archived":
            raise SkillNotFoundError("未找到原始素材。", details={"material_id": analysis.raw_material_id})
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
                result = self._raw_material_processor().analyze(
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
            self._sync_raw_material_analysis_job_metrics(job, analysis.analysis_result)
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
            self._sync_raw_material_analysis_job_metrics(job, analysis.analysis_result)
            material.status = "failed"
            material.error_message = str(exc)
            session.commit()
            LOGGER.exception("raw material analysis failed", extra={"material_id": material.id, "job_id": job_id})
            return analysis

    def finalize_exhausted_raw_material_analysis_job(
        self,
        session: Session,
        *,
        job_id: str,
        error_message: str,
    ) -> bool:
        """Idempotently fail analysis-owned state without committing the reaper transaction."""

        job = self.job_repository.get_runtime_job_for_update(session, job_id)
        if job is None or job.job_type != "raw_material_analysis":
            return False
        analysis_id = str((job.payload or {}).get("analysis_id") or "")
        if not analysis_id:
            return False
        analysis = self.repository.get_raw_material_analysis_for_update(session, analysis_id)
        if analysis is None or analysis.status in {"ready", "failed"}:
            return False

        reason = error_message.strip() or "Raw material analysis job attempts exhausted."
        error_details = {
            "error_type": "JobAttemptsExhausted",
            "message": reason,
            "job_id": job.id,
        }
        material = self.repository.get_raw_material_for_update(session, analysis.raw_material_id)
        analysis.status = "failed"
        analysis.error_message = reason
        analysis.error_details = error_details
        analysis.ended_at = analysis.ended_at or now_utc()
        if material is not None:
            analysis.analysis_result = self._failed_material_analysis_result(material, error_details)
            if material.status != "archived":
                material.status = "failed"
                material.error_message = reason
        return True

    def preview_skill_generation_intent(
        self,
        session: Session,
        *,
        skill_id: str,
        payload: GenerationIntentPreviewRequest,
    ) -> GenerationIntentPreviewResponse:
        definition = self._require_definition(session, skill_id)
        draft_version = self._require_draft_version(session, definition)
        head_sha = self.gitlab_gateway.get_branch_head(definition.gitlab_project_id, draft_version.source_ref)
        baseline = self._builder_revision_baseline(session, definition.id, head_sha)
        return self._generation_intent_preview(
            skill_id=definition.id,
            source_commit_sha=head_sha,
            user_description=payload.user_description,
            baseline_status=str(baseline["status"]),
        )

    def generate_skill_draft_from_raw_materials(
        self,
        session: Session,
        *,
        skill_id: str,
        payload: GenerateSkillDraftRequest,
    ) -> SkillRawMaterialGenerationResponse:
        definition = self._require_definition(session, skill_id)
        dedupe_key = self._skill_generation_dedupe_key(definition.id, payload.idempotency_key)
        if dedupe_key:
            existing_job = self.job_repository.get_runtime_job_by_dedupe_key(session, dedupe_key)
            if existing_job:
                generation_id = str((existing_job.payload or {}).get("generation_id") or "")
                existing_generation = self.repository.get_raw_material_generation(session, generation_id)
                if existing_generation:
                    return self._build_raw_material_generation_response(existing_generation)
        materials = self.repository.list_raw_materials(session, definition.id)
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

        draft_version = self._require_draft_version(session, definition)
        current_head = self.gitlab_gateway.get_branch_head(definition.gitlab_project_id, draft_version.source_ref)
        baseline = self._builder_revision_baseline(session, definition.id, current_head)
        intent = self._resolve_generation_intent(
            skill_id=definition.id,
            source_commit_sha=current_head,
            user_description=payload.user_description,
            confirmation=payload.generation_intent,
            baseline_status=str(baseline["status"]),
        )

        generation = SkillRawMaterialGeneration(
            skill_definition_id=definition.id,
            material_ids=material_ids,
            user_description=payload.user_description,
            status="pending",
            prompt_metadata={
                "agent_key": "psop.builder",
                "agent_run_id": "",
                "reference_files": [],
                "generation_intent": intent,
                "baseline_status": baseline.get("status"),
                "baseline_generation_id": baseline.get("generation_id", ""),
                "baseline_commit_sha": baseline.get("commit_sha", ""),
                "baseline_candidate_hash": baseline.get("candidate_hash", ""),
                "baseline_inheritance_enabled": intent.get("inheritance_enabled", False),
                "inherited_evidence_count": 0,
            },
            raw_response={
                "request": {
                    "material_ids": material_ids,
                    "user_description": payload.user_description,
                    "base_commit_sha": payload.base_commit_sha,
                    "generation_intent": intent,
                    "idempotency_key": payload.idempotency_key,
                }
            },
        )
        session.add(generation)
        session.flush()
        generation.prompt_metadata = {
            **(generation.prompt_metadata or {}),
            "agent_run_id": generation.id,
        }

        job = RuntimeJob(
            job_type=SKILL_RAW_MATERIAL_GENERATION_JOB_TYPE,
            status="pending",
            payload=self._skill_generation_job_payload(
                skill_definition_id=definition.id,
                generation_id=generation.id,
                material_ids=material_ids,
                base_commit_sha=payload.base_commit_sha,
                current_stage="queued",
            ),
            dedupe_key=dedupe_key or f"skill-raw-material-generation:{generation.id}",
            max_attempts=self.settings.runtime_job_max_attempts,
        )
        session.add(job)
        try:
            session.flush()
        except IntegrityError:
            session.rollback()
            if not dedupe_key:
                raise
            existing_job = self.job_repository.get_runtime_job_by_dedupe_key(session, dedupe_key)
            generation_id = str(((existing_job.payload if existing_job else {}) or {}).get("generation_id") or "")
            existing_generation = self.repository.get_raw_material_generation(session, generation_id)
            if existing_generation is None:
                raise
            return self._build_raw_material_generation_response(existing_generation)
        generation.prompt_metadata = {
            **(generation.prompt_metadata or {}),
            "job_id": job.id,
            "job_type": SKILL_RAW_MATERIAL_GENERATION_JOB_TYPE,
        }
        session.commit()

        if not self.settings.runtime_worker_enabled:
            self.process_skill_raw_material_generation_job(session, job.id)
            refreshed = self.repository.get_raw_material_generation(session, generation.id)
            return self._build_raw_material_generation_response(refreshed or generation)

        return self._build_raw_material_generation_response(generation)

    @staticmethod
    def _generation_intent_preview(
        *,
        skill_id: str,
        source_commit_sha: str,
        user_description: str,
        baseline_status: str = "none",
    ) -> GenerationIntentPreviewResponse:
        text = user_description.strip()
        action_words = ("删除", "移除", "仅保留", "保留素材", "替换", "修改", "不要", "去掉", "无需", "不需要", "不再", "改为", "调整")
        full_rebuild_words = ("全量重建", "全部重写", "完全重建", "从头生成", "重新生成全部")
        question_words = ("？", "?", "是否", "能否", "会不会", "为什么", "怎么")
        is_direct = any(word in text for word in action_words)
        is_full_rebuild = any(word in text for word in full_rebuild_words)
        source_changed_without_baseline = baseline_status in {"history_without_exact_baseline", "invalid_exact_baseline"}
        needs_confirmation = not is_full_rebuild and (
            source_changed_without_baseline or (not is_direct and any(word in text for word in question_words))
        )
        if is_full_rebuild:
            revision_mode = "full_rebuild"
        elif needs_confirmation:
            revision_mode = "confirmation_required"
        elif baseline_status == "exact":
            revision_mode = "direct_revision" if is_direct else "incremental_revision"
        else:
            revision_mode = "direct_revision" if is_direct else "generation"
        preview_hash = hashlib.sha256(
            json.dumps(
                {
                    "skill_id": skill_id,
                    "source_commit_sha": source_commit_sha,
                    "user_description": text,
                    "revision_mode": revision_mode,
                    "baseline_status": baseline_status,
                },
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        if not needs_confirmation:
            return GenerationIntentPreviewResponse(
                status="ready",
                revision_mode=revision_mode,
                summary="已识别为明确生成/修订指令，将按严格证据优先规则执行。",
                preview_hash=preview_hash,
            )
        return GenerationIntentPreviewResponse(
            status="confirmation_required",
            revision_mode=revision_mode,
            summary=(
                "当前源码没有与 Git commit 精确绑定的成功 candidate 基线；请先选择如何处理未被支持的现有内容。"
                if source_changed_without_baseline
                else "该描述是疑问式或修订范围不明确；请先选择如何处理未被素材直接支持的现有内容。"
            ),
            preview_hash=preview_hash,
            options=[
                GenerationIntentOption(
                    id="remove_unsupported",
                    label="移除未被素材支持的内容",
                    revision_instruction="移除或降级所有未被素材直接支持的事实性、强制性流程。",
                ),
                GenerationIntentOption(
                    id="optional_confirmation",
                    label="保留为可选并要求确认",
                    revision_instruction="仅将未被素材支持的内容保留为可选建议或待人工确认项，不得作为强制流程或验收要求。",
                ),
                GenerationIntentOption(
                    id="keep_current",
                    label="保留当前内容",
                    revision_instruction="保留当前内容，但未被素材支持的事实性或强制性内容必须明确标记为待人工确认。",
                ),
            ],
        )

    def _resolve_generation_intent(
        self,
        *,
        skill_id: str,
        source_commit_sha: str,
        user_description: str,
        confirmation: GenerationIntentConfirmation | None,
        baseline_status: str = "none",
    ) -> dict:
        preview = self._generation_intent_preview(
            skill_id=skill_id,
            source_commit_sha=source_commit_sha,
            user_description=user_description,
            baseline_status=baseline_status,
        )
        if preview.status == "ready":
            return {
                "mode": preview.revision_mode,
                "revision_instructions": [user_description.strip()],
                "preview_hash": preview.preview_hash,
                "confirmed": True,
                "baseline_status": baseline_status,
                "inheritance_enabled": baseline_status == "exact" and preview.revision_mode != "full_rebuild",
            }
        if confirmation is None or confirmation.preview_hash != preview.preview_hash:
            raise SkillValidationError(
                "生成意图不明确，请先确认修订动作。",
                details={"intent_preview": preview.model_dump(mode="json")},
            )
        selected = next((item for item in preview.options if item.id == confirmation.confirmed_option_id), None)
        if selected is None:
            raise SkillValidationError("无效的生成意图确认选项。")
        return {
            "mode": "confirmed_revision",
            "revision_instructions": [selected.revision_instruction, user_description.strip()],
            "preview_hash": preview.preview_hash,
            "confirmed_option_id": selected.id,
            "confirmed": True,
            "baseline_status": baseline_status,
            "inheritance_enabled": False,
        }

    def process_skill_raw_material_generation_job(
        self,
        session: Session,
        job_id: str,
    ) -> SkillRawMaterialGeneration:
        job = self.job_repository.get_runtime_job_for_update(session, job_id)
        if not job:
            raise SkillNotFoundError("未找到 Skill 生成任务。", details={"job_id": job_id})
        if job.job_type != SKILL_RAW_MATERIAL_GENERATION_JOB_TYPE:
            raise SkillValidationError("当前 worker 仅支持 Skill 生成任务。", details={"job_type": job.job_type})
        generation_id = str((job.payload or {}).get("generation_id") or "")
        generation = self.repository.get_raw_material_generation(session, generation_id)
        if not generation:
            raise SkillNotFoundError("未找到 Skill 生成记录。", details={"job_id": job_id, "generation_id": generation_id})
        if generation.status == "succeeded":
            job.status = "succeeded"
            job.lease_until = None
            session.commit()
            return generation

        job.status = "running"
        job.payload = self._skill_generation_job_payload(
            skill_definition_id=generation.skill_definition_id,
            generation_id=generation.id,
            material_ids=[str(item) for item in generation.material_ids],
            base_commit_sha=str((job.payload or {}).get("base_commit_sha") or "") or None,
            current_stage="loading_source",
        )
        generation.status = "running"
        generation.error_message = ""
        generation.prompt_metadata = {
            **(generation.prompt_metadata or {}),
            "job_id": job.id,
            "job_type": SKILL_RAW_MATERIAL_GENERATION_JOB_TYPE,
        }
        session.commit()

        try:
            self._run_skill_raw_material_generation(
                session,
                generation=generation,
                job=job,
                base_commit_sha=str((job.payload or {}).get("base_commit_sha") or "") or None,
            )
            return generation
        except Exception as exc:
            session.rollback()
            generation = self.repository.get_raw_material_generation(session, generation_id)
            job = self.job_repository.get_runtime_job(session, job_id)
            if not generation or not job:
                LOGGER.exception(
                    "skill raw material generation failed and state reload failed",
                    extra={"generation_id": generation_id, "job_id": job_id},
                )
                raise
            generation.status = "failed"
            generation.error_message = str(exc)
            error_details = self._exception_details(exc)
            generation.raw_response = {
                **(generation.raw_response or {}),
                "error": str(exc),
                "error_type": exc.__class__.__name__,
                "error_details": error_details,
            }
            job.status = "failed"
            job.last_error = str(exc)
            job.lease_until = None
            job.payload = self._set_skill_generation_job_stage(job.payload, "failed", "failed", str(exc))
            session.commit()
            LOGGER.exception(
                "skill raw material generation failed",
                extra={"generation_id": generation.id, "job_id": job_id},
            )
            return generation

    def finalize_exhausted_raw_material_generation_job(
        self,
        session: Session,
        *,
        job_id: str,
        error_message: str,
    ) -> bool:
        """Idempotently fail generation-owned state without committing the reaper transaction."""

        job = self.job_repository.get_runtime_job_for_update(session, job_id)
        if job is None or job.job_type != SKILL_RAW_MATERIAL_GENERATION_JOB_TYPE:
            return False
        generation_id = str((job.payload or {}).get("generation_id") or "")
        if not generation_id:
            return False
        generation = self.repository.get_raw_material_generation_for_update(session, generation_id)
        if generation is None or generation.status in {"succeeded", "failed", "cancelled"}:
            return False

        reason = error_message.strip() or "Skill raw material generation job attempts exhausted."
        generation.status = "failed"
        generation.error_message = reason
        generation.raw_response = {
            **(generation.raw_response or {}),
            "error": reason,
            "error_type": "JobAttemptsExhausted",
            "error_details": {"message": reason, "job_id": job.id},
        }
        job.payload = self._set_skill_generation_job_stage(job.payload, "failed", "failed", reason)
        return True

    def _run_skill_raw_material_generation(
        self,
        session: Session,
        *,
        generation: SkillRawMaterialGeneration,
        job: RuntimeJob,
        base_commit_sha: str | None,
    ) -> None:
        definition = self._require_definition(session, generation.skill_definition_id)
        draft_version = self._require_draft_version(session, definition)
        material_ids = [str(item) for item in generation.material_ids]
        materials = self.repository.list_raw_materials_by_ids(
            session,
            skill_definition_id=definition.id,
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
        generation_intent = dict((generation.prompt_metadata or {}).get("generation_intent") or {})
        previous_validation_summary = self._latest_builder_validation_summary(
            session,
            definition.id,
            current_generation_id=generation.id,
        )
        source_bundle = self.gitlab_gateway.get_skill_source(definition.gitlab_project_id, draft_version.source_ref)
        if base_commit_sha and source_bundle.head_commit_sha != base_commit_sha:
            raise SkillSourceConflictError(
                "source 已变更，请刷新后重试。",
                details={"expected": base_commit_sha, "actual": source_bundle.head_commit_sha},
            )
        revision_baseline = self._builder_revision_baseline(session, definition.id, source_bundle.head_commit_sha)
        revision_baseline["inheritance_enabled"] = bool(
            revision_baseline.get("status") == "exact" and generation_intent.get("inheritance_enabled") is True
        )

        prompt_payload = self._build_skill_generation_prompt_payload(
            definition=definition,
            draft_version=draft_version,
            source_bundle=source_bundle,
            materials=materials,
            user_description=generation.user_description,
            material_generation_context=material_generation_context,
            generation_intent=generation_intent,
            previous_validation_summary=previous_validation_summary,
            revision_baseline=revision_baseline,
        )
        builder_reference_asset_files = self._build_builder_reference_asset_file_payloads(
            session,
            material_generation_context=material_generation_context,
        )
        builder_invocation = self._build_psop_builder_invocation(
            prompt_payload=prompt_payload,
            material_ids=material_ids,
            agent_run_id=generation.id,
        )
        runtime_builder_invocation = builder_invocation.model_copy(deep=True)
        runtime_builder_invocation.context[BUILDER_REFERENCE_ASSET_FILES_CONTEXT_KEY] = builder_reference_asset_files
        builder_invocation_payload = builder_invocation.model_dump(mode="json")
        prompt_hash = hashlib.sha256(
            json.dumps(builder_invocation_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        prompt_metadata = {
            "agent_key": "psop.builder",
            "agent_run_id": generation.id,
            "job_id": job.id,
            "job_type": SKILL_RAW_MATERIAL_GENERATION_JOB_TYPE,
            "candidate_reference_asset_count": len(material_generation_context["candidate_reference_assets"]),
            "reference_asset_file_count": len(builder_reference_asset_files),
            "reference_files": [],
            "generation_intent": generation_intent,
            "previous_validation_summary": previous_validation_summary,
            "baseline_status": revision_baseline.get("status"),
            "baseline_generation_id": revision_baseline.get("generation_id", ""),
            "baseline_commit_sha": revision_baseline.get("commit_sha", ""),
            "baseline_candidate_hash": revision_baseline.get("candidate_hash", ""),
            "baseline_inheritance_enabled": revision_baseline.get("inheritance_enabled", False),
        }
        generation.prompt_hash = prompt_hash
        generation.prompt_metadata = prompt_metadata
        generation.raw_response = {"request": {"agent_invocation": builder_invocation_payload, "agent_prompt": prompt_metadata}}
        job.payload = self._set_skill_generation_job_stage(job.payload, "calling_model", "running")
        session.commit()

        agent_result = self._agent_harness_service().invoke(
            runtime_builder_invocation,
            persistence_session=session,
            persistence_context={
                "related_skill_definition_id": definition.id,
                "related_generation_id": generation.id,
                "related_job_id": job.id,
            },
        )
        self.job_repository.accumulate_llm_usage(job, self._agent_token_usage(agent_result))
        candidate_content = ""
        builder_artifact_path = ""
        prompt_metadata = {
            **prompt_metadata,
            "agent_run_id": agent_result.agent_run_id,
            "sandbox_path": agent_result.sandbox_path or "",
            "events_path": str(Path(agent_result.sandbox_path) / "events.jsonl") if agent_result.sandbox_path else "",
            "standard_search_summary": self._agent_standard_search_summary(agent_result),
            "selected_reference_assets": [],
            "builder_artifact_path": "",
            "builder_files_path": self._agent_artifact_path(agent_result, "skill_draft_files"),
        }
        generation.prompt_metadata = prompt_metadata
        generation.raw_response = {
            "request": {"agent_invocation": builder_invocation_payload, "agent_prompt": prompt_metadata},
            "agent_result": self._agent_result_summary(agent_result),
        }
        session.commit()
        if agent_result.status != "succeeded":
            validation_message = self._agent_validation_failure_message(agent_result)
            validation_diagnostics = self._agent_validation_diagnostics(agent_result)
            budget_failure_details = self._agent_budget_failure_details(agent_result)
            failure_kind = (
                "validation_failed"
                if validation_message
                else str(budget_failure_details.get("failure_kind") or "agent_execution_failed")
            )
            raise SkillsGatewayError(
                validation_message or "PSOP builder 智能体运行失败。",
                details={
                    "agent_run_id": agent_result.agent_run_id,
                    "error": agent_result.error_message,
                    "failure_kind": failure_kind,
                    "validation_diagnostic_count": len(validation_diagnostics),
                    "validation_diagnostics": validation_diagnostics,
                    **{
                        key: value
                        for key, value in budget_failure_details.items()
                        if key != "failure_kind"
                    },
                },
            )
        candidate_content, builder_artifact_path = self._read_builder_candidate_artifact(agent_result)
        prompt_metadata = {
            **prompt_metadata,
            "builder_artifact_path": builder_artifact_path,
        }
        generation.prompt_metadata = prompt_metadata
        job.payload = self._set_skill_generation_job_stage(job.payload, "resolving_references", "running")
        session.commit()

        generated = parse_generated_skill_draft(candidate_content)
        revision_provenance = generated.raw_parsed.get("revision_provenance")
        reference_binary_files, selected_reference_assets, reference_files = self._resolve_selected_reference_assets(
            session,
            selected_reference_assets=generated.selected_reference_assets,
            material_generation_context=material_generation_context,
        )
        generated = self._read_builder_materialized_draft_files(agent_result, generated)
        materialized_reference_image_count = int(generated.raw_parsed.get("materialized_reference_image_count") or 0)
        prompt_metadata = {
            **prompt_metadata,
            "selected_reference_assets": selected_reference_assets,
            "reference_files": reference_files,
            "materialized_reference_image_count": materialized_reference_image_count,
            "revision_provenance": revision_provenance if isinstance(revision_provenance, dict) else {},
            "inherited_evidence_count": (
                int(revision_provenance.get("inherited_evidence_count") or 0)
                if isinstance(revision_provenance, dict)
                else 0
            ),
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
            "request": {"agent_invocation": builder_invocation_payload, "agent_prompt": prompt_metadata},
            "content": candidate_content,
            "parsed": generated.raw_parsed,
            "agent_result": self._agent_result_summary(agent_result),
        }
        generation.generated_files = generated.files
        generation.prompt_metadata = prompt_metadata
        generation.generation_reason = generated.generation_reason
        generation.review_notes = generated.review_notes
        generation.material_usage = generated.material_usage
        generation.committed_commit_sha = committed_commit_sha
        generation.error_message = ""
        job.status = "succeeded"
        job.last_error = ""
        job.lease_until = None
        job.payload = self._set_skill_generation_job_stage(job.payload, "succeeded", "succeeded")
        session.commit()

    def _commit_generated_skill_files(
        self,
        *,
        definition: SkillDefinition,
        draft_version: SkillVersion,
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
            commit_message="Generate skill draft from raw materials via PSOP WEB IDE",
        )
        draft_version.source_commit_sha = new_commit_sha
        draft_version.manifest_snapshot = manifest_snapshot(document)
        draft_version.runtime_policy_snapshot = runtime_policy_snapshot(document)
        definition.updated_at = now_utc()
        return new_commit_sha

    def _build_psop_builder_invocation(
        self,
        *,
        prompt_payload: dict,
        material_ids: list[str],
        agent_run_id: str | None = None,
    ) -> AgentInvocation:
        output_contract = dict(prompt_payload.get("output_contract") or {})
        return AgentInvocation(
            agent_key="psop.builder",
            agent_run_id=agent_run_id,
            input={
                "text": self._builder_agent_input_text(prompt_payload),
                "task": prompt_payload.get("task"),
                "skill": prompt_payload.get("skill") or {},
                "user_description": prompt_payload.get("user_description") or "",
                "current_source": prompt_payload.get("current_source") or {},
                "material_ids": material_ids,
                "output_contract": output_contract,
                "generation_intent": prompt_payload.get("generation_intent") or {},
                "evidence_policy": prompt_payload.get("evidence_policy") or {},
                "previous_validation_summary": prompt_payload.get("previous_validation_summary") or [],
                "revision_baseline": ((prompt_payload.get("revision_baseline") or {}).get("summary") or {}),
                "execution_budget": prompt_payload.get("execution_budget") or {},
            },
            context={
                "material_analysis_results": prompt_payload.get("material_analysis_results") or [],
                "candidate_reference_assets": prompt_payload.get("candidate_reference_assets") or [],
                "standard_search_policy": {
                    "enabled": True,
                    "required_for_builder": False,
                    "max_results": self.settings.standard_lightrag_max_results,
                    "trust_level": "semi_trusted_reference",
                },
                BUILDER_REVISION_BASELINE_CONTEXT_KEY: prompt_payload.get("revision_baseline") or {},
            },
        )

    @staticmethod
    def _builder_agent_input_text(prompt_payload: dict) -> str:
        user_description = str(prompt_payload.get("user_description") or "").strip()
        if not user_description:
            user_description = f"基于素材生成 {((prompt_payload.get('skill') or {}).get('name') or 'PSOP Skill')} draft。"
        control_context = {
            "generation_intent": prompt_payload.get("generation_intent") or {},
            "evidence_policy": prompt_payload.get("evidence_policy") or {},
            "previous_validation_summary": prompt_payload.get("previous_validation_summary") or [],
            "revision_baseline": ((prompt_payload.get("revision_baseline") or {}).get("summary") or {}),
            "execution_budget": prompt_payload.get("execution_budget") or {},
        }
        return (
            f"用户构建请求：\n{user_description}\n\n"
            "以下是平台生成的结构化控制上下文，必须遵守，不是素材事实：\n"
            f"{json.dumps(control_context, ensure_ascii=False, sort_keys=True)}"
        )

    def _read_builder_candidate_artifact(self, agent_result: AgentResult) -> tuple[str, str]:
        artifact_ref = ""
        for artifact in agent_result.artifacts:
            if artifact.artifact_type == "skill_draft_candidate":
                artifact_ref = artifact.path or ""
                break
        candidates: list[Path] = []
        if artifact_ref.startswith("sandbox://") and agent_result.sandbox_path:
            relative = artifact_ref.removeprefix("sandbox://").lstrip("/")
            if relative:
                candidates.append(Path(agent_result.sandbox_path) / relative)
        elif artifact_ref.startswith("/"):
            candidates.append(Path(artifact_ref))
        if agent_result.sandbox_path:
            candidates.append(Path(agent_result.sandbox_path) / "outputs" / "builder-result.json")
        for candidate_path in candidates:
            if candidate_path.exists():
                return candidate_path.read_text(encoding="utf-8"), artifact_ref or "sandbox://outputs/builder-result.json"
        raise SkillsGatewayError(
            "PSOP builder 未生成 builder-result.json。",
            details={"agent_run_id": agent_result.agent_run_id, "artifact_ref": artifact_ref},
        )

    @staticmethod
    def _agent_token_usage(agent_result: AgentResult) -> dict[str, int]:
        usage_event_count = 0
        latest_total: dict | None = None
        for event in agent_result.events:
            if event.event_type != "agent.token.usage":
                continue
            usage_event_count += 1
            total = event.payload.get("total")
            if isinstance(total, dict):
                latest_total = total
        if not latest_total:
            return {}
        usage = {
            "input_tokens": int(latest_total.get("input_tokens") or 0),
            "output_tokens": int(latest_total.get("output_tokens") or 0),
            "total_tokens": int(latest_total.get("total_tokens") or 0),
        }
        if usage_event_count:
            usage["llm_calls"] = usage_event_count
        return usage

    @staticmethod
    def _agent_standard_search_summary(agent_result: AgentResult) -> dict:
        summaries = [
            event.payload
            for event in agent_result.events
            if event.event_type == "agent.tool.standard_search" and isinstance(event.payload, dict)
        ]
        if not summaries:
            return {"called": False, "result_count": 0, "standard_refs": []}
        latest = summaries[-1]
        return {
            "called": True,
            "status": latest.get("status") or "",
            "error_type": latest.get("error_type") or "",
            "result_count": latest.get("result_count") or 0,
            "standard_refs": latest.get("standard_refs") or [],
        }

    @staticmethod
    def _agent_budget_failure_details(agent_result: AgentResult) -> dict:
        budget_event = next(
            (
                event
                for event in reversed(agent_result.events)
                if event.event_type == "agent.budget.exceeded"
                and isinstance(event.payload, dict)
                and event.payload.get("budget_type") == "model_calls"
            ),
            None,
        )
        if budget_event is None:
            return {}
        tool_names = [
            str(event.payload.get("tool_name") or "")
            for event in agent_result.events
            if event.event_type == "agent.tool.started" and isinstance(event.payload, dict)
        ]
        submit_count = sum(tool_name == "psop.builder.submit_candidate" for tool_name in tool_names)
        payload = budget_event.payload
        return {
            "failure_kind": (
                "candidate_not_submitted_within_model_budget"
                if submit_count == 0
                else "model_call_budget_exceeded_during_repair"
            ),
            "model_call_limit": int(payload.get("limit") or 0),
            "model_call_count": int(payload.get("actual") or 0),
            "submit_candidate_call_count": submit_count,
            "last_tool_name": tool_names[-1] if tool_names else "",
        }

    @staticmethod
    def _agent_result_summary(agent_result: AgentResult) -> dict:
        return {
            "agent_run_id": agent_result.agent_run_id,
            "agent_key": agent_result.agent_key,
            "status": agent_result.status,
            "error_message": agent_result.error_message,
            "final_output": agent_result.final_output,
            "sandbox_path": agent_result.sandbox_path,
            "artifacts": [artifact.model_dump(mode="json") for artifact in agent_result.artifacts],
            "event_count": len(agent_result.events),
        }

    @staticmethod
    def _agent_validation_failure_message(agent_result: AgentResult) -> str:
        for event in reversed(agent_result.events):
            if event.event_type != "agent.validation.failed":
                continue
            payload = event.payload if isinstance(event.payload, dict) else {}
            diagnostics = payload.get("diagnostics") if isinstance(payload.get("diagnostics"), list) else []
            if diagnostics and isinstance(diagnostics[0], dict):
                first = diagnostics[0]
                return (
                    f"PSOP builder 候选校验失败（共 {len(diagnostics)} 项）："
                    f"{first.get('path') or 'candidate'}：{first.get('message') or payload.get('error') or ''}"
                )
            return f"PSOP builder 候选校验失败：{payload.get('error') or ''}".rstrip("：")
        return ""

    @staticmethod
    def _agent_validation_diagnostics(agent_result: AgentResult) -> list[dict]:
        for event in reversed(agent_result.events):
            if event.event_type != "agent.validation.failed":
                continue
            diagnostics = event.payload.get("diagnostics") if isinstance(event.payload, dict) else None
            if isinstance(diagnostics, list):
                return [item for item in diagnostics if isinstance(item, dict)]
        return []

    def _latest_builder_validation_summary(
        self,
        session: Session,
        skill_definition_id: str,
        *,
        current_generation_id: str,
    ) -> list[dict]:
        previous = self.repository.get_latest_completed_raw_material_generation(
            session,
            skill_definition_id=skill_definition_id,
            exclude_generation_id=current_generation_id,
        )
        if previous is None or previous.status != "failed":
            return []
        error_details = (previous.raw_response or {}).get("error_details") or {}
        diagnostics = error_details.get("validation_diagnostics") if isinstance(error_details, dict) else None
        if not isinstance(diagnostics, list):
            return []
        return [
            {
                "path": str(item.get("path") or "candidate"),
                "code": str(item.get("code") or "invalid_candidate"),
                "message": str(item.get("message") or "候选字段无效。"),
                "example": item.get("example"),
            }
            for item in diagnostics
            if isinstance(item, dict)
        ]

    def _builder_revision_baseline(
        self,
        session: Session,
        skill_definition_id: str,
        source_commit_sha: str,
    ) -> dict:
        exact_records = self.repository.list_succeeded_raw_material_generations(
            session,
            skill_definition_id=skill_definition_id,
            committed_commit_sha=source_commit_sha,
        )
        exact_record_found = bool(exact_records)
        for generation in exact_records:
            parsed = (generation.raw_response or {}).get("parsed")
            if not isinstance(parsed, dict) or parsed.get("schema_version") != "2.0":
                continue
            try:
                candidate = parse_builder_candidate(parsed)
            except ValueError:
                continue
            candidate_payload = candidate.model_dump(mode="json")
            candidate_hash = hashlib.sha256(
                json.dumps(candidate_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
            ).hexdigest()
            summary = {
                "status": "exact",
                "generation_id": generation.id,
                "commit_sha": source_commit_sha,
                "candidate_hash": candidate_hash,
                "workflow_stages": [item.model_dump(mode="json") for item in candidate.workflow_step_candidates],
                "safety_constraint_ids": [item.constraint_id for item in candidate.safety_constraints],
                "expected_evidence_ids": [item.requirement_id for item in candidate.expected_evidence_requirements],
                "instruction": "这是增量修订：未修改目标必须保持原稳定 ID；平台将机械判定是否继承 provenance。",
            }
            return {
                **summary,
                "candidate": candidate_payload,
                "summary": summary,
                "inheritance_enabled": True,
            }
        successful_history = exact_records or self.repository.list_succeeded_raw_material_generations(
            session,
            skill_definition_id=skill_definition_id,
        )
        status = "invalid_exact_baseline" if exact_record_found else (
            "history_without_exact_baseline" if successful_history else "none"
        )
        summary = {
            "status": status,
            "commit_sha": source_commit_sha,
            "instruction": (
                "当前源码不存在可继承的精确 candidate 基线；不得把 Markdown 当作强制内容证据。"
                if status != "none"
                else "该 Skill 尚无成功 candidate，按首次全量生成处理。"
            ),
        }
        return {**summary, "summary": summary, "inheritance_enabled": False}

    @staticmethod
    def _skill_generation_dedupe_key(skill_definition_id: str, idempotency_key: str | None) -> str:
        if not idempotency_key:
            return ""
        return f"skill-raw-material-generation:{skill_definition_id}:{idempotency_key.strip()}"

    @staticmethod
    def _agent_artifact_path(agent_result: AgentResult, artifact_type: str) -> str:
        for artifact in agent_result.artifacts:
            if artifact.artifact_type == artifact_type:
                return artifact.path or ""
        return ""

    def _agent_harness_service(self) -> AgentHarnessService:
        if self.agent_harness_service is None:
            self.agent_harness_service = build_agent_harness_service(self.settings)
        return self.agent_harness_service

    def _build_skill_generation_prompt_payload(
        self,
        *,
        definition: SkillDefinition,
        draft_version: SkillVersion,
        source_bundle,
        materials: list[SkillRawMaterial],
        user_description: str,
        material_generation_context: dict,
        generation_intent: dict,
        previous_validation_summary: list[dict],
        revision_baseline: dict,
    ) -> dict:
        return {
            "task": "generate_psop_skill_source_from_raw_materials",
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
            "generation_intent": generation_intent,
            "previous_validation_summary": previous_validation_summary,
            "revision_baseline": revision_baseline,
            "execution_budget": {
                "max_model_calls": 13,
                "first_submit_by_model_call": 8,
                "reserved_repair_calls": 4,
                "workspace_staging_allowed": False,
            },
            "evidence_policy": {
                "mode": "strict_evidence_first",
                "priority": [
                    "confirmed_revision_instruction",
                    "direct_material_evidence",
                    "traceable_industry_standard",
                    "current_source_as_revision_target",
                    "builder_inference",
                ],
                "rules": [
                    "当前 draft 仅是待修订内容，不能单独支撑新的事实性或强制性流程。",
                    "每个强制工作流、安全约束和完成标准必须由结构化 evidence_map.used_in 目标关联到素材、用户确认或可追溯标准。",
                    "builder_inference 与 human_confirmation_required 只能用于可选建议、审阅风险或待确认项。",
                    "标准检索不可用时不得引用 industry_standard，必须在 review_notes 写入“标准检索不可用，未引用行业标准”。",
                    "previous_validation_summary 不为空时，必须在首次提交前逐项避免其中列出的字段错误。",
                ],
            },
            "material_analysis_results": material_generation_context["material_analysis_results"],
            "candidate_reference_assets": material_generation_context["candidate_reference_assets"],
            "output_contract": {
                "format": "json_object",
                "schema_version": "2.0",
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
                    "schema_version",
                    "directory_tree",
                    "files",
                    "review_notes",
                    "generation_reason",
                    "material_usage",
                    "industry_standard_usage",
                    "selected_reference_assets",
                    "evidence_map",
                    "missing_questions",
                    "safety_constraints",
                    "workflow_step_candidates",
                    "expected_evidence_requirements",
                ],
                "id_policy": "所有 stage_id、constraint_id、requirement_id 必须匹配 ^[a-z][a-z0-9_]{1,63}$ 并在各自类型内唯一。",
                "workflow_heading_policy": "SKILL.md workflow 标题必须使用 ### [stage_id] title，并与 workflow_step_candidates 精确关联。",
                "structured_reference_policy": (
                    "evidence_map.used_in 与 industry_standard_usage.used_in 必须是 target_type/target_id 对象数组；"
                    "selected_reference_assets 必须包含 stage_ids；不得使用 v1 自由文本引用或兼容别名。"
                ),
                "draft_policy": "生成结果会提交到 GitLab draft 标准路径，但不会发布、不会编译。",
                "video_reference_policy": (
                    f"必须从 candidate_reference_assets 中选择 1 到 {MAX_SKILL_REFERENCE_ASSETS} 张最适合 Skill 运行时参考的关键帧，"
                    "输出到 selected_reference_assets。每一个 selected_reference_assets.reference_path 都必须至少被 "
                    "SKILL.md 的对应流程步骤用 Markdown 图片语法引用一次；SKILL.md、references/README.md、examples/ 和 tests/ "
                    "不得引用未出现在 selected_reference_assets 中的 reference_path。最终提交前平台会把已选参考图片原图提交到 references 目录，"
                    "不要使用 base64 data URI，也不要要求用户打开外部图片链接。"
                ),
                "material_analysis_policy": (
                    "material_analysis_results 是素材直接证据包；未被其直接支持的内容不得写为事实性、强制性流程或验收要求，"
                    "除非 generation_intent 中有已确认的明确修订指令。"
                ),
                "reference_selection_policy": (
                    "优先选择能支撑关键步骤、状态变化、工具/对象识别、安全风险和完成标准的画面；"
                    "避开 Logo、片头、转场、纯水印、重复画面和低信息帧。"
                ),
            },
        }

    def _collect_generation_material_context(
        self,
        session: Session,
        materials: list[SkillRawMaterial],
    ) -> dict:
        material_analysis_results: list[dict] = []
        candidate_reference_assets: list[dict] = []
        video_material_ids = [material.id for material in materials if self._is_video_material(material)]
        if not video_material_ids:
            raise SkillValidationError("生成 Skill 至少需要选择一个已分析完成的视频素材。")

        for material in materials:
            analysis = self.repository.get_latest_raw_material_analysis(session, material.id)
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
                raw_material_id=material.id,
                analysis_id=analysis.id,
            )
            for asset in assets:
                reference_path = asset.reference_path or self._keyframe_reference_path(asset.raw_material_id, asset.timestamp_ms)
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

    def _build_builder_reference_asset_file_payloads(
        self,
        session: Session,
        *,
        material_generation_context: dict,
    ) -> list[dict[str, object]]:
        candidate_assets = material_generation_context.get("candidate_reference_assets")
        if not isinstance(candidate_assets, list):
            return []

        payloads: list[dict[str, object]] = []
        seen_reference_paths: set[str] = set()
        for candidate in candidate_assets:
            if not isinstance(candidate, dict):
                continue
            asset_id = str(candidate.get("id") or candidate.get("asset_id") or "").strip()
            reference_path = str(candidate.get("reference_path") or "").strip()
            if not asset_id or not reference_path or reference_path in seen_reference_paths:
                continue
            try:
                asset = self.repository.get_derived_asset(session, asset_id)
                if not asset:
                    continue
                artifact_object = session.get(ArtifactObject, asset.artifact_object_id)
                if not artifact_object:
                    continue
                content = self.object_store.download_bytes(
                    bucket=artifact_object.bucket,
                    object_key=artifact_object.object_key,
                )
            except Exception:
                LOGGER.warning(
                    "failed to prepare builder reference asset file",
                    extra={"asset_id": asset_id, "reference_path": reference_path},
                    exc_info=True,
                )
                continue

            payloads.append(
                {
                    "asset_id": asset_id,
                    "reference_path": reference_path,
                    "mime_type": asset.mime_type,
                    "content_base64": base64.b64encode(content).decode("ascii"),
                    "content_sha256": hashlib.sha256(content).hexdigest(),
                    "size_bytes": len(content),
                }
            )
            seen_reference_paths.add(reference_path)
        return payloads

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
            reference_path = str(candidate.get("reference_path") or asset.reference_path or self._keyframe_reference_path(asset.raw_material_id, asset.timestamp_ms))
            binary_files[reference_path] = self.object_store.download_bytes(
                bucket=artifact_object.bucket,
                object_key=artifact_object.object_key,
            )
            selected_payload = {
                "asset_id": asset_id,
                "material_id": candidate.get("material_id", asset.raw_material_id),
                "analysis_id": candidate.get("analysis_id", asset.analysis_id),
                "timestamp_ms": asset.timestamp_ms,
                "reference_path": reference_path,
                "mime_type": asset.mime_type,
                "reason": str(item.get("reason") or "").strip(),
            }
            selected_payloads.append(selected_payload)
            reference_files.append(reference_path)
            seen_asset_ids.add(asset_id)
        return binary_files, selected_payloads, reference_files

    @staticmethod
    def _read_builder_materialized_draft_files(agent_result: AgentResult, generated: GeneratedSkillDraft) -> GeneratedSkillDraft:
        if not agent_result.sandbox_path:
            return generated
        root = Path(agent_result.sandbox_path) / "outputs" / "skill-draft"
        if not root.exists():
            return generated
        resolved_root = root.resolve()
        materialized_files: dict[str, str] = {}
        for relative_path in generated.files:
            target = (resolved_root / relative_path).resolve()
            try:
                target.relative_to(resolved_root)
            except ValueError as exc:
                raise SkillValidationError("生成文件路径非法。", details={"path": relative_path}) from exc
            if not target.exists():
                raise SkillValidationError("builder 未物化生成文件。", details={"path": relative_path})
            materialized_files[relative_path] = target.read_text(encoding="utf-8")

        raw_parsed = dict(generated.raw_parsed or {})
        raw_parsed["files"] = materialized_files
        return GeneratedSkillDraft(
            files=materialized_files,
            generation_reason=generated.generation_reason,
            review_notes=generated.review_notes,
            material_usage=generated.material_usage,
            selected_reference_assets=generated.selected_reference_assets,
            directory_tree=generated.directory_tree,
            raw_parsed=raw_parsed,
        )


    @staticmethod
    def _truncate_prompt_text(value: str, limit: int = 40_000) -> str:
        if len(value) <= limit:
            return value
        return value[: limit - 20].rstrip() + "\n...[truncated]"

    @staticmethod
    def _skill_generation_job_payload(
        *,
        skill_definition_id: str,
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
            "operation": "generate_skill_draft_from_raw_materials",
            "skill_definition_id": skill_definition_id,
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

    def _queue_raw_material_analysis(
        self,
        session: Session,
        material: SkillRawMaterial,
        *,
        force: bool = False,
    ) -> SkillRawMaterialAnalysis:
        existing = self.repository.get_latest_raw_material_analysis(session, material.id)
        if existing and (existing.status in {"pending", "running"} or (not force and existing.status == "ready")):
            return existing
        analysis = SkillRawMaterialAnalysis(
            skill_definition_id=material.skill_definition_id,
            raw_material_id=material.id,
            status="pending",
        )
        session.add(analysis)
        session.flush()
        material.status = "processing"
        material.error_message = ""
        job = RuntimeJob(
            job_type="raw_material_analysis",
            status="pending",
            payload={
                "skill_definition_id": material.skill_definition_id,
                "material_id": material.id,
                "analysis_id": analysis.id,
            },
            dedupe_key=f"raw-material-analysis:{analysis.id}",
            max_attempts=self.settings.runtime_job_max_attempts,
        )
        session.add(job)
        session.commit()
        if not self.settings.runtime_worker_enabled:
            self.process_raw_material_analysis_job(session, job.id)
            refreshed = self.repository.get_raw_material_analysis(session, analysis.id)
            return refreshed or analysis
        return analysis

    def _persist_video_derived_assets(
        self,
        session: Session,
        *,
        material: SkillRawMaterial,
        analysis: SkillRawMaterialAnalysis,
        result: VideoAnalysisResult,
    ) -> list[dict]:
        assets: list[dict] = []
        for keyframe in result.keyframes:
            reference_path = self._keyframe_reference_path(material.id, keyframe.timestamp_ms)
            object_key = "/".join(
                [
                    "skill-raw-material-derived-assets",
                    material.skill_definition_id,
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
                    "skill_id": material.skill_definition_id,
                    "raw_material_id": material.id,
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
                    "kind": "skill_raw_material_derived_asset",
                    "asset_kind": "video_keyframe",
                    "raw_material_id": material.id,
                    "analysis_id": analysis.id,
                    "timestamp_ms": keyframe.timestamp_ms,
                    "filename": keyframe.filename,
                    "reference_path": reference_path,
                    "asset_metadata": keyframe.metadata,
                },
            )
            session.add(artifact_object)
            session.flush()
            row = SkillRawMaterialDerivedAsset(
                skill_definition_id=material.skill_definition_id,
                raw_material_id=material.id,
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
        material: SkillRawMaterial,
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
    def _failed_material_analysis_result(material: SkillRawMaterial, error_details: dict) -> dict:
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
    def _is_video_material(material: SkillRawMaterial) -> bool:
        return material.material_kind == "video" or material.mime_type.startswith("video/")

    def _asr_gateway(self) -> AsrGateway:
        return self.asr_gateway or HttpAsrGateway.from_settings(self.settings)

    def _inference_gateway(self) -> LlmInferenceGateway:
        return self.inference_gateway or OpenAICompatibleInferenceGateway.from_settings(self.settings)

    @staticmethod
    def _sync_raw_material_analysis_job_metrics(job: RuntimeJob, analysis_result: dict) -> None:
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

    def _raw_material_processor(self) -> RawMaterialProcessor:
        return RawMaterialProcessor(
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

    def _require_raw_material(self, session: Session, *, skill_id: str, material_id: str) -> SkillRawMaterial:
        material = self.repository.get_raw_material(session, material_id)
        if not material or material.skill_definition_id != skill_id or material.status == "archived":
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
        definition: SkillDefinition,
        file_path: str,
        content: str,
    ):
        if file_path != definition.manifest_path:
            return None

        raise SkillValidationError("`skill.yaml` 是系统生成的 manifest 预览文件，请通过结构化配置表单修改。")

    def _document_from_version_snapshot(
        self,
        version: SkillVersion,
        source_skill_yaml_content: str | None = None,
    ) -> SkillDocument:
        if not version.manifest_snapshot and source_skill_yaml_content:
            return parse_skill_yaml(source_skill_yaml_content)
        return document_from_manifest_snapshot(version.manifest_snapshot)

    @staticmethod
    def _validate_manifest_identity(definition: SkillDefinition, document: SkillDocument) -> None:
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
        definition: SkillDefinition,
        draft_version: SkillVersion,
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

    def _require_definition(self, session: Session, skill_id: str) -> SkillDefinition:
        definition = self.repository.get_skill_definition(session, skill_id)
        if not definition:
            raise SkillNotFoundError("未找到对应的 Skill。", details={"skill_id": skill_id})
        return definition

    def _require_draft_version(self, session: Session, definition: SkillDefinition) -> SkillVersion:
        draft_version = self.repository.get_draft_version(session, definition)
        if not draft_version:
            raise SkillNotFoundError(
                "当前 Skill 不存在 draft version。",
                details={"skill_id": definition.id},
            )
        return draft_version

    def _build_skill_summary(self, session: Session, definition: SkillDefinition) -> SkillSummaryResponse:
        draft_version = self.repository.get_draft_version(session, definition)
        latest_published_version = self.repository.get_skill_version(session, definition.latest_published_version_id)
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
    def _build_skill_version_summary(version: SkillVersion | None) -> SkillVersionSummaryResponse | None:
        if not version:
            return None
        return SkillVersionSummaryResponse(
            id=version.id,
            version_no=version.version_no,
            status=version.status,
            source_ref=version.source_ref,
            source_commit_sha=version.source_commit_sha,
            manifest_snapshot=version.manifest_snapshot,
            runtime_policy_snapshot=version.runtime_policy_snapshot,
            created_at=version.created_at,
            updated_at=version.updated_at,
        )

    @staticmethod
    def _build_publish_record_summary(record: SkillPublishRecord) -> SkillPublishRecordResponse:
        return SkillPublishRecordResponse(
            id=record.id,
            skill_version_id=record.skill_version_id,
            publish_reason=record.publish_reason,
            publish_status=record.publish_status,
            published_commit_sha=record.published_commit_sha,
            release_ref=record.release_ref,
            published_at=record.published_at,
            created_at=record.created_at,
        )

    def _build_raw_material_response(self, session: Session, material: SkillRawMaterial) -> SkillRawMaterialResponse:
        analysis = self.repository.get_latest_raw_material_analysis(session, material.id)
        derived_asset_count = 0
        if analysis:
            derived_asset_count = len(
                self.repository.list_derived_assets(session, raw_material_id=material.id, analysis_id=analysis.id)
            )
        analysis_result = analysis.analysis_result if analysis else {}
        return SkillRawMaterialResponse(
            id=material.id,
            skill_definition_id=material.skill_definition_id,
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

    def _build_raw_material_detail_response(self, session: Session, material: SkillRawMaterial) -> SkillRawMaterialDetailResponse:
        analysis = self.repository.get_latest_raw_material_analysis(session, material.id)
        derived_assets = (
            self.repository.list_derived_assets(session, raw_material_id=material.id, analysis_id=analysis.id)
            if analysis
            else []
        )
        return SkillRawMaterialDetailResponse(
            **self._build_raw_material_response(session, material).model_dump(),
            analysis_result=analysis.analysis_result if analysis else {},
            derived_assets=[self._build_derived_asset_response(item) for item in derived_assets],
        )

    @staticmethod
    def _build_raw_material_generation_response(
        generation: SkillRawMaterialGeneration,
    ) -> SkillRawMaterialGenerationResponse:
        return SkillRawMaterialGenerationResponse(
            id=generation.id,
            job_id=str((generation.prompt_metadata or {}).get("job_id") or "") or None,
            skill_definition_id=generation.skill_definition_id,
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

    def _build_raw_material_analysis_response(
        self,
        session: Session,
        analysis: SkillRawMaterialAnalysis,
    ) -> SkillRawMaterialAnalysisResponse:
        assets = self.repository.list_derived_assets(
            session,
            raw_material_id=analysis.raw_material_id,
            analysis_id=analysis.id,
        )
        return SkillRawMaterialAnalysisResponse(
            id=analysis.id,
            raw_material_id=analysis.raw_material_id,
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
    def _build_derived_asset_response(asset: SkillRawMaterialDerivedAsset) -> SkillRawMaterialDerivedAssetResponse:
        return SkillRawMaterialDerivedAssetResponse(
            id=asset.id,
            raw_material_id=asset.raw_material_id,
            analysis_id=asset.analysis_id,
            artifact_object_id=asset.artifact_object_id,
            asset_kind=asset.asset_kind,
            timestamp_ms=asset.timestamp_ms,
            filename=asset.filename,
            mime_type=asset.mime_type,
            label=asset.label,
            observations=asset.observations or [],
            asset_metadata=asset.asset_metadata or {},
            reference_path=asset.reference_path or SkillsService._keyframe_reference_path(asset.raw_material_id, asset.timestamp_ms),
            created_at=asset.created_at,
        )

    @staticmethod
    def _keyframe_reference_path(raw_material_id: str, timestamp_ms: int) -> str:
        return f"references/video-keyframes/{raw_material_id}/{timestamp_ms:09d}.jpg"

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
