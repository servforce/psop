from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.config import Settings
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
    manifest_snapshot,
    parse_skill_yaml,
    render_skill_yaml,
    runtime_policy_snapshot,
)
from app.domain.skills.models import SkillDefinition, SkillPublishRecord, SkillVersion
from app.domain.skills.repository import SkillsRepository
from app.domain.skills.schemas import (
    CreateSkillRequest,
    PublishSkillRequest,
    PublishSkillResponse,
    SaveSkillSourceRequest,
    SkillDetailResponse,
    SkillPublishRecordResponse,
    SkillSourceResponse,
    SkillSummaryResponse,
    SkillVersionSummaryResponse,
    UpdateSkillRequest,
)
from app.gateway.gitlab import GitLabSkillSourceGateway


class SkillsService:
    """Application service for the Skills Management MVP."""

    def __init__(
        self,
        *,
        settings: Settings,
        gitlab_gateway: GitLabSkillSourceGateway,
        repository: SkillsRepository | None = None,
    ) -> None:
        self.settings = settings
        self.gitlab_gateway = gitlab_gateway
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
        recent_publish_records = self.repository.get_publish_records(session, definition.id)

        return SkillDetailResponse(
            **self._build_skill_summary(session, definition).model_dump(),
            created_at=definition.created_at,
            current_draft_version=self._build_skill_version_summary(draft_version),
            latest_published_version=self._build_skill_version_summary(latest_published_version),
            recent_publish_records=[
                self._build_publish_record_summary(record) for record in recent_publish_records[:10]
            ],
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
        document = parse_skill_yaml(source_bundle.skill_yaml_content)

        if payload.name is not None:
            document.skill.identity.name = payload.name
        if payload.description is not None:
            document.skill.identity.description = payload.description

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

    def get_skill_source(self, session: Session, skill_id: str) -> SkillSourceResponse:
        definition = self._require_definition(session, skill_id)
        draft_version = self._require_draft_version(session, definition)
        source_bundle = self.gitlab_gateway.get_skill_source(definition.gitlab_project_id, draft_version.source_ref)
        document = parse_skill_yaml(source_bundle.skill_yaml_content)

        if document.skill.identity.key != definition.key:
            raise SkillValidationError(
                "`skill.yaml` 中的 identity.key 与平台注册 key 不一致。",
                details={"expected": definition.key, "actual": document.skill.identity.key},
            )

        if draft_version.source_commit_sha != source_bundle.head_commit_sha:
            draft_version.source_commit_sha = source_bundle.head_commit_sha
            draft_version.manifest_snapshot = manifest_snapshot(document)
            draft_version.runtime_policy_snapshot = runtime_policy_snapshot(document)
            session.commit()

        return SkillSourceResponse(
            readme_content=source_bundle.readme_content,
            skill_md_content=source_bundle.skill_md_content,
            skill_yaml_content=source_bundle.skill_yaml_content,
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

        document = parse_skill_yaml(payload.skill_yaml_content)
        if document.skill.identity.key != definition.key:
            raise SkillValidationError(
                "`skill.yaml` 中的 identity.key 不可修改。",
                details={"expected": definition.key, "actual": document.skill.identity.key},
            )
        if document.skill.identity.name != definition.name:
            raise SkillValidationError(
                "`skill.yaml` 中的 identity.name 需与 Skill 基本信息一致，请先通过基本信息面板修改名称。",
                details={"expected": definition.name, "actual": document.skill.identity.name},
            )
        if document.skill.identity.description != definition.description:
            raise SkillValidationError(
                "`skill.yaml` 中的 identity.description 需与 Skill 基本信息一致，请先通过基本信息面板修改描述。",
                details={"expected": definition.description, "actual": document.skill.identity.description},
            )

        new_commit_sha = self.gitlab_gateway.commit_skill_source(
            project_id=definition.gitlab_project_id,
            branch=draft_version.source_ref,
            readme_content=payload.readme_content,
            skill_md_content=payload.skill_md_content,
            skill_yaml_content=payload.skill_yaml_content,
            commit_message="Update skill source via PSOP WEB IDE",
        )

        draft_version.source_commit_sha = new_commit_sha
        draft_version.manifest_snapshot = manifest_snapshot(document)
        draft_version.runtime_policy_snapshot = runtime_policy_snapshot(document)
        session.commit()

        return SkillSourceResponse(
            readme_content=payload.readme_content,
            skill_md_content=payload.skill_md_content,
            skill_yaml_content=payload.skill_yaml_content,
            source_ref=draft_version.source_ref,
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
        source_bundle = self.gitlab_gateway.get_skill_source(definition.gitlab_project_id, definition.default_branch)
        document = parse_skill_yaml(source_bundle.skill_yaml_content)

        if document.skill.identity.key != definition.key:
            raise SkillValidationError(
                "`skill.yaml` 中的 identity.key 与平台注册 key 不一致。",
                details={"expected": definition.key, "actual": document.skill.identity.key},
            )

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

        publish_record = SkillPublishRecord(
            skill_definition_id=definition.id,
            skill_version_id=published_version.id,
            publish_reason=payload.publish_reason,
            publish_status="published",
            published_commit_sha=source_bundle.head_commit_sha,
            release_ref=definition.default_branch,
        )
        session.add(publish_record)
        definition.latest_published_version_id = published_version.id
        session.commit()

        return PublishSkillResponse(
            publish_record=self._build_publish_record_summary(publish_record),
            published_version=self._build_skill_version_summary(published_version),
            published_commit_sha=source_bundle.head_commit_sha,
        )

    def list_publish_records(self, session: Session, *, skill_id: str) -> list[SkillPublishRecordResponse]:
        definition = self._require_definition(session, skill_id)
        return [
            self._build_publish_record_summary(record)
            for record in self.repository.get_publish_records(session, definition.id)
        ]

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
