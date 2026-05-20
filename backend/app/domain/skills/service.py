from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.logging import log_context
from app.core.observability import record_span_exception, start_span
from app.domain.skills.exceptions import (
    SkillConflictError,
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
from app.domain.skills.models import (
    SkillDefinition,
    SkillPublishRecord,
    SkillRawMaterial,
    SkillRawMaterialGeneration,
    SkillVersion,
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
    GenerateSkillDraftRequest,
    PublishSkillRequest,
    PublishSkillResponse,
    SaveSkillRepositoryFileRequest,
    SaveSkillSourceRequest,
    SkillDetailResponse,
    SkillPublishRecordResponse,
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
from app.domain.compiler.service import CompilerService
from app.gateway.inference import LlmInferenceGateway, OpenAICompatibleInferenceGateway
from app.gateway.gitlab import GitLabSkillSourceGateway
from app.infra.object_store import ObjectStoreService

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RawMaterialContent:
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
        object_store: ObjectStoreService | None = None,
        agent_prompt_service: AgentPromptService | None = None,
        repository: SkillsRepository | None = None,
    ) -> None:
        self.settings = settings
        self.gitlab_gateway = gitlab_gateway
        self.compiler_service = compiler_service
        self.inference_gateway = inference_gateway
        self.object_store = object_store or ObjectStoreService.from_settings(settings)
        self.agent_prompt_service = agent_prompt_service or AgentPromptService()
        self.repository = repository or SkillsRepository()

    def list_skills(
        self,
        session: Session,
        *,
        search: str | None = None,
        status: str | None = None,
    ) -> list[SkillSummaryResponse]:
        definitions = self.repository.list_skill_definitions(session, search=search, status=status)
        return [self._build_skill_summary(session, definition) for definition in definitions]

    def create_skill(self, session: Session, payload: CreateSkillRequest) -> SkillDetailResponse:
        existing = self.repository.get_skill_definition_by_key(session, payload.key)
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

        definition = SkillDefinition(
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
        self._sync_draft_after_repository_commit(draft_version, new_commit_sha)
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
        stored_material = processor.store_and_extract(
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
            status=stored_material.extraction.status,
            size_bytes=stored_material.stored.size_bytes,
            checksum=stored_material.stored.checksum,
            parse_summary=stored_material.extraction.parse_summary,
            extracted_text=stored_material.extraction.extracted_text,
            processing_metadata=stored_material.extraction.processing_metadata,
            error_message=stored_material.extraction.error_message,
        )
        session.add(material)
        session.commit()
        return self._build_raw_material_detail_response(material)

    def create_raw_material_from_url(
        self,
        session: Session,
        *,
        skill_id: str,
        source_url: str,
        name: str | None = None,
        description: str = "",
        material_kind: str | None = None,
    ) -> SkillRawMaterialDetailResponse:
        self._require_definition(session, skill_id)
        processor = self._raw_material_processor()
        fetched = processor.fetch_url(source_url)
        return self.upload_raw_material(
            session,
            skill_id=skill_id,
            filename=fetched.filename,
            content=fetched.content,
            mime_type=fetched.mime_type,
            name=name or fetched.filename,
            description=description,
            material_kind=material_kind or infer_material_kind(fetched.filename, fetched.mime_type, source_url=source_url),
            source_note=fetched.source_note,
        )

    def list_raw_materials(self, session: Session, *, skill_id: str) -> list[SkillRawMaterialResponse]:
        self._require_definition(session, skill_id)
        return [
            self._build_raw_material_response(material)
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
        return self._build_raw_material_detail_response(material)

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

    def generate_skill_draft_from_raw_materials(
        self,
        session: Session,
        *,
        skill_id: str,
        payload: GenerateSkillDraftRequest,
    ) -> SkillRawMaterialGenerationResponse:
        definition = self._require_definition(session, skill_id)
        draft_version = self._require_draft_version(session, definition)
        material_ids = list(dict.fromkeys(payload.material_ids))
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

        source_bundle = self.gitlab_gateway.get_skill_source(definition.gitlab_project_id, draft_version.source_ref)
        if payload.base_commit_sha and source_bundle.head_commit_sha != payload.base_commit_sha:
            raise SkillSourceConflictError(
                "source 已变更，请刷新后重试。",
                details={"expected": payload.base_commit_sha, "actual": source_bundle.head_commit_sha},
            )

        prompt_pack = self.agent_prompt_service.resolve_prompt_pack(
            session,
            usage_key="default.skill_creation_agent",
            fallback_ref="skill_creation/conversational_draft/v1",
        )
        prompt_payload = self._build_skill_generation_prompt_payload(
            definition=definition,
            draft_version=draft_version,
            source_bundle=source_bundle,
            materials=materials,
            user_description=payload.user_description,
        )
        system_prompt = prompt_pack.system_prompt
        user_prompt = json.dumps(prompt_payload, ensure_ascii=False, sort_keys=True, indent=2)
        prompt_hash = hashlib.sha256(f"{system_prompt}\n{user_prompt}".encode("utf-8")).hexdigest()
        prompt_metadata = prompt_pack.metadata()

        generation = SkillRawMaterialGeneration(
            skill_definition_id=definition.id,
            material_ids=material_ids,
            user_description=payload.user_description,
            status="running",
            prompt_hash=prompt_hash,
            prompt_metadata=prompt_metadata,
            raw_response={"request": {"prompt_payload": prompt_payload, "agent_prompt": prompt_metadata}},
        )
        session.add(generation)
        session.flush()

        try:
            completion = self.inference_gateway.complete(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                route_key=prompt_pack.route_key,
            )
            generated = parse_generated_skill_draft(completion.content)
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
            generation.generation_reason = generated.generation_reason
            generation.review_notes = generated.review_notes
            generation.material_usage = generated.material_usage
            generation.committed_commit_sha = committed_commit_sha
            generation.error_message = ""
            session.commit()
            return self._build_raw_material_generation_response(generation)
        except Exception as exc:
            generation.status = "failed"
            generation.error_message = str(exc)
            generation.raw_response = {
                **(generation.raw_response or {}),
                "error": str(exc),
                "error_type": exc.__class__.__name__,
            }
            session.commit()
            raise

    def _commit_generated_skill_files(
        self,
        *,
        definition: SkillDefinition,
        draft_version: SkillVersion,
        source_bundle,
        generated: GeneratedSkillDraft,
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
            commit_message="Generate skill draft from raw materials via PSOP WEB IDE",
        )
        draft_version.source_commit_sha = new_commit_sha
        draft_version.manifest_snapshot = manifest_snapshot(document)
        draft_version.runtime_policy_snapshot = runtime_policy_snapshot(document)
        return new_commit_sha

    def _build_skill_generation_prompt_payload(
        self,
        *,
        definition: SkillDefinition,
        draft_version: SkillVersion,
        source_bundle,
        materials: list[SkillRawMaterial],
        user_description: str,
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
            "raw_materials": [
                {
                    "id": material.id,
                    "name": material.name,
                    "description": material.description,
                    "material_kind": material.material_kind,
                    "mime_type": material.mime_type,
                    "filename": material.filename,
                    "source_note": material.source_note,
                    "parse_summary": material.parse_summary,
                    "extracted_text": self._truncate_prompt_text(material.extracted_text),
                    "processing_metadata": {
                        key: value
                        for key, value in (material.processing_metadata or {}).items()
                        if key not in {"raw"}
                    },
                }
                for material in materials
            ],
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
                ],
                "draft_policy": "生成结果会提交到 GitLab draft 标准路径，但不会发布、不会编译。",
            },
        }

    @staticmethod
    def _truncate_prompt_text(value: str, limit: int = 40_000) -> str:
        if len(value) <= limit:
            return value
        return value[: limit - 20].rstrip() + "\n...[truncated]"

    def _raw_material_processor(self) -> RawMaterialProcessor:
        inference_gateway = self.inference_gateway or OpenAICompatibleInferenceGateway.from_settings(self.settings)
        return RawMaterialProcessor(
            settings=self.settings,
            inference_gateway=inference_gateway,
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
        draft_version: SkillVersion,
        commit_sha: str,
        document=None,
        readme_content: str | None = None,
        skill_md_content: str | None = None,
    ) -> None:
        draft_version.source_commit_sha = commit_sha
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

    @staticmethod
    def _build_raw_material_response(material: SkillRawMaterial) -> SkillRawMaterialResponse:
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
            parse_summary=material.parse_summary,
            processing_metadata=material.processing_metadata or {},
            error_message=material.error_message,
            created_at=material.created_at,
            updated_at=material.updated_at,
        )

    @classmethod
    def _build_raw_material_detail_response(cls, material: SkillRawMaterial) -> SkillRawMaterialDetailResponse:
        return SkillRawMaterialDetailResponse(
            **cls._build_raw_material_response(material).model_dump(),
            extracted_text=material.extracted_text,
        )

    @staticmethod
    def _build_raw_material_generation_response(
        generation: SkillRawMaterialGeneration,
    ) -> SkillRawMaterialGenerationResponse:
        return SkillRawMaterialGenerationResponse(
            id=generation.id,
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
