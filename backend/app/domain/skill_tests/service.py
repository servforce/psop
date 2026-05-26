from __future__ import annotations

import hashlib
import json
import posixpath
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import isfinite
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.domain.agent_prompts.service import AgentPromptService
from app.domain.compiler.models import ArtifactObject
from app.domain.jobs.models import RuntimeJob
from app.domain.jobs.repository import JobRepository
from app.domain.runtime.models import Run
from app.domain.runtime.schemas import AppendTerminalEventRequest, CreateInvocationRequest, InvocationResponse
from app.domain.runtime.service import RuntimeService
from app.domain.skill_tests.models import (
    SkillTestAsset,
    SkillTestExpectationEvaluation,
    SkillTestScenario,
    SkillTestScenarioRun,
)
from app.domain.skill_tests.repository import SkillTestRepository
from app.domain.skill_tests.schemas import (
    DeleteSkillTestAssetResponse,
    ForkSkillDebugRequest,
    ForkSkillTestScenarioRequest,
    SkillTestAssetResponse,
    SkillTestExpectationEvaluationResponse,
    SkillTestScenarioCreateRequest,
    SkillTestScenarioResponse,
    SkillTestScenarioReviewResponse,
    SkillTestScenarioRunResponse,
    SkillTestScenarioRunSummary,
    SkillTestStageActualOutputResponse,
    SkillTestStageHumanReviewResponse,
    SkillTestStageJudgeResultResponse,
    SkillTestStageOutputResponse,
    SkillTestScenarioUpdateRequest,
    StartSkillTestScenarioRunRequest,
)
from app.domain.skills.exceptions import SkillConflictError, SkillNotFoundError, SkillValidationError
from app.domain.skills.models import SkillDefinition, now_utc
from app.gateway.inference import LlmInferenceGateway
from app.infra.object_store import ObjectStoreService


TIMELINE_SCHEMA_VERSION = "psop-skill-test-timeline/v1"
TIMELINE_DRIVER_JOB_TYPE = "skill_test_timeline_driver"
DEFAULT_TIMELINE_DURATION_MS = 1_800_000
OPEN_SCENARIO_RUN_STATUSES = {"pending", "queued", "running", "waiting_input"}
TERMINAL_RUNTIME_STATUSES = {"succeeded", "failed", "cancelled"}
DEFAULT_JUDGE_TRANSCRIPT_BUDGET_CHARS = 60_000
DEFAULT_JUDGE_EVENT_BUDGET_CHARS = 8_000
DEFAULT_JUDGE_FINAL_OUTPUT_BUDGET_CHARS = 8_000


@dataclass(frozen=True, slots=True)
class SkillTestAssetContent:
    content: bytes
    mime_type: str
    filename: str


DEFAULT_TIMELINE_LANES = [
    {"id": "sensor.gps", "kind": "input", "label": "GPS", "event_kind": "sensor.gps.reading.v1", "mime_type": "application/json"},
    {
        "id": "sensor.pose3d",
        "kind": "input",
        "label": "三轴定位",
        "event_kind": "sensor.pose3d.reading.v1",
        "mime_type": "application/json",
    },
    {"id": "input.text", "kind": "input", "label": "文本", "event_kind": "terminal.text.input.v1"},
    {"id": "input.image", "kind": "input", "label": "图片", "event_kind": "terminal.image.input.v1"},
    {"id": "input.audio", "kind": "input", "label": "音频", "event_kind": "terminal.audio.input.v1"},
    {"id": "input.video", "kind": "input", "label": "视频", "event_kind": "terminal.video.input.v1"},
    {"id": "expected.semantic", "kind": "output", "label": "文本"},
]
SENSOR_LANE_REQUIRED_FIELDS = {
    "sensor.gps": ("latitude", "longitude"),
    "sensor.pose3d": ("x", "y", "z"),
}
SENSOR_LANE_NUMERIC_FIELDS = {
    "sensor.gps": ("latitude", "longitude", "altitude", "accuracy_m"),
    "sensor.pose3d": ("x", "y", "z", "roll", "pitch", "yaw"),
}


class SkillTestService:
    def __init__(
        self,
        *,
        settings: Settings,
        inference_gateway: LlmInferenceGateway,
        object_store: ObjectStoreService,
        repository: SkillTestRepository | None = None,
        runtime_service: RuntimeService | None = None,
        job_repository: JobRepository | None = None,
        agent_prompt_service: AgentPromptService | None = None,
    ) -> None:
        self.settings = settings
        self.inference_gateway = inference_gateway
        self.object_store = object_store
        self.repository = repository or SkillTestRepository()
        self.runtime_service = runtime_service or RuntimeService(settings=settings, inference_gateway=inference_gateway)
        self.job_repository = job_repository or JobRepository()
        self.agent_prompt_service = agent_prompt_service or AgentPromptService()

    def list_scenarios(self, session: Session, skill_id: str) -> list[SkillTestScenarioResponse]:
        self._get_skill(session, skill_id)
        scenarios = self.repository.list_scenarios(session, skill_id)
        return [self._build_scenario_response(session, item) for item in scenarios]

    def create_scenario(
        self,
        session: Session,
        skill_id: str,
        payload: SkillTestScenarioCreateRequest,
    ) -> SkillTestScenarioResponse:
        self._get_skill(session, skill_id)
        self._validate_target_artifact(session, skill_id, payload.target_compile_artifact_id)
        timeline = self._normalize_timeline(payload.timeline, duration_ms=payload.duration_ms)
        scenario = SkillTestScenario(
            skill_definition_id=skill_id,
            target_compile_artifact_id=payload.target_compile_artifact_id,
            name=self._normalize_name(payload.name, field="name"),
            description=payload.description or "",
            target_version_selector=payload.target_version_selector or "latest",
            duration_ms=timeline["duration_ms"],
            timeline=timeline,
            judge_policy=self._normalize_judge_policy(payload.judge_policy),
            fork_seed=payload.fork_seed or {},
            status="active",
        )
        session.add(scenario)
        session.commit()
        return self._build_scenario_response(session, scenario)

    def get_scenario(self, session: Session, skill_id: str, scenario_id: str) -> SkillTestScenarioResponse:
        scenario = self._get_scenario(session, skill_id, scenario_id)
        return self._build_scenario_response(session, scenario)

    def update_scenario(
        self,
        session: Session,
        skill_id: str,
        scenario_id: str,
        payload: SkillTestScenarioUpdateRequest,
    ) -> SkillTestScenarioResponse:
        scenario = self._get_scenario(session, skill_id, scenario_id)
        if "target_compile_artifact_id" in payload.model_fields_set:
            self._validate_target_artifact(session, skill_id, payload.target_compile_artifact_id)
            scenario.target_compile_artifact_id = payload.target_compile_artifact_id
        if payload.name is not None:
            scenario.name = self._normalize_name(payload.name, field="name")
        if payload.description is not None:
            scenario.description = payload.description
        if payload.target_version_selector is not None:
            scenario.target_version_selector = payload.target_version_selector or "latest"
        if payload.duration_ms is not None:
            scenario.duration_ms = payload.duration_ms
        if payload.timeline is not None:
            scenario.timeline = self._normalize_timeline(payload.timeline, duration_ms=scenario.duration_ms)
            scenario.duration_ms = scenario.timeline["duration_ms"]
        if payload.judge_policy is not None:
            scenario.judge_policy = self._normalize_judge_policy(payload.judge_policy)
        if payload.fork_seed is not None:
            scenario.fork_seed = payload.fork_seed
        if payload.status is not None:
            if payload.status not in {"active", "archived"}:
                raise SkillValidationError("测试场景状态无效。", details={"status": payload.status})
            scenario.status = payload.status
        session.commit()
        return self._build_scenario_response(session, scenario)

    def delete_scenario(self, session: Session, skill_id: str, scenario_id: str) -> SkillTestScenarioResponse:
        scenario = self._get_scenario(session, skill_id, scenario_id)
        scenario.status = "archived"
        session.commit()
        return self._build_scenario_response(session, scenario)

    def upload_asset(
        self,
        session: Session,
        skill_id: str,
        scenario_id: str,
        *,
        filename: str,
        content: bytes,
        mime_type: str,
        name: str | None = None,
        description: str = "",
        lane_id: str = "input.file",
    ) -> SkillTestAssetResponse:
        scenario = self._get_scenario(session, skill_id, scenario_id)
        self._validate_upload(filename=filename, content=content, mime_type=mime_type)
        safe_filename = self._safe_filename(filename)
        object_key = posixpath.join("skill-tests", "scenarios", skill_id, scenario.id, f"{uuid.uuid4()}-{safe_filename}")
        stored = self.object_store.upload_bytes(
            object_key=object_key,
            content=content,
            media_type=mime_type,
            metadata={
                "skill_id": skill_id,
                "test_scenario_id": scenario.id,
                "filename": safe_filename,
                "lane_id": lane_id or "input.file",
            },
        )
        artifact_object = ArtifactObject(
            bucket=stored.bucket,
            object_key=stored.object_key,
            media_type=stored.media_type,
            size_bytes=stored.size_bytes,
            checksum=stored.checksum,
            content_json={
                "kind": "skill_test_asset",
                "filename": safe_filename,
                "name": name or safe_filename,
                "description": description,
                "lane_id": lane_id or "input.file",
                "metadata": stored.metadata,
            },
        )
        session.add(artifact_object)
        session.flush()
        asset = SkillTestAsset(
            skill_definition_id=skill_id,
            scenario_id=scenario.id,
            artifact_object_id=artifact_object.id,
            name=name or safe_filename,
            description=description or "",
            lane_id=lane_id or "input.file",
            filename=safe_filename,
            mime_type=stored.media_type,
            size_bytes=stored.size_bytes,
            checksum=stored.checksum,
        )
        session.add(asset)
        session.commit()
        return self._build_asset_response(asset)

    def list_assets(self, session: Session, skill_id: str, scenario_id: str) -> list[SkillTestAssetResponse]:
        self._get_scenario(session, skill_id, scenario_id)
        return [self._build_asset_response(item) for item in self.repository.list_assets(session, scenario_id)]

    def get_asset_content(
        self,
        session: Session,
        skill_id: str,
        scenario_id: str,
        asset_id: str,
    ) -> SkillTestAssetContent:
        scenario = self._get_scenario(session, skill_id, scenario_id)
        asset = self.repository.get_asset(session, asset_id)
        if not asset or asset.scenario_id != scenario.id:
            raise SkillNotFoundError("未找到测试资源。", details={"asset_id": asset_id})
        artifact_object = self.repository.get_artifact_object(session, asset.artifact_object_id)
        if not artifact_object:
            raise SkillNotFoundError("未找到测试资源对象。", details={"artifact_object_id": asset.artifact_object_id})
        content = self.object_store.download_bytes(bucket=artifact_object.bucket, object_key=artifact_object.object_key)
        return SkillTestAssetContent(
            content=content,
            mime_type=asset.mime_type or artifact_object.media_type or "application/octet-stream",
            filename=asset.filename or "asset.bin",
        )

    def delete_asset(
        self,
        session: Session,
        skill_id: str,
        scenario_id: str,
        asset_id: str,
    ) -> DeleteSkillTestAssetResponse:
        self._get_scenario(session, skill_id, scenario_id)
        asset = self.repository.get_asset(session, asset_id)
        if not asset or asset.scenario_id != scenario_id:
            raise SkillNotFoundError("未找到测试资源。", details={"asset_id": asset_id})
        session.delete(asset)
        session.commit()
        return DeleteSkillTestAssetResponse(deleted=True, asset_id=asset_id)

    def start_run(
        self,
        session: Session,
        skill_id: str,
        scenario_id: str,
        payload: StartSkillTestScenarioRunRequest,
    ) -> SkillTestScenarioRunResponse:
        skill = self._get_skill(session, skill_id)
        scenario = self._get_scenario(session, skill_id, scenario_id)
        open_run = self._get_open_scenario_run(session, scenario)
        if open_run:
            raise SkillConflictError(
                "当前测试场景已有进行中运行。",
                details={"scenario_run_id": open_run.id, "run_id": open_run.run_id, "status": open_run.status},
            )

        timeline = self._normalize_timeline(payload.timeline_override or scenario.timeline, duration_ms=scenario.duration_ms)
        started_at = now_utc()
        scenario_run = SkillTestScenarioRun(
            skill_definition_id=skill_id,
            scenario_id=scenario.id,
            status="running",
            driver_status="pending",
            driver_cursor=0,
            driver_events=[],
            timeline=timeline,
            result_summary=self._initial_result_summary(timeline),
            time_origin=started_at,
            started_at=started_at,
        )
        session.add(scenario_run)
        session.flush()

        terminal_context = self._build_run_terminal_context(
            skill_id=skill_id,
            scenario=scenario,
            scenario_run=scenario_run,
            override=payload.terminal_context_override,
        )
        if scenario.fork_seed:
            invocation = self._start_forked_invocation(session, scenario=scenario, scenario_run=scenario_run, terminal_context=terminal_context)
        else:
            invocation = self.runtime_service.create_invocation(
                session,
                CreateInvocationRequest(
                    skill_key=skill.key,
                    version_selector=scenario.target_version_selector or "latest",
                    compile_artifact_id=scenario.target_compile_artifact_id,
                    input_envelope={},
                    gateway_type="terminal",
                    terminal_context=terminal_context,
                ),
            )
        scenario_run.invocation_id = invocation.id
        scenario_run.run_id = invocation.run_id
        self._sync_scenario_run_from_runtime(session, scenario_run)
        driver_job = self._ensure_driver_job_pending(session, scenario_run, available_at=started_at)
        session.commit()
        if not self.settings.runtime_worker_enabled:
            return self.process_driver_job(session, driver_job.id)
        return self._build_run_response(scenario_run)

    def list_runs(self, session: Session, skill_id: str, scenario_id: str) -> list[SkillTestScenarioRunResponse]:
        self._get_scenario(session, skill_id, scenario_id)
        runs = self.repository.list_runs(session, scenario_id)
        for item in runs:
            self._sync_scenario_run_from_runtime(session, item)
        session.commit()
        return [self._build_run_response(item) for item in runs]

    def get_run(self, session: Session, scenario_run_id: str) -> SkillTestScenarioRunResponse:
        scenario_run = self._get_scenario_run(session, scenario_run_id)
        self._sync_scenario_run_from_runtime(session, scenario_run)
        session.commit()
        return self._build_run_response(scenario_run)

    def cancel_run(self, session: Session, scenario_run_id: str, *, reason: str = "cancelled by user") -> SkillTestScenarioRunResponse:
        scenario_run = self._get_scenario_run(session, scenario_run_id)
        self._sync_scenario_run_from_runtime(session, scenario_run)
        if scenario_run.status in {"passed", "failed"}:
            raise SkillValidationError("测试运行已结束，不能终止。", details={"scenario_run_id": scenario_run_id, "status": scenario_run.status})
        if scenario_run.status == "cancelled":
            self._cancel_driver_job(session, scenario_run, reason=reason)
            session.commit()
            return self._build_run_response(scenario_run)

        if scenario_run.run_id:
            self.runtime_service.cancel_run(session, scenario_run.run_id, reason=reason or "cancelled by user")
            self._sync_scenario_run_from_runtime(session, scenario_run)
        else:
            scenario_run.status = "cancelled"
            scenario_run.driver_status = "cancelled"
            scenario_run.ended_at = scenario_run.ended_at or now_utc()

        scenario_run.status = "cancelled"
        scenario_run.driver_status = "cancelled"
        scenario_run.ended_at = scenario_run.ended_at or now_utc()
        scenario_run.result_summary = {
            **(scenario_run.result_summary or {}),
            "status": "cancelled",
            "reason": reason or "cancelled by user",
        }
        self._cancel_driver_job(session, scenario_run, reason=reason)
        session.commit()
        return self._build_run_response(scenario_run)

    def process_driver_job(self, session: Session, job_id: str) -> SkillTestScenarioRunResponse:
        job = self.job_repository.get_runtime_job(session, job_id)
        if not job:
            raise SkillNotFoundError("未找到测试时间轴 Driver Job。", details={"job_id": job_id})
        scenario_run_id = job.payload.get("scenario_run_id")
        if not scenario_run_id:
            raise SkillValidationError("测试时间轴 Driver Job 缺少 scenario_run_id。", details={"job_id": job_id})
        response = self.process_timeline_driver_for_run(session, str(scenario_run_id))
        scenario_run = self._get_scenario_run(session, str(scenario_run_id))
        if scenario_run.driver_status in {"completed", "failed", "cancelled"}:
            job.status = "succeeded" if scenario_run.driver_status == "completed" else scenario_run.driver_status
        elif job.status == "running":
            job.status = "pending"
        job.last_error = ""
        self._sync_driver_job_metrics(session, job, str(scenario_run_id))
        session.commit()
        return response

    def process_timeline_driver_for_run(self, session: Session, scenario_run_id: str) -> SkillTestScenarioRunResponse:
        scenario_run = self._get_scenario_run(session, scenario_run_id)
        if not scenario_run.run_id:
            raise SkillValidationError("测试场景运行尚未关联 Runtime Run。", details={"scenario_run_id": scenario_run_id})
        run = self.repository.get_run(session, scenario_run.run_id)
        if not run:
            raise SkillNotFoundError("未找到测试场景关联 Run。", details={"run_id": scenario_run.run_id})

        self._sync_scenario_run_from_runtime(session, scenario_run, run=run)
        if scenario_run.status == "cancelled" or scenario_run.driver_status == "cancelled":
            session.commit()
            return self._build_run_response(scenario_run)
        input_events = self._timeline_input_events(scenario_run.timeline)
        now = now_utc()
        cursor = max(0, min(scenario_run.driver_cursor, len(input_events)))
        if run.status in TERMINAL_RUNTIME_STATUSES and cursor < len(input_events):
            scenario_run.status = "failed"
            scenario_run.driver_status = "failed"
            scenario_run.ended_at = scenario_run.ended_at or now
            scenario_run.result_summary = {
                **(scenario_run.result_summary or {}),
                "status": "failed",
                "reason": "runtime_ended_before_required_inputs_sent",
                "remaining_input_event_ids": [item["id"] for item in input_events[cursor:]],
            }
            session.commit()
            return self._build_run_response(scenario_run)

        sent_any = False
        while cursor < len(input_events):
            event = input_events[cursor]
            scheduled_at = self._scenario_time(scenario_run, int(event.get("at_ms") or 0))
            if scheduled_at > now:
                if sent_any:
                    run = self._process_runtime_after_timeline_batch(session, scenario_run) or run
                    now = now_utc()
                    if run.status in TERMINAL_RUNTIME_STATUSES and cursor < len(input_events):
                        scenario_run.status = "failed"
                        scenario_run.driver_status = "failed"
                        scenario_run.ended_at = scenario_run.ended_at or now
                        scenario_run.result_summary = {
                            **(scenario_run.result_summary or {}),
                            "status": "failed",
                            "reason": "runtime_ended_before_required_inputs_sent",
                            "remaining_input_event_ids": [item["id"] for item in input_events[cursor:]],
                        }
                        session.commit()
                        return self._build_run_response(scenario_run)
                    if scheduled_at <= now:
                        continue
                scenario_run.driver_status = "waiting_time"
                scenario_run.driver_cursor = cursor
                self._ensure_driver_job_pending(session, scenario_run, available_at=scheduled_at)
                session.commit()
                return self._build_run_response(scenario_run)
            append_response = self._append_timeline_input_event(session, scenario_run, event, scheduled_at=scheduled_at)
            actual_sent_at = now_utc()
            driver_events = list(scenario_run.driver_events or [])
            driver_events.append(
                {
                    "status": "sent",
                    "event_id": event["id"],
                    "lane_id": event.get("lane_id"),
                    "at_ms": int(event.get("at_ms") or 0),
                    "scheduled_at": scheduled_at.isoformat(),
                    "actual_sent_at": actual_sent_at.isoformat(),
                    "drift_ms": max(0, int((actual_sent_at - scheduled_at).total_seconds() * 1000)),
                    "terminal_event_id": append_response.event_id,
                    "terminal_seq": append_response.seq_no,
                }
            )
            scenario_run.driver_events = driver_events
            cursor += 1
            scenario_run.driver_cursor = cursor
            sent_any = True
            now = now_utc()

        scenario_run.driver_status = "completed"
        scenario_run.driver_cursor = cursor
        if sent_any:
            self._process_runtime_after_timeline_batch(session, scenario_run)
        session.commit()
        return self.evaluate_run(session, scenario_run.id)

    def get_review(self, session: Session, scenario_run_id: str) -> SkillTestScenarioReviewResponse:
        scenario_run = self._get_scenario_run(session, scenario_run_id)
        scenario = self._get_scenario(session, scenario_run.skill_definition_id, scenario_run.scenario_id)
        self._sync_scenario_run_from_runtime(session, scenario_run)
        replay = self.runtime_service.build_replay(session, scenario_run.run_id) if scenario_run.run_id else None
        evaluations = self.repository.list_expectation_evaluations(session, scenario_run.id)
        cursor_anchors = self._build_cursor_anchors(scenario_run, replay)
        return SkillTestScenarioReviewResponse(
            scenario=self._build_scenario_response(session, scenario),
            scenario_run=self._build_run_response(scenario_run),
            replay=replay.model_dump(mode="json") if replay else None,
            scenario_timeline=scenario_run.timeline or scenario.timeline,
            replay_timeline=[item.model_dump(mode="json") for item in replay.timeline] if replay else [],
            cursor_anchors=cursor_anchors,
            driver_events=list(scenario_run.driver_events or []),
            expectation_evaluations=[self._build_evaluation_response(item) for item in evaluations],
            stage_outputs=self._build_stage_outputs(
                scenario_run=scenario_run,
                replay=replay,
                evaluations=evaluations,
                cursor_anchors=cursor_anchors,
            ),
        )

    def evaluate_run(self, session: Session, scenario_run_id: str) -> SkillTestScenarioRunResponse:
        scenario_run = self._get_scenario_run(session, scenario_run_id)
        if not scenario_run.run_id:
            raise SkillValidationError("测试场景运行尚未关联 Runtime Run。", details={"scenario_run_id": scenario_run_id})
        run = self.repository.get_run(session, scenario_run.run_id)
        if not run:
            raise SkillNotFoundError("未找到测试场景关联 Run。", details={"run_id": scenario_run.run_id})
        scenario = self._get_scenario(session, scenario_run.skill_definition_id, scenario_run.scenario_id)
        self._sync_scenario_run_from_runtime(session, scenario_run, run=run)
        expectations = self._timeline_expectation_events(scenario_run.timeline)
        self.repository.delete_expectation_evaluations(session, scenario_run.id)
        replay = self.runtime_service.build_replay(session, run.id)
        output_events = [item for item in replay.terminal_events if item.direction == "output"]
        now = now_utc()
        summary = {
            "total": len(expectations),
            "passed": 0,
            "failed": 0,
            "inconclusive": 0,
            "pending": 0,
            "status": scenario_run.status,
        }
        for expectation in expectations:
            cutoff = self._scenario_time(scenario_run, int(expectation.get("at_ms") or 0))
            if run.status not in TERMINAL_RUNTIME_STATUSES and cutoff > now:
                summary["pending"] += 1
                continue
            scoped_outputs = [item for item in output_events if self._aware_datetime(item.occurred_at) <= cutoff]
            evaluation = self._evaluate_expectation(
                session,
                scenario=scenario,
                scenario_run=scenario_run,
                expectation=expectation,
                scoped_outputs=scoped_outputs,
                final_output=run.final_output,
                run_status=run.status,
                cutoff=cutoff,
            )
            summary[evaluation.status] = int(summary.get(evaluation.status, 0)) + 1

        if summary["pending"] > 0 or run.status not in TERMINAL_RUNTIME_STATUSES:
            if scenario_run.status not in {"failed", "cancelled"}:
                scenario_run.status = "running"
        elif summary["failed"] > 0 or summary["inconclusive"] > 0 or run.status != "succeeded":
            scenario_run.status = "failed"
            scenario_run.ended_at = scenario_run.ended_at or now_utc()
        else:
            scenario_run.status = "passed"
            scenario_run.ended_at = scenario_run.ended_at or now_utc()
        summary["status"] = scenario_run.status
        scenario_run.result_summary = summary
        session.commit()
        return self._build_run_response(scenario_run)

    def fork_scenario(
        self,
        session: Session,
        scenario_run_id: str,
        payload: ForkSkillTestScenarioRequest,
    ) -> SkillTestScenarioResponse:
        source_run = self._get_scenario_run(session, scenario_run_id)
        source_scenario = self._get_scenario(session, source_run.skill_definition_id, source_run.scenario_id)
        if not source_run.run_id:
            raise SkillValidationError("测试场景运行尚未关联 Runtime Run。", details={"scenario_run_id": scenario_run_id})
        cursor = payload.cursor
        timeline = self._fork_timeline(source_run.timeline, time_ms=cursor.time_ms)
        fork_seed = {
            "source_scenario_id": source_scenario.id,
            "source_scenario_run_id": source_run.id,
            "source_run_id": source_run.run_id,
            "snapshot_seq": cursor.snapshot_seq,
            "terminal_seq": cursor.terminal_seq,
            "time_ms": cursor.time_ms,
        }
        scenario = SkillTestScenario(
            skill_definition_id=source_run.skill_definition_id,
            target_compile_artifact_id=source_scenario.target_compile_artifact_id,
            name=payload.name or f"{source_scenario.name} fork",
            description=payload.description if payload.description is not None else source_scenario.description,
            target_version_selector=source_scenario.target_version_selector,
            duration_ms=timeline["duration_ms"],
            timeline=timeline,
            judge_policy=source_scenario.judge_policy,
            fork_seed=fork_seed,
            status="active",
        )
        session.add(scenario)
        session.flush()
        scenario.timeline = self._copy_referenced_assets_for_fork(
            session,
            source_scenario=source_scenario,
            target_scenario=scenario,
            timeline=timeline,
        )
        session.commit()
        return self._build_scenario_response(session, scenario)

    def fork_debug(
        self,
        session: Session,
        scenario_run_id: str,
        payload: ForkSkillDebugRequest,
    ) -> InvocationResponse:
        source_run = self._get_scenario_run(session, scenario_run_id)
        if not source_run.run_id:
            raise SkillValidationError("测试场景运行尚未关联 Runtime Run。", details={"scenario_run_id": scenario_run_id})
        terminal_context = {
            "terminal_kind": "web",
            "operator_mode": "debug",
            "debug_context": {
                "kind": "skill_debug",
                "skill_id": source_run.skill_definition_id,
                "source": "skill_test_scenario_run",
                "scenario_run_id": source_run.id,
                "cursor": payload.cursor.model_dump(),
            },
        }
        return self.runtime_service.fork_invocation_from_snapshot(
            session,
            source_run_id=source_run.run_id,
            snapshot_seq=payload.cursor.snapshot_seq,
            terminal_seq=payload.cursor.terminal_seq,
            terminal_context=terminal_context,
            input_envelope={},
        )

    def _start_forked_invocation(
        self,
        session: Session,
        *,
        scenario: SkillTestScenario,
        scenario_run: SkillTestScenarioRun,
        terminal_context: dict[str, Any],
    ) -> InvocationResponse:
        seed = scenario.fork_seed or {}
        source_run_id = seed.get("source_run_id")
        if not source_run_id:
            raise SkillValidationError("Fork 场景缺少 source_run_id。", details={"scenario_id": scenario.id})
        return self.runtime_service.fork_invocation_from_snapshot(
            session,
            source_run_id=str(source_run_id),
            snapshot_seq=int(seed.get("snapshot_seq") or 0),
            terminal_seq=int(seed.get("terminal_seq") or 0),
            terminal_context=terminal_context,
            input_envelope={
                "skill_test_scenario_id": scenario.id,
                "skill_test_scenario_run_id": scenario_run.id,
                "fork_seed": seed,
            },
        )

    def _append_timeline_input_event(
        self,
        session: Session,
        scenario_run: SkillTestScenarioRun,
        event: dict[str, Any],
        *,
        scheduled_at: datetime,
    ):
        asset_id = event.get("asset_id")
        artifact_object_id = event.get("artifact_object_id")
        payload_inline = event.get("payload_inline")
        if asset_id:
            asset = self.repository.get_asset(session, str(asset_id))
            if not asset or asset.scenario_id != scenario_run.scenario_id:
                raise SkillValidationError("时间轴事件引用的测试资源不存在。", details={"asset_id": asset_id})
            artifact_object_id = asset.artifact_object_id
            payload_inline = self._payload_for_asset_event(event, asset)
        return self.runtime_service.append_terminal_event(
            session,
            scenario_run.run_id or "",
            AppendTerminalEventRequest(
                direction="input",
                event_kind=str(event.get("event_kind") or self._default_event_kind_for_lane(str(event.get("lane_id") or ""))),
                mime_type=str(event.get("mime_type") or self._default_mime_for_lane(str(event.get("lane_id") or ""))),
                payload_inline=payload_inline,
                artifact_object_id=artifact_object_id,
                source={"kind": "skill_test_timeline_driver"},
                external_event_id=f"skill-test-scenario-run:{scenario_run.id}:timeline:{event['id']}",
                occurred_at=scheduled_at,
            ),
            process_after_append=False,
        )

    def _evaluate_expectation(
        self,
        session: Session,
        *,
        scenario: SkillTestScenario,
        scenario_run: SkillTestScenarioRun,
        expectation: dict[str, Any],
        scoped_outputs: list[Any],
        final_output: str,
        run_status: str,
        cutoff: datetime,
    ) -> SkillTestExpectationEvaluation:
        policy = scenario.judge_policy or {}
        prompt_pack = self.agent_prompt_service.resolve_prompt_pack(
            session,
            usage_key="skill_test.semantic_judge",
            fallback_ref="skill_test/semantic_judge/v1",
        )
        route_key = str(policy.get("route_key") or prompt_pack.route_key or "skill-test-judge")
        prompt_payload = self._build_judge_prompt_payload(
            expectation=expectation,
            scoped_outputs=scoped_outputs,
            final_output=final_output,
            run_status=run_status,
            cutoff=cutoff,
            policy=policy,
        )
        system_prompt = prompt_pack.system_prompt
        user_prompt = json.dumps(prompt_payload, ensure_ascii=False, sort_keys=True)
        prompt_hash = hashlib.sha256(f"{system_prompt}\n{user_prompt}".encode("utf-8")).hexdigest()
        request_snapshot = {
            "route_key": route_key,
            "agent_prompt": prompt_pack.metadata(),
            "system_prompt": system_prompt,
            "user_prompt": user_prompt,
            "prompt_payload": prompt_payload,
        }
        status = "inconclusive"
        confidence = 0.0
        reason = "Judge 未能给出有效结论。"
        evidence_refs: list[dict[str, Any]] = []
        raw_response: dict[str, Any] = {}
        provider = ""
        model = ""
        try:
            completion = self.inference_gateway.complete(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                route_key=route_key,
            )
            provider = completion.provider
            model = completion.model
            parsed = json.loads(completion.content)
            raw_response = {
                "request": request_snapshot,
                "content": completion.content,
                "parsed": parsed,
                "usage": completion.usage,
                "raw": completion.raw_response,
            }
            parsed_status = str(parsed.get("status") or "").lower()
            status = parsed_status if parsed_status in {"passed", "failed", "inconclusive"} else "inconclusive"
            confidence = self._coerce_confidence(parsed.get("confidence"))
            reason = str(parsed.get("reason") or reason)
            raw_refs = parsed.get("evidence_refs")
            evidence_refs = raw_refs if isinstance(raw_refs, list) else []
        except Exception as exc:
            raw_response = {"request": request_snapshot, "error": str(exc), "error_type": exc.__class__.__name__}
            reason = f"Judge 调用失败或响应非法：{exc.__class__.__name__}"
            status = "inconclusive"

        evaluation = SkillTestExpectationEvaluation(
            scenario_run_id=scenario_run.id,
            expectation_id=str(expectation["id"]),
            status=status,
            confidence=confidence,
            reason=reason,
            evidence_refs=evidence_refs,
            judge_provider=provider,
            judge_model=model,
            prompt_hash=prompt_hash,
            raw_response=raw_response,
        )
        session.add(evaluation)
        return evaluation

    def _ensure_driver_job_pending(
        self,
        session: Session,
        scenario_run: SkillTestScenarioRun,
        *,
        available_at: datetime | None = None,
    ) -> RuntimeJob:
        dedupe_key = f"job:skill-test-timeline-driver:{scenario_run.id}"
        job = self.job_repository.get_runtime_job_by_dedupe_key(session, dedupe_key)
        if job:
            if job.status in {"succeeded", "failed", "cancelled"}:
                job.attempt_no = 0
            job.job_type = TIMELINE_DRIVER_JOB_TYPE
            job.status = "pending"
            job.payload = {"scenario_run_id": scenario_run.id}
            job.run_id = scenario_run.run_id
            job.available_at = available_at or now_utc()
            job.last_error = ""
            return job
        job = RuntimeJob(
            job_type=TIMELINE_DRIVER_JOB_TYPE,
            status="pending",
            payload={"scenario_run_id": scenario_run.id},
            run_id=scenario_run.run_id,
            dedupe_key=dedupe_key,
            available_at=available_at or now_utc(),
            max_attempts=self.settings.runtime_job_max_attempts,
        )
        session.add(job)
        return job

    def _cancel_driver_job(self, session: Session, scenario_run: SkillTestScenarioRun, *, reason: str = "cancelled by user") -> None:
        job = self.job_repository.get_runtime_job_by_dedupe_key(session, f"job:skill-test-timeline-driver:{scenario_run.id}")
        if job and job.status not in {"succeeded", "failed", "cancelled"}:
            job.status = "cancelled"
            job.last_error = reason or "cancelled by user"

    def _sync_scenario_run_from_runtime(
        self,
        session: Session,
        scenario_run: SkillTestScenarioRun,
        *,
        run: Run | None = None,
    ) -> None:
        if scenario_run.status in {"passed", "failed", "cancelled"}:
            return
        runtime_run = run or self.repository.get_run(session, scenario_run.run_id)
        if not runtime_run:
            return
        if runtime_run.status == "cancelled":
            scenario_run.status = "cancelled"
            scenario_run.driver_status = "cancelled"
            scenario_run.ended_at = scenario_run.ended_at or runtime_run.ended_at or now_utc()
            scenario_run.result_summary = {
                **(scenario_run.result_summary or {}),
                "status": "cancelled",
                "reason": runtime_run.exit_reason or "runtime_cancelled",
            }
        elif runtime_run.status == "failed":
            scenario_run.status = "failed"
            scenario_run.ended_at = scenario_run.ended_at or runtime_run.ended_at or now_utc()
            scenario_run.result_summary = {
                **(scenario_run.result_summary or {}),
                "status": "failed",
                "reason": runtime_run.exit_reason or "runtime_failed",
            }
        elif runtime_run.status == "succeeded" and scenario_run.driver_status == "completed":
            scenario_run.status = "running"
            scenario_run.ended_at = scenario_run.ended_at or runtime_run.ended_at
        else:
            scenario_run.status = runtime_run.status if runtime_run.status in OPEN_SCENARIO_RUN_STATUSES else "running"

    def _process_runtime_after_timeline_batch(self, session: Session, scenario_run: SkillTestScenarioRun) -> Run | None:
        if not scenario_run.run_id:
            return None
        run = self.repository.get_run(session, scenario_run.run_id)
        if not run:
            return None
        if not self.settings.runtime_worker_enabled and run.status not in TERMINAL_RUNTIME_STATUSES:
            self.runtime_service.process_run(session, run.id)
            run = self.repository.get_run(session, scenario_run.run_id) or run
        self._sync_scenario_run_from_runtime(session, scenario_run, run=run)
        return run

    def _get_open_scenario_run(self, session: Session, scenario: SkillTestScenario) -> SkillTestScenarioRun | None:
        for item in self.repository.list_open_runs(session, scenario.id):
            self._sync_scenario_run_from_runtime(session, item)
            if item.status in OPEN_SCENARIO_RUN_STATUSES:
                return item
        return None

    def _get_skill(self, session: Session, skill_id: str) -> SkillDefinition:
        skill = self.repository.get_skill(session, skill_id)
        if not skill or skill.status == "archived":
            raise SkillNotFoundError("未找到 Skill。", details={"skill_id": skill_id})
        return skill

    def _get_scenario(self, session: Session, skill_id: str, scenario_id: str) -> SkillTestScenario:
        scenario = self.repository.get_scenario(session, scenario_id)
        if not scenario or scenario.skill_definition_id != skill_id or scenario.status == "archived":
            raise SkillNotFoundError("未找到测试场景。", details={"skill_id": skill_id, "scenario_id": scenario_id})
        return scenario

    def _get_scenario_run(self, session: Session, scenario_run_id: str) -> SkillTestScenarioRun:
        scenario_run = self.repository.get_scenario_run(session, scenario_run_id)
        if not scenario_run:
            raise SkillNotFoundError("未找到测试场景运行。", details={"scenario_run_id": scenario_run_id})
        return scenario_run

    def _validate_target_artifact(self, session: Session, skill_id: str, artifact_id: str | None) -> None:
        if not artifact_id:
            return
        artifact = self.repository.get_artifact(session, artifact_id)
        if not artifact or artifact.status != "ready":
            raise SkillValidationError("指定编译产物不存在或尚不可运行。", details={"compile_artifact_id": artifact_id})
        version = self.repository.get_skill_version(session, artifact.skill_version_id)
        if not version or version.skill_definition_id != skill_id:
            raise SkillValidationError("指定编译产物不属于当前 Skill。", details={"compile_artifact_id": artifact_id})

    @staticmethod
    def _normalize_timeline_lanes(lanes: Any) -> list[dict[str, Any]]:
        raw_lanes = [dict(item) for item in lanes if isinstance(item, dict) and item.get("id")] if isinstance(lanes, list) else []
        raw_by_id: dict[str, dict[str, Any]] = {}
        for item in raw_lanes:
            lane_id = str(item["id"])
            raw_by_id.setdefault(lane_id, {**item, "id": lane_id})
        default_ids = {item["id"] for item in DEFAULT_TIMELINE_LANES}
        normalized = [{**raw_by_id.get(item["id"], {}), **item} for item in DEFAULT_TIMELINE_LANES]
        normalized.extend({**item, "id": str(item["id"])} for item in raw_lanes if str(item["id"]) not in default_ids)
        return normalized

    def _normalize_timeline(self, value: dict[str, Any] | None, *, duration_ms: int) -> dict[str, Any]:
        raw = value if isinstance(value, dict) else {}
        normalized_duration = int(raw.get("duration_ms") or duration_ms or DEFAULT_TIMELINE_DURATION_MS)
        if normalized_duration < 1:
            raise SkillValidationError("测试场景时长必须大于 0。", details={"duration_ms": normalized_duration})
        lanes = self._normalize_timeline_lanes(raw.get("lanes"))
        lane_ids = {str(item.get("id")) for item in lanes if isinstance(item, dict) and item.get("id")}
        events = raw.get("events")
        normalized_events: list[dict[str, Any]] = []
        if isinstance(events, list):
            for index, event in enumerate(events):
                if not isinstance(event, dict):
                    continue
                normalized_events.append(self._normalize_timeline_event(event, index=index, lane_ids=lane_ids))
        normalized_events.sort(key=lambda item: (int(item.get("at_ms") or 0), str(item.get("id") or "")))
        return {
            "schema_version": str(raw.get("schema_version") or TIMELINE_SCHEMA_VERSION),
            "duration_ms": normalized_duration,
            "lanes": lanes,
            "events": normalized_events,
            "fork_seed": raw.get("fork_seed") or {},
        }

    def _normalize_timeline_event(self, event: dict[str, Any], *, index: int, lane_ids: set[str]) -> dict[str, Any]:
        lane_id = str(event.get("lane_id") or "")
        if not lane_id:
            raise SkillValidationError("时间轴事件缺少 lane_id。", details={"index": index})
        if lane_ids and lane_id not in lane_ids:
            lane_ids.add(lane_id)
        at_ms = int(event.get("at_ms") or 0)
        if at_ms < 0:
            raise SkillValidationError("时间轴事件 at_ms 不能小于 0。", details={"index": index, "at_ms": at_ms})
        event_id = str(event.get("id") or f"event_{index + 1}")
        normalized = dict(event)
        normalized["id"] = event_id
        normalized["lane_id"] = lane_id
        normalized["at_ms"] = at_ms
        normalized["required"] = bool(event.get("required", True))
        if self._is_expectation_event(normalized):
            expectation = str(event.get("expectation") or "").strip()
            if not expectation:
                raise SkillValidationError("语义输出事件缺少 expectation。", details={"event_id": event_id})
            normalized["lane_id"] = "expected.semantic"
            normalized["expectation"] = expectation
        else:
            normalized["event_kind"] = str(event.get("event_kind") or self._default_event_kind_for_lane(lane_id))
            normalized["mime_type"] = str(event.get("mime_type") or self._default_mime_for_lane(lane_id))
            if self._is_sensor_lane(lane_id):
                normalized["payload_inline"] = self._normalize_sensor_payload(
                    lane_id=lane_id,
                    payload=event.get("payload_inline"),
                    event_id=event_id,
                )
        return normalized

    @staticmethod
    def _normalize_judge_policy(value: dict[str, Any] | None) -> dict[str, Any]:
        policy = dict(value or {})
        policy.setdefault("route_key", "skill-test-judge")
        policy.setdefault("confidence_threshold", 0.7)
        policy.setdefault("inconclusive_as", "failed")
        return policy

    @staticmethod
    def _normalize_name(value: str, *, field: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise SkillValidationError(f"{field} 不能为空。")
        return normalized

    def _validate_upload(self, *, filename: str, content: bytes, mime_type: str) -> None:
        if not content:
            raise SkillValidationError("上传文件不能为空。")
        if len(content) > self.settings.test_data_max_upload_bytes:
            raise SkillValidationError(
                "上传文件超过大小限制。",
                details={"max_bytes": self.settings.test_data_max_upload_bytes, "size_bytes": len(content)},
            )
        if not filename:
            raise SkillValidationError("上传文件名不能为空。")
        if not mime_type:
            raise SkillValidationError("上传文件类型不能为空。")

    @staticmethod
    def _safe_filename(filename: str) -> str:
        cleaned = filename.replace("\\", "/").split("/")[-1].strip()
        return cleaned or "upload.bin"

    @staticmethod
    def _is_expectation_event(event: dict[str, Any]) -> bool:
        return event.get("lane_id") == "expected.semantic" or "expectation" in event

    @staticmethod
    def _is_sensor_lane(lane_id: str) -> bool:
        return lane_id in SENSOR_LANE_REQUIRED_FIELDS

    def _normalize_sensor_payload(self, *, lane_id: str, payload: Any, event_id: str) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise SkillValidationError(
                "传感器事件 payload_inline 必须是对象。",
                details={"event_id": event_id, "lane_id": lane_id},
            )
        normalized = dict(payload)
        for field_name in SENSOR_LANE_REQUIRED_FIELDS[lane_id]:
            if field_name not in normalized or normalized[field_name] in ("", None):
                raise SkillValidationError(
                    "传感器事件缺少必填数值字段。",
                    details={"event_id": event_id, "lane_id": lane_id, "field": field_name},
                )
        for field_name in SENSOR_LANE_NUMERIC_FIELDS[lane_id]:
            if field_name not in normalized or normalized[field_name] in ("", None):
                continue
            try:
                normalized[field_name] = float(normalized[field_name])
            except (TypeError, ValueError):
                raise SkillValidationError(
                    "传感器事件字段必须是数值。",
                    details={"event_id": event_id, "lane_id": lane_id, "field": field_name},
                ) from None
            if not isfinite(normalized[field_name]):
                raise SkillValidationError(
                    "传感器事件字段必须是有限数值。",
                    details={"event_id": event_id, "lane_id": lane_id, "field": field_name},
                )
        timestamp = normalized.get("timestamp")
        if timestamp is not None:
            normalized["timestamp"] = str(timestamp)
        return normalized

    def _timeline_input_events(self, timeline: dict[str, Any]) -> list[dict[str, Any]]:
        return [item for item in timeline.get("events", []) if isinstance(item, dict) and not self._is_expectation_event(item)]

    def _timeline_expectation_events(self, timeline: dict[str, Any]) -> list[dict[str, Any]]:
        return [item for item in timeline.get("events", []) if isinstance(item, dict) and self._is_expectation_event(item)]

    def _scenario_time(self, scenario_run: SkillTestScenarioRun, at_ms: int) -> datetime:
        origin = self._aware_datetime(scenario_run.time_origin or scenario_run.started_at or scenario_run.created_at)
        return self._aware_datetime(origin) + timedelta(milliseconds=max(0, at_ms))

    @staticmethod
    def _aware_datetime(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value

    @classmethod
    def _coerce_datetime(cls, value: Any) -> datetime | None:
        if isinstance(value, datetime):
            return cls._aware_datetime(value)
        if isinstance(value, str) and value.strip():
            try:
                return cls._aware_datetime(datetime.fromisoformat(value.replace("Z", "+00:00")))
            except ValueError:
                return None
        return None

    def _payload_for_asset_event(self, event: dict[str, Any], asset: SkillTestAsset) -> dict[str, Any]:
        raw_payload = event.get("payload_inline")
        payload = raw_payload.copy() if isinstance(raw_payload, dict) else {}
        if isinstance(raw_payload, str) and raw_payload.strip():
            payload["caption"] = raw_payload.strip()
        result = {
            "asset_id": asset.id,
            "artifact_object_id": asset.artifact_object_id,
            "filename": asset.filename,
            "name": asset.name,
            "description": asset.description,
            "mime_type": asset.mime_type,
            "size_bytes": asset.size_bytes,
            "checksum": asset.checksum,
        }
        result.update(payload)
        result.update(
            {
                "asset_id": asset.id,
                "artifact_object_id": asset.artifact_object_id,
                "filename": asset.filename,
                "name": asset.name,
                "mime_type": asset.mime_type,
                "size_bytes": asset.size_bytes,
                "checksum": asset.checksum,
            }
        )
        return result

    @staticmethod
    def _default_event_kind_for_lane(lane_id: str) -> str:
        if lane_id == "sensor.gps":
            return "sensor.gps.reading.v1"
        if lane_id == "sensor.pose3d":
            return "sensor.pose3d.reading.v1"
        if "image" in lane_id:
            return "terminal.image.input.v1"
        if "audio" in lane_id:
            return "terminal.audio.input.v1"
        if "video" in lane_id:
            return "terminal.video.input.v1"
        return "terminal.text.input.v1"

    @staticmethod
    def _default_mime_for_lane(lane_id: str) -> str:
        if lane_id in SENSOR_LANE_REQUIRED_FIELDS:
            return "application/json"
        if "image" in lane_id:
            return "image/*"
        if "audio" in lane_id:
            return "audio/*"
        if "video" in lane_id:
            return "video/*"
        return "text/plain"

    @staticmethod
    def _coerce_confidence(value: Any) -> float:
        try:
            confidence = float(value)
        except (TypeError, ValueError):
            return 0.0
        return max(0.0, min(1.0, confidence))

    @classmethod
    def _build_judge_prompt_payload(
        cls,
        *,
        expectation: dict[str, Any],
        scoped_outputs: list[Any],
        final_output: str,
        run_status: str,
        cutoff: datetime,
        policy: dict[str, Any],
    ) -> dict[str, Any]:
        transcript_budget = cls._coerce_judge_budget(
            policy.get("transcript_budget_chars"),
            default=DEFAULT_JUDGE_TRANSCRIPT_BUDGET_CHARS,
            maximum=150_000,
        )
        event_budget = cls._coerce_judge_budget(
            policy.get("event_budget_chars"),
            default=DEFAULT_JUDGE_EVENT_BUDGET_CHARS,
            maximum=40_000,
        )
        final_output_budget = cls._coerce_judge_budget(
            policy.get("final_output_budget_chars"),
            default=DEFAULT_JUDGE_FINAL_OUTPUT_BUDGET_CHARS,
            maximum=40_000,
        )
        compact_outputs, compaction = cls._compact_judge_terminal_outputs(
            scoped_outputs,
            transcript_budget=transcript_budget,
            event_budget=event_budget,
        )
        final_output_text, final_output_truncated = cls._truncate_judge_text(
            cls._judge_text(final_output),
            final_output_budget,
        )
        compaction.update(
            {
                "final_output_budget_chars": final_output_budget,
                "final_output_chars": len(cls._judge_text(final_output)),
                "final_output_included_chars": len(final_output_text),
                "final_output_truncated": final_output_truncated,
            }
        )
        return {
            "expectation": expectation.get("expectation") or "",
            "cutoff_occurred_at": cutoff.isoformat(),
            "terminal_outputs_before_cutoff": compact_outputs,
            "terminal_output_count_before_cutoff": len(scoped_outputs),
            "final_output": final_output_text,
            "run_status": run_status,
            "input_compaction": compaction,
        }

    @classmethod
    def _compact_judge_terminal_outputs(
        cls,
        scoped_outputs: list[Any],
        *,
        transcript_budget: int,
        event_budget: int,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        candidates = [cls._compact_judge_terminal_output(item, event_budget=event_budget) for item in scoped_outputs]
        included_reversed: list[dict[str, Any]] = []
        included_chars = 0
        for candidate in reversed(candidates):
            candidate_chars = cls._json_chars(candidate)
            if included_reversed and included_chars + candidate_chars > transcript_budget:
                continue
            if not included_reversed and candidate_chars > transcript_budget:
                static_chars = max(0, candidate_chars - len(str(candidate.get("payload_text") or "")))
                available_payload_chars = max(0, transcript_budget - static_chars)
                payload_text, truncated = cls._truncate_judge_text(str(candidate.get("payload_text") or ""), available_payload_chars)
                candidate = {
                    **candidate,
                    "payload_text": payload_text,
                    "payload_truncated": True,
                    "truncation_reason": "transcript_budget" if truncated else candidate.get("truncation_reason"),
                }
                candidate_chars = cls._json_chars(candidate)
            if included_chars + candidate_chars <= transcript_budget or not included_reversed:
                included_reversed.append(candidate)
                included_chars += candidate_chars
        included = list(reversed(included_reversed))
        included_seq_no = {item.get("seq_no") for item in included}
        omitted_seq_no = [item.get("seq_no") for item in candidates if item.get("seq_no") not in included_seq_no]
        return included, {
            "strategy": "recent_outputs_with_per_event_truncation",
            "transcript_budget_chars": transcript_budget,
            "event_budget_chars": event_budget,
            "terminal_output_count": len(scoped_outputs),
            "included_terminal_output_count": len(included),
            "omitted_terminal_output_count": max(0, len(scoped_outputs) - len(included)),
            "omitted_seq_no": omitted_seq_no,
            "included_chars": included_chars,
            "truncated_seq_no": [item.get("seq_no") for item in included if item.get("payload_truncated")],
        }

    @classmethod
    def _compact_judge_terminal_output(cls, event: Any, *, event_budget: int) -> dict[str, Any]:
        payload_text = cls._judge_text(cls._event_value(event, "payload_inline"))
        truncated_text, truncated = cls._truncate_judge_text(payload_text, event_budget)
        return {
            "seq_no": cls._event_value(event, "seq_no"),
            "occurred_at": cls._judge_datetime(cls._event_value(event, "occurred_at")),
            "event_kind": cls._event_value(event, "event_kind"),
            "mime_type": cls._event_value(event, "mime_type"),
            "payload_text": truncated_text,
            "payload_chars": len(payload_text),
            "included_payload_chars": len(truncated_text),
            "payload_truncated": truncated,
            "truncation_reason": "event_budget" if truncated else "",
        }

    @staticmethod
    def _coerce_judge_budget(value: Any, *, default: int, maximum: int) -> int:
        try:
            budget = int(value)
        except (TypeError, ValueError):
            budget = default
        return max(1_000, min(maximum, budget))

    @staticmethod
    def _truncate_judge_text(value: str, max_chars: int) -> tuple[str, bool]:
        if len(value) <= max_chars:
            return value, False
        if max_chars <= 0:
            return "", bool(value)
        omitted = len(value) - max_chars
        marker = f"\n...[truncated {omitted} chars]...\n"
        if max_chars <= len(marker) + 8:
            return value[:max_chars], True
        available = max_chars - len(marker)
        head_chars = max(1, available // 2)
        tail_chars = max(1, available - head_chars)
        return f"{value[:head_chars]}{marker}{value[-tail_chars:]}", True

    @staticmethod
    def _judge_text(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        try:
            return json.dumps(value, ensure_ascii=False, sort_keys=True)
        except TypeError:
            return str(value)

    @staticmethod
    def _json_chars(value: Any) -> int:
        return len(json.dumps(value, ensure_ascii=False, sort_keys=True))

    @staticmethod
    def _judge_datetime(value: Any) -> str:
        if isinstance(value, datetime):
            return value.isoformat()
        return str(value or "")

    @staticmethod
    def _event_value(event: Any, name: str) -> Any:
        if isinstance(event, dict):
            return event.get(name)
        return getattr(event, name, None)

    def _build_run_terminal_context(
        self,
        *,
        skill_id: str,
        scenario: SkillTestScenario,
        scenario_run: SkillTestScenarioRun,
        override: dict[str, Any] | None,
    ) -> dict[str, Any]:
        base = {
            "terminal_kind": "web",
            "operator_mode": "test",
            "test_context": {
                "kind": "skill_blackbox_timeline_test",
                "skill_id": skill_id,
                "skill_test_scenario_id": scenario.id,
                "skill_test_scenario_run_id": scenario_run.id,
            },
        }
        if override:
            base.update(override)
            base.setdefault("test_context", {}).update(
                {
                    "kind": "skill_blackbox_timeline_test",
                    "skill_id": skill_id,
                    "skill_test_scenario_id": scenario.id,
                    "skill_test_scenario_run_id": scenario_run.id,
                }
            )
        return base

    @staticmethod
    def _initial_result_summary(timeline: dict[str, Any]) -> dict[str, Any]:
        expectations = [item for item in timeline.get("events", []) if isinstance(item, dict) and SkillTestService._is_expectation_event(item)]
        return {
            "total": len(expectations),
            "passed": 0,
            "failed": 0,
            "inconclusive": 0,
            "pending": len(expectations),
            "status": "running",
        }

    def _fork_timeline(self, timeline: dict[str, Any], *, time_ms: int) -> dict[str, Any]:
        duration_ms = max(1000, int(timeline.get("duration_ms") or DEFAULT_TIMELINE_DURATION_MS))
        events: list[dict[str, Any]] = []
        for item in timeline.get("events", []):
            if not isinstance(item, dict):
                continue
            at_ms = int(item.get("at_ms") or 0)
            if at_ms > time_ms:
                continue
            shifted = dict(item)
            shifted["at_ms"] = at_ms
            shifted["id"] = f"fork_{shifted.get('id') or uuid.uuid4()}"
            events.append(shifted)
        return self._normalize_timeline(
            {
                "schema_version": TIMELINE_SCHEMA_VERSION,
                "duration_ms": duration_ms,
                "lanes": timeline.get("lanes") or DEFAULT_TIMELINE_LANES,
                "events": events,
            },
            duration_ms=duration_ms,
        )

    def _copy_referenced_assets_for_fork(
        self,
        session: Session,
        *,
        source_scenario: SkillTestScenario,
        target_scenario: SkillTestScenario,
        timeline: dict[str, Any],
    ) -> dict[str, Any]:
        asset_ids = []
        seen_asset_ids = set()
        for event in timeline.get("events", []):
            if not isinstance(event, dict) or not event.get("asset_id"):
                continue
            asset_id = str(event["asset_id"])
            if asset_id in seen_asset_ids:
                continue
            seen_asset_ids.add(asset_id)
            asset_ids.append(asset_id)
        if not asset_ids:
            return timeline

        asset_id_map: dict[str, str] = {}
        for asset_id in asset_ids:
            asset = self.repository.get_asset(session, asset_id)
            if not asset or asset.skill_definition_id != source_scenario.skill_definition_id:
                raise SkillValidationError("Fork 时间轴引用的测试资源不存在。", details={"asset_id": asset_id})
            forked_asset_id = str(uuid.uuid4())
            asset_id_map[asset_id] = forked_asset_id
            session.add(
                SkillTestAsset(
                    id=forked_asset_id,
                    skill_definition_id=target_scenario.skill_definition_id,
                    scenario_id=target_scenario.id,
                    artifact_object_id=asset.artifact_object_id,
                    name=asset.name,
                    description=asset.description,
                    lane_id=asset.lane_id,
                    filename=asset.filename,
                    mime_type=asset.mime_type,
                    size_bytes=asset.size_bytes,
                    checksum=asset.checksum,
                )
            )

        remapped_events = []
        for event in timeline.get("events", []):
            if not isinstance(event, dict):
                continue
            remapped = dict(event)
            asset_id = str(remapped.get("asset_id") or "")
            if asset_id in asset_id_map:
                remapped["asset_id"] = asset_id_map[asset_id]
            remapped_events.append(remapped)
        return self._normalize_timeline(
            {
                **timeline,
                "events": remapped_events,
            },
            duration_ms=int(timeline.get("duration_ms") or target_scenario.duration_ms),
        )

    def _build_stage_outputs(
        self,
        *,
        scenario_run: SkillTestScenarioRun,
        replay,
        evaluations: list[SkillTestExpectationEvaluation],
        cursor_anchors: list[dict[str, Any]],
    ) -> list[SkillTestStageOutputResponse]:
        stage_events = sorted(
            self._timeline_expectation_events(scenario_run.timeline),
            key=lambda item: (int(item.get("at_ms") or 0), str(item.get("id") or "")),
        )
        evaluation_by_id = {item.expectation_id: item for item in evaluations}
        output_events = []
        if replay:
            output_events = sorted(
                [item for item in replay.terminal_events if getattr(item, "direction", "") == "output"],
                key=lambda item: (self._terminal_event_at_ms(scenario_run, item), int(getattr(item, "seq_no", 0) or 0)),
            )

        previous_stage_ms = -1
        stage_outputs: list[SkillTestStageOutputResponse] = []
        for stage_event in stage_events:
            stage_id = str(stage_event["id"])
            stage_time_ms = int(stage_event.get("at_ms") or 0)
            actual_outputs = [
                self._build_stage_actual_output(scenario_run, item)
                for item in output_events
                if previous_stage_ms < self._terminal_event_at_ms(scenario_run, item) <= stage_time_ms
            ]
            stage_outputs.append(
                SkillTestStageOutputResponse(
                    stage_id=stage_id,
                    event_id=stage_id,
                    time_ms=stage_time_ms,
                    expectation=str(stage_event.get("expectation") or ""),
                    actual_outputs=actual_outputs,
                    judge_result=self._build_stage_judge_result(evaluation_by_id.get(stage_id)),
                    human_review=SkillTestStageHumanReviewResponse(),
                    cursor=self._cursor_for_time_ms(stage_time_ms, cursor_anchors),
                )
            )
            previous_stage_ms = stage_time_ms
        return stage_outputs

    def _build_stage_actual_output(self, scenario_run: SkillTestScenarioRun, event: Any) -> SkillTestStageActualOutputResponse:
        seq_no = self._event_value(event, "seq_no")
        event_id = str(self._event_value(event, "id") or f"terminal_output_{seq_no or uuid.uuid4()}")
        return SkillTestStageActualOutputResponse(
            id=f"stage_output_{event_id}",
            terminal_event_id=event_id,
            seq_no=int(seq_no) if seq_no is not None else None,
            at_ms=self._terminal_event_at_ms(scenario_run, event),
            occurred_at=self._coerce_datetime(self._event_value(event, "occurred_at")),
            event_kind=str(self._event_value(event, "event_kind") or ""),
            mime_type=str(self._event_value(event, "mime_type") or ""),
            payload_inline=self._event_value(event, "payload_inline"),
        )

    @staticmethod
    def _build_stage_judge_result(evaluation: SkillTestExpectationEvaluation | None) -> SkillTestStageJudgeResultResponse:
        if not evaluation:
            return SkillTestStageJudgeResultResponse()
        return SkillTestStageJudgeResultResponse(
            status=evaluation.status,
            confidence=evaluation.confidence,
            reason=evaluation.reason,
            evidence_refs=evaluation.evidence_refs,
            judge_provider=evaluation.judge_provider,
            judge_model=evaluation.judge_model,
            prompt_hash=evaluation.prompt_hash,
            evaluation_id=evaluation.id,
            created_at=evaluation.created_at,
        )

    def _terminal_event_at_ms(self, scenario_run: SkillTestScenarioRun, event: Any) -> int:
        origin = self._aware_datetime(scenario_run.time_origin or scenario_run.started_at or scenario_run.created_at)
        occurred_at = self._coerce_datetime(self._event_value(event, "occurred_at"))
        if not occurred_at:
            return 0
        return max(0, int((self._aware_datetime(occurred_at) - origin).total_seconds() * 1000))

    def _cursor_for_time_ms(self, time_ms: int, cursor_anchors: list[dict[str, Any]]) -> dict[str, int]:
        cutoff_ms = max(0, int(time_ms or 0))
        eligible = [item for item in cursor_anchors if int(item.get("time_ms") or 0) <= cutoff_ms]
        return {
            "time_ms": cutoff_ms,
            "terminal_seq": max([int(item.get("terminal_seq") or 0) for item in eligible] or [0]),
            "snapshot_seq": max([int(item.get("snapshot_seq") or 0) for item in eligible] or [0]),
        }

    def _build_cursor_anchors(self, scenario_run: SkillTestScenarioRun, replay) -> list[dict[str, Any]]:
        if not replay:
            return []
        origin = self._aware_datetime(scenario_run.time_origin or scenario_run.started_at or scenario_run.created_at)
        snapshots = sorted(replay.snapshots, key=lambda item: item.seq_no)
        anchors: list[dict[str, Any]] = []
        latest_snapshot_seq = 0
        latest_terminal_seq = 0
        for item in replay.timeline:
            occurred_at = self._aware_datetime(item.occurred_at)
            while snapshots and self._aware_datetime(snapshots[0].created_at) <= occurred_at:
                latest_snapshot_seq = snapshots.pop(0).seq_no
            payload = item.payload if isinstance(item.payload, dict) else {}
            if item.event_type == "terminal.event.appended":
                latest_terminal_seq = max(latest_terminal_seq, int(payload.get("seq_no") or 0))
            anchors.append(
                {
                    "time_ms": max(0, int((occurred_at - origin).total_seconds() * 1000)),
                    "occurred_at": item.occurred_at.isoformat(),
                    "terminal_seq": latest_terminal_seq,
                    "snapshot_seq": latest_snapshot_seq,
                    "event_type": item.event_type,
                }
            )
        return anchors

    def _build_scenario_response(self, session: Session, scenario: SkillTestScenario) -> SkillTestScenarioResponse:
        latest_run = self.repository.get_latest_run(session, scenario.id)
        if latest_run:
            self._sync_scenario_run_from_runtime(session, latest_run)
        return SkillTestScenarioResponse(
            id=scenario.id,
            skill_definition_id=scenario.skill_definition_id,
            name=scenario.name,
            description=scenario.description,
            target_version_selector=scenario.target_version_selector,
            target_compile_artifact_id=scenario.target_compile_artifact_id,
            duration_ms=scenario.duration_ms,
            timeline=scenario.timeline,
            judge_policy=scenario.judge_policy,
            fork_seed=scenario.fork_seed,
            status=scenario.status,
            latest_run=self._build_run_summary(latest_run) if latest_run else None,
            created_at=scenario.created_at,
            updated_at=scenario.updated_at,
        )

    @staticmethod
    def _build_asset_response(asset: SkillTestAsset) -> SkillTestAssetResponse:
        return SkillTestAssetResponse(
            id=asset.id,
            skill_definition_id=asset.skill_definition_id,
            scenario_id=asset.scenario_id,
            artifact_object_id=asset.artifact_object_id,
            name=asset.name,
            description=asset.description,
            lane_id=asset.lane_id,
            filename=asset.filename,
            mime_type=asset.mime_type,
            size_bytes=asset.size_bytes,
            checksum=asset.checksum,
            created_at=asset.created_at,
        )

    @staticmethod
    def _build_run_response(scenario_run: SkillTestScenarioRun) -> SkillTestScenarioRunResponse:
        return SkillTestScenarioRunResponse(
            id=scenario_run.id,
            skill_definition_id=scenario_run.skill_definition_id,
            scenario_id=scenario_run.scenario_id,
            invocation_id=scenario_run.invocation_id,
            run_id=scenario_run.run_id,
            status=scenario_run.status,
            driver_status=scenario_run.driver_status,
            driver_cursor=scenario_run.driver_cursor,
            driver_events=list(scenario_run.driver_events or []),
            timeline=scenario_run.timeline,
            result_summary=scenario_run.result_summary,
            time_origin=scenario_run.time_origin,
            started_at=scenario_run.started_at,
            ended_at=scenario_run.ended_at,
            created_at=scenario_run.created_at,
            updated_at=scenario_run.updated_at,
        )

    @staticmethod
    def _build_run_summary(scenario_run: SkillTestScenarioRun) -> SkillTestScenarioRunSummary:
        return SkillTestScenarioRunSummary(
            id=scenario_run.id,
            status=scenario_run.status,
            driver_status=scenario_run.driver_status,
            run_id=scenario_run.run_id,
            result_summary=scenario_run.result_summary,
            created_at=scenario_run.created_at,
            ended_at=scenario_run.ended_at,
        )

    @staticmethod
    def _build_evaluation_response(evaluation: SkillTestExpectationEvaluation) -> SkillTestExpectationEvaluationResponse:
        return SkillTestExpectationEvaluationResponse(
            id=evaluation.id,
            scenario_run_id=evaluation.scenario_run_id,
            expectation_id=evaluation.expectation_id,
            status=evaluation.status,
            confidence=evaluation.confidence,
            reason=evaluation.reason,
            evidence_refs=evaluation.evidence_refs,
            judge_provider=evaluation.judge_provider,
            judge_model=evaluation.judge_model,
            prompt_hash=evaluation.prompt_hash,
            raw_response=evaluation.raw_response,
            created_at=evaluation.created_at,
        )

    def _sync_driver_job_metrics(self, session: Session, job: RuntimeJob, scenario_run_id: str) -> None:
        evaluations = self.repository.list_expectation_evaluations(session, scenario_run_id)
        totals = {"llm_calls": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        for evaluation in evaluations:
            usage = (evaluation.raw_response or {}).get("usage")
            if not isinstance(usage, dict):
                continue
            token_seen = False
            for key in ("input_tokens", "output_tokens", "total_tokens"):
                value = usage.get(key)
                if isinstance(value, int) and not isinstance(value, bool):
                    totals[key] += value
                    token_seen = True
            if token_seen:
                totals["llm_calls"] += 1
        if totals["llm_calls"] > 0:
            job.metrics = {**(job.metrics or {}), **totals}
