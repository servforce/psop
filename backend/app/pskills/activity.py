from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.compiler.models import PSkillCompileRequest
from app.compiler.repository import CompilerRepository
from app.jobs.progress import ensure_publish_progress_payload
from app.jobs.repository import JobRepository
from app.pskills.exceptions import SkillNotFoundError
from app.pskills.models import PSkillPublishRecord, PSkillVersion
from app.pskills.repository import SkillsRepository


class PSkillActivityService:
    """Builds PSkill activity snapshots from persisted publish and compile facts."""

    def __init__(
        self,
        *,
        skills_repository: SkillsRepository | None = None,
        compiler_repository: CompilerRepository | None = None,
        job_repository: JobRepository | None = None,
    ) -> None:
        self.skills_repository = skills_repository or SkillsRepository()
        self.compiler_repository = compiler_repository or CompilerRepository()
        self.job_repository = job_repository or JobRepository()

    def build_snapshot(self, session: Session, skill_id: str) -> dict[str, Any]:
        definition = self.skills_repository.get_pskill_definition(session, skill_id)
        if not definition:
            raise SkillNotFoundError("未找到 PSkill。", details={"skill_id": skill_id})

        compile_requests = self.compiler_repository.list_compile_requests(session, pskill_id=skill_id)
        publishes = self.skills_repository.get_publish_records(session, skill_id)
        active = any(item.status in {"pending", "running"} for item in compile_requests) or any(
            item.publish_status in {"requested", "compiling"} for item in publishes
        )
        return {
            "pskill": {
                "id": definition.id,
                "key": definition.key,
                "name": definition.name,
                "status": definition.status,
                "latest_draft_version_id": definition.latest_draft_version_id,
                "latest_published_version_id": definition.latest_published_version_id,
                "updated_at": self._iso(definition.updated_at),
            },
            "active": active,
            "terminal": not active,
            "versions": [
                self._version_payload(item)
                for item in self.skills_repository.list_pskill_versions(session, skill_id)[:10]
            ],
            "publishes": [self._publish_payload(item) for item in publishes[:10]],
            "compile_requests": [self._compile_request_payload(session, item) for item in compile_requests[:10]],
        }

    def _compile_request_payload(self, session: Session, compile_request: PSkillCompileRequest) -> dict[str, Any]:
        job = self.job_repository.get_compile_job(session, compile_request.id)
        progress = ensure_publish_progress_payload(job.payload if job else {"compile_request_id": compile_request.id})
        artifact = self.compiler_repository.get_artifact_for_request(session, compile_request.id)
        return {
            "id": compile_request.id,
            "pskill_version_id": compile_request.pskill_version_id,
            "agent_run_id": compile_request.agent_run_id,
            "trigger_type": compile_request.trigger_type,
            "source_commit_sha": compile_request.source_commit_sha,
            "status": compile_request.status,
            "artifact_id": artifact.id if artifact else None,
            "error_message": compile_request.error_message,
            "requested_at": self._iso(compile_request.requested_at),
            "started_at": self._iso(compile_request.started_at),
            "finished_at": self._iso(compile_request.finished_at),
            "job": {
                "id": job.id if job else None,
                "status": job.status if job else None,
                "attempt_no": job.attempt_no if job else 0,
                "max_attempts": job.max_attempts if job else 0,
                "worker_name": job.worker_name if job else "",
                "last_error": job.last_error if job else "",
            },
            "progress": {
                "current_stage": progress.get("current_stage"),
                "terminal": bool(progress.get("terminal")),
                "terminal_status": progress.get("terminal_status"),
                "error_message": progress.get("error_message") or compile_request.error_message,
                "updated_at": progress.get("updated_at"),
                "stages": progress.get("progress_stages", []),
            },
        }

    @staticmethod
    def _version_payload(version: PSkillVersion) -> dict[str, Any]:
        return {
            "id": version.id,
            "version_no": version.version_no,
            "status": version.status,
            "source_ref": version.source_ref,
            "source_commit_sha": version.source_commit_sha,
            "created_at": PSkillActivityService._iso(version.created_at),
            "updated_at": PSkillActivityService._iso(version.updated_at),
        }

    @staticmethod
    def _publish_payload(record: PSkillPublishRecord) -> dict[str, Any]:
        return {
            "id": record.id,
            "pskill_version_id": record.pskill_version_id,
            "publish_reason": record.publish_reason,
            "publish_status": record.publish_status,
            "published_commit_sha": record.published_commit_sha,
            "release_ref": record.release_ref,
            "published_at": PSkillActivityService._iso(record.published_at),
            "created_at": PSkillActivityService._iso(record.created_at),
        }

    @staticmethod
    def _iso(value: datetime | None) -> str | None:
        return value.isoformat() if value else None
