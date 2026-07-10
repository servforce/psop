from __future__ import annotations

import hashlib
import json
import logging
import posixpath
import re
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.compiler.agent import SkillCompileAgent
from app.agent_harness.agents.psop.compiler.schemas import validate_compiler_candidate
from app.agent_harness.schemas import AgentInvocation, AgentResult
from app.agent_harness.service import AgentHarnessService
from app.agent_harness.tools.builtin.compiler import allowed_runtime_snapshot
from app.agents.registry import DomainPackRegistry
from app.domain.compiler.formal_v5 import (
    FORMAL_REVISION,
    FormalDiagnostic,
    validate_and_normalize_artifact,
)
from app.core.config import Settings
from app.core.logging import log_context
from app.core.observability import record_span_exception, start_span
from app.domain.compiler.models import ArtifactObject, CompileDiagnostic, EgCompileArtifact, SkillCompileRequest
from app.domain.compiler.repository import CompilerRepository
from app.domain.compiler.schemas import (
    CompileArtifactResponse,
    CompileArtifactUpdateRequest,
    CompileDiagnosticResponse,
    CompileRequestResponse,
    PublishProgressResponse,
    PublishProgressStageResponse,
)
from app.domain.jobs.models import RuntimeJob
from app.domain.jobs.progress import (
    build_publish_progress_payload,
    ensure_publish_progress_payload,
    mark_publish_stage,
)
from app.domain.jobs.repository import JobRepository
from app.domain.skills.exceptions import SkillNotFoundError, SkillValidationError, SkillsError
from app.domain.skills.manifest import SkillDocument, document_from_manifest_snapshot
from app.domain.skills.models import SkillDefinition, SkillPublishRecord, SkillVersion, now_utc
from app.gateway.inference import LlmInferenceGateway, OpenAICompatibleInferenceGateway
from app.gateway.gitlab import GitLabSkillSourceGateway
from app.infra.object_store import ObjectStoreService

LOGGER = logging.getLogger(__name__)
MARKDOWN_IMAGE_PATTERN = re.compile(r"!\[([^\]]*)\]\(([^)\n]+)\)")
REFERENCE_IMAGE_MEDIA_TYPES = {
    ".gif": "image/gif",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}


class PublishProgressReporter:
    """Persists publish progress into runtime_job.payload after each stage change."""

    def __init__(
        self,
        *,
        session: Session,
        job_repository: JobRepository,
        job_id: str,
    ) -> None:
        self.session = session
        self.job_repository = job_repository
        self.job_id = job_id

    def mark(self, stage_key: str, status: str, message: str = "", *, error_message: str = "") -> None:
        job = self.job_repository.get_runtime_job(self.session, self.job_id)
        if not job:
            return

        job.payload = mark_publish_stage(
            job.payload,
            stage_key,
            status,
            message,
            error_message=error_message,
        )
        self.session.commit()


class CompilerService:
    """Compiles frozen Skill source revisions into MVP formal-v5 EG artifacts."""

    def __init__(
        self,
        *,
        settings: Settings,
        gitlab_gateway: GitLabSkillSourceGateway,
        inference_gateway: LlmInferenceGateway | None = None,
        compile_agent: SkillCompileAgent | None = None,
        agent_harness_service: AgentHarnessService | None = None,
        domain_pack_registry: DomainPackRegistry | None = None,
        object_store: ObjectStoreService | None = None,
        repository: CompilerRepository | None = None,
        job_repository: JobRepository | None = None,
    ) -> None:
        self.settings = settings
        self.gitlab_gateway = gitlab_gateway
        self.inference_gateway = inference_gateway or OpenAICompatibleInferenceGateway.from_settings(settings)
        self.compile_agent = compile_agent or SkillCompileAgent(self.inference_gateway)
        self.agent_harness_service = agent_harness_service
        self.domain_pack_registry = domain_pack_registry or DomainPackRegistry()
        self.object_store = object_store
        self.repository = repository or CompilerRepository()
        self.job_repository = job_repository or JobRepository()

    def create_compile_request_for_publish(
        self,
        session: Session,
        *,
        skill_definition: SkillDefinition,
        skill_version: SkillVersion,
        publish_record_id: str | None = None,
    ) -> SkillCompileRequest:
        if not skill_version.source_commit_sha:
            raise SkillValidationError("发布版本缺少冻结 commit SHA，无法创建编译任务。")

        dedupe_key = f"compile:{skill_version.id}:{skill_version.source_commit_sha}"
        existing = self.repository.get_compile_request_by_dedupe_key(session, dedupe_key)
        if existing:
            return existing

        compile_request = SkillCompileRequest(
            skill_definition_id=skill_definition.id,
            skill_version_id=skill_version.id,
            trigger_type="publish",
            source_commit_sha=skill_version.source_commit_sha,
            status="pending",
            dedupe_key=dedupe_key,
        )
        session.add(compile_request)
        session.flush()
        LOGGER.info(
            "compile request created for publish",
            extra={
                "skill_id": skill_definition.id,
                "skill_key": skill_definition.key,
                "skill_version_id": skill_version.id,
                "compile_request_id": compile_request.id,
                "publish_record_id": publish_record_id,
            },
        )

        progress_payload = build_publish_progress_payload(
            compile_request_id=compile_request.id,
            publish_record_id=publish_record_id,
            skill_definition_id=skill_definition.id,
            skill_version_id=skill_version.id,
            published_commit_sha=skill_version.source_commit_sha,
        )
        job = RuntimeJob(
            job_type="compile",
            status="pending",
            payload=progress_payload,
            compile_request_id=compile_request.id,
            dedupe_key=f"job:{dedupe_key}",
            max_attempts=self.settings.runtime_job_max_attempts,
        )
        session.add(job)
        session.flush()
        LOGGER.info(
            "compile job queued",
            extra={
                "skill_id": skill_definition.id,
                "skill_key": skill_definition.key,
                "skill_version_id": skill_version.id,
                "compile_request_id": compile_request.id,
                "publish_record_id": publish_record_id,
                "job_id": job.id,
            },
        )
        return compile_request

    def create_manual_compile_request_for_skill(
        self,
        session: Session,
        *,
        skill_id: str,
    ) -> CompileRequestResponse:
        skill_definition = self.repository.get_skill_definition(session, skill_id)
        if not skill_definition:
            raise SkillNotFoundError("未找到对应的 Skill。", details={"skill_id": skill_id})

        skill_version = None
        if skill_definition.latest_published_version_id:
            skill_version = self.repository.get_skill_version(session, skill_definition.latest_published_version_id)
        if not skill_version and skill_definition.latest_draft_version_id:
            skill_version = self.repository.get_skill_version(session, skill_definition.latest_draft_version_id)
        if not skill_version:
            skill_version = session.scalar(
                select(SkillVersion)
                .where(SkillVersion.skill_definition_id == skill_definition.id)
                .order_by(SkillVersion.updated_at.desc())
            )
        if not skill_version:
            raise SkillValidationError("当前 Skill 没有可编译的版本。", details={"skill_id": skill_id})

        source_commit_sha = skill_version.source_commit_sha
        if not source_commit_sha and skill_version.source_ref:
            source_commit_sha = self.gitlab_gateway.get_branch_head(
                skill_definition.gitlab_project_id,
                skill_version.source_ref,
            )
            skill_version.source_commit_sha = source_commit_sha
            session.flush()
        if not source_commit_sha:
            raise SkillValidationError(
                "当前 Skill 版本缺少冻结 commit SHA，无法创建手动编译任务。",
                details={"skill_id": skill_id, "skill_version_id": skill_version.id},
            )

        compile_request = SkillCompileRequest(
            skill_definition_id=skill_definition.id,
            skill_version_id=skill_version.id,
            trigger_type="manual",
            source_commit_sha=source_commit_sha,
            status="pending",
            dedupe_key=f"compile:manual:{skill_version.id}:{source_commit_sha}:{uuid4()}",
        )
        session.add(compile_request)
        session.flush()

        progress_payload = build_publish_progress_payload(
            compile_request_id=compile_request.id,
            publish_record_id=None,
            skill_definition_id=skill_definition.id,
            skill_version_id=skill_version.id,
            published_commit_sha=source_commit_sha,
        )
        progress_payload["operation"] = "compile"
        for stage in progress_payload["progress_stages"]:
            if stage["key"] == "publish_finalizing":
                stage["label"] = "完成编译"

        job = RuntimeJob(
            job_type="compile",
            status="pending",
            payload=progress_payload,
            compile_request_id=compile_request.id,
            dedupe_key=f"job:compile:{compile_request.id}",
            max_attempts=self.settings.runtime_job_max_attempts,
        )
        session.add(job)
        session.flush()
        session.commit()
        LOGGER.info(
            "manual compile job queued",
            extra={
                "skill_id": skill_definition.id,
                "skill_key": skill_definition.key,
                "skill_version_id": skill_version.id,
                "compile_request_id": compile_request.id,
                "job_id": job.id,
            },
        )
        return self._build_compile_request_response(session, compile_request)

    def process_compile_request(
        self,
        session: Session,
        compile_request_id: str,
        *,
        progress: PublishProgressReporter | None = None,
        mark_job_terminal: bool = True,
    ) -> SkillCompileRequest:
        compile_request = self.repository.get_compile_request(session, compile_request_id)
        if not compile_request:
            raise SkillNotFoundError("未找到编译请求。", details={"compile_request_id": compile_request_id})

        if compile_request.status == "succeeded":
            return compile_request

        skill_definition = self.repository.get_skill_definition(session, compile_request.skill_definition_id)
        skill_version = self.repository.get_skill_version(session, compile_request.skill_version_id)
        if not skill_definition or not skill_version:
            raise SkillNotFoundError("编译请求关联的 Skill 或版本不存在。")

        with log_context(
            skill_id=skill_definition.id,
            skill_key=skill_definition.key,
            skill_version_id=skill_version.id,
            compile_request_id=compile_request.id,
        ):
            LOGGER.info("compile request started")

        compile_request.status = "running"
        compile_request.started_at = now_utc()
        session.flush()

        request_id = compile_request.id
        version_id = skill_version.id
        definition_id = skill_definition.id
        definition_key = skill_definition.key

        try:
            if progress:
                progress.mark("source_loaded", "running", "正在读取冻结 commit 下的 Skill source。")
            with start_span(
                "compile.source_load",
                skill_id=skill_definition.id,
                skill_key=skill_definition.key,
                compile_request_id=compile_request.id,
                source_commit_sha=compile_request.source_commit_sha,
            ):
                source = self.gitlab_gateway.get_skill_source(
                    skill_definition.gitlab_project_id,
                    compile_request.source_commit_sha,
                )
            LOGGER.info(
                "compile source loaded",
                extra={
                    "skill_id": skill_definition.id,
                    "skill_key": skill_definition.key,
                    "compile_request_id": compile_request.id,
                    "source_commit_sha": compile_request.source_commit_sha,
                },
            )
            if progress:
                progress.mark(
                    "source_loaded",
                    "succeeded",
                    f"已读取冻结 commit {compile_request.source_commit_sha[:12]}。",
                )
                progress.mark("manifest_checked", "running", "正在校验发布版本 manifest snapshot。")
            with start_span(
                "compile.manifest_check",
                skill_id=skill_definition.id,
                skill_key=skill_definition.key,
                compile_request_id=compile_request.id,
            ):
                document = document_from_manifest_snapshot(skill_version.manifest_snapshot)
                diagnostics = self._validate_document(skill_definition, document)
            self._add_diagnostics(session, compile_request, skill_version, diagnostics)

            blocking = [item for item in diagnostics if item["severity"] == "error"]
            if blocking:
                compile_request.status = "failed"
                compile_request.error_message = blocking[0]["message"]
                compile_request.finished_at = now_utc()
                if progress:
                    progress.mark(
                        "manifest_checked",
                        "failed",
                        blocking[0]["message"],
                        error_message=blocking[0]["message"],
                    )
                if mark_job_terminal:
                    self._mark_job(session, compile_request.id, "failed", compile_request.error_message)
                session.commit()
                LOGGER.warning(
                    "compile manifest validation failed",
                    extra={
                        "skill_id": skill_definition.id,
                        "skill_key": skill_definition.key,
                        "compile_request_id": compile_request.id,
                        "error": compile_request.error_message,
                    },
                )
                return compile_request
            if progress:
                progress.mark("manifest_checked", "succeeded", "manifest snapshot 校验通过。")

            reference_assets = self._build_reference_assets_for_compile(
                session=session,
                compile_request=compile_request,
                skill_definition=skill_definition,
                skill_version=skill_version,
                source=source,
            )

            with start_span(
                "compile.agent",
                skill_id=skill_definition.id,
                skill_key=skill_definition.key,
                skill_version_id=skill_version.id,
                compile_request_id=compile_request.id,
            ):
                artifact, agent_diagnostics = self._compile_with_agent(
                    session=session,
                    compile_request=compile_request,
                    skill_definition=skill_definition,
                    skill_version=skill_version,
                    document=document,
                    source=source,
                    reference_assets=reference_assets,
                    progress=progress,
                )
            self._add_diagnostics(session, compile_request, skill_version, [item.as_dict() for item in agent_diagnostics])
            if artifact is None:
                error_message = agent_diagnostics[-1].message if agent_diagnostics else "Skill 编译智能体未生成合法 EG artifact。"
                compile_request.status = "failed"
                compile_request.error_message = error_message
                compile_request.finished_at = now_utc()
                if mark_job_terminal:
                    self._mark_job(session, compile_request.id, "failed", error_message)
                session.commit()
                LOGGER.warning(
                    "compile agent failed to produce valid artifact",
                    extra={
                        "skill_id": skill_definition.id,
                        "skill_key": skill_definition.key,
                        "compile_request_id": compile_request.id,
                        "error": error_message,
                    },
                )
                return compile_request

            if progress:
                progress.mark("artifact_emitting", "running", "正在写入 EG 编译产物。")
            artifact["compile_request_id"] = compile_request.id
            artifact.setdefault("skill", {})
            artifact["skill"].update(
                {
                    "id": skill_definition.id,
                    "key": skill_definition.key,
                    "name": skill_definition.name,
                    "version_id": skill_version.id,
                    "version_no": skill_version.version_no,
                    "source_commit_sha": compile_request.source_commit_sha,
                }
            )
            with start_span(
                "compile.emit",
                skill_id=skill_definition.id,
                skill_key=skill_definition.key,
                skill_version_id=skill_version.id,
                compile_request_id=compile_request.id,
            ):
                artifact_json = json.dumps(artifact, ensure_ascii=False, sort_keys=True).encode("utf-8")
                checksum = hashlib.sha256(artifact_json).hexdigest()
                artifact_object = ArtifactObject(
                    bucket=self.settings.object_store_bucket,
                    object_key=(
                        f"skills/{skill_definition.key}/versions/{skill_version.version_no}/"
                        f"{compile_request.id}/eg.compile.artifact.json"
                    ),
                    media_type="application/json",
                    size_bytes=len(artifact_json),
                    checksum=checksum,
                    content_json=artifact,
                )
                session.add(artifact_object)
                session.flush()

                eg_artifact = EgCompileArtifact(
                    skill_compile_request_id=compile_request.id,
                    skill_version_id=skill_version.id,
                    artifact_object_id=artifact_object.id,
                    formal_revision=artifact["formal_revision"],
                    artifact_version=artifact["artifact_version"],
                    graph_summary=artifact["graph_summary"],
                    capability_summary=artifact["capability_summary"],
                    status="ready",
                )
                session.add(eg_artifact)
                session.flush()
            if progress:
                progress.mark("artifact_emitting", "succeeded", "EG 编译产物已写入。")
            compile_request.status = "succeeded"
            compile_request.error_message = ""
            compile_request.finished_at = now_utc()
            if mark_job_terminal:
                self._mark_job(session, compile_request.id, "succeeded")
            session.commit()
            LOGGER.info(
                "compile request succeeded",
                extra={
                    "skill_id": skill_definition.id,
                    "skill_key": skill_definition.key,
                    "skill_version_id": skill_version.id,
                    "compile_request_id": compile_request.id,
                    "artifact_id": eg_artifact.id,
                },
            )
            return compile_request
        except Exception as exc:
            error_message = self._format_exception_message(exc)
            diagnostic_location = getattr(exc, "details", None)
            session.rollback()

            compile_request = self.repository.get_compile_request(session, request_id)
            skill_version = self.repository.get_skill_version(session, version_id)
            if not compile_request or not skill_version:
                LOGGER.exception(
                    "compile request failed unexpectedly and failure state could not be reloaded",
                    extra={
                        "skill_id": definition_id,
                        "skill_key": definition_key,
                        "skill_version_id": version_id,
                        "compile_request_id": request_id,
                        "error": error_message,
                    },
                )
                raise

            compile_request.status = "failed"
            compile_request.error_message = error_message
            compile_request.finished_at = now_utc()
            session.add(
                CompileDiagnostic(
                    skill_compile_request_id=request_id,
                    skill_version_id=version_id,
                    severity="error",
                    code="compile.failed",
                    message=error_message,
                    location=diagnostic_location,
                    category="compiler",
                )
            )
            if progress:
                self._mark_current_progress_failed(session, progress.job_id, error_message)
            if mark_job_terminal:
                self._mark_job(session, request_id, "failed", error_message)
            session.commit()
            LOGGER.exception(
                "compile request failed unexpectedly",
                extra={
                    "skill_id": definition_id,
                    "skill_key": definition_key,
                    "skill_version_id": version_id,
                    "compile_request_id": request_id,
                    "error": error_message,
                },
            )
            return compile_request

    def process_compile_job(self, session: Session, job_id: str) -> SkillCompileRequest:
        job = self.job_repository.get_runtime_job(session, job_id)
        if not job:
            raise SkillNotFoundError("未找到运行任务。", details={"job_id": job_id})
        if job.job_type != "compile":
            raise SkillValidationError("当前 worker 仅支持 compile job。", details={"job_type": job.job_type})

        compile_request_id = job.compile_request_id or job.payload.get("compile_request_id")
        if not compile_request_id:
            raise SkillValidationError("compile job 缺少 compile_request_id。", details={"job_id": job_id})

        progress = PublishProgressReporter(
            session=session,
            job_repository=self.job_repository,
            job_id=job.id,
        )
        with log_context(job_id=job.id, compile_request_id=compile_request_id):
            LOGGER.info("processing compile job")
            with start_span("job.compile", job_id=job.id, compile_request_id=compile_request_id):
                compile_request = self.process_compile_request(
                    session,
                    compile_request_id,
                    progress=progress,
                    mark_job_terminal=False,
                )
                self._finalize_publish_job(session, job.id, compile_request)
        return compile_request

    def process_compile_job_for_request(self, session: Session, compile_request_id: str) -> SkillCompileRequest:
        job = self.job_repository.get_compile_job(session, compile_request_id)
        if job:
            if job.status != "running":
                job.status = "running"
                job.attempt_no += 1
                job.started_at = job.started_at or now_utc()
                job.lease_until = now_utc()
                session.commit()
            return self.process_compile_job(session, job.id)
        return self.process_compile_request(session, compile_request_id)

    def _build_reference_assets_for_compile(
        self,
        *,
        session: Session,
        compile_request: SkillCompileRequest,
        skill_definition: SkillDefinition,
        skill_version: SkillVersion,
        source,
    ) -> list[dict[str, Any]]:
        candidates = _extract_reference_image_candidates(
            {
                "README.md": getattr(source, "readme_content", ""),
                "SKILL.md": getattr(source, "skill_md_content", ""),
            }
        )
        if not candidates:
            return []
        if self.object_store is None:
            LOGGER.warning(
                "reference images skipped because object store is unavailable",
                extra={
                    "skill_id": skill_definition.id,
                    "skill_key": skill_definition.key,
                    "compile_request_id": compile_request.id,
                    "candidate_count": len(candidates),
                },
            )
            return []

        assets: list[dict[str, Any]] = []
        for candidate in candidates:
            reference_path = str(candidate["reference_path"])
            try:
                content = self.gitlab_gateway.get_repository_file_bytes(
                    skill_definition.gitlab_project_id,
                    compile_request.source_commit_sha,
                    reference_path,
                )
            except Exception as exc:
                LOGGER.warning(
                    "reference image read skipped",
                    extra={
                        "skill_id": skill_definition.id,
                        "skill_key": skill_definition.key,
                        "compile_request_id": compile_request.id,
                        "reference_path": reference_path,
                        "error": str(exc),
                    },
                )
                continue
            if not content:
                LOGGER.warning(
                    "empty reference image skipped",
                    extra={
                        "skill_id": skill_definition.id,
                        "skill_key": skill_definition.key,
                        "compile_request_id": compile_request.id,
                        "reference_path": reference_path,
                    },
                )
                continue
            media_type = str(candidate["mime_type"])
            checksum = hashlib.sha256(content).hexdigest()
            object_key = (
                f"skills/{skill_definition.key}/versions/{skill_version.version_no}/"
                f"{compile_request.id}/reference-images/{checksum[:16]}/{Path(reference_path).name}"
            )
            try:
                stored = self.object_store.upload_bytes(
                    object_key=object_key,
                    content=content,
                    media_type=media_type,
                    metadata={
                        "skill_key": skill_definition.key,
                        "source_commit_sha": compile_request.source_commit_sha,
                        "reference_path": reference_path,
                    },
                )
            except Exception as exc:
                LOGGER.warning(
                    "reference image upload skipped",
                    extra={
                        "skill_id": skill_definition.id,
                        "skill_key": skill_definition.key,
                        "compile_request_id": compile_request.id,
                        "reference_path": reference_path,
                        "error": str(exc),
                    },
                )
                continue
            artifact_object = ArtifactObject(
                bucket=stored.bucket,
                object_key=stored.object_key,
                media_type=stored.media_type,
                size_bytes=stored.size_bytes,
                checksum=stored.checksum,
            )
            session.add(artifact_object)
            session.flush()
            assets.append(
                {
                    "reference_path": reference_path,
                    "artifact_object_id": artifact_object.id,
                    "mime_type": artifact_object.media_type,
                    "title": str(candidate["title"]),
                    "source_ref": str(candidate["source_ref"]),
                    "display_order": int(candidate["display_order"]),
                    "size_bytes": artifact_object.size_bytes,
                    "checksum": artifact_object.checksum,
                }
            )
        return assets

    def list_compile_requests(
        self,
        session: Session,
        *,
        skill_id: str | None = None,
        status: str | None = None,
    ) -> list[CompileRequestResponse]:
        return [
            self._build_compile_request_response(session, item)
            for item in self.repository.list_compile_requests(session, skill_id=skill_id, status=status)
        ]

    def get_compile_request(self, session: Session, compile_request_id: str) -> CompileRequestResponse:
        compile_request = self.repository.get_compile_request(session, compile_request_id)
        if not compile_request:
            raise SkillNotFoundError("未找到编译请求。", details={"compile_request_id": compile_request_id})
        return self._build_compile_request_response(session, compile_request)

    def get_compile_progress(self, session: Session, compile_request_id: str) -> PublishProgressResponse:
        compile_request = self.repository.get_compile_request(session, compile_request_id)
        if not compile_request:
            raise SkillNotFoundError("未找到编译请求。", details={"compile_request_id": compile_request_id})

        job = self.job_repository.get_compile_job(session, compile_request_id)
        payload = ensure_publish_progress_payload(job.payload if job else {"compile_request_id": compile_request_id})
        publish_record = None
        publish_record_id = payload.get("publish_record_id")
        if publish_record_id:
            publish_record = session.get(SkillPublishRecord, publish_record_id)

        return PublishProgressResponse(
            compile_request=self._build_compile_request_response(session, compile_request),
            publish_record_id=publish_record_id,
            publish_status=publish_record.publish_status if publish_record else None,
            current_stage=payload["current_stage"],
            terminal=bool(payload.get("terminal")),
            terminal_status=payload.get("terminal_status"),
            error_message=payload.get("error_message") or compile_request.error_message,
            updated_at=payload.get("updated_at"),
            stages=[PublishProgressStageResponse(**stage) for stage in payload["progress_stages"]],
        )

    def list_diagnostics(self, session: Session, compile_request_id: str) -> list[CompileDiagnosticResponse]:
        return [
            self._build_diagnostic_response(item)
            for item in self.repository.list_compile_diagnostics(session, compile_request_id)
        ]

    def get_artifact(self, session: Session, artifact_id: str) -> CompileArtifactResponse:
        artifact = self.repository.get_artifact(session, artifact_id)
        if not artifact:
            raise SkillNotFoundError("未找到编译产物。", details={"compile_artifact_id": artifact_id})
        return self._build_artifact_response(session, artifact, include_payload=True)

    def update_artifact(
        self,
        session: Session,
        artifact_id: str,
        request: CompileArtifactUpdateRequest,
    ) -> CompileArtifactResponse:
        artifact = self.repository.get_artifact(session, artifact_id)
        if not artifact:
            raise SkillNotFoundError("未找到编译产物。", details={"compile_artifact_id": artifact_id})

        artifact_object = self.repository.get_artifact_object(session, artifact.artifact_object_id)
        if not artifact_object:
            raise SkillNotFoundError("未找到编译产物对象。", details={"artifact_object_id": artifact.artifact_object_id})

        validation = validate_and_normalize_artifact(request.artifact)
        if validation.has_errors or validation.artifact is None:
            raise SkillValidationError(
                "EG artifact 未通过 formal-v5 校验。",
                details={"diagnostics": [item.as_dict() for item in validation.diagnostics]},
            )

        normalized = validation.artifact
        normalized["compile_request_id"] = artifact.skill_compile_request_id

        compile_request = self.repository.get_compile_request(session, artifact.skill_compile_request_id)
        skill_version = self.repository.get_skill_version(session, artifact.skill_version_id)
        skill_definition = (
            self.repository.get_skill_definition(session, compile_request.skill_definition_id)
            if compile_request
            else None
        )
        if skill_definition and skill_version and compile_request:
            normalized.setdefault("skill", {})
            normalized["skill"].update(
                {
                    "id": skill_definition.id,
                    "key": skill_definition.key,
                    "name": skill_definition.name,
                    "version_id": skill_version.id,
                    "version_no": skill_version.version_no,
                    "source_commit_sha": compile_request.source_commit_sha,
                }
            )

        artifact_json = json.dumps(normalized, ensure_ascii=False, sort_keys=True).encode("utf-8")
        artifact_object.content_json = normalized
        artifact_object.size_bytes = len(artifact_json)
        artifact_object.checksum = hashlib.sha256(artifact_json).hexdigest()
        artifact.formal_revision = normalized["formal_revision"]
        artifact.artifact_version = normalized["artifact_version"]
        artifact.graph_summary = normalized["graph_summary"]
        artifact.capability_summary = normalized["capability_summary"]
        artifact.status = "ready"
        session.commit()
        LOGGER.info(
            "compile artifact updated",
            extra={
                "compile_request_id": artifact.skill_compile_request_id,
                "artifact_id": artifact.id,
                "artifact_object_id": artifact.artifact_object_id,
            },
        )
        return self._build_artifact_response(session, artifact, include_payload=True)

    def _validate_document(self, skill_definition: SkillDefinition, document: SkillDocument) -> list[dict[str, Any]]:
        diagnostics: list[dict[str, Any]] = [
            {
                "severity": "info",
                "code": "compile.agent.enabled",
                "message": "使用 SKILL 编译智能体生成 formal-v5 EG candidate，并执行确定性校验。",
                "category": "compiler",
            }
        ]
        if document.skill.identity.key != skill_definition.key:
            diagnostics.append(
                {
                    "severity": "error",
                    "code": "skill.identity_key_mismatch",
                    "message": "manifest 中的 identity.key 与平台注册 key 不一致。",
                    "location": {"path": "skill.identity.key"},
                }
            )
        if document.skill.compile_config.formal_revision != FORMAL_REVISION:
            diagnostics.append(
                {
                    "severity": "error",
                    "code": "compile.unsupported_formal_revision",
                    "message": f"MVP 仅支持 formal revision `{FORMAL_REVISION}`。",
                    "location": {"path": "skill.compile_config.formal_revision"},
                }
            )
        return diagnostics

    def _compile_with_agent(
        self,
        *,
        session: Session,
        compile_request: SkillCompileRequest,
        skill_definition: SkillDefinition,
        skill_version: SkillVersion,
        document: SkillDocument,
        source,
        reference_assets: list[dict[str, Any]],
        progress: PublishProgressReporter | None = None,
    ) -> tuple[dict[str, Any] | None, list[FormalDiagnostic]]:
        if self.agent_harness_service is not None:
            return self._compile_with_harness_agent(
                session=session,
                compile_request=compile_request,
                skill_definition=skill_definition,
                skill_version=skill_version,
                document=document,
                source=source,
                reference_assets=reference_assets,
                progress=progress,
            )
        diagnostics: list[FormalDiagnostic] = []
        repair_diagnostics: list[FormalDiagnostic] = []
        compiler_metadata: dict[str, Any] = {}
        context_recorded = False
        for attempt in range(2):
            LOGGER.info(
                "compile agent attempt started",
                extra={
                    "skill_id": skill_definition.id,
                    "skill_key": skill_definition.key,
                    "skill_version_id": skill_version.id,
                    "attempt": attempt + 1,
                },
            )
            if progress:
                message = "正在调用 SKILL 编译智能体生成 formal-v5 EG candidate。"
                if attempt == 1:
                    message = "正在根据校验结果请求编译智能体修正 EG candidate。"
                progress.mark("agent_compiling", "running", message)
            with start_span(
                "compile.agent.invoke",
                skill_id=skill_definition.id,
                skill_key=skill_definition.key,
                skill_version_id=skill_version.id,
                attempt=attempt + 1,
            ) as span:
                try:
                    candidate = self.compile_agent.compile(
                        skill_definition=skill_definition,
                        skill_version=skill_version,
                        document=document,
                        source=source,
                        reference_assets=reference_assets,
                        repair_diagnostics=repair_diagnostics,
                        session=session,
                    )
                    if progress:
                        self.job_repository.accumulate_llm_usage(
                            self.job_repository.get_runtime_job(session, progress.job_id),
                            candidate.usage,
                        )
                        session.flush()
                except Exception as exc:
                    record_span_exception(span, exc)
                    raise
            if not context_recorded:
                diagnostics.extend(candidate.context_diagnostics)
                compiler_metadata = candidate.compiler_metadata
                context_recorded = True
            diagnostics.extend(candidate.diagnostics)
            if candidate.artifact is None:
                repair_diagnostics = candidate.diagnostics
                if attempt == 0:
                    continue
                if progress:
                    progress.mark(
                        "agent_compiling",
                        "failed",
                        "编译智能体修正后仍未返回合法 JSON。",
                        error_message="编译智能体修正后仍未返回合法 JSON。",
                    )
                diagnostics.append(
                    FormalDiagnostic(
                        severity="error",
                        code="compile.agent.repair_failed",
                        message="编译智能体修正后仍未返回合法 JSON。",
                    )
                )
                return None, diagnostics

            if progress:
                progress.mark("agent_compiling", "succeeded", "编译智能体已返回 EG candidate。")
                progress.mark("artifact_validating", "running", "正在执行 formal-v5 确定性校验。")
            with start_span(
                "compile.validate",
                skill_id=skill_definition.id,
                skill_key=skill_definition.key,
                skill_version_id=skill_version.id,
                attempt=attempt + 1,
            ):
                validation = validate_and_normalize_artifact(candidate.artifact)
            diagnostics.extend(validation.diagnostics)
            if validation.artifact is not None and not validation.has_errors:
                if compiler_metadata:
                    validation.artifact["compiler_metadata"] = compiler_metadata
                if progress:
                    progress.mark("artifact_validating", "succeeded", "EG artifact 已通过 formal-v5 校验。")
                return validation.artifact, diagnostics

            repair_diagnostics = validation.diagnostics
            if attempt == 0:
                continue
            if progress:
                progress.mark(
                    "artifact_validating",
                    "failed",
                    "编译智能体修正后仍未生成通过 formal-v5 校验的 EG artifact。",
                    error_message="编译智能体修正后仍未生成通过 formal-v5 校验的 EG artifact。",
                )
            diagnostics.append(
                FormalDiagnostic(
                    severity="error",
                    code="compile.agent.repair_failed",
                    message="编译智能体修正后仍未生成通过 formal-v5 校验的 EG artifact。",
                )
            )
            return None, diagnostics

        return None, diagnostics

    def _compile_with_harness_agent(
        self,
        *,
        session: Session,
        compile_request: SkillCompileRequest,
        skill_definition: SkillDefinition,
        skill_version: SkillVersion,
        document: SkillDocument,
        source,
        reference_assets: list[dict[str, Any]],
        progress: PublishProgressReporter | None = None,
    ) -> tuple[dict[str, Any] | None, list[FormalDiagnostic]]:
        diagnostics: list[FormalDiagnostic] = []
        repair_diagnostics: list[FormalDiagnostic] = []
        compiler_metadata, context_diagnostics, domain_pack_context = self._compiler_harness_context_metadata(document)
        context_recorded = False
        for attempt in range(2):
            LOGGER.info(
                "psop.compiler harness attempt started",
                extra={
                    "skill_id": skill_definition.id,
                    "skill_key": skill_definition.key,
                    "skill_version_id": skill_version.id,
                    "compile_request_id": compile_request.id,
                    "attempt": attempt + 1,
                },
            )
            if progress:
                message = "正在调用 psop.compiler 生成 formal-v5 EG candidate。"
                if attempt == 1:
                    message = "正在根据校验结果请求 psop.compiler 修正 EG candidate。"
                progress.mark("agent_compiling", "running", message)
            invocation = AgentInvocation(
                agent_key="psop.compiler",
                input={
                    "text": (
                        "将冻结的 PSOP Skill source 编译为 formal-v5 PSOP-EG candidate，"
                        "必须调用 psop.compiler.submit_candidate 写入 sandbox outputs。"
                    )
                },
                context=self._compiler_invocation_context(
                    compile_request=compile_request,
                    skill_definition=skill_definition,
                    skill_version=skill_version,
                    document=document,
                    source=source,
                    reference_assets=reference_assets,
                    domain_pack_context=domain_pack_context,
                    repair_diagnostics=repair_diagnostics,
                ),
                memory_scope="psop.compiler",
                workspace_id=compile_request.id,
            )
            with start_span(
                "compile.agent.invoke",
                skill_id=skill_definition.id,
                skill_key=skill_definition.key,
                skill_version_id=skill_version.id,
                compile_request_id=compile_request.id,
                attempt=attempt + 1,
                agent_key="psop.compiler",
            ) as span:
                try:
                    assert self.agent_harness_service is not None
                    agent_result = self.agent_harness_service.invoke(
                        invocation,
                        persistence_session=session,
                        persistence_context={
                            "related_skill_definition_id": skill_definition.id,
                            "related_job_id": progress.job_id if progress else "",
                        },
                    )
                except Exception as exc:
                    record_span_exception(span, exc)
                    raise
            if progress:
                self.job_repository.accumulate_llm_usage(
                    self.job_repository.get_runtime_job(session, progress.job_id),
                    self._agent_token_usage(agent_result),
                )
                session.flush()
            if not context_recorded:
                diagnostics.extend(context_diagnostics)
                context_recorded = True
            if agent_result.status != "succeeded":
                failure = FormalDiagnostic(
                    severity="error",
                    code="compile.agent.failed",
                    message=agent_result.error_message or "psop.compiler 未成功完成运行。",
                    location={
                        "agent_run_id": agent_result.agent_run_id,
                        "sandbox_path": agent_result.sandbox_path,
                    },
                )
                diagnostics.append(failure)
                repair_diagnostics = [failure]
                if attempt == 0:
                    continue
                if progress:
                    progress.mark(
                        "agent_compiling",
                        "failed",
                        failure.message,
                        error_message=failure.message,
                    )
                diagnostics.append(
                    FormalDiagnostic(
                        severity="error",
                        code="compile.agent.repair_failed",
                        message="psop.compiler 修正后仍未成功生成候选产物。",
                    )
                )
                return None, diagnostics

            candidate, candidate_error = self._read_harness_compiler_candidate(agent_result)
            if candidate_error is not None:
                diagnostics.append(candidate_error)
                repair_diagnostics = [candidate_error]
                if attempt == 0:
                    continue
                if progress:
                    progress.mark(
                        "agent_compiling",
                        "failed",
                        candidate_error.message,
                        error_message=candidate_error.message,
                    )
                diagnostics.append(
                    FormalDiagnostic(
                        severity="error",
                        code="compile.agent.repair_failed",
                        message="psop.compiler 修正后仍未返回合法 compiler candidate。",
                    )
                )
                return None, diagnostics

            assert candidate is not None
            diagnostics.extend(self._candidate_diagnostics(candidate.diagnostics))
            if progress:
                progress.mark("agent_compiling", "succeeded", "psop.compiler 已提交 EG candidate。")
                progress.mark("artifact_validating", "running", "正在执行 formal-v5 确定性校验。")
            with start_span(
                "compile.validate",
                skill_id=skill_definition.id,
                skill_key=skill_definition.key,
                skill_version_id=skill_version.id,
                compile_request_id=compile_request.id,
                attempt=attempt + 1,
            ):
                validation = validate_and_normalize_artifact(candidate.artifact)
            diagnostics.extend(validation.diagnostics)
            if validation.artifact is not None and not validation.has_errors:
                validation.artifact["compiler_metadata"] = {
                    **compiler_metadata,
                    "agent_run": {
                        "agent_key": agent_result.agent_key,
                        "agent_run_id": agent_result.agent_run_id,
                        "sandbox_path": agent_result.sandbox_path or "",
                    },
                }
                if progress:
                    progress.mark("artifact_validating", "succeeded", "EG artifact 已通过 formal-v5 校验。")
                return validation.artifact, diagnostics

            repair_diagnostics = validation.diagnostics
            if attempt == 0:
                continue
            if progress:
                progress.mark(
                    "artifact_validating",
                    "failed",
                    "psop.compiler 修正后仍未生成通过 formal-v5 校验的 EG artifact。",
                    error_message="psop.compiler 修正后仍未生成通过 formal-v5 校验的 EG artifact。",
                )
            diagnostics.append(
                FormalDiagnostic(
                    severity="error",
                    code="compile.agent.repair_failed",
                    message="psop.compiler 修正后仍未生成通过 formal-v5 校验的 EG artifact。",
                )
            )
            return None, diagnostics
        return None, diagnostics

    def _compiler_invocation_context(
        self,
        *,
        compile_request: SkillCompileRequest,
        skill_definition: SkillDefinition,
        skill_version: SkillVersion,
        document: SkillDocument,
        source,
        reference_assets: list[dict[str, Any]],
        domain_pack_context: dict[str, Any],
        repair_diagnostics: list[FormalDiagnostic],
    ) -> dict[str, Any]:
        return {
            "compile_request": {
                "id": compile_request.id,
                "trigger_type": compile_request.trigger_type,
                "source_commit_sha": compile_request.source_commit_sha,
            },
            "skill": {
                "id": skill_definition.id,
                "key": skill_definition.key,
                "name": skill_definition.name,
                "description": skill_definition.description,
                "version_id": skill_version.id,
                "version_no": skill_version.version_no,
                "source_commit_sha": skill_version.source_commit_sha,
            },
            "source": {
                "source_commit_sha": compile_request.source_commit_sha,
                "head_commit_sha": getattr(source, "head_commit_sha", ""),
                "reference_assets": reference_assets,
                "files": {
                    "README.md": getattr(source, "readme_content", ""),
                    "SKILL.md": getattr(source, "skill_md_content", ""),
                },
            },
            "manifest_snapshot": document.skill.model_dump(mode="json"),
            "runtime_policy_snapshot": skill_version.runtime_policy_snapshot or {},
            "allowed_runtime": allowed_runtime_snapshot(),
            "domain_pack": domain_pack_context,
            "repair_diagnostics": [item.as_dict() for item in repair_diagnostics],
            "output_contract": {
                "formal_revision": FORMAL_REVISION,
                "candidate_artifact_ref": "sandbox://outputs/compiler-result.json",
                "eg_artifact_ref": "sandbox://outputs/eg.compile.artifact.json",
            },
        }

    def _compiler_harness_context_metadata(
        self,
        document: SkillDocument,
    ) -> tuple[dict[str, Any], list[FormalDiagnostic], dict[str, Any]]:
        domain_resolution = self.domain_pack_registry.resolve(_domain_pack_ref(document))
        domain_metadata = {
            **domain_resolution.pack.metadata(),
            "requested_ref": domain_resolution.requested_ref,
            "used_default": domain_resolution.used_default,
        }
        compiler_metadata = {
            "agent_prompt": {
                "agent_key": "psop.compiler",
                "version": "v1",
                "source": "agent_harness",
            },
            "domain_pack": domain_metadata,
        }
        diagnostics = [
            FormalDiagnostic(
                severity="info",
                code="compile.agent.prompt_pack",
                message="使用 psop.compiler Agent Harness 与 compiler Agent Skills 生成 EG candidate。",
                location=compiler_metadata,
            )
        ]
        if domain_resolution.used_default:
            diagnostics.append(
                FormalDiagnostic(
                    severity="warning",
                    code="compile.agent.domain_pack_fallback",
                    message=(
                        f"未找到 domain_pack `{domain_resolution.requested_ref}`，"
                        f"已回退到 `{domain_resolution.pack.key}`。"
                    ),
                    location={
                        "requested_ref": domain_resolution.requested_ref,
                        "fallback_domain_pack": domain_resolution.pack.metadata(),
                        "reason": domain_resolution.fallback_reason,
                    },
                )
            )
        domain_pack_context = {
            "domain_pack_ref": domain_resolution.pack.key,
            "metadata": domain_metadata,
            "guidance_summary": domain_resolution.pack.title,
            "guidance": domain_resolution.pack.guidance,
        }
        return compiler_metadata, diagnostics, domain_pack_context

    @staticmethod
    def _read_harness_compiler_candidate(agent_result: AgentResult):
        if not agent_result.sandbox_path:
            return None, FormalDiagnostic(
                severity="error",
                code="compile.agent.missing_artifact",
                message="psop.compiler 结果缺少 sandbox_path。",
            )
        candidate_path = Path(agent_result.sandbox_path) / "outputs" / "compiler-result.json"
        if not candidate_path.exists():
            return None, FormalDiagnostic(
                severity="error",
                code="compile.agent.missing_artifact",
                message="psop.compiler 未生成 compiler-result.json。",
                location={"agent_run_id": agent_result.agent_run_id, "sandbox_path": agent_result.sandbox_path},
            )
        try:
            payload = json.loads(candidate_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            return None, FormalDiagnostic(
                severity="error",
                code="compile.agent.invalid_json",
                message=f"psop.compiler 生成的 compiler-result.json 不是合法 JSON：{exc.msg}",
                location={"line": exc.lineno, "column": exc.colno},
            )
        try:
            return validate_compiler_candidate(payload), None
        except ValueError as exc:
            return None, FormalDiagnostic(
                severity="error",
                code="compile.agent.invalid_candidate",
                message=str(exc),
                location={"path": "sandbox://outputs/compiler-result.json"},
            )

    @staticmethod
    def _candidate_diagnostics(items: list[dict[str, Any]]) -> list[FormalDiagnostic]:
        diagnostics: list[FormalDiagnostic] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            if _is_standard_search_availability_diagnostic(item):
                continue
            diagnostics.append(
                FormalDiagnostic(
                    severity=str(item.get("severity") or "warning"),
                    code=str(item.get("code") or "compile.agent.candidate_diagnostic"),
                    message=str(item.get("message") or "psop.compiler candidate diagnostic."),
                    location=item.get("location") if isinstance(item.get("location"), dict) else None,
                    category=str(item.get("category") or "compiler"),
                )
            )
        return diagnostics

    @staticmethod
    def _agent_token_usage(agent_result: AgentResult) -> dict[str, int]:
        usage: dict[str, int] = {}
        for event in agent_result.events:
            if event.event_type != "agent.token.usage":
                continue
            total = event.payload.get("total")
            if isinstance(total, dict):
                usage = {
                    "input_tokens": int(total.get("input_tokens") or 0),
                    "output_tokens": int(total.get("output_tokens") or 0),
                    "total_tokens": int(total.get("total_tokens") or 0),
                }
        return usage

    def _mark_job(self, session: Session, compile_request_id: str, status: str, error: str = "") -> None:
        job = self.job_repository.get_runtime_job_by_dedupe_key(session, f"job:compile:{compile_request_id}")
        if not job:
            # Fallback for the request-version dedupe key used at creation time.
            job = self.job_repository.get_compile_job(session, compile_request_id)
        if job:
            job.status = status
            job.last_error = error

    def _finalize_publish_job(
        self,
        session: Session,
        job_id: str,
        compile_request: SkillCompileRequest,
    ) -> None:
        job = self.job_repository.get_runtime_job(session, job_id)
        if not job:
            return

        publish_record_id = job.payload.get("publish_record_id")
        publish_record = session.get(SkillPublishRecord, publish_record_id) if publish_record_id else None
        definition = self.repository.get_skill_definition(session, compile_request.skill_definition_id)
        is_publish_job = publish_record is not None

        job.payload = mark_publish_stage(
            job.payload,
            "publish_finalizing",
            "running",
            "正在写入发布终态。",
        )
        session.flush()
        LOGGER.info(
            "publish finalizing",
            extra={
                "job_id": job.id,
                "compile_request_id": compile_request.id,
                "publish_record_id": publish_record_id,
                "skill_id": compile_request.skill_definition_id,
                "skill_version_id": compile_request.skill_version_id,
                "compile_status": compile_request.status,
            },
        )

        if compile_request.status == "succeeded":
            if publish_record:
                publish_record.publish_status = "published"
            if definition and is_publish_job:
                definition.latest_published_version_id = compile_request.skill_version_id
            job.status = "succeeded"
            job.last_error = ""
            job.payload = mark_publish_stage(
                job.payload,
                "publish_finalizing",
                "succeeded",
                "发布完成，最新可运行版本已更新。" if is_publish_job else "编译完成，EG artifact 已生成。",
                terminal_status="succeeded",
            )
        else:
            error_message = compile_request.error_message or (
                "编译失败，发布未生效。" if is_publish_job else "编译失败，未生成可用 EG artifact。"
            )
            if publish_record:
                publish_record.publish_status = "failed"
            job.status = "failed"
            job.last_error = error_message
            job.payload = mark_publish_stage(
                job.payload,
                "publish_finalizing",
                "failed",
                error_message,
                terminal_status="failed",
                error_message=error_message,
            )
        session.commit()
        LOGGER.info(
            "publish finalized",
            extra={
                "job_id": job.id,
                "compile_request_id": compile_request.id,
                "publish_record_id": publish_record_id,
                "skill_id": compile_request.skill_definition_id,
                "skill_version_id": compile_request.skill_version_id,
                "job_status": job.status,
            },
        )

    def _mark_current_progress_failed(self, session: Session, job_id: str, error_message: str) -> None:
        job = self.job_repository.get_runtime_job(session, job_id)
        if not job:
            return
        payload = ensure_publish_progress_payload(job.payload)
        current_stage = payload.get("current_stage") or "source_loaded"
        job.payload = mark_publish_stage(
            payload,
            current_stage,
            "failed",
            error_message,
            error_message=error_message,
        )

    @staticmethod
    def _format_exception_message(exc: Exception) -> str:
        message = getattr(exc, "message", None) or str(exc) or exc.__class__.__name__
        details = getattr(exc, "details", None)
        if isinstance(exc, SkillsError) and isinstance(details, dict):
            error_type = details.get("error_type")
            status_code = details.get("status_code")
            model = details.get("model")
            if error_type and str(error_type) not in message:
                message = f"{message}（{error_type}）"
            if status_code:
                message = f"{message}（HTTP {status_code}）"
            if model:
                message = f"{message} model={model}"
        return message

    @staticmethod
    def _add_diagnostics(
        session: Session,
        compile_request: SkillCompileRequest,
        skill_version: SkillVersion,
        diagnostics: list[dict[str, Any]],
    ) -> None:
        for diagnostic in diagnostics:
            session.add(
                CompileDiagnostic(
                    skill_compile_request_id=compile_request.id,
                    skill_version_id=skill_version.id,
                    severity=diagnostic["severity"],
                    code=diagnostic["code"],
                    message=diagnostic["message"],
                    location=diagnostic.get("location"),
                    category=diagnostic.get("category", "compiler"),
                )
            )

    def _build_compile_request_response(
        self,
        session: Session,
        compile_request: SkillCompileRequest,
    ) -> CompileRequestResponse:
        artifact = self.repository.get_artifact_for_request(session, compile_request.id)
        return CompileRequestResponse(
            id=compile_request.id,
            skill_definition_id=compile_request.skill_definition_id,
            skill_version_id=compile_request.skill_version_id,
            trigger_type=compile_request.trigger_type,
            source_commit_sha=compile_request.source_commit_sha,
            status=compile_request.status,
            dedupe_key=compile_request.dedupe_key,
            requested_at=compile_request.requested_at,
            started_at=compile_request.started_at,
            finished_at=compile_request.finished_at,
            error_message=compile_request.error_message,
            artifact_id=artifact.id if artifact else None,
            created_at=compile_request.created_at,
            updated_at=compile_request.updated_at,
        )

    @staticmethod
    def _build_diagnostic_response(diagnostic: CompileDiagnostic) -> CompileDiagnosticResponse:
        return CompileDiagnosticResponse(
            id=diagnostic.id,
            skill_compile_request_id=diagnostic.skill_compile_request_id,
            skill_version_id=diagnostic.skill_version_id,
            severity=diagnostic.severity,
            code=diagnostic.code,
            message=diagnostic.message,
            location=diagnostic.location,
            category=diagnostic.category,
            created_at=diagnostic.created_at,
        )

    def _build_artifact_response(
        self,
        session: Session,
        artifact: EgCompileArtifact,
        *,
        include_payload: bool,
    ) -> CompileArtifactResponse:
        artifact_object = self.repository.get_artifact_object(session, artifact.artifact_object_id)
        return CompileArtifactResponse(
            id=artifact.id,
            skill_compile_request_id=artifact.skill_compile_request_id,
            skill_version_id=artifact.skill_version_id,
            artifact_object_id=artifact.artifact_object_id,
            formal_revision=artifact.formal_revision,
            artifact_version=artifact.artifact_version,
            graph_summary=artifact.graph_summary,
            capability_summary=artifact.capability_summary,
            status=artifact.status,
            created_at=artifact.created_at,
            artifact=artifact_object.content_json if include_payload and artifact_object else None,
        )


def _extract_reference_image_candidates(files: dict[str, str]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    display_order = 0
    for source_file in ("README.md", "SKILL.md"):
        content = files.get(source_file) or ""
        for match in MARKDOWN_IMAGE_PATTERN.finditer(content):
            reference_path = _normalize_reference_image_path(match.group(2))
            if not reference_path or reference_path in seen_paths:
                continue
            seen_paths.add(reference_path)
            display_order += 1
            alt_text = str(match.group(1) or "").strip()
            title = alt_text or _title_from_reference_path(reference_path)
            candidates.append(
                {
                    "reference_path": reference_path,
                    "mime_type": _mime_type_for_reference_image(reference_path),
                    "title": title,
                    "source_ref": f"source.{source_file}:image:{reference_path}",
                    "display_order": display_order,
                }
            )
    return candidates


def _normalize_reference_image_path(raw_target: str) -> str | None:
    target = _markdown_image_url(raw_target)
    if not target or "\\" in target:
        return None
    parsed = urlsplit(target)
    if parsed.scheme or parsed.netloc:
        return None
    path = unquote(parsed.path).strip()
    if not path or "\\" in path or path.startswith("/"):
        return None
    normalized = posixpath.normpath(path)
    if normalized == "." or normalized == "references" or normalized.startswith("../"):
        return None
    if normalized.startswith("./"):
        normalized = normalized[2:]
    if not normalized.startswith("references/"):
        return None
    if _mime_type_for_reference_image(normalized) is None:
        return None
    return normalized


def _markdown_image_url(raw_target: str) -> str:
    target = str(raw_target or "").strip()
    if not target:
        return ""
    if target.startswith("<") and ">" in target:
        return target[1 : target.index(">")].strip()
    return target.split(maxsplit=1)[0].strip("<>")


def _mime_type_for_reference_image(reference_path: str) -> str | None:
    suffix = Path(reference_path).suffix.lower()
    return REFERENCE_IMAGE_MEDIA_TYPES.get(suffix)


def _title_from_reference_path(reference_path: str) -> str:
    stem = Path(reference_path).stem.strip()
    return stem.replace("-", " ").replace("_", " ") or Path(reference_path).name


def _is_standard_search_availability_diagnostic(item: dict[str, Any]) -> bool:
    text = json.dumps(item, ensure_ascii=False, default=str).lower()
    standard_search_terms = (
        "行业标准检索",
        "标准检索",
        "lightrag",
        "standard search",
    )
    availability_terms = (
        "不可用",
        "暂时不可用",
        "连接拒绝",
        "拒绝连接",
        "连接失败",
        "连接错误",
        "unavailable",
        "connection refused",
        "connection error",
        "connect error",
        "refused",
    )
    return any(term in text for term in standard_search_terms) and any(term in text for term in availability_terms)


def _domain_pack_ref(document: SkillDocument) -> str | None:
    value = getattr(document.skill.compile_config, "domain_pack", None)
    if isinstance(value, str) and value.strip():
        return value.strip()
    extra = getattr(document.skill.compile_config, "__pydantic_extra__", None) or {}
    extra_value = extra.get("domain_pack")
    return extra_value.strip() if isinstance(extra_value, str) and extra_value.strip() else None
