from __future__ import annotations

import math
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.domain.jobs.models import RuntimeJob, TERMINAL_JOB_STATUSES
from app.domain.jobs.repository import JobRepository
from app.domain.jobs.schemas import (
    RuntimeJobProgressResponse,
    RuntimeJobResponse,
    RuntimeJobStatsResponse,
    RuntimeJobTokenUsageResponse,
)
from app.domain.runtime.models import Run
from app.domain.skill_tests.models import SkillTestScenarioRun
from app.domain.skills.models import SkillRawMaterialAnalysis, SkillRawMaterialGeneration, now_utc


class JobQueryService:
    """Read model for the platform task page backed by runtime_job."""

    def __init__(self, *, repository: JobRepository | None = None) -> None:
        self.repository = repository or JobRepository()

    def list_runtime_jobs(
        self,
        session: Session,
        *,
        status: str | None = None,
        job_type: str | None = None,
        q: str | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[RuntimeJobResponse]:
        now = now_utc()
        jobs = self.repository.list_runtime_jobs(
            session,
            status=status,
            job_type=job_type,
            q=q,
            created_from=created_from,
            created_to=created_to,
            limit=limit,
            offset=offset,
        )
        return [self._build_job_response(session, job, now=now) for job in jobs]

    def get_runtime_job_stats(self, session: Session, *, window_hours: int = 24) -> RuntimeJobStatsResponse:
        now = now_utc()
        created_from = now - timedelta(hours=window_hours)
        jobs = self.repository.list_runtime_jobs(
            session,
            created_from=created_from,
            limit=None,
            offset=None,
        )
        status_counts = Counter(job.status for job in jobs)
        type_counts = Counter(job.job_type for job in jobs)

        durations = [
            duration
            for duration in (self._duration_ms(job, now=now, completed_only=True) for job in jobs)
            if duration is not None
        ]
        durations.sort()
        terminal_attempts = (
            self._status_count(status_counts, "succeeded")
            + self._failed_count(status_counts)
            + self._cancelled_count(status_counts)
        )
        success_rate = None
        if terminal_attempts:
            success_rate = round(self._status_count(status_counts, "succeeded") / terminal_attempts, 4)

        return RuntimeJobStatsResponse(
            window_hours=window_hours,
            total=len(jobs),
            pending=self._pending_count(status_counts),
            running=self._status_count(status_counts, "running"),
            succeeded=self._status_count(status_counts, "succeeded"),
            failed=self._failed_count(status_counts),
            cancelled=self._cancelled_count(status_counts),
            success_rate=success_rate,
            avg_duration_ms=round(sum(durations) / len(durations)) if durations else None,
            p95_duration_ms=self._percentile(durations, 95),
            max_duration_ms=max(durations) if durations else None,
            token_usage=self._sum_token_usage(jobs),
            by_status=dict(status_counts),
            by_type=dict(type_counts),
        )

    def _build_job_response(self, session: Session, job: RuntimeJob, *, now: datetime) -> RuntimeJobResponse:
        return RuntimeJobResponse(
            id=job.id,
            job_type=job.job_type,
            status=job.status,
            payload=dict(job.payload or {}),
            dedupe_key=job.dedupe_key,
            run_id=job.run_id,
            compile_request_id=job.compile_request_id,
            worker_name=job.worker_name or "",
            metrics=dict(job.metrics or {}),
            progress=self._progress(session, job),
            token_usage=self._token_usage(job),
            duration_ms=self._duration_ms(job, now=now),
            elapsed_ms=self._elapsed_ms(job.created_at, now=now),
            lease_until=job.lease_until,
            available_at=job.available_at,
            attempt_no=job.attempt_no,
            max_attempts=job.max_attempts,
            last_error=job.last_error or "",
            started_at=job.started_at,
            finished_at=job.finished_at,
            created_at=job.created_at,
            updated_at=job.updated_at,
        )

    def _progress(self, session: Session, job: RuntimeJob) -> RuntimeJobProgressResponse:
        if job.job_type == "compile":
            return self._compile_progress(job)
        if job.job_type == "runtime":
            return self._runtime_progress(session, job)
        if job.job_type == "skill_test_timeline_driver":
            return self._skill_test_progress(session, job)
        if job.job_type == "raw_material_analysis":
            return self._raw_material_analysis_progress(session, job)
        if job.job_type == "skill_raw_material_generation":
            return self._skill_generation_progress(session, job)
        return self._fallback_progress(job)

    def _compile_progress(self, job: RuntimeJob) -> RuntimeJobProgressResponse:
        payload = job.payload or {}
        stages = [stage for stage in payload.get("progress_stages", []) if isinstance(stage, dict)]
        if not stages:
            return self._fallback_progress(job)
        total = len(stages)
        completed = sum(1 for stage in stages if stage.get("status") == "succeeded")
        active = next(
            (
                stage
                for stage in stages
                if stage.get("key") == payload.get("current_stage")
                or stage.get("status") in {"running", "failed"}
            ),
            stages[min(completed, total - 1)],
        )
        if job.status == "succeeded":
            percent = 100
        else:
            running_credit = 0.5 if active.get("status") == "running" else 0
            percent = int(round(((completed + running_credit) / total) * 100))
            percent = max(0, min(100, percent))
        return RuntimeJobProgressResponse(
            percent=percent,
            current_stage=str(active.get("key") or ""),
            label=str(active.get("label") or self._fallback_label(job)),
            detail=str(active.get("message") or payload.get("error_message") or job.last_error or ""),
        )

    def _runtime_progress(self, session: Session, job: RuntimeJob) -> RuntimeJobProgressResponse:
        run = session.get(Run, job.run_id) if job.run_id else None
        if not run:
            return self._fallback_progress(job)
        if run.status in {"succeeded", "failed", "cancelled"} or job.status in TERMINAL_JOB_STATUSES:
            percent = 100
        elif run.status in {"queued", "waiting_runtime"}:
            percent = 0
        elif run.status == "waiting_input":
            percent = 80
        else:
            percent = min(95, 30 + max(0, run.latest_snapshot_seq) * 5 + max(0, run.latest_trace_seq))
        detail_parts = [
            f"run={run.status}",
            f"phase={run.runtime_phase or '-'}",
            f"snapshot={run.latest_snapshot_seq}",
            f"trace={run.latest_trace_seq}",
        ]
        return RuntimeJobProgressResponse(
            percent=percent,
            current_stage=run.runtime_phase or run.status,
            label=self._runtime_label(run.status),
            detail=" / ".join(detail_parts),
        )

    def _skill_test_progress(self, session: Session, job: RuntimeJob) -> RuntimeJobProgressResponse:
        scenario_run_id = str((job.payload or {}).get("scenario_run_id") or "")
        scenario_run = session.get(SkillTestScenarioRun, scenario_run_id) if scenario_run_id else None
        if not scenario_run:
            return self._fallback_progress(job)
        input_events = self._timeline_input_events(scenario_run.timeline)
        total = len(input_events)
        cursor = max(0, min(int(scenario_run.driver_cursor or 0), total))
        terminal = scenario_run.driver_status in {"completed", "failed", "cancelled"} or job.status in TERMINAL_JOB_STATUSES
        percent = 100 if terminal else (int(round((cursor / total) * 100)) if total else self._fallback_percent(job.status))
        return RuntimeJobProgressResponse(
            percent=percent,
            current_stage=scenario_run.driver_status or scenario_run.status,
            label=self._skill_test_label(scenario_run.driver_status),
            detail=f"{cursor}/{total} input events" if total else "",
        )

    def _raw_material_analysis_progress(self, session: Session, job: RuntimeJob) -> RuntimeJobProgressResponse:
        analysis_id = str((job.payload or {}).get("analysis_id") or "")
        analysis = session.get(SkillRawMaterialAnalysis, analysis_id) if analysis_id else None
        if not analysis:
            return self._fallback_progress(job)
        percent_by_status = {
            "pending": 0,
            "running": 50,
            "processing": 50,
            "ready": 100,
            "succeeded": 100,
            "failed": 100,
            "cancelled": 100,
        }
        label_by_status = {
            "pending": "等待分析",
            "running": "素材分析中",
            "processing": "素材分析中",
            "ready": "分析完成",
            "succeeded": "分析完成",
            "failed": "分析失败",
            "cancelled": "已取消",
        }
        return RuntimeJobProgressResponse(
            percent=percent_by_status.get(analysis.status, self._fallback_percent(job.status)),
            current_stage=analysis.status,
            label=label_by_status.get(analysis.status, self._fallback_label(job)),
            detail=analysis.error_message or job.last_error or "",
        )

    def _skill_generation_progress(self, session: Session, job: RuntimeJob) -> RuntimeJobProgressResponse:
        generation_id = str((job.payload or {}).get("generation_id") or "")
        generation = session.get(SkillRawMaterialGeneration, generation_id) if generation_id else None
        stages = [stage for stage in (job.payload or {}).get("progress_stages", []) if isinstance(stage, dict)]
        current_stage = str((job.payload or {}).get("current_stage") or (generation.status if generation else job.status))
        if stages:
            total = len(stages)
            completed = sum(1 for stage in stages if stage.get("status") == "succeeded")
            active = next((stage for stage in stages if stage.get("key") == current_stage), stages[min(completed, total - 1)])
            if job.status in TERMINAL_JOB_STATUSES or (generation and generation.status in {"succeeded", "failed"}):
                percent = 100
            else:
                running_credit = 0.5 if active.get("status") == "running" else 0
                percent = int(round(((completed + running_credit) / total) * 100))
                percent = max(0, min(95, percent))
            return RuntimeJobProgressResponse(
                percent=percent,
                current_stage=current_stage,
                label=str(active.get("label") or self._skill_generation_label(current_stage, job.status)),
                detail=(generation.error_message if generation else "") or job.last_error or str(active.get("message") or ""),
            )
        return RuntimeJobProgressResponse(
            percent=100 if job.status in TERMINAL_JOB_STATUSES else self._fallback_percent(job.status),
            current_stage=current_stage,
            label=self._skill_generation_label(current_stage, job.status),
            detail=(generation.error_message if generation else "") or job.last_error or "",
        )

    def _fallback_progress(self, job: RuntimeJob) -> RuntimeJobProgressResponse:
        return RuntimeJobProgressResponse(
            percent=self._fallback_percent(job.status),
            current_stage=job.status,
            label=self._fallback_label(job),
            detail=str((job.payload or {}).get("operation") or job.last_error or ""),
        )

    @staticmethod
    def _timeline_input_events(timeline: dict[str, Any] | None) -> list[dict[str, Any]]:
        events = (timeline or {}).get("events")
        if not isinstance(events, list):
            return []
        return [
            event
            for event in events
            if isinstance(event, dict)
            and (
                str(event.get("direction") or "").lower() == "input"
                or str(event.get("lane_id") or "").startswith("input")
                or str(event.get("event_kind") or "").endswith(".input.v1")
            )
        ]

    @staticmethod
    def _duration_ms(job: RuntimeJob, *, now: datetime, completed_only: bool = False) -> int | None:
        if not job.started_at:
            return None
        if completed_only and not job.finished_at:
            return None
        started_at = JobQueryService._aware_datetime(job.started_at)
        end = JobQueryService._aware_datetime(job.finished_at) if job.finished_at else now
        return max(0, int((end - started_at).total_seconds() * 1000))

    @staticmethod
    def _elapsed_ms(created_at: datetime | None, *, now: datetime) -> int | None:
        if not created_at:
            return None
        return max(0, int((now - JobQueryService._aware_datetime(created_at)).total_seconds() * 1000))

    @staticmethod
    def _aware_datetime(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value

    @staticmethod
    def _token_usage(job: RuntimeJob) -> RuntimeJobTokenUsageResponse | None:
        metrics = job.metrics or {}
        values: dict[str, int | None] = {}
        has_value = False
        for key in ("input_tokens", "output_tokens", "total_tokens", "llm_calls"):
            value = metrics.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                values[key] = value
                if key != "llm_calls":
                    has_value = True
            else:
                values[key] = None
        if not has_value:
            return None
        return RuntimeJobTokenUsageResponse(**values)

    def _sum_token_usage(self, jobs: list[RuntimeJob]) -> RuntimeJobTokenUsageResponse | None:
        totals = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "llm_calls": 0}
        has_value = False
        for job in jobs:
            usage = self._token_usage(job)
            if not usage:
                continue
            for key in totals:
                value = getattr(usage, key)
                if value is not None:
                    totals[key] += value
                    if key != "llm_calls":
                        has_value = True
        return RuntimeJobTokenUsageResponse(**totals) if has_value else None

    @staticmethod
    def _percentile(values: list[int], percentile: int) -> int | None:
        if not values:
            return None
        index = max(0, min(len(values) - 1, math.ceil(len(values) * percentile / 100) - 1))
        return values[index]

    @staticmethod
    def _status_count(counts: Counter[str], status: str) -> int:
        return int(counts.get(status, 0))

    @staticmethod
    def _pending_count(counts: Counter[str]) -> int:
        return int(sum(counts.get(status, 0) for status in ("pending", "retryable_failed")))

    @staticmethod
    def _failed_count(counts: Counter[str]) -> int:
        return int(sum(counts.get(status, 0) for status in ("failed", "deadletter", "dead_letter")))

    @staticmethod
    def _cancelled_count(counts: Counter[str]) -> int:
        return int(sum(counts.get(status, 0) for status in ("cancelled", "canceled")))

    @staticmethod
    def _fallback_percent(status: str) -> int:
        if status in {"succeeded", "failed", "cancelled", "canceled", "deadletter", "dead_letter"}:
            return 100
        if status == "running":
            return 50
        return 0

    @staticmethod
    def _fallback_label(job: RuntimeJob) -> str:
        labels = {
            "pending": "等待执行",
            "retryable_failed": "等待重试",
            "running": "执行中",
            "succeeded": "已成功",
            "failed": "已失败",
            "cancelled": "已取消",
            "canceled": "已取消",
            "deadletter": "已进入死信",
            "dead_letter": "已进入死信",
        }
        return labels.get(job.status, job.status)

    @staticmethod
    def _runtime_label(status: str) -> str:
        labels = {
            "queued": "等待运行",
            "waiting_runtime": "等待运行",
            "running": "运行中",
            "waiting_input": "等待输入",
            "succeeded": "运行完成",
            "failed": "运行失败",
            "cancelled": "已取消",
        }
        return labels.get(status, status)

    @staticmethod
    def _skill_test_label(status: str) -> str:
        labels = {
            "pending": "等待驱动",
            "running": "驱动中",
            "waiting_time": "等待时间点",
            "completed": "驱动完成",
            "failed": "驱动失败",
            "cancelled": "已取消",
        }
        return labels.get(status, status)

    @staticmethod
    def _skill_generation_label(stage: str, status: str) -> str:
        if status == "failed" or stage == "failed":
            return "生成失败"
        labels = {
            "queued": "等待生成",
            "loading_source": "读取素材与源码",
            "calling_model": "构建智能体生成中",
            "resolving_references": "整理参考图片",
            "committing_source": "提交源码草稿",
            "succeeded": "生成完成",
        }
        return labels.get(stage, "Skill 生成")
