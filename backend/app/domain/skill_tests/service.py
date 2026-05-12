from __future__ import annotations

import hashlib
import json
import posixpath
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import Settings
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
DEFAULT_TIMELINE_LANES = [
    {"id": "input.text", "kind": "input", "label": "文本", "event_kind": "terminal.text.input.v1"},
    {"id": "input.image", "kind": "input", "label": "图片", "event_kind": "terminal.image.input.v1"},
    {"id": "input.audio", "kind": "input", "label": "音频", "event_kind": "terminal.audio.input.v1"},
    {"id": "input.video", "kind": "input", "label": "视频", "event_kind": "terminal.video.input.v1"},
    {"id": "expected.semantic", "kind": "output", "label": "语义输出"},
]


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
    ) -> None:
        self.settings = settings
        self.inference_gateway = inference_gateway
        self.object_store = object_store
        self.repository = repository or SkillTestRepository()
        self.runtime_service = runtime_service or RuntimeService(settings=settings, inference_gateway=inference_gateway)
        self.job_repository = job_repository or JobRepository()

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
            run = self.repository.get_run(session, scenario_run.run_id) or run
            self._sync_scenario_run_from_runtime(session, scenario_run, run=run)
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

        scenario_run.driver_status = "completed"
        scenario_run.driver_cursor = cursor
        if sent_any:
            self._sync_scenario_run_from_runtime(session, scenario_run)
        session.commit()
        return self.evaluate_run(session, scenario_run.id)

    def get_review(self, session: Session, scenario_run_id: str) -> SkillTestScenarioReviewResponse:
        scenario_run = self._get_scenario_run(session, scenario_run_id)
        scenario = self._get_scenario(session, scenario_run.skill_definition_id, scenario_run.scenario_id)
        self._sync_scenario_run_from_runtime(session, scenario_run)
        replay = self.runtime_service.build_replay(session, scenario_run.run_id) if scenario_run.run_id else None
        evaluations = self.repository.list_expectation_evaluations(session, scenario_run.id)
        return SkillTestScenarioReviewResponse(
            scenario=self._build_scenario_response(session, scenario),
            scenario_run=self._build_run_response(scenario_run),
            replay=replay.model_dump(mode="json") if replay else None,
            scenario_timeline=scenario_run.timeline or scenario.timeline,
            replay_timeline=[item.model_dump(mode="json") for item in replay.timeline] if replay else [],
            cursor_anchors=self._build_cursor_anchors(scenario_run, replay),
            driver_events=list(scenario_run.driver_events or []),
            expectation_evaluations=[self._build_evaluation_response(item) for item in evaluations],
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
        route_key = str(policy.get("route_key") or "skill-test-judge")
        prompt_payload = {
            "expectation": expectation.get("expectation") or "",
            "cutoff_occurred_at": cutoff.isoformat(),
            "terminal_outputs_before_cutoff": [item.model_dump(mode="json") for item in scoped_outputs],
            "final_output": final_output,
            "run_status": run_status,
        }
        system_prompt = (
            "你是 PSOP Skill 黑盒时序测试 Judge。"
            "只根据给定时间点以前的真实 terminal output 判断语义期望是否满足。"
            "必须只输出 JSON，字段为 status、confidence、reason、evidence_refs、missing_evidence。"
            "status 只能是 passed、failed、inconclusive。"
        )
        user_prompt = json.dumps(prompt_payload, ensure_ascii=False, sort_keys=True)
        prompt_hash = hashlib.sha256(f"{system_prompt}\n{user_prompt}".encode("utf-8")).hexdigest()
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
            raw_response = {"content": completion.content, "parsed": parsed, "usage": completion.usage, "raw": completion.raw_response}
            parsed_status = str(parsed.get("status") or "").lower()
            status = parsed_status if parsed_status in {"passed", "failed", "inconclusive"} else "inconclusive"
            confidence = self._coerce_confidence(parsed.get("confidence"))
            reason = str(parsed.get("reason") or reason)
            raw_refs = parsed.get("evidence_refs")
            evidence_refs = raw_refs if isinstance(raw_refs, list) else []
        except Exception as exc:
            raw_response = {"error": str(exc), "error_type": exc.__class__.__name__}
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

    def _normalize_timeline(self, value: dict[str, Any] | None, *, duration_ms: int) -> dict[str, Any]:
        raw = value if isinstance(value, dict) else {}
        normalized_duration = int(raw.get("duration_ms") or duration_ms or DEFAULT_TIMELINE_DURATION_MS)
        if normalized_duration < 1:
            raise SkillValidationError("测试场景时长必须大于 0。", details={"duration_ms": normalized_duration})
        lanes = raw.get("lanes")
        if not isinstance(lanes, list) or not lanes:
            lanes = DEFAULT_TIMELINE_LANES
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
        if "image" in lane_id:
            return "terminal.image.input.v1"
        if "audio" in lane_id:
            return "terminal.audio.input.v1"
        if "video" in lane_id:
            return "terminal.video.input.v1"
        return "terminal.text.input.v1"

    @staticmethod
    def _default_mime_for_lane(lane_id: str) -> str:
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
        duration_ms = max(1000, int(timeline.get("duration_ms") or DEFAULT_TIMELINE_DURATION_MS) - time_ms)
        events: list[dict[str, Any]] = []
        for item in timeline.get("events", []):
            if not isinstance(item, dict):
                continue
            at_ms = int(item.get("at_ms") or 0)
            if at_ms < time_ms:
                continue
            shifted = dict(item)
            shifted["at_ms"] = at_ms - time_ms
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

    def _build_cursor_anchors(self, scenario_run: SkillTestScenarioRun, replay) -> list[dict[str, Any]]:
        if not replay:
            return []
        origin = self._aware_datetime(scenario_run.time_origin or scenario_run.started_at or scenario_run.created_at)
        snapshots = sorted(replay.snapshots, key=lambda item: item.seq_no)
        anchors: list[dict[str, Any]] = []
        latest_snapshot_seq = 0
        for item in replay.timeline:
            occurred_at = self._aware_datetime(item.occurred_at)
            while snapshots and self._aware_datetime(snapshots[0].created_at) <= occurred_at:
                latest_snapshot_seq = snapshots.pop(0).seq_no
            payload = item.payload if isinstance(item.payload, dict) else {}
            terminal_seq = int(payload.get("seq_no") or 0) if item.event_type == "terminal.event.appended" else 0
            anchors.append(
                {
                    "time_ms": max(0, int((occurred_at - origin).total_seconds() * 1000)),
                    "occurred_at": item.occurred_at.isoformat(),
                    "terminal_seq": terminal_seq,
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
