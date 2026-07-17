from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent_harness.persistence.models import AgentEventRecord, AgentRunRecord
from app.agent_harness.persistence.schemas import (
    AgentRunFinalResponse,
    AgentRunStepResponse,
    AgentRunTimelineResponse,
)
from app.domain.jobs.models import RuntimeJob
from app.domain.jobs.schemas import RuntimeJobProgressResponse, RuntimeJobTokenUsageResponse
from app.domain.jobs.service import JobQueryService
from app.domain.skills.exceptions import SkillNotFoundError
from app.domain.skills.models import SkillRawMaterialGeneration, now_utc


class AgentRunQueryService:
    def __init__(self, job_query_service: JobQueryService | None = None) -> None:
        self.job_query_service = job_query_service or JobQueryService()

    def get_run_timeline(self, session: Session, agent_run_id: str) -> AgentRunTimelineResponse:
        record = session.get(AgentRunRecord, agent_run_id)
        generation = self._generation_for_run(session, agent_run_id, record)
        if record is None and generation is None:
            raise SkillNotFoundError("未找到 Agent Run。", details={"agent_run_id": agent_run_id})
        return self._build_timeline(session, agent_run_id=agent_run_id, record=record, generation=generation)

    def get_latest_run_timeline(
        self,
        session: Session,
        *,
        agent_key: str,
        related_skill_definition_id: str,
    ) -> AgentRunTimelineResponse:
        record = session.scalar(
            select(AgentRunRecord)
            .where(
                AgentRunRecord.agent_key == agent_key,
                AgentRunRecord.related_skill_definition_id == related_skill_definition_id,
            )
            .order_by(AgentRunRecord.updated_at.desc(), AgentRunRecord.created_at.desc())
            .limit(1)
        )
        generation = self._latest_generation_for_agent(
            session,
            agent_key=agent_key,
            related_skill_definition_id=related_skill_definition_id,
        )
        if generation is not None:
            agent_run_id = self._generation_agent_run_id(generation)
            generation_record = session.get(AgentRunRecord, agent_run_id)
            if generation_record is not None:
                return self.get_run_timeline(session, generation_record.id)
            if record is None or generation.created_at >= record.created_at:
                return self._build_timeline(session, agent_run_id=agent_run_id, record=None, generation=generation)
        if record is not None:
            return self.get_run_timeline(session, record.id)
        if generation is None:
            raise SkillNotFoundError(
                "未找到 Agent Run。",
                details={"agent_key": agent_key, "related_skill_definition_id": related_skill_definition_id},
            )
        return self._build_timeline(
            session,
            agent_run_id=self._generation_agent_run_id(generation),
            record=None,
            generation=generation,
        )

    def _build_timeline(
        self,
        session: Session,
        *,
        agent_run_id: str,
        record: AgentRunRecord | None,
        generation: SkillRawMaterialGeneration | None,
    ) -> AgentRunTimelineResponse:
        job = self._job_for_run(session, record, generation)
        job_response = self.job_query_service._build_job_response(session, job, now=now_utc()) if job else None
        events = self._events_for_run(session, agent_run_id) if record else []
        status = self._run_status(record, generation, job)
        terminal = status in {"succeeded", "failed", "cancelled", "canceled", "deadletter", "dead_letter"}
        elapsed_ms = (
            job_response.elapsed_ms
            if job_response
            else self._elapsed_ms(generation.created_at if generation else None)
        )
        if job_response and terminal and job_response.duration_ms is not None:
            elapsed_ms = job_response.duration_ms
        validation_diagnostics = self._validation_diagnostics(events)
        candidate_submission_attempts = sum(
            1
            for event in events
            if event.event_type == "agent.tool.started" and (event.payload or {}).get("tool_name") == "psop.builder.submit_candidate"
        )
        candidate_correction_attempts = max(0, candidate_submission_attempts - 1)
        model_call_count = sum(1 for event in events if event.event_type == "agent.model.started")
        if not model_call_count:
            # 兼容早期仅记录 token usage 的历史 Agent Run。
            model_call_count = sum(1 for event in events if event.event_type == "agent.token.usage")
        return AgentRunTimelineResponse(
            agent_run_id=agent_run_id,
            agent_key=record.agent_key if record else str((generation.prompt_metadata or {}).get("agent_key") or ""),
            status=status,
            user_description=generation.user_description if generation else "",
            related_skill_definition_id=(
                record.related_skill_definition_id if record else (generation.skill_definition_id if generation else "")
            ),
            related_generation_id=record.related_generation_id if record else (generation.id if generation else ""),
            related_job_id=record.related_job_id if record else (job.id if job else ""),
            related_runtime_run_id=record.related_runtime_run_id if record else "",
            progress=job_response.progress if job_response else self._generation_progress(generation),
            elapsed_ms=elapsed_ms,
            token_usage=job_response.token_usage if job_response else self._token_usage_from_events(events),
            model_call_count=model_call_count,
            candidate_submission_attempts=candidate_submission_attempts,
            candidate_correction_attempts=candidate_correction_attempts,
            job_attempt_no=job.attempt_no if job else 0,
            job_max_attempts=job.max_attempts if job else 0,
            failure_kind="validation_failed" if validation_diagnostics else "",
            validation_diagnostics=validation_diagnostics,
            steps=[*self._job_steps(job), *self._event_steps(events)],
            final=self._final_for_generation(generation),
            error_message=(
                (record.error_message if record else "")
                or (generation.error_message if generation else "")
                or (job.last_error if job else "")
            ),
            created_at=record.created_at if record else (generation.created_at if generation else None),
            updated_at=record.updated_at if record else (generation.updated_at if generation else None),
        )

    def _generation_for_run(
        self,
        session: Session,
        agent_run_id: str,
        record: AgentRunRecord | None,
    ) -> SkillRawMaterialGeneration | None:
        generation_id = record.related_generation_id if record and record.related_generation_id else agent_run_id
        generation = session.get(SkillRawMaterialGeneration, generation_id)
        if generation is not None:
            return generation
        if record and record.related_generation_id:
            return session.get(SkillRawMaterialGeneration, record.related_generation_id)
        return None

    def _latest_generation_for_agent(
        self,
        session: Session,
        *,
        agent_key: str,
        related_skill_definition_id: str,
    ) -> SkillRawMaterialGeneration | None:
        candidates = session.scalars(
            select(SkillRawMaterialGeneration)
            .where(SkillRawMaterialGeneration.skill_definition_id == related_skill_definition_id)
            .order_by(SkillRawMaterialGeneration.created_at.desc())
            .limit(50)
        ).all()
        for generation in candidates:
            metadata = generation.prompt_metadata or {}
            if metadata.get("agent_key") == agent_key and self._generation_agent_run_id(generation):
                return generation
        return None

    @staticmethod
    def _generation_agent_run_id(generation: SkillRawMaterialGeneration) -> str:
        return str((generation.prompt_metadata or {}).get("agent_run_id") or generation.id)

    def _job_for_run(
        self,
        session: Session,
        record: AgentRunRecord | None,
        generation: SkillRawMaterialGeneration | None,
    ) -> RuntimeJob | None:
        job_id = record.related_job_id if record and record.related_job_id else ""
        if not job_id and generation:
            job_id = str((generation.prompt_metadata or {}).get("job_id") or "")
        if job_id:
            job = session.get(RuntimeJob, job_id)
            if job:
                return job
        if generation:
            return session.scalar(
                select(RuntimeJob).where(RuntimeJob.dedupe_key == f"skill-raw-material-generation:{generation.id}")
            )
        return None

    @staticmethod
    def _events_for_run(session: Session, agent_run_id: str) -> list[AgentEventRecord]:
        return list(
            session.scalars(
                select(AgentEventRecord)
                .where(AgentEventRecord.agent_run_id == agent_run_id)
                .order_by(AgentEventRecord.seq_no.asc())
            ).all()
        )

    @staticmethod
    def _run_status(
        record: AgentRunRecord | None,
        generation: SkillRawMaterialGeneration | None,
        job: RuntimeJob | None,
    ) -> str:
        if generation and generation.status in {"succeeded", "failed"}:
            return generation.status
        if job and job.status in {"succeeded", "failed", "cancelled", "canceled", "deadletter", "dead_letter"}:
            return job.status
        if record and record.status == "failed":
            return record.status
        if generation and generation.status:
            return generation.status
        if job:
            return job.status
        if record:
            return record.status
        return "pending"

    @staticmethod
    def _generation_progress(generation: SkillRawMaterialGeneration | None) -> RuntimeJobProgressResponse | None:
        if generation is None:
            return None
        labels = {
            "pending": "等待生成",
            "running": "构建智能体生成中",
            "succeeded": "生成完成",
            "failed": "生成失败",
        }
        return RuntimeJobProgressResponse(
            percent=100 if generation.status in {"succeeded", "failed"} else 0,
            current_stage=generation.status,
            label=labels.get(generation.status, generation.status),
            detail=generation.error_message or "",
        )

    @staticmethod
    def _elapsed_ms(started_at: datetime | None) -> int | None:
        if not started_at:
            return None
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=timezone.utc)
        return max(0, int((now_utc() - started_at).total_seconds() * 1000))

    def _job_steps(self, job: RuntimeJob | None) -> list[AgentRunStepResponse]:
        if job is None:
            return []
        stages = [stage for stage in (job.payload or {}).get("progress_stages", []) if isinstance(stage, dict)]
        return [
            AgentRunStepResponse(
                key=f"job:{stage.get('key') or index}",
                title=str(stage.get("label") or stage.get("key") or "Agent 阶段"),
                status=self._normalize_step_status(str(stage.get("status") or "pending")),
                detail=str(stage.get("message") or ""),
                event_type="job.stage",
            )
            for index, stage in enumerate(stages)
        ]

    def _event_steps(self, events: list[AgentEventRecord]) -> list[AgentRunStepResponse]:
        steps: list[AgentRunStepResponse] = []
        for event in events:
            step = self._event_step(event)
            if step is not None:
                steps.append(step)
        return steps

    def _event_step(self, event: AgentEventRecord) -> AgentRunStepResponse | None:
        payload = event.payload or {}
        title = self._event_title(event.event_type, payload)
        if not title:
            return None
        return AgentRunStepResponse(
            key=f"event:{event.seq_no}",
            title=title,
            status=self._event_status(event.event_type, payload),
            detail=self._event_detail(event.event_type, payload),
            event_type=event.event_type,
            occurred_at=event.occurred_at,
            duration_ms=self._safe_int(payload.get("duration_ms")),
            metadata=self._event_metadata(event.event_type, payload),
        )

    @staticmethod
    def _event_title(event_type: str, payload: dict[str, Any]) -> str:
        if event_type == "agent.validation.failed":
            return "候选产物校验失败"
        if event_type == "agent.memory.read":
            return "读取 memory"
        if event_type == "agent.skill.loaded":
            return f"加载 Skill：{payload.get('skill_name') or ''}".rstrip("：")
        if event_type in {"agent.tool.started", "agent.tool.completed", "agent.tool.failed"}:
            return _tool_title(str(payload.get("tool_name") or ""))
        if event_type == "agent.tool.standard_search":
            return "检索行业标准"
        if event_type == "agent.artifact.created":
            return _artifact_title(str(payload.get("artifact_type") or ""))
        if event_type == "agent.token.usage":
            return "统计 token usage"
        if event_type == "agent.run.started":
            return "启动 Agent Run"
        if event_type == "agent.run.completed":
            return "完成 Agent Run"
        if event_type == "agent.run.failed":
            return "Agent Run 失败"
        if event_type == "agent.required_artifact.missing":
            return "补交必需产物"
        return ""

    @staticmethod
    def _event_status(event_type: str, payload: dict[str, Any]) -> str:
        if event_type.endswith(".failed") or payload.get("result_status") == "error":
            return "failed"
        if event_type == "agent.tool.started":
            return "running"
        if event_type in {"agent.token.usage", "agent.tool.standard_search"}:
            return "info"
        return "succeeded"

    @staticmethod
    def _event_detail(event_type: str, payload: dict[str, Any]) -> str:
        if event_type in {"agent.tool.started", "agent.tool.completed", "agent.tool.failed"}:
            tool_name = str(payload.get("tool_name") or "")
            if payload.get("result_message"):
                return str(payload.get("result_message"))
            if payload.get("error"):
                return str(payload.get("error"))
            return tool_name
        if event_type == "agent.validation.failed":
            return str(payload.get("error") or "请按诊断修复 candidate 后重试。")
        if event_type == "agent.memory.read":
            scope = str(payload.get("scope") or "")
            keys = payload.get("keys") if isinstance(payload.get("keys"), list) else []
            return f"{scope}：{len(keys)} 个键" if scope else f"{len(keys)} 个键"
        if event_type == "agent.tool.standard_search":
            status = str(payload.get("status") or "")
            count = payload.get("result_count") or 0
            return f"{status}，{count} 条结果" if status else f"{count} 条结果"
        if event_type == "agent.artifact.created":
            return str(payload.get("artifact_ref") or "")
        if event_type == "agent.token.usage":
            total = payload.get("total") if isinstance(payload.get("total"), dict) else {}
            total_tokens = total.get("total_tokens") if isinstance(total, dict) else None
            return f"{total_tokens} tokens" if total_tokens is not None else ""
        if event_type == "agent.required_artifact.missing":
            return str(payload.get("artifact_ref") or "")
        if payload.get("error"):
            return str(payload.get("error"))
        return ""

    @staticmethod
    def _event_metadata(event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        if event_type in {"agent.tool.started", "agent.tool.completed", "agent.tool.failed"}:
            return {
                "tool_name": str(payload.get("tool_name") or ""),
                "result_status": str(payload.get("result_status") or ""),
                "result_type": str(payload.get("result_type") or ""),
            }
        if event_type == "agent.validation.failed":
            return {
                "attempt": payload.get("attempt") or 0,
                "validation_stage": str(payload.get("validation_stage") or ""),
                "diagnostics": payload.get("diagnostics") if isinstance(payload.get("diagnostics"), list) else [],
            }
        if event_type == "agent.tool.standard_search":
            return {
                "result_count": payload.get("result_count") or 0,
                "standard_refs": payload.get("standard_refs") if isinstance(payload.get("standard_refs"), list) else [],
                "error_type": str(payload.get("error_type") or ""),
            }
        if event_type == "agent.artifact.created":
            return {
                "artifact_type": str(payload.get("artifact_type") or ""),
                "file_count": payload.get("file_count") or 0,
            }
        if event_type == "agent.token.usage":
            total = payload.get("total") if isinstance(payload.get("total"), dict) else {}
            return {key: total.get(key) for key in ("input_tokens", "output_tokens", "total_tokens")}
        return {}

    @staticmethod
    def _validation_diagnostics(events: list[AgentEventRecord]) -> list[dict[str, Any]]:
        for event in reversed(events):
            if event.event_type != "agent.validation.failed":
                continue
            raw = (event.payload or {}).get("diagnostics")
            if not isinstance(raw, list):
                continue
            return [item for item in raw if isinstance(item, dict)][:8]
        return []

    @staticmethod
    def _normalize_step_status(value: str) -> str:
        if value in {"pending", "running", "succeeded", "failed"}:
            return value
        if value in {"cancelled", "canceled", "deadletter", "dead_letter"}:
            return "failed"
        return "info"

    def _token_usage_from_events(self, events: list[AgentEventRecord]) -> RuntimeJobTokenUsageResponse | None:
        total: dict[str, Any] | None = None
        llm_calls = 0
        for event in events:
            if event.event_type != "agent.token.usage":
                continue
            llm_calls += 1
            payload_total = event.payload.get("total") if isinstance(event.payload, dict) else None
            if isinstance(payload_total, dict):
                total = payload_total
        if not total:
            return None
        return RuntimeJobTokenUsageResponse(
            input_tokens=self._safe_int(total.get("input_tokens")),
            output_tokens=self._safe_int(total.get("output_tokens")),
            total_tokens=self._safe_int(total.get("total_tokens")),
            llm_calls=llm_calls,
        )

    @staticmethod
    def _final_for_generation(generation: SkillRawMaterialGeneration | None) -> AgentRunFinalResponse:
        if generation is None:
            return AgentRunFinalResponse()
        metadata = generation.prompt_metadata or {}
        return AgentRunFinalResponse(
            generation_reason=generation.generation_reason or "",
            review_notes=[str(item) for item in (generation.review_notes or [])],
            generated_file_paths=sorted(str(path) for path in dict(generation.generated_files or {}).keys()),
            reference_files=[str(item) for item in (metadata.get("reference_files") or [])],
            committed_commit_sha=generation.committed_commit_sha or "",
            standard_search_summary=metadata.get("standard_search_summary") if isinstance(metadata.get("standard_search_summary"), dict) else {},
        )

    @staticmethod
    def _safe_int(value: Any) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None


def _tool_title(tool_name: str) -> str:
    labels = {
        "load_skill": "加载 Agent Skill",
        "load_skill_resource": "加载 Agent Skill 资源",
        "psop.builder.read_current_source": "读取当前源码",
        "psop.builder.list_materials": "列出素材",
        "psop.builder.read_material_analysis": "读取素材解析",
        "psop.builder.list_reference_assets": "列出参考资产",
        "psop.standard.search": "检索行业标准",
        "psop.builder.submit_candidate": "提交 Skill candidate",
        "workspace.write_text": "写入 workspace 中间产物",
        "workspace.read_text": "读取 workspace 中间产物",
        "workspace.list": "列出 workspace",
    }
    return labels.get(tool_name, tool_name or "调用工具")


def _artifact_title(artifact_type: str) -> str:
    labels = {
        "skill_draft_candidate": "创建 builder-result.json",
        "skill_draft_files": "物化 Skill draft 文件",
    }
    return labels.get(artifact_type, f"创建产物：{artifact_type}" if artifact_type else "创建产物")
