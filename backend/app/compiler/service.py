from __future__ import annotations

import hashlib
import json
import logging
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.schemas import AppendAgentEventRequest, CreateAgentRunRequest
from app.agents.service import AgentService
from app.compiler.agent import SkillCompileAgent
from app.compiler.formal_v5 import (
    FORMAL_REVISION,
    FormalDiagnostic,
    validate_and_normalize_artifact,
)
from app.core.config import Settings
from app.core.logging import log_context
from app.core.observability import record_span_exception, set_span_attributes, start_span
from app.compiler.models import ArtifactObject, CompileDiagnostic, EgCompileArtifact, PSkillCompileRequest
from app.compiler.repository import CompilerRepository
from app.compiler.schemas import (
    CompileArtifactResponse,
    CompileArtifactUpdateRequest,
    CompileArtifactValidationDiagnosticResponse,
    CompileArtifactValidationResponse,
    CompileDiagnosticResponse,
    CompileRequestProgressSummaryResponse,
    CompileRequestResponse,
    PublishProgressResponse,
    PublishProgressStageResponse,
)
from app.jobs.models import RuntimeJob
from app.jobs.progress import (
    build_publish_progress_payload,
    ensure_publish_progress_payload,
    mark_publish_stage,
)
from app.jobs.repository import JobRepository
from app.jobs.types import PSKILL_COMPILE_JOB_TYPE, is_pskill_compile_job_type
from app.pskills.exceptions import SkillNotFoundError, SkillValidationError, SkillsError
from app.pskills.manifest import SkillDocument, document_from_manifest_snapshot
from app.pskills.models import PSkillDefinition, PSkillPublishRecord, PSkillVersion, now_utc
from app.gateway.inference import LlmInferenceGateway, OpenAICompatibleInferenceGateway
from app.gateway.gitlab import GitLabSkillSourceGateway

LOGGER = logging.getLogger(__name__)


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
        repository: CompilerRepository | None = None,
        job_repository: JobRepository | None = None,
        agent_service: AgentService | None = None,
    ) -> None:
        self.settings = settings
        self.gitlab_gateway = gitlab_gateway
        self.inference_gateway = inference_gateway or OpenAICompatibleInferenceGateway.from_settings(settings)
        self.compile_agent = compile_agent or SkillCompileAgent(self.inference_gateway)
        self.repository = repository or CompilerRepository()
        self.job_repository = job_repository or JobRepository()
        self.agent_service = agent_service or AgentService()

    def _compiler_span_attributes(
        self,
        *,
        compile_request: PSkillCompileRequest | None = None,
        pskill_definition: PSkillDefinition | None = None,
        pskill_version: PSkillVersion | None = None,
        job: RuntimeJob | None = None,
        artifact: EgCompileArtifact | None = None,
        artifact_object: ArtifactObject | None = None,
        **overrides: Any,
    ) -> dict[str, Any]:
        attributes: dict[str, Any] = {}
        if job is not None:
            payload = job.payload or {}
            pskill_definition_id = payload.get("pskill_definition_id")
            pskill_version_id = payload.get("pskill_version_id")
            attributes.update(
                {
                    "job_id": job.id,
                    "job_type": job.job_type,
                    "job_status": job.status,
                    "compile_request_id": job.compile_request_id or payload.get("compile_request_id"),
                    "publish_record_id": payload.get("publish_record_id"),
                    "skill_id": pskill_definition_id,
                    "pskill_definition_id": pskill_definition_id,
                    "pskill_version_id": pskill_version_id,
                    "skill_version_id": pskill_version_id,
                    "source_commit_sha": payload.get("published_commit_sha"),
                }
            )
        if compile_request is not None:
            attributes.update(
                {
                    "compile_request_id": compile_request.id,
                    "skill_id": compile_request.pskill_definition_id,
                    "pskill_definition_id": compile_request.pskill_definition_id,
                    "pskill_version_id": compile_request.pskill_version_id,
                    "skill_version_id": compile_request.pskill_version_id,
                    "compile_status": compile_request.status,
                    "trigger_type": compile_request.trigger_type,
                    "source_commit_sha": compile_request.source_commit_sha,
                    "agent_run_id": compile_request.agent_run_id,
                }
            )
        if pskill_definition is not None:
            attributes.update(
                {
                    "skill_id": pskill_definition.id,
                    "pskill_definition_id": pskill_definition.id,
                    "skill_key": pskill_definition.key,
                }
            )
        if pskill_version is not None:
            attributes.update(
                {
                    "pskill_version_id": pskill_version.id,
                    "skill_version_id": pskill_version.id,
                    "pskill_version_no": pskill_version.version_no,
                }
            )
        if artifact is not None:
            attributes.update(
                {
                    "compile_artifact_id": artifact.id,
                    "artifact_status": artifact.status,
                    "formal_revision": artifact.formal_revision,
                    "artifact_version": artifact.artifact_version,
                    "artifact_object_id": artifact.artifact_object_id,
                }
            )
        if artifact_object is not None:
            attributes["artifact_object_id"] = artifact_object.id
        attributes.update(overrides)
        return attributes

    def create_compile_request_for_publish(
        self,
        session: Session,
        *,
        pskill_definition: PSkillDefinition,
        pskill_version: PSkillVersion,
        publish_record_id: str | None = None,
    ) -> PSkillCompileRequest:
        if not pskill_version.source_commit_sha:
            raise SkillValidationError("发布版本缺少冻结 commit SHA，无法创建编译任务。")

        dedupe_key = f"compile:{pskill_version.id}:{pskill_version.source_commit_sha}"
        existing = self.repository.get_compile_request_by_dedupe_key(session, dedupe_key)
        if existing:
            return existing

        compile_request = PSkillCompileRequest(
            pskill_definition_id=pskill_definition.id,
            pskill_version_id=pskill_version.id,
            trigger_type="publish",
            source_commit_sha=pskill_version.source_commit_sha,
            status="pending",
            dedupe_key=dedupe_key,
        )
        session.add(compile_request)
        session.flush()
        self._ensure_compile_agent_run(
            session,
            compile_request,
            pskill_definition=pskill_definition,
            pskill_version=pskill_version,
        )
        LOGGER.info(
            "compile request created for publish",
            extra={
                "skill_id": pskill_definition.id,
                "skill_key": pskill_definition.key,
                "pskill_version_id": pskill_version.id,
                "compile_request_id": compile_request.id,
                "publish_record_id": publish_record_id,
            },
        )

        progress_payload = build_publish_progress_payload(
            compile_request_id=compile_request.id,
            publish_record_id=publish_record_id,
            pskill_definition_id=pskill_definition.id,
            pskill_version_id=pskill_version.id,
            published_commit_sha=pskill_version.source_commit_sha,
        )
        job = RuntimeJob(
            job_type=PSKILL_COMPILE_JOB_TYPE,
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
                "skill_id": pskill_definition.id,
                "skill_key": pskill_definition.key,
                "pskill_version_id": pskill_version.id,
                "compile_request_id": compile_request.id,
                "publish_record_id": publish_record_id,
                "job_id": job.id,
            },
        )
        return compile_request

    def create_manual_compile_request_for_pskill(
        self,
        session: Session,
        *,
        pskill_id: str,
    ) -> CompileRequestResponse:
        pskill_definition = self.repository.get_pskill_definition(session, pskill_id)
        if not pskill_definition:
            raise SkillNotFoundError("未找到对应的 PSkill。", details={"pskill_id": pskill_id})

        pskill_version = None
        if pskill_definition.latest_published_version_id:
            pskill_version = self.repository.get_pskill_version(session, pskill_definition.latest_published_version_id)
        if not pskill_version and pskill_definition.latest_draft_version_id:
            pskill_version = self.repository.get_pskill_version(session, pskill_definition.latest_draft_version_id)
        if not pskill_version:
            pskill_version = session.scalar(
                select(PSkillVersion)
                .where(PSkillVersion.pskill_definition_id == pskill_definition.id)
                .order_by(PSkillVersion.updated_at.desc())
            )
        if not pskill_version:
            raise SkillValidationError("当前 PSkill 没有可编译的版本。", details={"pskill_id": pskill_id})

        source_commit_sha = pskill_version.source_commit_sha
        if not source_commit_sha and pskill_version.source_ref:
            source_commit_sha = self.gitlab_gateway.get_branch_head(
                pskill_definition.gitlab_project_id,
                pskill_version.source_ref,
            )
            pskill_version.source_commit_sha = source_commit_sha
            session.flush()
        if not source_commit_sha:
            raise SkillValidationError(
                "当前 PSkill 版本缺少冻结 commit SHA，无法创建手动编译任务。",
                details={"pskill_id": pskill_id, "pskill_version_id": pskill_version.id},
            )

        compile_request = PSkillCompileRequest(
            pskill_definition_id=pskill_definition.id,
            pskill_version_id=pskill_version.id,
            trigger_type="manual",
            source_commit_sha=source_commit_sha,
            status="pending",
            dedupe_key=f"compile:manual:{pskill_version.id}:{source_commit_sha}:{uuid4()}",
        )
        session.add(compile_request)
        session.flush()
        self._ensure_compile_agent_run(
            session,
            compile_request,
            pskill_definition=pskill_definition,
            pskill_version=pskill_version,
        )

        progress_payload = build_publish_progress_payload(
            compile_request_id=compile_request.id,
            publish_record_id=None,
            pskill_definition_id=pskill_definition.id,
            pskill_version_id=pskill_version.id,
            published_commit_sha=source_commit_sha,
        )
        progress_payload["operation"] = "compile"
        for stage in progress_payload["progress_stages"]:
            if stage["key"] == "publish_finalizing":
                stage["label"] = "完成编译"

        job = RuntimeJob(
            job_type=PSKILL_COMPILE_JOB_TYPE,
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
                "skill_id": pskill_definition.id,
                "skill_key": pskill_definition.key,
                "pskill_version_id": pskill_version.id,
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
    ) -> PSkillCompileRequest:
        compile_request = self.repository.get_compile_request(session, compile_request_id)
        if not compile_request:
            raise SkillNotFoundError("未找到编译请求。", details={"compile_request_id": compile_request_id})

        if compile_request.status == "succeeded":
            return compile_request

        pskill_definition = self.repository.get_pskill_definition(session, compile_request.pskill_definition_id)
        pskill_version = self.repository.get_pskill_version(session, compile_request.pskill_version_id)
        if not pskill_definition or not pskill_version:
            raise SkillNotFoundError("编译请求关联的 Skill 或版本不存在。")
        self._ensure_compile_agent_run(
            session,
            compile_request,
            pskill_definition=pskill_definition,
            pskill_version=pskill_version,
        )

        with log_context(
            skill_id=pskill_definition.id,
            skill_key=pskill_definition.key,
            pskill_version_id=pskill_version.id,
            compile_request_id=compile_request.id,
        ):
            LOGGER.info("compile request started")

        compile_request.status = "running"
        compile_request.started_at = now_utc()
        self._mark_compile_agent_started(session, compile_request)
        session.flush()

        try:
            if progress:
                progress.mark("source_loaded", "running", "正在读取冻结 commit 下的 Skill source。")
            with start_span(
                "compile.source_load",
                **self._compiler_span_attributes(
                    compile_request=compile_request,
                    pskill_definition=pskill_definition,
                    pskill_version=pskill_version,
                ),
            ):
                source = self.gitlab_gateway.get_skill_source(
                    pskill_definition.gitlab_project_id,
                    compile_request.source_commit_sha,
                )
            LOGGER.info(
                "compile source loaded",
                extra={
                    "skill_id": pskill_definition.id,
                    "skill_key": pskill_definition.key,
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
                **self._compiler_span_attributes(
                    compile_request=compile_request,
                    pskill_definition=pskill_definition,
                    pskill_version=pskill_version,
                ),
            ):
                document = document_from_manifest_snapshot(pskill_version.manifest_snapshot)
                diagnostics = self._validate_document(pskill_definition, document)
            self._add_diagnostics(session, compile_request, pskill_version, diagnostics)

            blocking = [item for item in diagnostics if item["severity"] == "error"]
            if blocking:
                compile_request.status = "failed"
                compile_request.error_message = blocking[0]["message"]
                compile_request.finished_at = now_utc()
                self._mark_compile_agent_failed(session, compile_request, blocking[0]["message"])
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
                        "skill_id": pskill_definition.id,
                        "skill_key": pskill_definition.key,
                        "compile_request_id": compile_request.id,
                        "error": compile_request.error_message,
                    },
                )
                return compile_request
            if progress:
                progress.mark("manifest_checked", "succeeded", "manifest snapshot 校验通过。")

            with start_span(
                "compile.agent",
                **self._compiler_span_attributes(
                    compile_request=compile_request,
                    pskill_definition=pskill_definition,
                    pskill_version=pskill_version,
                ),
            ):
                artifact, agent_diagnostics = self._compile_with_agent(
                    session=session,
                    compile_request=compile_request,
                    pskill_definition=pskill_definition,
                    pskill_version=pskill_version,
                    document=document,
                    source=source,
                    progress=progress,
                )
            self._add_diagnostics(session, compile_request, pskill_version, [item.as_dict() for item in agent_diagnostics])
            if artifact is None:
                error_message = agent_diagnostics[-1].message if agent_diagnostics else "PSkill 编译智能体未生成合法 EG artifact。"
                compile_request.status = "failed"
                compile_request.error_message = error_message
                compile_request.finished_at = now_utc()
                self._mark_compile_agent_failed(session, compile_request, error_message)
                if mark_job_terminal:
                    self._mark_job(session, compile_request.id, "failed", error_message)
                session.commit()
                LOGGER.warning(
                    "compile agent failed to produce valid artifact",
                    extra={
                        "skill_id": pskill_definition.id,
                        "skill_key": pskill_definition.key,
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
                    "id": pskill_definition.id,
                    "key": pskill_definition.key,
                    "name": pskill_definition.name,
                    "version_id": pskill_version.id,
                    "version_no": pskill_version.version_no,
                    "source_commit_sha": compile_request.source_commit_sha,
                }
            )
            with start_span(
                "compile.emit",
                **self._compiler_span_attributes(
                    compile_request=compile_request,
                    pskill_definition=pskill_definition,
                    pskill_version=pskill_version,
                ),
            ) as span:
                artifact_json = json.dumps(artifact, ensure_ascii=False, sort_keys=True).encode("utf-8")
                checksum = hashlib.sha256(artifact_json).hexdigest()
                artifact_object = ArtifactObject(
                    bucket=self.settings.object_store_bucket,
                    object_key=(
                        f"skills/{pskill_definition.key}/versions/{pskill_version.version_no}/"
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
                    compile_request_id=compile_request.id,
                    pskill_version_id=pskill_version.id,
                    artifact_object_id=artifact_object.id,
                    formal_revision=artifact["formal_revision"],
                    artifact_version=artifact["artifact_version"],
                    graph_summary=artifact["graph_summary"],
                    capability_summary=artifact["capability_summary"],
                    status="ready",
                )
                session.add(eg_artifact)
                session.flush()
                set_span_attributes(
                    span,
                    self._compiler_span_attributes(
                        compile_request=compile_request,
                        pskill_definition=pskill_definition,
                        pskill_version=pskill_version,
                        artifact=eg_artifact,
                        artifact_object=artifact_object,
                    ),
                )
            if progress:
                progress.mark("artifact_emitting", "succeeded", "EG 编译产物已写入。")
            compile_request.status = "succeeded"
            compile_request.error_message = ""
            compile_request.finished_at = now_utc()
            self._mark_compile_agent_succeeded(
                session,
                compile_request,
                output_payload={
                    "artifact_id": eg_artifact.id,
                    "graph_summary": eg_artifact.graph_summary,
                    "capability_summary": eg_artifact.capability_summary,
                },
            )
            if mark_job_terminal:
                self._mark_job(session, compile_request.id, "succeeded")
            session.commit()
            LOGGER.info(
                "compile request succeeded",
                extra={
                    "skill_id": pskill_definition.id,
                    "skill_key": pskill_definition.key,
                    "pskill_version_id": pskill_version.id,
                    "compile_request_id": compile_request.id,
                    "artifact_id": eg_artifact.id,
                },
            )
            return compile_request
        except Exception as exc:
            error_message = self._format_exception_message(exc)
            compile_request.status = "failed"
            compile_request.error_message = error_message
            compile_request.finished_at = now_utc()
            self._mark_compile_agent_failed(session, compile_request, error_message)
            session.add(
                CompileDiagnostic(
                    compile_request_id=compile_request.id,
                    pskill_version_id=pskill_version.id,
                    severity="error",
                    code="compile.failed",
                    message=error_message,
                    location=getattr(exc, "details", None),
                    category="compiler",
                )
            )
            if progress:
                self._mark_current_progress_failed(session, progress.job_id, error_message)
            if mark_job_terminal:
                self._mark_job(session, compile_request.id, "failed", error_message)
            session.commit()
            LOGGER.exception(
                "compile request failed unexpectedly",
                extra={
                    "skill_id": pskill_definition.id,
                    "skill_key": pskill_definition.key,
                    "pskill_version_id": pskill_version.id,
                    "compile_request_id": compile_request.id,
                    "error": error_message,
                },
            )
            return compile_request

    def process_compile_job(self, session: Session, job_id: str) -> PSkillCompileRequest:
        job = self.job_repository.get_runtime_job(session, job_id)
        if not job:
            raise SkillNotFoundError("未找到运行任务。", details={"job_id": job_id})
        if not is_pskill_compile_job_type(job.job_type):
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
            with start_span(
                "job.compile",
                **self._compiler_span_attributes(
                    job=job,
                    compile_request_id=compile_request_id,
                ),
            ) as span:
                compile_request = self.process_compile_request(
                    session,
                    compile_request_id,
                    progress=progress,
                    mark_job_terminal=False,
                )
                self._finalize_publish_job(session, job.id, compile_request)
                set_span_attributes(
                    span,
                    self._compiler_span_attributes(
                        compile_request=compile_request,
                        artifact=self.repository.get_artifact_for_request(session, compile_request.id),
                        job_status=job.status,
                    ),
                )
        return compile_request

    def process_compile_job_for_request(self, session: Session, compile_request_id: str) -> PSkillCompileRequest:
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

    def list_compile_requests(
        self,
        session: Session,
        *,
        pskill_id: str | None = None,
        status: str | None = None,
    ) -> list[CompileRequestResponse]:
        return [
            self._build_compile_request_response(session, item)
            for item in self.repository.list_compile_requests(session, pskill_id=pskill_id, status=status)
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
            publish_record = session.get(PSkillPublishRecord, publish_record_id)

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

    def validate_artifact(self, session: Session, artifact_id: str) -> CompileArtifactValidationResponse:
        artifact = self.repository.get_artifact(session, artifact_id)
        if not artifact:
            raise SkillNotFoundError("未找到编译产物。", details={"compile_artifact_id": artifact_id})
        artifact_object = self.repository.get_artifact_object(session, artifact.artifact_object_id)
        if not artifact_object:
            raise SkillNotFoundError("未找到编译产物对象。", details={"artifact_object_id": artifact.artifact_object_id})

        validation = validate_and_normalize_artifact(artifact_object.content_json)
        normalized = validation.artifact
        if normalized is not None:
            normalized["compile_request_id"] = artifact.compile_request_id
        return CompileArtifactValidationResponse(
            artifact_id=artifact.id,
            compile_request_id=artifact.compile_request_id,
            pskill_version_id=artifact.pskill_version_id,
            valid=not validation.has_errors and normalized is not None,
            diagnostics=[
                CompileArtifactValidationDiagnosticResponse(**diagnostic.as_dict())
                for diagnostic in validation.diagnostics
            ],
            graph_summary=normalized.get("graph_summary") if normalized else None,
            capability_summary=normalized.get("capability_summary") if normalized else None,
            normalized_artifact=normalized,
        )

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
        normalized["compile_request_id"] = artifact.compile_request_id

        compile_request = self.repository.get_compile_request(session, artifact.compile_request_id)
        pskill_version = self.repository.get_pskill_version(session, artifact.pskill_version_id)
        pskill_definition = (
            self.repository.get_pskill_definition(session, compile_request.pskill_definition_id)
            if compile_request
            else None
        )
        if pskill_definition and pskill_version and compile_request:
            normalized.setdefault("skill", {})
            normalized["skill"].update(
                {
                    "id": pskill_definition.id,
                    "key": pskill_definition.key,
                    "name": pskill_definition.name,
                    "version_id": pskill_version.id,
                    "version_no": pskill_version.version_no,
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
                "compile_request_id": artifact.compile_request_id,
                "artifact_id": artifact.id,
                "artifact_object_id": artifact.artifact_object_id,
            },
        )
        return self._build_artifact_response(session, artifact, include_payload=True)

    def _ensure_compile_agent_run(
        self,
        session: Session,
        compile_request: PSkillCompileRequest,
        *,
        pskill_definition: PSkillDefinition,
        pskill_version: PSkillVersion,
    ) -> str:
        if compile_request.agent_run_id:
            return compile_request.agent_run_id
        run = self.agent_service.create_run(
            session,
            CreateAgentRunRequest(
                agent_key="pskill.compiler",
                owner_type="pskill_compile_request",
                owner_id=compile_request.id,
                input_payload={
                    "compile_request_id": compile_request.id,
                    "pskill_definition_id": pskill_definition.id,
                    "pskill_version_id": pskill_version.id,
                    "source_commit_sha": compile_request.source_commit_sha,
                    "trigger_type": compile_request.trigger_type,
                },
            ),
            commit=False,
        )
        compile_request.agent_run_id = run.id
        self.agent_service.append_event(
            session,
            run.id,
            AppendAgentEventRequest(
                event_type="compile.request.linked",
                phase="compiler",
                payload={"compile_request_id": compile_request.id, "pskill_key": pskill_definition.key},
            ),
            commit=False,
        )
        return run.id

    def _mark_compile_agent_started(self, session: Session, compile_request: PSkillCompileRequest) -> None:
        if not compile_request.agent_run_id:
            return
        agent_run = self.agent_service.get_run_model(session, compile_request.agent_run_id)
        agent_run.status = "running"
        agent_run.started_at = agent_run.started_at or now_utc()
        self.agent_service.append_event(
            session,
            agent_run.id,
            AppendAgentEventRequest(
                event_type="compile.request.started",
                phase="compiler",
                payload={"compile_request_id": compile_request.id},
            ),
            commit=False,
        )

    def _mark_compile_agent_succeeded(
        self,
        session: Session,
        compile_request: PSkillCompileRequest,
        *,
        output_payload: dict[str, Any],
    ) -> None:
        if not compile_request.agent_run_id:
            return
        agent_run = self.agent_service.get_run_model(session, compile_request.agent_run_id)
        agent_run.status = "succeeded"
        agent_run.output_payload = {
            "compile_request_id": compile_request.id,
            "ready": True,
            **output_payload,
        }
        agent_run.error_message = ""
        agent_run.ended_at = now_utc()
        self.agent_service.append_event(
            session,
            agent_run.id,
            AppendAgentEventRequest(
                event_type="compile.request.succeeded",
                phase="compiler",
                payload=agent_run.output_payload,
            ),
            commit=False,
        )

    def _mark_compile_agent_failed(
        self,
        session: Session,
        compile_request: PSkillCompileRequest,
        error_message: str,
    ) -> None:
        if not compile_request.agent_run_id:
            return
        agent_run = self.agent_service.get_run_model(session, compile_request.agent_run_id)
        agent_run.status = "failed"
        agent_run.error_message = error_message
        agent_run.ended_at = now_utc()
        self.agent_service.append_event(
            session,
            agent_run.id,
            AppendAgentEventRequest(
                event_type="compile.request.failed",
                phase="compiler",
                payload={"compile_request_id": compile_request.id, "error_message": error_message},
            ),
            commit=False,
        )

    def _record_compile_agent_model_call(
        self,
        session: Session,
        compile_request: PSkillCompileRequest,
        *,
        attempt: int,
        candidate,
        repair_diagnostics: list[FormalDiagnostic],
    ) -> None:
        if not compile_request.agent_run_id:
            return
        response_payload = {
            "attempt": attempt,
            "artifact_candidate_returned": candidate.artifact is not None,
            "diagnostics": [item.as_dict() for item in candidate.diagnostics],
            "context_diagnostics": [item.as_dict() for item in candidate.context_diagnostics],
            "compiler_metadata": candidate.compiler_metadata,
        }
        self.agent_service.record_model_call(
            session,
            agent_run_id=compile_request.agent_run_id,
            provider="llm_inference_gateway",
            route_key=str(candidate.compiler_metadata.get("agent_prompt", {}).get("route_key") or "text"),
            model_name=str(candidate.compiler_metadata.get("agent_prompt", {}).get("model") or ""),
            status="succeeded" if candidate.artifact is not None else "failed",
            request_payload={
                "compile_request_id": compile_request.id,
                "attempt": attempt,
                "repair_diagnostics": [item.as_dict() for item in repair_diagnostics],
            },
            response_payload=response_payload,
            usage_json=dict(candidate.usage or {}),
            commit=False,
        )
        self.agent_service.append_event(
            session,
            compile_request.agent_run_id,
            AppendAgentEventRequest(
                event_type="compile.agent.model_call.completed",
                phase="compiler",
                payload={
                    "compile_request_id": compile_request.id,
                    "attempt": attempt,
                    "artifact_candidate_returned": candidate.artifact is not None,
                },
            ),
            commit=False,
        )

    def _validate_document(self, pskill_definition: PSkillDefinition, document: SkillDocument) -> list[dict[str, Any]]:
        diagnostics: list[dict[str, Any]] = [
            {
                "severity": "info",
                "code": "compile.agent.enabled",
                "message": "使用 SKILL 编译智能体生成 formal-v5 EG candidate，并执行确定性校验。",
                "category": "compiler",
            }
        ]
        if document.skill.identity.key != pskill_definition.key:
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
        compile_request: PSkillCompileRequest,
        pskill_definition: PSkillDefinition,
        pskill_version: PSkillVersion,
        document: SkillDocument,
        source,
        progress: PublishProgressReporter | None = None,
    ) -> tuple[dict[str, Any] | None, list[FormalDiagnostic]]:
        diagnostics: list[FormalDiagnostic] = []
        repair_diagnostics: list[FormalDiagnostic] = []
        compiler_metadata: dict[str, Any] = {}
        context_recorded = False
        for attempt in range(2):
            LOGGER.info(
                "compile agent attempt started",
                extra={
                    "skill_id": pskill_definition.id,
                    "skill_key": pskill_definition.key,
                    "pskill_version_id": pskill_version.id,
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
                **self._compiler_span_attributes(
                    compile_request=compile_request,
                    pskill_definition=pskill_definition,
                    pskill_version=pskill_version,
                ),
                attempt=attempt + 1,
            ) as span:
                try:
                    candidate = self.compile_agent.compile(
                        pskill_definition=pskill_definition,
                        pskill_version=pskill_version,
                        document=document,
                        source=source,
                        repair_diagnostics=repair_diagnostics,
                        session=session,
                    )
                    if progress:
                        self.job_repository.accumulate_llm_usage(
                            self.job_repository.get_runtime_job(session, progress.job_id),
                            candidate.usage,
                        )
                        session.flush()
                    self._record_compile_agent_model_call(
                        session,
                        compile_request,
                        attempt=attempt + 1,
                        candidate=candidate,
                        repair_diagnostics=repair_diagnostics,
                    )
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
                **self._compiler_span_attributes(
                    compile_request=compile_request,
                    pskill_definition=pskill_definition,
                    pskill_version=pskill_version,
                ),
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
        compile_request: PSkillCompileRequest,
    ) -> None:
        job = self.job_repository.get_runtime_job(session, job_id)
        if not job:
            return

        publish_record_id = job.payload.get("publish_record_id")
        publish_record = session.get(PSkillPublishRecord, publish_record_id) if publish_record_id else None
        definition = self.repository.get_pskill_definition(session, compile_request.pskill_definition_id)
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
                "skill_id": compile_request.pskill_definition_id,
                "pskill_version_id": compile_request.pskill_version_id,
                "compile_status": compile_request.status,
            },
        )

        if compile_request.status == "succeeded":
            if publish_record:
                publish_record.publish_status = "published"
            if definition and is_publish_job:
                definition.latest_published_version_id = compile_request.pskill_version_id
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
                "skill_id": compile_request.pskill_definition_id,
                "pskill_version_id": compile_request.pskill_version_id,
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
        compile_request: PSkillCompileRequest,
        pskill_version: PSkillVersion,
        diagnostics: list[dict[str, Any]],
    ) -> None:
        for diagnostic in diagnostics:
            session.add(
                CompileDiagnostic(
                    compile_request_id=compile_request.id,
                    pskill_version_id=pskill_version.id,
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
        compile_request: PSkillCompileRequest,
    ) -> CompileRequestResponse:
        artifact = self.repository.get_artifact_for_request(session, compile_request.id)
        progress = self._build_compile_request_progress_summary(session, compile_request)
        return CompileRequestResponse(
            id=compile_request.id,
            pskill_definition_id=compile_request.pskill_definition_id,
            pskill_version_id=compile_request.pskill_version_id,
            agent_run_id=compile_request.agent_run_id,
            trigger_type=compile_request.trigger_type,
            source_commit_sha=compile_request.source_commit_sha,
            status=compile_request.status,
            dedupe_key=compile_request.dedupe_key,
            requested_at=compile_request.requested_at,
            started_at=compile_request.started_at,
            finished_at=compile_request.finished_at,
            error_message=compile_request.error_message,
            artifact_id=artifact.id if artifact else None,
            progress=progress,
            created_at=compile_request.created_at,
            updated_at=compile_request.updated_at,
        )

    def _build_compile_request_progress_summary(
        self,
        session: Session,
        compile_request: PSkillCompileRequest,
    ) -> CompileRequestProgressSummaryResponse:
        job = self.job_repository.get_compile_job(session, compile_request.id)
        payload = ensure_publish_progress_payload(job.payload if job else {"compile_request_id": compile_request.id})
        stages = payload["progress_stages"]
        current_stage = str(payload.get("current_stage") or "")
        current = next((stage for stage in stages if stage.get("key") == current_stage), None)
        completed_stages = sum(1 for stage in stages if stage.get("status") == "succeeded")
        total_stages = len(stages)
        percent = round((completed_stages / total_stages) * 100) if total_stages else 0
        if payload.get("terminal_status") == "succeeded":
            percent = 100
        return CompileRequestProgressSummaryResponse(
            current_stage=current_stage,
            current_stage_label=str((current or {}).get("label") or current_stage),
            current_stage_status=str((current or {}).get("status") or "pending"),
            terminal=bool(payload.get("terminal")),
            terminal_status=payload.get("terminal_status"),
            completed_stages=completed_stages,
            total_stages=total_stages,
            percent=percent,
            error_message=str(payload.get("error_message") or compile_request.error_message or ""),
            updated_at=payload.get("updated_at"),
        )

    @staticmethod
    def _build_diagnostic_response(diagnostic: CompileDiagnostic) -> CompileDiagnosticResponse:
        return CompileDiagnosticResponse(
            id=diagnostic.id,
            compile_request_id=diagnostic.compile_request_id,
            pskill_version_id=diagnostic.pskill_version_id,
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
        compile_request = self.repository.get_compile_request(session, artifact.compile_request_id)
        return CompileArtifactResponse(
            id=artifact.id,
            compile_request_id=artifact.compile_request_id,
            compile_request=(
                self._build_compile_request_response(session, compile_request)
                if compile_request
                else None
            ),
            pskill_version_id=artifact.pskill_version_id,
            artifact_object_id=artifact.artifact_object_id,
            formal_revision=artifact.formal_revision,
            artifact_version=artifact.artifact_version,
            graph_summary=artifact.graph_summary,
            capability_summary=artifact.capability_summary,
            status=artifact.status,
            created_at=artifact.created_at,
            artifact=artifact_object.content_json if include_payload and artifact_object else None,
        )
