from __future__ import annotations

import base64
import hashlib
import json
import logging
import time
from collections.abc import Sequence
from datetime import timedelta
from pathlib import Path
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent_harness.agents.psop.runner.schemas import (
    RUNNER_OBSERVATION_ARTIFACT_REF,
    RUNNER_OBSERVATION_VIRTUAL_PATH,
    validate_runner_observation,
)
from app.agent_harness.schemas import AgentInvocation, AgentInvocationAttachment, AgentResult
from app.agent_harness.service import AgentHarnessService
from app.core.config import Settings
from app.core.logging import log_context
from app.core.observability import add_metric_counter, record_span_exception, start_span
from app.domain.agent_prompts.service import AgentPromptService
from app.domain.jobs.models import RuntimeJob
from app.domain.jobs.repository import JobLease, JobRepository
from app.domain.jobs.schemas import RuntimeJobResponse
from app.domain.runtime.events import NoopRuntimeEventSink, RuntimeEventSink
from app.domain.runtime.models import (
    RunCapabilityBinding,
    Run,
    SessionTokenSnapshot,
    SkillInvocation,
    TerminalEvent,
    TerminalEventPart,
    TerminalSession,
    TraceEvent,
)
from app.domain.runtime.repository import RuntimeRepository
from app.domain.runtime.schemas import (
    AppendTerminalEventRequest,
    BindingRequirementResponse,
    CreateInvocationRequest,
    InvocationResponse,
    ReplayDetailResponse,
    ReplayTimelineItem,
    ResolveRunBindingsRequest,
    RunCapabilityBindingResponse,
    RunResponse,
    RunTaskStatusResponse,
    SessionTokenSnapshotResponse,
    TerminalEventAppendResponse,
    TerminalEventResponse,
    TerminalSessionDetailResponse,
    TerminalSessionResponse,
    TerminalTranscriptSummary,
    TraceEventResponse,
    TerminalEventPartInput,
    TerminalEventPartResponse,
)
from app.domain.skills.exceptions import SkillsError, SkillNotFoundError, SkillValidationError
from app.domain.skills.models import now_utc
from app.gateway.inference import LlmAttachment, LlmInferenceGateway, MULTIMODAL_ROUTE_KEY, TEXT_ROUTE_KEY
from app.infra.object_store import ObjectStoreService

LOGGER = logging.getLogger(__name__)
RUNNER_MAX_IMAGE_ATTACHMENTS = 4

RUNTIME_LLM_LANGUAGE_POLICY = """平台级输出语言要求：
- 所有面向终端用户展示的自然语言必须使用简体中文。
- 如果当前节点要求输出 JSON，JSON 字段名和 decision 等协议枚举值保持英文协议值；next_phase 是兼容可选字段。
- reason、terminal_message、final_response、summary 等自然语言字段值必须使用简体中文。
- 不要因为附件内容、文件名、模型默认行为或上游 prompt 语言而改用英文。
- 如果当前节点要求只输出 JSON，不要在 JSON 外追加任何说明。"""


class RuntimeStepTimeoutError(TimeoutError):
    """Raised when one Execution Graph node exhausts its shared time budget."""


class RuntimeLeaseLostError(RuntimeError):
    """Raised when an attempt no longer owns the durable runtime job."""


class RuntimeService:
    """Invocation, RuntimeKernel and Replay service for the issue #1 MVP slice."""

    def __init__(
        self,
        *,
        settings: Settings,
        inference_gateway: LlmInferenceGateway,
        repository: RuntimeRepository | None = None,
        job_repository: JobRepository | None = None,
        agent_prompt_service: AgentPromptService | None = None,
        object_store: ObjectStoreService | None = None,
        agent_harness_service: AgentHarnessService,
        runtime_event_sink: RuntimeEventSink | None = None,
        lease_is_healthy: Callable[[], bool] | None = None,
    ) -> None:
        self.settings = settings
        self.inference_gateway = inference_gateway
        self.repository = repository or RuntimeRepository()
        self.job_repository = job_repository or JobRepository()
        self.agent_prompt_service = agent_prompt_service or AgentPromptService()
        self.object_store = object_store
        self.agent_harness_service = agent_harness_service
        self.runtime_event_sink = runtime_event_sink or NoopRuntimeEventSink()
        self._lease_is_healthy = lease_is_healthy
        self._active_job_lease: JobLease | None = None

    def create_invocation(self, session: Session, payload: CreateInvocationRequest) -> InvocationResponse:
        skill_definition = self.repository.get_skill_definition_by_key(session, payload.skill_key)
        if not skill_definition or skill_definition.status == "archived":
            raise SkillNotFoundError("未找到可调用的 Skill。", details={"skill_key": payload.skill_key})

        if payload.compile_artifact_id:
            artifact = self.repository.get_artifact(session, payload.compile_artifact_id)
            if not artifact:
                raise SkillValidationError("指定编译产物不存在。", details={"compile_artifact_id": payload.compile_artifact_id})
            if artifact.status != "ready":
                raise SkillValidationError("指定编译产物尚不可运行。", details={"compile_artifact_id": payload.compile_artifact_id})
            skill_version = self.repository.get_skill_version(session, artifact.skill_version_id)
            if not skill_version or skill_version.skill_definition_id != skill_definition.id:
                raise SkillValidationError("指定编译产物不属于当前 Skill。", details={"compile_artifact_id": payload.compile_artifact_id})
        else:
            skill_version = self.repository.get_skill_version(session, skill_definition.latest_published_version_id)
            if not skill_version or skill_version.status != "published":
                raise SkillValidationError("当前 Skill 尚无已发布版本，无法发起运行。")

            artifact = self.repository.get_latest_ready_artifact(session, skill_version.id)
            if not artifact:
                raise SkillValidationError("当前 Skill 尚无成功编译产物，无法发起运行。")
        artifact_object = self.repository.get_artifact_object(session, artifact.artifact_object_id)
        artifact_payload = artifact_object.content_json if artifact_object else {}
        gateway_type = "terminal" if payload.gateway_type in {"web", "terminal"} else payload.gateway_type
        terminal_context = payload.terminal_context or {
            "terminal_kind": "web" if payload.gateway_type in {"web", "terminal"} else payload.gateway_type
        }

        invocation = SkillInvocation(
            skill_definition_id=skill_definition.id,
            skill_version_id=skill_version.id,
            compile_artifact_id=artifact.id,
            gateway_type=gateway_type,
            input_envelope=payload.input_envelope,
            terminal_context=terminal_context,
            binding_preferences=payload.binding_preferences,
            status="running",
        )
        session.add(invocation)
        session.flush()

        run = Run(
            invocation_id=invocation.id,
            skill_definition_id=skill_definition.id,
            skill_version_id=skill_version.id,
            compile_artifact_id=artifact.id,
            status="waiting_runtime",
            runtime_phase=self._initial_phase(artifact_payload),
        )
        session.add(run)
        session.flush()

        terminal_session = TerminalSession(
            run_id=run.id,
            mode=str(terminal_context.get("terminal_kind") or "web"),
            status="open",
        )
        session.add(terminal_session)
        session.flush()
        run.terminal_session_id = terminal_session.id
        bindings = self._ensure_default_run_bindings(
            session,
            run=run,
            terminal_session=terminal_session,
            terminal_context=terminal_context,
            binding_preferences=payload.binding_preferences,
        )
        self._append_trace_event(
            session,
            run=run,
            phase="binding",
            event_type="binding.resolved",
            payload={"bindings": [self._binding_payload(item) for item in bindings]},
        )

        initial_token = self._build_initial_token({}, artifact_payload)
        session.add(
            SessionTokenSnapshot(
                run_id=run.id,
                seq_no=0,
                token_payload=initial_token,
                enabled_set=[node["id"] for node in self._enabled_nodes(artifact_payload, initial_token)],
                selection_summary={"selected": None, "reason": "initial"},
                snapshot_hash=self._hash_payload(initial_token),
            )
        )
        self._ensure_runtime_job_pending(session, run)
        self._commit_and_publish(
            session,
            run_id=run.id,
            previous_terminal_seq=0,
            previous_trace_seq=0,
        )
        LOGGER.info(
            "runtime invocation created",
            extra={
                "skill_id": skill_definition.id,
                "skill_key": skill_definition.key,
                "skill_version_id": skill_version.id,
                "invocation_id": invocation.id,
                "run_id": run.id,
                "artifact_id": artifact.id,
            },
        )

        refreshed = self.repository.get_invocation(session, invocation.id)
        if not refreshed:
            raise SkillNotFoundError("Invocation 创建后无法读取。")
        return self._build_invocation_response(session, refreshed)

    def fork_invocation_from_snapshot(
        self,
        session: Session,
        *,
        source_run_id: str,
        snapshot_seq: int,
        terminal_seq: int,
        terminal_context: dict[str, Any],
        input_envelope: dict[str, Any] | None = None,
    ) -> InvocationResponse:
        source_run = self.repository.get_run(session, source_run_id)
        if not source_run:
            raise SkillNotFoundError("未找到可 fork 的源 Run。", details={"run_id": source_run_id})
        source_invocation = self.repository.get_invocation(session, source_run.invocation_id)
        if not source_invocation:
            raise SkillNotFoundError("未找到源 Invocation。", details={"run_id": source_run_id})
        artifact = self.repository.get_artifact(session, source_run.compile_artifact_id)
        if not artifact:
            raise SkillNotFoundError("未找到源 Run 的编译产物。", details={"run_id": source_run_id})
        artifact_object = self.repository.get_artifact_object(session, artifact.artifact_object_id)
        artifact_payload = artifact_object.content_json if artifact_object else {}
        source_snapshots = self.repository.list_snapshots(session, source_run_id)
        source_snapshot = next((item for item in source_snapshots if item.seq_no == snapshot_seq), None)
        if not source_snapshot:
            raise SkillValidationError(
                "指定 fork snapshot 不存在。",
                details={"run_id": source_run_id, "snapshot_seq": snapshot_seq},
            )

        invocation = SkillInvocation(
            skill_definition_id=source_run.skill_definition_id,
            skill_version_id=source_run.skill_version_id,
            compile_artifact_id=source_run.compile_artifact_id,
            gateway_type="terminal",
            input_envelope=input_envelope or {},
            terminal_context=terminal_context,
            binding_preferences=source_invocation.binding_preferences,
            status="running",
        )
        session.add(invocation)
        session.flush()

        token = self._compact_runtime_token(source_snapshot.token_payload)
        run = Run(
            invocation_id=invocation.id,
            skill_definition_id=source_run.skill_definition_id,
            skill_version_id=source_run.skill_version_id,
            compile_artifact_id=source_run.compile_artifact_id,
            status="waiting_input" if token.get("status") == "waiting" else "queued",
            runtime_phase=self._runtime_phase_from_token(token),
            started_at=now_utc(),
        )
        session.add(run)
        session.flush()

        terminal_session = TerminalSession(
            run_id=run.id,
            mode=str(terminal_context.get("terminal_kind") or "web"),
            status="open",
        )
        session.add(terminal_session)
        session.flush()
        run.terminal_session_id = terminal_session.id
        bindings = self._ensure_default_run_bindings(
            session,
            run=run,
            terminal_session=terminal_session,
            terminal_context=terminal_context,
            binding_preferences=source_invocation.binding_preferences,
        )
        self._append_trace_event(
            session,
            run=run,
            phase="binding",
            event_type="binding.resolved",
            payload={"bindings": [self._binding_payload(item) for item in bindings]},
        )

        copied_terminal_events: list[dict[str, Any]] = []
        for source_event in self.repository.list_terminal_events(session, source_run_id, to_seq=terminal_seq):
            source_parts = [
                TerminalEventPartInput(
                    part_id=part.part_id,
                    kind=part.kind,
                    mime_type=part.mime_type,
                    text=part.text_inline,
                    artifact_object_id=part.artifact_object_id,
                    size_bytes=part.size_bytes,
                    checksum=part.checksum,
                    metadata=part.part_metadata,
                )
                for part in self.repository.list_terminal_event_parts(session, source_event.id)
            ]
            copied = self._append_terminal_event(
                session,
                run=run,
                terminal_session=terminal_session,
                direction=source_event.direction,
                event_kind=source_event.event_kind,
                mime_type=source_event.mime_type,
                payload_inline=source_event.payload_inline,
                artifact_object_id=source_event.artifact_object_id,
                parts=source_parts,
                binding_id=self._default_binding_id(bindings, source_event.direction),
                source_ref={
                    **(source_event.source_ref or {}),
                    "forked_from": {
                        "run_id": source_run_id,
                        "terminal_event_id": source_event.id,
                        "seq_no": source_event.seq_no,
                    },
                },
                external_event_id=f"fork:{run.id}:terminal:{source_event.seq_no}",
                occurred_at=source_event.occurred_at,
            )
            copied_terminal_events.append(self._terminal_event_token_payload_with_parts(session, copied))

        terminal = token.setdefault("terminal", {})
        terminal["events"] = copied_terminal_events
        terminal["latest_seq"] = run.latest_terminal_seq
        token.setdefault("metadata", {})["terminal_cursor"] = run.latest_terminal_seq
        session.add(
            SessionTokenSnapshot(
                run_id=run.id,
                seq_no=0,
                token_payload=token,
                enabled_set=[node["id"] for node in self._enabled_nodes(artifact_payload, token)],
                selection_summary={
                    "selected": None,
                    "reason": "forked",
                    "source_run_id": source_run_id,
                    "source_snapshot_seq": snapshot_seq,
                    "source_terminal_seq": terminal_seq,
                },
                snapshot_hash=self._hash_payload(token),
            )
        )
        self._append_trace_event(
            session,
            run=run,
            phase="fork",
            event_type="runtime.fork.created",
            payload={
                "source_run_id": source_run_id,
                "source_snapshot_seq": snapshot_seq,
                "source_terminal_seq": terminal_seq,
                "terminal_prefix_count": len(copied_terminal_events),
            },
        )
        if run.status != "waiting_input":
            session.add(
                RuntimeJob(
                    job_type="runtime",
                    status="pending",
                    payload={"run_id": run.id},
                    run_id=run.id,
                    dedupe_key=f"job:runtime:{run.id}",
                    available_at=now_utc() + timedelta(seconds=2),
                    max_attempts=self.settings.runtime_job_max_attempts,
                )
            )
        session.commit()
        refreshed = self.repository.get_invocation(session, invocation.id)
        if not refreshed:
            raise SkillNotFoundError("Fork Invocation 创建后无法读取。")
        return self._build_invocation_response(session, refreshed)

    def process_run(
        self,
        session: Session,
        run_id: str,
        *,
        job_lease: JobLease | None = None,
    ) -> Run:
        self._active_job_lease = job_lease
        run = self.repository.get_run(session, run_id)
        if not run:
            raise SkillNotFoundError("未找到 Run。", details={"run_id": run_id})
        job = self.job_repository.get_runtime_job_by_dedupe_key_for_update(
            session,
            f"job:runtime:{run.id}",
        )
        if job_lease is not None:
            if job is None or job.id != job_lease.job_id:
                raise RuntimeLeaseLostError("Runtime job 与当前 lease 不匹配。")
            self._validate_active_job_lease(session)
        if run.status in {"succeeded", "failed", "cancelled", "aborted"}:
            if job is not None:
                job.status = "succeeded" if run.status in {"succeeded", "aborted"} else run.status
                job.worker_name = ""
                job.lease_until = None
                job.finished_at = job.finished_at or now_utc()
                if run.status == "failed":
                    job.last_error = run.exit_reason
                session.commit()
            return run
        publish_terminal_seq = run.latest_terminal_seq
        publish_trace_seq = run.latest_trace_seq

        artifact = self.repository.get_artifact(session, run.compile_artifact_id)
        if not artifact:
            raise SkillNotFoundError("Run 关联的编译产物不存在。")
        artifact_object = self.repository.get_artifact_object(session, artifact.artifact_object_id)
        artifact_payload = artifact_object.content_json if artifact_object else {}
        invocation = self.repository.get_invocation(session, run.invocation_id)
        if not invocation:
            raise SkillNotFoundError("Run 关联的 Invocation 不存在。")

        with log_context(
            run_id=run.id,
            invocation_id=invocation.id,
            skill_id=run.skill_definition_id,
            skill_version_id=run.skill_version_id,
        ):
            LOGGER.info("runtime loop started")

        if job:
            if job.status != "running":
                job.attempt_no += 1
            job.status = "running"

        run.started_at = run.started_at or now_utc()
        invocation.status = "running"
        session.flush()

        token: dict[str, Any] = {}
        try:
            token = self.repository.list_snapshots(session, run.id)[-1].token_payload
            token = self._compact_runtime_token(token)
            turn_input_accept_after_seq = int(_get_path(token, "metadata.terminal_cursor") or 0)
            if self._token_is_waiting_for_terminal_input(token):
                token = self._sync_terminal_events(session, run=run, token=token)
            run.status = "running"
            run.runtime_phase = str(token.get("phase") or self._initial_phase(artifact_payload))
            if token.get("status") == "waiting":
                run.status = "waiting_input"
                run.runtime_phase = self._runtime_phase_from_token(token)
                invocation.status = "running"
                self._finish_runtime_job_turn(session=session, job=job, run=run, token=token, status="succeeded")
                self._sync_runtime_job_metrics(job, token)
                publish_terminal_seq, publish_trace_seq = self._commit_and_publish(
                    session,
                    run_id=run.id,
                    previous_terminal_seq=publish_terminal_seq,
                    previous_trace_seq=publish_trace_seq,
                    terminal_directions={"output"},
                )
                LOGGER.info("runtime loop waiting for terminal evidence")
                return run
            # Persist the attempt marker and release the initial read transaction.
            # The selected node may now perform object-store/model/tool I/O without
            # retaining a Run row lock or a database checkout.
            session.commit()
            session.info.pop("runtime_lease_fence", None)
            max_steps = int(artifact_payload.get("policies", {}).get("max_steps") or 16)
            with start_span(
                "runtime.loop",
                run_id=run.id,
                invocation_id=invocation.id,
                skill_id=run.skill_definition_id,
                skill_version_id=run.skill_version_id,
                compile_artifact_id=run.compile_artifact_id,
            ) as loop_span:
                for _ in range(max_steps):
                    if self._halt_success(artifact_payload, token):
                        loop_span.set_attribute("runtime.exit_reason", "halt_success")
                        break
                    if self._halt_aborted(artifact_payload, token):
                        loop_span.set_attribute("runtime.exit_reason", "halt_aborted")
                        break

                    enabled_nodes = self._enabled_nodes(artifact_payload, token)
                    if not enabled_nodes:
                        if self._halt_wait(artifact_payload, token):
                            run.status = "waiting_input"
                            run.runtime_phase = self._runtime_phase_from_token(token)
                            invocation.status = "running"
                            self._finish_runtime_job_turn(session=session, job=job, run=run, token=token, status="succeeded")
                            self._sync_runtime_job_metrics(job, token)
                            publish_terminal_seq, publish_trace_seq = self._commit_and_publish(
                                session,
                                run_id=run.id,
                                previous_terminal_seq=publish_terminal_seq,
                                previous_trace_seq=publish_trace_seq,
                                terminal_directions={"output"},
                            )
                            LOGGER.info("runtime loop waiting for input")
                            return run
                        raise RuntimeError("Runtime deadlock: no enabled nodes and no wait condition matched.")

                    node = self._select_node(enabled_nodes)
                    expected_snapshot_seq = run.latest_snapshot_seq
                    with start_span(
                        "runtime.actor",
                        run_id=run.id,
                        invocation_id=invocation.id,
                        skill_id=run.skill_definition_id,
                        node_id=node.get("id"),
                        node_kind=node.get("kind"),
                    ) as actor_span:
                        try:
                            LOGGER.info(
                                "runtime actor selected",
                                extra={"node_id": node.get("id"), "node_kind": node.get("kind")},
                            )
                            observation = self._execute_node(
                                session=session,
                                run=run,
                                node=node,
                                token=token,
                                artifact_payload=artifact_payload,
                            )
                        except Exception as exc:
                            record_span_exception(actor_span, exc)
                            raise
                    run = self._lock_runtime_write(
                        session,
                        run_id=run.id,
                        expected_snapshot_seq=expected_snapshot_seq,
                    )
                    token = self._merge_observation(node=node, token=token, observation=observation)
                    token, entered_wait = self._apply_node_interaction(
                        session,
                        run=run,
                        token=token,
                        node=node,
                        observation=observation,
                        artifact_payload=artifact_payload,
                        wait_input_accept_after_seq=turn_input_accept_after_seq,
                    )
                    token = self._append_runtime_step(
                        session,
                        run=run,
                        token=token,
                        node=node,
                        observation=observation,
                        enabled_after=[]
                        if entered_wait
                        else [item["id"] for item in self._enabled_nodes(artifact_payload, token)],
                    )
                    if entered_wait:
                        run.status = "waiting_input"
                        run.runtime_phase = self._runtime_phase_from_token(token)
                        invocation.status = "running"
                        self._finish_runtime_job_turn(session=session, job=job, run=run, token=token, status="succeeded")
                        self._sync_runtime_job_metrics(job, token)
                        publish_terminal_seq, publish_trace_seq = self._commit_and_publish(
                            session,
                            run_id=run.id,
                            previous_terminal_seq=publish_terminal_seq,
                            previous_trace_seq=publish_trace_seq,
                            terminal_directions={"output"},
                            publish_task_status=True,
                        )
                        LOGGER.info("runtime loop entered wait checkpoint")
                        return run
                    self._sync_runtime_job_metrics(job, token)
                    publish_terminal_seq, publish_trace_seq = self._commit_and_publish(
                        session,
                        run_id=run.id,
                        previous_terminal_seq=publish_terminal_seq,
                        previous_trace_seq=publish_trace_seq,
                        terminal_directions={"output"},
                        publish_task_status=True,
                    )
                else:
                    raise RuntimeError(f"Runtime exceeded max_steps={max_steps}.")

            if self._halt_aborted(artifact_payload, token):
                final_output = str(_get_path(token, "outputs.final_response") or "")
                run.status = "aborted"
                run.runtime_phase = "aborted"
                run.exit_reason = self._truncate_exit_reason(final_output or "aborted")
                run.final_output = final_output
                run.ended_at = now_utc()
                invocation.status = "aborted"
                self._close_terminal_session(session, run)
                self._finish_runtime_job_turn(session=session, job=job, run=run, token=token, status="succeeded")
                self._sync_runtime_job_metrics(job, token)
                publish_terminal_seq, publish_trace_seq = self._commit_and_publish(
                    session,
                    run_id=run.id,
                    previous_terminal_seq=publish_terminal_seq,
                    previous_trace_seq=publish_trace_seq,
                    terminal_directions={"output"},
                    publish_task_status=True,
                )
                LOGGER.info("runtime loop aborted", extra={"final_output_length": len(run.final_output or "")})
                return run

            if not self._halt_success(artifact_payload, token):
                raise RuntimeError("Runtime stopped without success halt condition.")

            run.status = "succeeded"
            run.runtime_phase = "completed"
            run.exit_reason = "completed"
            run.final_output = str(_get_path(token, "outputs.final_response") or "")
            run.ended_at = now_utc()
            invocation.status = "succeeded"
            self._close_terminal_session(session, run)
            self._finish_runtime_job_turn(session=session, job=job, run=run, token=token, status="succeeded")
            self._sync_runtime_job_metrics(job, token)
            publish_terminal_seq, publish_trace_seq = self._commit_and_publish(
                session,
                run_id=run.id,
                previous_terminal_seq=publish_terminal_seq,
                previous_trace_seq=publish_trace_seq,
                terminal_directions={"output"},
                publish_task_status=True,
            )
            LOGGER.info("runtime loop succeeded", extra={"final_output_length": len(run.final_output or "")})
            return run
        except Exception as exc:
            if isinstance(exc, RuntimeLeaseLostError):
                session.rollback()
                raise
            if self._is_recoverable_terminal_turn_failure(token):
                self._recover_terminal_turn_failure(
                    session,
                    run=run,
                    invocation=invocation,
                    token=token,
                    error=exc,
                    job=job,
                )
                self._sync_runtime_job_metrics(job, token)
                publish_terminal_seq, publish_trace_seq = self._commit_and_publish(
                    session,
                    run_id=run.id,
                    previous_terminal_seq=publish_terminal_seq,
                    previous_trace_seq=publish_trace_seq,
                    terminal_directions={"output"},
                    publish_task_status=True,
                )
                LOGGER.exception("runtime turn processing failed; returned to waiting input", extra={"error": str(exc)})
                return run

            if isinstance(exc, RuntimeStepTimeoutError):
                # Initialization/instruction turns are retried by the durable job
                # worker. Do not publish a partially executed node as final state.
                session.rollback()
                raise

            run.status = "failed"
            run.runtime_phase = "failed"
            run.exit_reason = str(exc)
            run.ended_at = now_utc()
            invocation.status = "failed"
            self._finish_runtime_job_turn(session=session, job=job, run=run, token=token, status="failed", last_error=str(exc))
            self._sync_runtime_job_metrics(job, token)
            failure_trace = self._append_trace_event(
                session,
                run=run,
                phase="failed",
                event_type="runtime.failed",
                payload=self._runtime_exception_trace_payload(exc, recoverable=False),
            )
            session.flush()
            self._append_runtime_failure_terminal_event(
                session,
                run=run,
                error=str(exc),
                trace_event=failure_trace,
            )
            self._close_terminal_session(session, run)
            publish_terminal_seq, publish_trace_seq = self._commit_and_publish(
                session,
                run_id=run.id,
                previous_terminal_seq=publish_terminal_seq,
                previous_trace_seq=publish_trace_seq,
                terminal_directions={"output"},
                publish_task_status=True,
            )
            LOGGER.exception("runtime loop failed", extra={"error": str(exc)})
            return run

    def finalize_exhausted_job(
        self,
        session: Session,
        *,
        job_id: str,
        error_message: str,
    ) -> bool:
        """Idempotently terminate the domain state for an exhausted runtime job."""

        self._active_job_lease = None
        job = self.job_repository.get_runtime_job_for_update(session, job_id)
        if job is None or job.job_type != "runtime" or not job.run_id:
            return False
        run = self.repository.get_run_for_update(session, job.run_id)
        if run is None or run.status in {"succeeded", "failed", "cancelled", "aborted"}:
            return False

        previous_terminal_seq = run.latest_terminal_seq
        previous_trace_seq = run.latest_trace_seq
        reason = error_message.strip() or "Runtime job attempts exhausted."
        run.status = "failed"
        run.runtime_phase = "failed"
        run.exit_reason = self._truncate_exit_reason(reason)
        run.ended_at = now_utc()
        invocation = self.repository.get_invocation(session, run.invocation_id)
        if invocation is not None:
            invocation.status = "failed"
        job.status = "failed"
        job.worker_name = ""
        job.lease_until = None
        job.last_error = reason
        failure_trace = self._append_trace_event(
            session,
            run=run,
            phase="failed",
            event_type="runtime.job.attempts_exhausted",
            payload={"error": reason, "job_id": job.id, "recoverable": False},
        )
        session.flush()
        self._append_runtime_failure_terminal_event(
            session,
            run=run,
            error=reason,
            trace_event=failure_trace,
        )
        self._close_terminal_session(session, run)
        self._commit_and_publish(
            session,
            run_id=run.id,
            previous_terminal_seq=previous_terminal_seq,
            previous_trace_seq=previous_trace_seq,
            terminal_directions={"output"},
            publish_task_status=True,
        )
        return True

    def list_invocations(
        self,
        session: Session,
        *,
        skill_key: str | None = None,
        status: str | None = None,
    ) -> list[InvocationResponse]:
        return [
            self._build_invocation_response(session, item)
            for item in self.repository.list_invocations(session, skill_key=skill_key, status=status)
        ]

    def get_invocation(self, session: Session, invocation_id: str) -> InvocationResponse:
        invocation = self.repository.get_invocation(session, invocation_id)
        if not invocation:
            raise SkillNotFoundError("未找到 Invocation。", details={"invocation_id": invocation_id})
        return self._build_invocation_response(session, invocation)

    def list_runs(
        self,
        session: Session,
        *,
        status: Sequence[str] | None = None,
        skill_id: str | None = None,
    ) -> list[RunResponse]:
        return [
            self._build_run_response(session, item)
            for item in self.repository.list_runs(session, status=status, skill_id=skill_id)
        ]

    def get_run(self, session: Session, run_id: str) -> RunResponse:
        run = self.repository.get_run(session, run_id)
        if not run:
            raise SkillNotFoundError("未找到 Run。", details={"run_id": run_id})
        return self._build_run_response(session, run)

    def get_run_task_status(self, session: Session, run_id: str) -> RunTaskStatusResponse:
        run = self.repository.get_run(session, run_id)
        if not run:
            raise SkillNotFoundError("未找到 Run。", details={"run_id": run_id})

        artifact = self.repository.get_artifact(session, run.compile_artifact_id)
        artifact_object = self.repository.get_artifact_object(session, artifact.artifact_object_id) if artifact else None
        artifact_payload = artifact_object.content_json if artifact_object and isinstance(artifact_object.content_json, dict) else {}
        snapshot = self.repository.get_latest_snapshot(session, run.id)
        token = snapshot.token_payload if snapshot and isinstance(snapshot.token_payload, dict) else {}
        return self._build_run_task_status_response(
            session=session,
            run=run,
            artifact_payload=artifact_payload,
            token=token,
            snapshot=snapshot,
        )

    def cancel_run(self, session: Session, run_id: str, *, reason: str = "cancelled by user") -> RunResponse:
        job = self.job_repository.get_runtime_job_by_dedupe_key_for_update(
            session,
            f"job:runtime:{run_id}",
        )
        run = self.repository.get_run_for_update(session, run_id)
        if not run:
            raise SkillNotFoundError("未找到 Run。", details={"run_id": run_id})
        if run.status == "cancelled":
            return self._build_run_response(session, run)
        if run.status in {"succeeded", "failed", "aborted"}:
            raise SkillValidationError("Run 已结束，不能取消。", details={"run_id": run_id, "status": run.status})

        previous_terminal_seq = run.latest_terminal_seq
        previous_trace_seq = run.latest_trace_seq
        invocation = self.repository.get_invocation(session, run.invocation_id)
        run.status = "cancelled"
        run.runtime_phase = "cancelled"
        run.exit_reason = reason
        run.ended_at = now_utc()
        if invocation:
            invocation.status = "cancelled"
        self._close_terminal_session(session, run)
        self._append_cancel_snapshot(session, run=run, reason=reason)
        self._append_trace_event(
            session,
            run=run,
            phase="cancelled",
            event_type="runtime.cancelled",
            payload={"reason": reason},
        )
        if job and job.status not in {"succeeded", "failed", "cancelled"}:
            job.status = "cancelled"
            job.last_error = reason
            job.worker_name = ""
            job.lease_until = None
            job.finished_at = now_utc()
        self._commit_and_publish(
            session,
            run_id=run_id,
            previous_terminal_seq=previous_terminal_seq,
            previous_trace_seq=previous_trace_seq,
            publish_task_status=True,
        )
        return self._build_run_response(session, run)

    def list_snapshots(self, session: Session, run_id: str) -> list[SessionTokenSnapshotResponse]:
        return [self._build_snapshot_response(item) for item in self.repository.list_snapshots(session, run_id)]

    def list_trace_events(
        self,
        session: Session,
        run_id: str,
        *,
        event_type: str | None = None,
    ) -> list[TraceEventResponse]:
        return [
            self._build_trace_event_response(item)
            for item in self.repository.list_trace_events(session, run_id, event_type=event_type)
        ]

    def runtime_event_envelope(self, session: Session, *, event_type: str, run_id: str, seq_no: int) -> dict[str, Any] | None:
        if event_type == "run.task_status.updated":
            response = self.get_run_task_status(session, run_id)
            return {
                "event_type": event_type,
                "run_id": run_id,
                "invocation_id": None,
                "seq_no": response.snapshot_seq,
                "occurred_at": response.updated_at.isoformat(),
                "payload": response.model_dump(mode="json"),
            }
        if event_type == "terminal.event.appended":
            record = self.repository.get_terminal_event_by_seq(session, run_id, seq_no)
            response = self._build_terminal_event_response(session, record) if record is not None else None
        elif event_type == "trace.event.appended":
            record = self.repository.get_trace_event_by_seq(session, run_id, seq_no)
            response = self._build_trace_event_response(record) if record is not None else None
        else:
            return None
        if response is None:
            return None
        return {
            "event_type": event_type,
            "run_id": run_id,
            "invocation_id": None,
            "seq_no": seq_no,
            "occurred_at": response.occurred_at.isoformat(),
            "payload": response.model_dump(mode="json"),
        }

    def get_terminal_session(self, session: Session, run_id: str) -> TerminalSessionDetailResponse:
        terminal_session = self.repository.get_terminal_session_for_run(session, run_id)
        if not terminal_session:
            raise SkillNotFoundError("未找到 Terminal Session。", details={"run_id": run_id})
        events = self.repository.list_terminal_events(session, run_id)
        return TerminalSessionDetailResponse(
            terminal_session=self._build_terminal_session_response(terminal_session),
            transcript_summary=TerminalTranscriptSummary(
                latest_seq=events[-1].seq_no if events else 0,
                event_count=len(events),
            ),
        )

    def list_terminal_events(
        self,
        session: Session,
        run_id: str,
        *,
        from_seq: int | None = None,
        to_seq: int | None = None,
    ) -> list[TerminalEventResponse]:
        if not self.repository.get_run(session, run_id):
            raise SkillNotFoundError("未找到 Run。", details={"run_id": run_id})
        return [
            self._build_terminal_event_response(session, item)
            for item in self.repository.list_terminal_events(session, run_id, from_seq=from_seq, to_seq=to_seq)
        ]

    def get_terminal_event(self, session: Session, run_id: str, event_id: str) -> TerminalEventResponse:
        event = self.repository.get_terminal_event(session, event_id)
        if not event or event.run_id != run_id:
            raise SkillNotFoundError("未找到 Terminal Event。", details={"run_id": run_id, "event_id": event_id})
        return self._build_terminal_event_response(session, event)

    def get_terminal_event_part(
        self,
        session: Session,
        run_id: str,
        event_id: str,
        part_id: str,
    ) -> TerminalEventPartResponse:
        event = self.repository.get_terminal_event(session, event_id)
        if not event or event.run_id != run_id:
            raise SkillNotFoundError("未找到 Terminal Event。", details={"run_id": run_id, "event_id": event_id})
        part = self.repository.get_terminal_event_part_by_public_id(session, terminal_event_id=event_id, part_id=part_id)
        if not part or part.run_id != run_id:
            raise SkillNotFoundError(
                "未找到 Terminal Event Part。",
                details={"run_id": run_id, "event_id": event_id, "part_id": part_id},
            )
        return self._build_terminal_event_part_response(part)

    def append_terminal_event(
        self,
        session: Session,
        run_id: str,
        payload: AppendTerminalEventRequest,
        *,
        idempotency_key: str | None = None,
        process_after_append: bool = False,
    ) -> TerminalEventAppendResponse:
        runtime_job = self.job_repository.get_runtime_job_by_dedupe_key_for_update(
            session,
            f"job:runtime:{run_id}",
        )
        run = self.repository.get_run_for_update(session, run_id)
        if not run:
            raise SkillNotFoundError("未找到 Run。", details={"run_id": run_id})

        external_event_id = payload.external_event_id or idempotency_key
        if external_event_id:
            existing = self.repository.get_terminal_event_by_external_id(
                session,
                run_id=run_id,
                external_event_id=external_event_id,
            )
            if existing:
                event_response = self._build_terminal_event_response(session, existing)
                return TerminalEventAppendResponse(
                    accepted=True,
                    event_id=existing.id,
                    seq_no=existing.seq_no,
                    event=event_response,
                )

        if run.status in {"succeeded", "failed", "cancelled", "aborted"}:
            raise SkillValidationError("Run 已结束，不能继续追加终端输入。", details={"run_id": run_id, "status": run.status})
        terminal_session = self.repository.get_terminal_session_for_run(session, run_id)
        if not terminal_session or terminal_session.status != "open":
            raise SkillValidationError("当前 Run 没有可用的 Terminal Session。", details={"run_id": run_id})

        previous_terminal_seq = run.latest_terminal_seq
        previous_trace_seq = run.latest_trace_seq
        event = self._append_terminal_event(
            session,
            run=run,
            terminal_session=terminal_session,
            direction=payload.direction,
            event_kind=payload.event_kind,
            mime_type=payload.mime_type,
            payload_inline=payload.payload_inline,
            artifact_object_id=payload.artifact_object_id,
            parts=payload.parts,
            binding_id=payload.binding_id,
            source_ref=payload.source.model_dump(),
            external_event_id=external_event_id,
            occurred_at=payload.occurred_at,
        )
        if payload.direction == "input":
            self._ensure_runtime_job_pending(session, run, existing_job=runtime_job)
        if run.status == "waiting_input" and process_after_append:
            self._commit_and_publish(
                session,
                run_id=run.id,
                previous_terminal_seq=previous_terminal_seq,
                previous_trace_seq=previous_trace_seq,
            )
            self.process_run(session, run.id)
            event = session.get(TerminalEvent, event.id) or event
        else:
            self._commit_and_publish(
                session,
                run_id=run.id,
                previous_terminal_seq=previous_terminal_seq,
                previous_trace_seq=previous_trace_seq,
            )
        event_response = self._build_terminal_event_response(session, event)
        return TerminalEventAppendResponse(
            accepted=True,
            event_id=event.id,
            seq_no=event.seq_no,
            event=event_response,
        )

    def list_binding_requirements(self, session: Session, run_id: str) -> list[BindingRequirementResponse]:
        if not self.repository.get_run(session, run_id):
            raise SkillNotFoundError("未找到 Run。", details={"run_id": run_id})
        return [
            BindingRequirementResponse(
                requirement_key="terminal.input",
                binding_type="terminal",
                capability="terminal.text.input.v1",
                direction="input",
            ),
            BindingRequirementResponse(
                requirement_key="terminal.output",
                binding_type="terminal",
                capability="terminal.text.output.v1",
                direction="output",
            ),
        ]

    def list_run_bindings(self, session: Session, run_id: str) -> list[RunCapabilityBindingResponse]:
        if not self.repository.get_run(session, run_id):
            raise SkillNotFoundError("未找到 Run。", details={"run_id": run_id})
        return [self._build_run_binding_response(item) for item in self.repository.list_run_bindings(session, run_id)]

    def get_run_binding(self, session: Session, run_id: str, binding_id: str) -> RunCapabilityBindingResponse:
        binding = self.repository.get_run_capability_binding(session, binding_id)
        if not binding or binding.run_id != run_id:
            raise SkillNotFoundError("未找到 Run Binding。", details={"run_id": run_id, "binding_id": binding_id})
        return self._build_run_binding_response(binding)

    def resolve_run_bindings(
        self,
        session: Session,
        run_id: str,
        payload: ResolveRunBindingsRequest,
    ) -> list[RunCapabilityBindingResponse]:
        self.job_repository.get_runtime_job_by_dedupe_key_for_update(
            session,
            f"job:runtime:{run_id}",
        )
        run = self.repository.get_run_for_update(session, run_id)
        if not run:
            raise SkillNotFoundError("未找到 Run。", details={"run_id": run_id})
        previous_terminal_seq = run.latest_terminal_seq
        previous_trace_seq = run.latest_trace_seq
        terminal_session = self.repository.get_terminal_session_for_run(session, run_id)
        if not terminal_session:
            raise SkillValidationError("当前 Run 没有 Terminal Session。", details={"run_id": run_id})

        for item in payload.bindings:
            existing = self.repository.get_run_binding_by_requirement(
                session,
                run_id=run_id,
                requirement_key=item.requirement_key,
            )
            if not existing:
                existing = RunCapabilityBinding(
                    run_id=run.id,
                    compile_artifact_id=run.compile_artifact_id,
                    requirement_key=item.requirement_key,
                    binding_type="terminal",
                    capability=self._capability_for_requirement(item.requirement_key),
                    target_kind=item.target_kind,
                    target_ref=item.target_ref or terminal_session.id,
                    channel=item.channel,
                    status="active",
                )
                session.add(existing)
            else:
                existing.target_kind = item.target_kind
                existing.target_ref = item.target_ref or terminal_session.id
                existing.channel = item.channel
                existing.status = "active"
        session.flush()
        bindings = self.repository.list_run_bindings(session, run_id)
        self._append_trace_event(
            session,
            run=run,
            phase="binding",
            event_type="binding.updated",
            payload={"bindings": [self._binding_payload(item) for item in bindings]},
        )
        self._commit_and_publish(
            session,
            run_id=run_id,
            previous_terminal_seq=previous_terminal_seq,
            previous_trace_seq=previous_trace_seq,
        )
        return [self._build_run_binding_response(item) for item in bindings]

    def list_runtime_jobs(
        self,
        session: Session,
        *,
        status: str | None = None,
        job_type: str | None = None,
    ) -> list[RuntimeJobResponse]:
        return [
            self._build_runtime_job_response(item)
            for item in self.job_repository.list_runtime_jobs(session, status=status, job_type=job_type)
        ]

    def build_replay(self, session: Session, run_id: str) -> ReplayDetailResponse:
        run = self.repository.get_run(session, run_id)
        if not run:
            raise SkillNotFoundError("未找到 Run。", details={"run_id": run_id})

        snapshots = self.list_snapshots(session, run_id)
        trace_events = self.list_trace_events(session, run_id)
        terminal_events = self.list_terminal_events(session, run_id)
        timeline = [self._build_timeline_item(event) for event in trace_events]
        timeline.extend(self._build_terminal_timeline_item(event) for event in terminal_events)
        timeline.sort(key=lambda item: (item.occurred_at, item.seq_no, item.event_type))
        return ReplayDetailResponse(
            run=self._build_run_response(session, run),
            timeline=timeline,
            snapshots=snapshots,
            trace_events=trace_events,
            terminal_events=terminal_events,
            bindings=self.list_run_bindings(session, run_id),
        )

    def _append_step(
        self,
        session: Session,
        *,
        run: Run,
        token: dict[str, Any],
        phase: str,
        event_type: str,
        observation: dict[str, Any],
        next_phase: str,
        summary: str,
    ) -> dict[str, Any]:
        next_token = json.loads(json.dumps(token, ensure_ascii=False))
        next_token["phase"] = next_phase
        next_token.setdefault("observations", {})[phase] = observation
        next_seq = run.latest_snapshot_seq + 1
        run.latest_snapshot_seq = next_seq
        run.runtime_phase = phase
        session.add(
            SessionTokenSnapshot(
                run_id=run.id,
                seq_no=next_seq,
                token_payload=next_token,
                enabled_set=[next_phase] if next_phase != "completed" else [],
                selection_summary={"selected": phase, "next_phase": next_phase},
                snapshot_hash=self._hash_payload(next_token),
            )
        )
        self._append_trace_event(
            session,
            run=run,
            phase=phase,
            event_type=event_type,
            payload={"observation": observation, "summary": summary},
        )
        session.flush()
        return next_token

    def _append_runtime_step(
        self,
        session: Session,
        *,
        run: Run,
        token: dict[str, Any],
        node: dict[str, Any],
        observation: dict[str, Any],
        enabled_after: list[str],
    ) -> dict[str, Any]:
        next_seq = run.latest_snapshot_seq + 1
        run.latest_snapshot_seq = next_seq
        run.runtime_phase = str(token.get("phase") or node["id"])
        trace_observation = self._observation_for_trace(observation)
        session.add(
            SessionTokenSnapshot(
                run_id=run.id,
                seq_no=next_seq,
                token_payload=token,
                enabled_set=enabled_after,
                selection_summary={"selected": node["id"], "kind": node.get("kind"), "next_enabled": enabled_after},
                snapshot_hash=self._hash_payload(token),
            )
        )
        event_type = "runtime.agent.completed" if _get_path(trace_observation, "runner.agent_key") == "psop.runner" else self._event_type_for_node(node)
        self._append_trace_event(
            session,
            run=run,
            phase=str(node.get("id")),
            event_type=event_type,
            payload={
                "node_id": node.get("id"),
                "node_kind": node.get("kind"),
                "observation": trace_observation,
                "summary": observation.get("summary") or observation.get("content") or observation.get("final_response") or "",
            },
        )
        if node.get("kind") == "terminal" and observation.get("final_response"):
            terminal_session = self.repository.get_terminal_session_for_run(session, run.id)
            if terminal_session:
                self._append_terminal_event(
                    session,
                    run=run,
                    terminal_session=terminal_session,
                    direction="output",
                    event_kind="terminal.text.output.v1",
                    mime_type="text/plain",
                    payload_inline=str(observation["final_response"]),
                    binding_id=None,
                    source_ref={"kind": "runtime", "node_id": str(node.get("id"))},
                )
        session.flush()
        return token

    def _apply_node_interaction(
        self,
        session: Session,
        *,
        run: Run,
        token: dict[str, Any],
        node: dict[str, Any],
        observation: dict[str, Any],
        artifact_payload: dict[str, Any],
        wait_input_accept_after_seq: int,
    ) -> tuple[dict[str, Any], bool]:
        interaction = node.get("interaction") if isinstance(node.get("interaction"), dict) else {}
        next_token = token
        entered_wait = False

        terminal_message = self._terminal_message_from_observation(observation)
        resolved_next_phase = ""
        decision = ""
        if self._node_is_evaluation(node):
            decision = str(observation.get("decision") or "").strip().lower()
            if decision in {"proceed", "complete", "abort"}:
                resolved_next_phase = self._resolve_evaluation_transition(
                    artifact_payload=artifact_payload,
                    node=node,
                    decision=decision,
                )
                if not resolved_next_phase:
                    raise RuntimeError(
                        f"Evaluation node `{node.get('id')}` has no runtime transition for decision `{decision}`."
                    )
            self._merge_evidence_progress_from_observation(
                token=next_token,
                node=node,
                observation=observation,
                artifact_payload=artifact_payload,
            )

        resumed_from_existing_input = False
        if interaction.get("wait_after_output"):
            checkpoint_accept_after_seq = self._wait_checkpoint_accept_after_seq(
                next_token,
                default_accept_after_seq=wait_input_accept_after_seq,
            )
            if self._has_prior_wait_checkpoint(next_token):
                checkpoint_accept_after_seq = max(checkpoint_accept_after_seq, int(run.latest_terminal_seq or 0))
            next_token = self._enter_wait_checkpoint(
                run=run,
                token=next_token,
                node=node,
                observation=observation,
                artifact_payload=artifact_payload,
                accept_after_seq=checkpoint_accept_after_seq,
            )
            resumed_from_existing_input = self._consume_pending_terminal_inputs_for_wait(
                session,
                run=run,
                token=next_token,
            )
            entered_wait = not resumed_from_existing_input
            self._append_trace_event(
                session,
                run=run,
                phase=str(node.get("id")),
                event_type="runtime.wait_checkpoint.entered",
                payload={
                    "wait": next_token.get("control", {}).get("wait", {}),
                    "resumed_from_existing_input": resumed_from_existing_input,
                },
            )

        suppress_terminal_output = resumed_from_existing_input or self._should_suppress_evaluation_terminal_output(
            artifact_payload=artifact_payload,
            node=node,
            decision=decision,
            resolved_next_phase=resolved_next_phase,
        )
        should_output = bool(interaction.get("output_to_terminal")) or (
            self._node_is_evaluation(node) and bool(terminal_message)
        )
        if should_output and not suppress_terminal_output and terminal_message:
            terminal_session = self.repository.get_terminal_session_for_run(session, run.id)
            if terminal_session and terminal_session.status == "open":
                self._append_terminal_event(
                    session,
                    run=run,
                    terminal_session=terminal_session,
                    direction="output",
                    event_kind="terminal.text.output.v1",
                    mime_type=str(interaction.get("output_mime_type") or "text/markdown"),
                    payload_inline=terminal_message,
                    binding_id=None,
                    source_ref={"kind": "runtime", "node_id": str(node.get("id")), "agent_key": "psop.runner"},
                )

        if self._node_is_evaluation(node):
            latest_evaluation = self._evaluation_summary(node, observation)
            if resolved_next_phase:
                latest_evaluation["resolved_next_phase"] = resolved_next_phase
            next_token.setdefault("control", {})["latest_evaluation"] = latest_evaluation
            decision = str(observation.get("decision") or "").strip().lower()
            if decision in {"retry", "need_more_evidence"}:
                wait = next_token.setdefault("control", {}).get("wait")
                if isinstance(wait, dict):
                    wait["status"] = "waiting"
                    wait["reason"] = str(observation.get("reason") or wait.get("reason") or "等待更多现场证据。")
                    next_token["status"] = "waiting"
                    next_token["phase"] = "waiting"
                    entered_wait = True
            elif decision == "abort":
                final_message = terminal_message or str(observation.get("reason") or "任务已终止。")
                requested_next_phase = str(observation.get("next_phase") or "").strip()
                next_token.setdefault("outputs", {})["final_response"] = final_message
                next_token.setdefault("control", {})["abort"] = {
                    "requested_by_node": str(node.get("id") or ""),
                    "reason": str(observation.get("reason") or ""),
                    "next_phase": requested_next_phase,
                    "resolved_next_phase": resolved_next_phase,
                    "occurred_at": now_utc().isoformat(),
                }
                if self._transition_enters_aborted_terminal(artifact_payload, resolved_next_phase):
                    next_token["phase"] = resolved_next_phase
                else:
                    next_token["status"] = "aborted"
                    next_token["phase"] = "aborted"
            elif decision in {"proceed", "complete"}:
                next_token["phase"] = resolved_next_phase

        return next_token, entered_wait

    def _resolve_evaluation_transition(
        self,
        *,
        artifact_payload: dict[str, Any],
        node: dict[str, Any],
        decision: str,
    ) -> str:
        node_ids = self._artifact_node_ids(artifact_payload)
        transition = self._transition_from_node_interaction(node=node, decision=decision)
        if transition:
            return transition if transition in node_ids else ""
        return self._transition_from_dependency_graph(
            artifact_payload=artifact_payload,
            node=node,
            decision=decision,
            node_ids=node_ids,
        )

    @staticmethod
    def _transition_from_node_interaction(*, node: dict[str, Any], decision: str) -> str:
        interaction = node.get("interaction") if isinstance(node.get("interaction"), dict) else {}
        transitions = interaction.get("transitions")
        if not isinstance(transitions, dict):
            return ""
        candidates = [decision]
        if decision == "proceed":
            candidates.append("continue")
        for key in candidates:
            raw = transitions.get(key)
            if isinstance(raw, str):
                return raw.strip()
            if isinstance(raw, dict):
                for field in ("phase", "next_phase", "to"):
                    value = raw.get(field)
                    if isinstance(value, str) and value.strip():
                        return value.strip()
        return ""

    def _transition_from_dependency_graph(
        self,
        *,
        artifact_payload: dict[str, Any],
        node: dict[str, Any],
        decision: str,
        node_ids: set[str],
    ) -> str:
        node_id = str(node.get("id") or "")
        edges = artifact_payload.get("dependency_graph_for_view")
        if not isinstance(edges, list):
            return ""
        targets = [
            str(edge.get("to") or "")
            for edge in edges
            if isinstance(edge, dict) and str(edge.get("from") or "") == node_id and str(edge.get("to") or "") in node_ids
        ]
        if not targets:
            return ""
        if decision == "abort":
            abort_targets = [
                target for target in targets if self._transition_enters_aborted_terminal(artifact_payload, target)
            ]
            if len(abort_targets) == 1:
                return abort_targets[0]
            if "terminal" in targets:
                return "terminal"
            return targets[0] if len(targets) == 1 else ""
        if decision == "complete":
            if "terminal" in targets:
                return "terminal"
            non_abort_targets = [
                target for target in targets if not self._transition_enters_aborted_terminal(artifact_payload, target)
            ]
            return non_abort_targets[0] if len(non_abort_targets) == 1 else ""
        non_abort_targets = [
            target
            for target in targets
            if not self._transition_enters_aborted_terminal(artifact_payload, target) and target != "terminal"
        ]
        if len(non_abort_targets) == 1:
            return non_abort_targets[0]
        return targets[0] if len(targets) == 1 else ""

    @staticmethod
    def _artifact_node_ids(artifact_payload: dict[str, Any]) -> set[str]:
        nodes = artifact_payload.get("nodes")
        if not isinstance(nodes, list):
            return set()
        return {
            str(node.get("id") or "")
            for node in nodes
            if isinstance(node, dict) and str(node.get("id") or "")
        }

    def _transition_enters_aborted_terminal(self, artifact_payload: dict[str, Any], phase: str) -> bool:
        node = self._artifact_node_by_id(artifact_payload, phase)
        if not node or str(node.get("kind") or "") != "terminal":
            return False
        if "abort" in str(node.get("id") or "").lower():
            return True
        merge = node.get("merge")
        if not isinstance(merge, list):
            return False
        return any(
            isinstance(operation, dict)
            and operation.get("op") == "set"
            and operation.get("path") == "status"
            and operation.get("value") == "aborted"
            for operation in merge
        )

    def _transition_enters_terminal(self, artifact_payload: dict[str, Any], phase: str) -> bool:
        node = self._artifact_node_by_id(artifact_payload, phase)
        return bool(node and str(node.get("kind") or "") == "terminal")

    @staticmethod
    def _artifact_node_by_id(artifact_payload: dict[str, Any], node_id: str) -> dict[str, Any] | None:
        nodes = artifact_payload.get("nodes")
        if not isinstance(nodes, list):
            return None
        for node in nodes:
            if isinstance(node, dict) and str(node.get("id") or "") == node_id:
                return node
        return None

    @staticmethod
    def _latest_consumed_terminal_seq(token: dict[str, Any]) -> int:
        control = token.get("control") if isinstance(token.get("control"), dict) else {}
        consumed = control.get("terminal_consumption") if isinstance(control.get("terminal_consumption"), list) else []
        seqs = [
            item.get("seq_no")
            for item in consumed
            if isinstance(item, dict) and isinstance(item.get("seq_no"), int)
        ]
        return max(seqs) if seqs else 0

    @classmethod
    def _wait_checkpoint_accept_after_seq(cls, token: dict[str, Any], *, default_accept_after_seq: int) -> int:
        candidates = [default_accept_after_seq, cls._latest_consumed_terminal_seq(token)]
        if cls._has_prior_wait_checkpoint(token):
            candidates.append(cls._latest_synced_terminal_seq(token))
        return max(candidates)

    @staticmethod
    def _has_prior_wait_checkpoint(token: dict[str, Any]) -> bool:
        control = token.get("control") if isinstance(token.get("control"), dict) else {}
        wait_checkpoints = control.get("wait_checkpoints")
        terminal_consumption = control.get("terminal_consumption")
        return (
            isinstance(control.get("wait"), dict)
            or (isinstance(wait_checkpoints, list) and bool(wait_checkpoints))
            or (isinstance(terminal_consumption, list) and bool(terminal_consumption))
        )

    @staticmethod
    def _latest_synced_terminal_seq(token: dict[str, Any]) -> int:
        seqs: list[int] = []
        for value in (
            _get_path(token, "metadata.terminal_cursor"),
            _get_path(token, "terminal.latest_seq"),
        ):
            try:
                seq = int(value or 0)
            except (TypeError, ValueError):
                continue
            seqs.append(seq)
        return max(seqs) if seqs else 0

    def _append_runner_attachment_warning(
        self,
        session: Session,
        *,
        run: Run,
        node: dict[str, Any],
        source_ref: str,
        artifact_object_id: str,
        reason: str,
    ) -> None:
        self._append_trace_event(
            session,
            run=run,
            phase=str(node.get("id") or ""),
            event_type="runtime.runner.attachment.warning",
            payload={
                "node_id": str(node.get("id") or ""),
                "source_ref": source_ref,
                "artifact_object_id": artifact_object_id,
                "reason": reason,
            },
        )

    @staticmethod
    def _node_is_evaluation(node: dict[str, Any]) -> bool:
        interaction = node.get("interaction") if isinstance(node.get("interaction"), dict) else {}
        return bool(interaction.get("evaluation")) or str(node.get("id") or "").startswith("evaluate_")

    @staticmethod
    def _terminal_message_from_observation(observation: dict[str, Any]) -> str:
        value = (
            observation.get("terminal_message")
            or observation.get("content")
            or observation.get("final_response")
            or observation.get("summary")
        )
        return str(value).strip() if value is not None else ""

    @staticmethod
    def _evaluation_summary(node: dict[str, Any], observation: dict[str, Any]) -> dict[str, Any]:
        summary = {
            "node_id": str(node.get("id") or ""),
            "decision": str(observation.get("decision") or ""),
            "reason": str(observation.get("reason") or ""),
            "next_phase": str(observation.get("next_phase") or ""),
            "terminal_message": str(observation.get("terminal_message") or ""),
        }
        if isinstance(observation.get("evidence_assessment"), dict):
            summary["evidence_assessment"] = observation["evidence_assessment"]
        if observation.get("final_response"):
            summary["final_response"] = str(observation.get("final_response") or "")
        return summary

    def _should_suppress_evaluation_terminal_output(
        self,
        *,
        artifact_payload: dict[str, Any],
        node: dict[str, Any],
        decision: str,
        resolved_next_phase: str,
    ) -> bool:
        return (
            self._node_is_evaluation(node)
            and decision in {"complete", "abort"}
            and bool(resolved_next_phase)
            and self._transition_enters_terminal(artifact_payload, resolved_next_phase)
        )

    @staticmethod
    def _enter_wait_checkpoint(
        *,
        run: Run,
        token: dict[str, Any],
        node: dict[str, Any],
        observation: dict[str, Any],
        artifact_payload: dict[str, Any],
        accept_after_seq: int,
    ) -> dict[str, Any]:
        interaction = node.get("interaction") if isinstance(node.get("interaction"), dict) else {}
        checkpoint_id = str(interaction.get("checkpoint_id") or f"{node.get('id')}:wait")
        observation_expected_inputs = observation.get("expected_inputs")
        if isinstance(observation_expected_inputs, list) and observation_expected_inputs:
            expected_inputs = RuntimeService._normalize_wait_expected_inputs(observation_expected_inputs)
        else:
            expected_inputs = interaction.get("expected_inputs") if isinstance(interaction.get("expected_inputs"), list) else []
        wait = {
            "status": "waiting",
            "checkpoint_id": checkpoint_id,
            "workflow_step_id": str(interaction.get("workflow_step_id") or node.get("id") or ""),
            "reason": str(observation.get("wait_reason") or interaction.get("wait_reason") or "等待用户提交现场证据。"),
            "expected_inputs": expected_inputs,
            "resume_phase": str(interaction.get("resume_phase") or f"evaluate_{node.get('id')}"),
            "entered_by_node": str(node.get("id") or ""),
            "entered_at": now_utc().isoformat(),
            "run_id": run.id,
            "input_window": {
                "accept_after_seq": accept_after_seq,
                "policy": "checkpoint_scoped",
            },
            "evidence": [],
        }
        control = token.setdefault("control", {})
        control["wait"] = wait
        control["evidence_progress"] = RuntimeService._build_evidence_progress(
            wait=wait,
            artifact_payload=artifact_payload,
            updated_by_node=str(node.get("id") or ""),
        )
        control.setdefault("wait_checkpoints", []).append(
            {
                "checkpoint_id": checkpoint_id,
                "workflow_step_id": wait["workflow_step_id"],
                "entered_by_node": wait["entered_by_node"],
                "entered_at": wait["entered_at"],
            }
        )
        token["status"] = "waiting"
        token["phase"] = "waiting"
        return token

    @classmethod
    def _build_evidence_progress(
        cls,
        *,
        wait: dict[str, Any],
        artifact_payload: dict[str, Any],
        updated_by_node: str,
    ) -> dict[str, Any]:
        requirements = cls._evidence_requirements_for_workflow_step(
            artifact_payload=artifact_payload,
            workflow_step_id=str(wait.get("workflow_step_id") or ""),
        )
        now = now_utc().isoformat()
        return {
            "checkpoint_id": str(wait.get("checkpoint_id") or ""),
            "workflow_step_id": str(wait.get("workflow_step_id") or ""),
            "requirements": [
                {
                    **requirement,
                    "status": "missing",
                    "accepted_event_refs": [],
                    "rejected_event_refs": [],
                    "latest_event_refs": [],
                    "reason": "",
                    "updated_at": now,
                    "updated_by_node": updated_by_node,
                }
                for requirement in requirements
            ],
            "updated_at": now,
            "updated_by_node": updated_by_node,
        }

    @classmethod
    def _evidence_requirements_for_workflow_step(
        cls,
        *,
        artifact_payload: dict[str, Any],
        workflow_step_id: str,
    ) -> list[dict[str, Any]]:
        runtime_contract = artifact_payload.get("runtime_contract")
        expected_evidence = runtime_contract.get("expected_evidence") if isinstance(runtime_contract, dict) else {}
        raw = expected_evidence.get(workflow_step_id) if isinstance(expected_evidence, dict) else None
        if isinstance(raw, dict) and isinstance(raw.get("items"), list):
            raw_items = raw["items"]
        elif isinstance(raw, list):
            raw_items = raw
        elif isinstance(raw, dict):
            raw_items = [
                {"requirement_key": str(key), **value} if isinstance(value, dict) else {"requirement_key": str(key), "description": str(value)}
                for key, value in raw.items()
            ]
        else:
            raw_items = []

        requirements: list[dict[str, Any]] = []
        for index, item in enumerate(raw_items, start=1):
            if not isinstance(item, dict):
                item = {"description": str(item)}
            requirement_key = str(item.get("requirement_key") or item.get("id") or item.get("key") or f"evidence_{index}").strip()
            if not requirement_key:
                requirement_key = f"evidence_{index}"
            description = str(item.get("description") or item.get("title") or requirement_key)
            requirement = {
                "requirement_key": requirement_key,
                "description": description,
            }
            for field in ("kind", "event_kind"):
                value = item.get(field)
                if value:
                    requirement[field] = value
            requirements.append(requirement)
        return requirements

    @classmethod
    def _merge_evidence_progress_from_observation(
        cls,
        *,
        token: dict[str, Any],
        node: dict[str, Any],
        observation: dict[str, Any],
        artifact_payload: dict[str, Any],
    ) -> None:
        evidence_assessment = observation.get("evidence_assessment")
        if not isinstance(evidence_assessment, dict):
            return
        requirement_results = evidence_assessment.get("requirement_results")
        if not isinstance(requirement_results, list) or not requirement_results:
            return

        control = token.setdefault("control", {})
        wait = control.get("wait") if isinstance(control.get("wait"), dict) else {}
        progress = control.get("evidence_progress") if isinstance(control.get("evidence_progress"), dict) else {}
        if not cls._evidence_progress_matches_wait(progress, wait):
            progress = cls._build_evidence_progress(
                wait=wait,
                artifact_payload=artifact_payload,
                updated_by_node=str(node.get("id") or ""),
            )
            control["evidence_progress"] = progress

        requirements = progress.get("requirements") if isinstance(progress.get("requirements"), list) else []
        by_key = {
            str(item.get("requirement_key") or ""): item
            for item in requirements
            if isinstance(item, dict) and str(item.get("requirement_key") or "")
        }
        now = now_utc().isoformat()
        for result in requirement_results:
            if not isinstance(result, dict):
                continue
            requirement_key = str(result.get("requirement_key") or "").strip()
            requirement = by_key.get(requirement_key)
            if not requirement:
                continue
            status = str(result.get("status") or "").strip().lower()
            if status not in {"accepted", "rejected", "missing", "ambiguous"}:
                continue
            if requirement.get("status") == "accepted" and status in {"missing", "ambiguous"}:
                continue
            event_refs = cls._string_list(result.get("event_refs"))
            requirement["status"] = status
            requirement["latest_event_refs"] = event_refs
            requirement["reason"] = str(result.get("reason") or "")
            requirement["updated_at"] = now
            requirement["updated_by_node"] = str(node.get("id") or "")
            if status == "accepted":
                requirement["accepted_event_refs"] = cls._unique_strings(
                    [*cls._string_list(requirement.get("accepted_event_refs")), *event_refs]
                )
            elif status == "rejected":
                requirement["rejected_event_refs"] = cls._unique_strings(
                    [*cls._string_list(requirement.get("rejected_event_refs")), *event_refs]
                )
        progress["updated_at"] = now
        progress["updated_by_node"] = str(node.get("id") or "")

    @staticmethod
    def _evidence_progress_matches_wait(progress: dict[str, Any], wait: dict[str, Any]) -> bool:
        return (
            isinstance(progress, dict)
            and isinstance(wait, dict)
            and str(progress.get("checkpoint_id") or "") == str(wait.get("checkpoint_id") or "")
            and str(progress.get("workflow_step_id") or "") == str(wait.get("workflow_step_id") or "")
        )

    @staticmethod
    def _string_list(value: Any) -> list[str]:
        return [str(item).strip() for item in value if str(item).strip()] if isinstance(value, list) else []

    @staticmethod
    def _unique_strings(values: list[str]) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for value in values:
            if value in seen:
                continue
            seen.add(value)
            result.append(value)
        return result

    @staticmethod
    def _normalize_wait_expected_inputs(items: list[Any]) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for item in items:
            if isinstance(item, dict):
                normalized.append(dict(item))
                continue
            kind = str(item or "").strip()
            if kind:
                normalized.append({"kind": kind})
        return normalized

    def _consume_pending_terminal_inputs_for_wait(
        self,
        session: Session,
        *,
        run: Run,
        token: dict[str, Any],
    ) -> bool:
        control = token.get("control") if isinstance(token.get("control"), dict) else {}
        wait = control.get("wait") if isinstance(control.get("wait"), dict) else None
        if not isinstance(control, dict) or not isinstance(wait, dict) or token.get("status") != "waiting":
            return False

        input_window = wait.get("input_window") if isinstance(wait.get("input_window"), dict) else {}
        try:
            accept_after_seq = int(input_window.get("accept_after_seq") or 0)
        except (TypeError, ValueError):
            accept_after_seq = 0
        delivery_upper_seq = int(run.latest_terminal_seq or 0)
        if delivery_upper_seq <= accept_after_seq:
            return False

        terminal = token.setdefault("terminal", {})
        token_events = terminal.setdefault("events", [])
        input_envelope = token.setdefault("input_envelope", {})
        existing_event_ids = {
            str(item.get("id") or "")
            for item in token_events
            if isinstance(item, dict) and str(item.get("id") or "")
        }
        consumed_evidence: list[dict[str, Any]] = []
        for event in self.repository.list_terminal_events(
            session,
            run.id,
            from_seq=accept_after_seq + 1,
            to_seq=delivery_upper_seq,
        ):
            if event.direction != "input":
                continue
            event_payload = self._terminal_event_token_payload_with_parts(session, event)
            if not self._can_consume_terminal_input_for_wait(
                event_payload=event_payload,
                wait=wait,
                control=control,
            ):
                continue
            parts = event_payload.get("parts") if isinstance(event_payload.get("parts"), list) else []
            input_text = self._terminal_input_text(event, parts)
            if input_text:
                input_envelope["user_input"] = input_text
                input_envelope.setdefault("text", input_text)
            evidence = {
                **event_payload,
                "text": input_text,
                "input_bundle": event_payload.get("input_bundle")
                or self._terminal_input_bundle_from_parts(event, parts),
            }
            if str(event_payload.get("id") or "") not in existing_event_ids:
                token_events.append(evidence)
                existing_event_ids.add(str(event_payload.get("id") or ""))
            terminal["latest_seq"] = max(int(terminal.get("latest_seq") or 0), event.seq_no)
            token.setdefault("metadata", {})["terminal_cursor"] = max(
                int(_get_path(token, "metadata.terminal_cursor") or 0),
                event.seq_no,
            )
            wait.setdefault("evidence", []).append(evidence)
            consumed_evidence.append(evidence)
            self._record_terminal_consumption(control=control, wait=wait, evidence=evidence)

        if not consumed_evidence:
            return False
        latest_evidence = consumed_evidence[-1]
        wait["latest_event_seq"] = latest_evidence.get("seq_no")
        wait["status"] = "received"
        control["latest_evidence"] = latest_evidence
        token["status"] = "running"
        token["phase"] = str(wait.get("resume_phase") or token.get("phase") or "start")
        return True

    @staticmethod
    def _truncate_exit_reason(value: str, *, max_length: int = 255) -> str:
        if len(value) <= max_length:
            return value
        return value[: max_length - 3] + "..."

    @staticmethod
    def _runtime_phase_from_token(token: dict[str, Any]) -> str:
        wait = token.get("control", {}).get("wait") if isinstance(token.get("control"), dict) else None
        if isinstance(wait, dict) and token.get("status") == "waiting":
            return str(wait.get("checkpoint_id") or wait.get("workflow_step_id") or "waiting")
        return str(token.get("phase") or "")

    def _close_terminal_session(self, session: Session, run: Run) -> None:
        terminal_session = self.repository.get_terminal_session_for_run(session, run.id)
        if terminal_session and terminal_session.status == "open":
            terminal_session.status = "closed"
            terminal_session.closed_at = now_utc()

    @staticmethod
    def _is_recoverable_terminal_turn_failure(token: dict[str, Any]) -> bool:
        control = token.get("control") if isinstance(token, dict) else {}
        control = control if isinstance(control, dict) else {}
        wait = control.get("wait")
        latest_evidence = control.get("latest_evidence")
        return (
            isinstance(wait, dict)
            and isinstance(latest_evidence, dict)
            and latest_evidence.get("direction") == "input"
            and wait.get("status") in {"received", "processing"}
        )

    def _recover_terminal_turn_failure(
        self,
        session: Session,
        *,
        run: Run,
        invocation: SkillInvocation,
        token: dict[str, Any],
        error: Exception,
        job: RuntimeJob | None,
    ) -> None:
        latest_evidence = token.get("control", {}).get("latest_evidence") if isinstance(token.get("control"), dict) else {}
        error_text = str(error)
        if isinstance(error, RuntimeStepTimeoutError):
            add_metric_counter(
                "psop.jobs.step_timeout",
                attributes={"recoverable": True},
                description="Runtime steps that exhausted their shared deadline",
            )
        failure_trace = self._append_trace_event(
            session,
            run=run,
            phase="waiting",
            event_type=(
                "runtime.step.timeout"
                if isinstance(error, RuntimeStepTimeoutError)
                else "runtime.message_processing.failed"
            ),
            payload=self._runtime_exception_trace_payload(
                error,
                recoverable=True,
                latest_input_event_seq=latest_evidence.get("seq_no") if isinstance(latest_evidence, dict) else None,
            ),
        )
        session.flush()
        terminal_event = self._append_runtime_recoverable_failure_terminal_event(
            session,
            run=run,
            trace_event=failure_trace,
        )
        recovered_token = self._recover_terminal_turn_token(
            token,
            error=error_text,
            trace_event=failure_trace,
            terminal_event=terminal_event,
        )
        run.status = "waiting_input"
        run.runtime_phase = self._runtime_phase_from_token(recovered_token)
        run.exit_reason = ""
        run.ended_at = None
        invocation.status = "running"
        self._finish_runtime_job_turn(session=session, job=job, run=run, token=recovered_token, status="succeeded")
        self._append_recoverable_failure_snapshot(
            session,
            run=run,
            token=recovered_token,
            trace_event=failure_trace,
        )

    def _append_runtime_recoverable_failure_terminal_event(
        self,
        session: Session,
        *,
        run: Run,
        trace_event: TraceEvent,
    ) -> TerminalEvent | None:
        terminal_session = self.repository.get_terminal_session_for_run(session, run.id)
        if not terminal_session or terminal_session.status != "open":
            return None

        try:
            return self._append_terminal_event(
                session,
                run=run,
                terminal_session=terminal_session,
                direction="output",
                event_kind="terminal.text.output.v1",
                mime_type="text/plain",
                payload_inline=self._runtime_recoverable_failure_terminal_message(),
                binding_id=None,
                source_ref={
                    "kind": "runtime",
                    "status": "recoverable_failed",
                    "trace_event_id": trace_event.id,
                },
                external_event_id=f"runtime:{run.id}:message-processing-failed:{trace_event.seq_no}",
                trace_event_id=trace_event.id,
            )
        except Exception:
            LOGGER.exception("failed to append recoverable runtime failure terminal event", extra={"run_id": run.id})
            return None

    @staticmethod
    def _runtime_recoverable_failure_terminal_message() -> str:
        return "刚才服务器开小差了，请您重试！"

    def _sync_runtime_job_metrics(self, job: RuntimeJob | None, token: dict[str, Any]) -> None:
        budgets = token.get("budgets") if isinstance(token, dict) else None
        self.job_repository.set_llm_usage_from_budgets(job, budgets if isinstance(budgets, dict) else None)

    def _compact_runtime_token(self, token: dict[str, Any]) -> dict[str, Any]:
        compacted = json.loads(json.dumps(token, ensure_ascii=False, default=str))
        observations = compacted.get("observations")
        if not isinstance(observations, dict):
            return compacted

        for observation in observations.values():
            self._compact_llm_observation(observation)
        return compacted

    def _compact_llm_observation(self, observation: Any) -> None:
        if not isinstance(observation, dict):
            return
        input_payload = observation.get("input")
        if not isinstance(input_payload, dict):
            return
        if "system_prompt" not in input_payload and "user_prompt" not in input_payload:
            return

        observation.pop("input", None)
        summary = self._llm_prompt_summary(
            system_prompt=str(input_payload.get("system_prompt") or ""),
            user_prompt=str(input_payload.get("user_prompt") or ""),
            route_key=input_payload.get("route_key") if isinstance(input_payload.get("route_key"), str) else None,
        )
        existing_summary = observation.get("input_summary")
        if isinstance(existing_summary, dict):
            summary.update(existing_summary)
        observation["input_summary"] = summary

    @staticmethod
    def _llm_prompt_summary(
        *,
        system_prompt: str,
        user_prompt: str,
        route_key: str | None = None,
        agent_prompt: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        system_bytes = system_prompt.encode("utf-8")
        user_bytes = user_prompt.encode("utf-8")
        combined = system_bytes + b"\0" + user_bytes
        summary: dict[str, Any] = {
            "prompt_hash": hashlib.sha256(combined).hexdigest(),
            "system_prompt_hash": hashlib.sha256(system_bytes).hexdigest(),
            "user_prompt_hash": hashlib.sha256(user_bytes).hexdigest(),
            "system_chars": len(system_prompt),
            "user_chars": len(user_prompt),
            "total_chars": len(system_prompt) + len(user_prompt),
        }
        if route_key:
            summary["route_key"] = route_key
        if agent_prompt:
            summary["agent_prompt"] = agent_prompt
        return summary

    def _runtime_exception_trace_payload(
        self,
        exc: Exception,
        *,
        recoverable: bool | None = None,
        latest_input_event_seq: Any | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "error": str(exc),
            "error_type": exc.__class__.__name__,
        }
        if recoverable is not None:
            payload["recoverable"] = recoverable
        if latest_input_event_seq is not None:
            payload["latest_input_event_seq"] = latest_input_event_seq

        if isinstance(exc, SkillsError):
            payload["error_code"] = exc.error_code
            payload["status_code"] = exc.status_code

        details = getattr(exc, "details", None)
        if isinstance(details, dict) and details:
            payload["error_details"] = self._normalize_trace_error_details(details)
        return payload

    def _normalize_trace_error_details(self, details: dict[str, Any]) -> dict[str, Any]:
        normalized = self._sanitize_trace_payload_value(details)
        if not isinstance(normalized, dict):
            return {}

        body = details.get("body")
        parsed_body = self._parse_trace_error_body(body)
        if parsed_body is not None and "body_json" not in normalized:
            normalized["body_json"] = self._sanitize_trace_payload_value(parsed_body)
            self._copy_provider_error_summary(normalized, parsed_body)
        return normalized

    @classmethod
    def _copy_provider_error_summary(cls, target: dict[str, Any], parsed_body: Any) -> None:
        if not isinstance(parsed_body, dict):
            return
        for key in ("request_id", "id"):
            if key in parsed_body and key not in target:
                target[key] = cls._sanitize_trace_payload_value(parsed_body[key])
        provider_error = parsed_body.get("error")
        if not isinstance(provider_error, dict):
            return
        for key in ("code", "type", "message"):
            if key in provider_error:
                target[f"provider_error_{key}"] = cls._sanitize_trace_payload_value(provider_error[key])

    @staticmethod
    def _parse_trace_error_body(body: Any) -> Any | None:
        if not isinstance(body, str) or not body.strip():
            return None
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return None

    @classmethod
    def _sanitize_trace_payload_value(cls, value: Any, *, depth: int = 0) -> Any:
        if depth > 8:
            return cls._truncate_trace_string(str(value))
        if value is None or isinstance(value, (bool, int, float)):
            return value
        if isinstance(value, str):
            return cls._truncate_trace_string(value)
        if isinstance(value, dict):
            sanitized: dict[str, Any] = {}
            for key, child in list(value.items())[:100]:
                key_text = str(key)
                if cls._is_sensitive_trace_detail_key(key_text):
                    sanitized[key_text] = "[redacted]"
                else:
                    sanitized[key_text] = cls._sanitize_trace_payload_value(child, depth=depth + 1)
            return sanitized
        if isinstance(value, (list, tuple, set)):
            return [cls._sanitize_trace_payload_value(item, depth=depth + 1) for item in list(value)[:100]]
        return cls._truncate_trace_string(str(value))

    @staticmethod
    def _is_sensitive_trace_detail_key(key: str) -> bool:
        lowered = key.lower()
        sensitive_parts = ("api_key", "apikey", "authorization", "password", "secret", "token")
        return any(part in lowered for part in sensitive_parts)

    @staticmethod
    def _truncate_trace_string(value: str, *, max_length: int = 8000) -> str:
        if len(value) <= max_length:
            return value
        return f"{value[:max_length]}...[truncated {len(value) - max_length} chars]"

    def _recover_terminal_turn_token(
        self,
        token: dict[str, Any],
        *,
        error: str,
        trace_event: TraceEvent,
        terminal_event: TerminalEvent | None,
    ) -> dict[str, Any]:
        recovered = json.loads(json.dumps(token, ensure_ascii=False, default=str))
        control = recovered.setdefault("control", {})
        wait = control.get("wait")
        if isinstance(wait, dict):
            wait["status"] = "waiting"
            wait.setdefault("recoverable_errors", []).append(
                {
                    "trace_event_id": trace_event.id,
                    "trace_seq_no": trace_event.seq_no,
                    "error": error,
                    "occurred_at": now_utc().isoformat(),
                }
            )
        control["latest_processing_error"] = {
            "trace_event_id": trace_event.id,
            "trace_seq_no": trace_event.seq_no,
            "recoverable": True,
            "occurred_at": now_utc().isoformat(),
        }
        recovered["status"] = "waiting"
        recovered["phase"] = "waiting"
        if terminal_event:
            event_payload = self._terminal_event_token_payload(terminal_event)
            terminal = recovered.setdefault("terminal", {})
            terminal.setdefault("events", []).append(event_payload)
            terminal["latest_seq"] = terminal_event.seq_no
            recovered.setdefault("metadata", {})["terminal_cursor"] = terminal_event.seq_no
        return recovered

    def _append_recoverable_failure_snapshot(
        self,
        session: Session,
        *,
        run: Run,
        token: dict[str, Any],
        trace_event: TraceEvent,
    ) -> None:
        next_seq = run.latest_snapshot_seq + 1
        run.latest_snapshot_seq = next_seq
        session.add(
            SessionTokenSnapshot(
                run_id=run.id,
                seq_no=next_seq,
                token_payload=token,
                enabled_set=[],
                selection_summary={
                    "selected": None,
                    "reason": "recoverable_terminal_turn_failure",
                    "trace_event_id": trace_event.id,
                },
                snapshot_hash=self._hash_payload(token),
            )
        )

    def _append_runtime_failure_terminal_event(
        self,
        session: Session,
        *,
        run: Run,
        error: str,
        trace_event: TraceEvent,
    ) -> None:
        terminal_session = self.repository.get_terminal_session_for_run(session, run.id)
        if not terminal_session or terminal_session.status != "open":
            return

        try:
            self._append_terminal_event(
                session,
                run=run,
                terminal_session=terminal_session,
                direction="output",
                event_kind="terminal.text.output.v1",
                mime_type="text/plain",
                payload_inline=self._runtime_failure_terminal_message(error),
                binding_id=None,
                source_ref={
                    "kind": "runtime",
                    "status": "failed",
                    "trace_event_id": trace_event.id,
                },
                external_event_id=f"runtime:{run.id}:failed",
                trace_event_id=trace_event.id,
            )
        except Exception:
            LOGGER.exception("failed to append runtime failure terminal event", extra={"run_id": run.id})

    @staticmethod
    def _runtime_failure_terminal_message(error: str) -> str:
        reason = error.strip() or "未知错误"
        return (
            "Runtime 执行失败，当前运行已停止。\n\n"
            f"错误原因：{reason}\n\n"
            "请查看运行回放或 Trace Events 获取更多排障信息。"
        )

    def _append_cancel_snapshot(self, session: Session, *, run: Run, reason: str) -> None:
        snapshots = self.repository.list_snapshots(session, run.id)
        if not snapshots:
            return
        token = json.loads(json.dumps(snapshots[-1].token_payload, ensure_ascii=False))
        token["status"] = "cancelled"
        token["phase"] = "cancelled"
        control = token.setdefault("control", {})
        control.pop("wait", None)
        control["cancelled"] = {
            "reason": reason,
            "cancelled_at": now_utc().isoformat(),
            "run_id": run.id,
        }
        next_seq = run.latest_snapshot_seq + 1
        run.latest_snapshot_seq = next_seq
        session.add(
            SessionTokenSnapshot(
                run_id=run.id,
                seq_no=next_seq,
                token_payload=token,
                enabled_set=[],
                selection_summary={"selected": None, "reason": "cancelled"},
                snapshot_hash=self._hash_payload(token),
            )
        )

    def _append_trace_event(
        self,
        session: Session,
        *,
        run: Run,
        phase: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> TraceEvent:
        seq_no = run.latest_trace_seq + 1
        run.latest_trace_seq = seq_no
        event = TraceEvent(
            run_id=run.id,
            seq_no=seq_no,
            phase=phase,
            event_type=event_type,
            span_id=f"{run.id[:8]}-{seq_no:04d}",
            parent_span_id="",
            payload=payload,
        )
        session.add(event)
        return event

    def _append_terminal_event(
        self,
        session: Session,
        *,
        run: Run,
        terminal_session: TerminalSession,
        direction: str,
        event_kind: str,
        mime_type: str,
        payload_inline: Any | None,
        binding_id: str | None = None,
        artifact_object_id: str | None = None,
        parts: list[TerminalEventPartInput] | None = None,
        source_ref: dict[str, Any] | None = None,
        external_event_id: str | None = None,
        trace_event_id: str | None = None,
        occurred_at=None,
    ) -> TerminalEvent:
        normalized_direction = direction.strip().lower()
        if normalized_direction not in {"input", "output"}:
            raise SkillValidationError("terminal event direction 只能是 input 或 output。", details={"direction": direction})
        if not event_kind:
            raise SkillValidationError("terminal event 必须包含 event_kind。")
        if not mime_type:
            raise SkillValidationError("terminal event 必须包含 mime_type。")
        if terminal_session.run_id != run.id:
            raise SkillValidationError("Terminal Session 与 Run 不匹配。")

        resolved_binding_id = binding_id or self._default_binding_id(
            self.repository.list_run_bindings(session, run.id),
            normalized_direction,
        )
        if resolved_binding_id:
            binding = self.repository.get_run_capability_binding(session, resolved_binding_id)
            if not binding or binding.run_id != run.id or binding.status != "active":
                raise SkillValidationError("terminal event binding 无效。", details={"binding_id": resolved_binding_id})

        session.flush()
        locked_run = self.repository.get_run_for_update(session, run.id)
        if not locked_run:
            raise SkillNotFoundError("未找到 Run。", details={"run_id": run.id})
        run = locked_run

        next_seq = run.latest_terminal_seq + 1
        run.latest_terminal_seq = next_seq
        event = TerminalEvent(
            terminal_session_id=terminal_session.id,
            run_id=run.id,
            trace_event_id=trace_event_id,
            artifact_object_id=artifact_object_id,
            run_capability_binding_id=resolved_binding_id,
            direction=normalized_direction,
            event_kind=event_kind,
            mime_type=mime_type,
            payload_inline=payload_inline,
            seq_no=next_seq,
            external_event_id=external_event_id,
            source_ref=source_ref or {},
            occurred_at=occurred_at or now_utc(),
        )
        session.add(event)
        session.flush()
        for order_index, part in enumerate(self._normalize_terminal_event_parts(event, parts or []), start=1):
            if part.artifact_object_id and not self.repository.get_artifact_object(session, part.artifact_object_id):
                raise SkillValidationError(
                    "terminal event part artifact_object_id 无效。",
                    details={"part_id": part.part_id, "artifact_object_id": part.artifact_object_id},
                )
            session.add(
                TerminalEventPart(
                    terminal_event_id=event.id,
                    run_id=run.id,
                    artifact_object_id=part.artifact_object_id,
                    part_id=part.part_id,
                    order_index=order_index,
                    kind=part.kind,
                    mime_type=part.mime_type,
                    text_inline=part.text or "",
                    size_bytes=int(part.size_bytes or 0),
                    checksum=part.checksum or "",
                    part_metadata=part.metadata,
                )
            )
        session.flush()
        return event

    def _normalize_terminal_event_parts(
        self,
        event: TerminalEvent,
        parts: list[TerminalEventPartInput],
    ) -> list[TerminalEventPartInput]:
        if parts:
            normalized_parts: list[TerminalEventPartInput] = []
            part_counts: dict[str, int] = {}
            for index, part in enumerate(parts):
                normalized_parts.append(self._validate_terminal_event_part(part, index, part_counts))
            return normalized_parts
        if event.artifact_object_id:
            return []
        if event.direction == "output":
            mime_type = (event.mime_type or "").strip().lower()
            event_kind = (event.event_kind or "").strip().lower()
            if not (mime_type.startswith("text/") or event_kind.startswith("terminal.text.")):
                return []
        text = self._terminal_input_text_from_payload(event.payload_inline)
        if text:
            return [
                TerminalEventPartInput(
                    part_id="text_1",
                    kind="text",
                    mime_type=event.mime_type if event.direction == "output" else "text/plain",
                    text=text,
                )
            ]
        return []

    def _validate_terminal_event_part(
        self,
        part: TerminalEventPartInput,
        index: int,
        part_counts: dict[str, int] | None = None,
    ) -> TerminalEventPartInput:
        kind = (part.kind or "").strip().lower()
        if not kind:
            kind = self._part_kind_for_mime_type((part.mime_type or "").strip().lower())
        if kind not in {"text", "image", "video", "audio"}:
            raise SkillValidationError("terminal event part kind 仅支持 text/image/video/audio。", details={"kind": part.kind})
        if kind == "text" and not str(part.text or "").strip():
            raise SkillValidationError("text part 必须包含 text。", details={"part_id": part.part_id})
        if kind != "text" and not part.artifact_object_id:
            raise SkillValidationError("多模态 part 必须绑定 artifact_object_id。", details={"part_id": part.part_id})
        mime_type = (part.mime_type or "").strip().lower()
        if kind != "text" and not mime_type.startswith(f"{kind}/"):
            raise SkillValidationError(
                "terminal event part kind 与 mime_type 不匹配。",
                details={"part_id": part.part_id, "kind": kind, "mime_type": part.mime_type},
            )
        part_id = (part.part_id or "").strip()
        kind_index = index + 1
        if part_counts is not None:
            part_counts[kind] = part_counts.get(kind, 0) + 1
            kind_index = part_counts[kind]
        if not part_id:
            part_id = f"{kind}_{kind_index}"
        return TerminalEventPartInput(
            part_id=part_id,
            kind=kind,
            mime_type=part.mime_type or ("text/plain" if kind == "text" else "application/octet-stream"),
            text=part.text,
            artifact_object_id=part.artifact_object_id,
            size_bytes=part.size_bytes,
            checksum=part.checksum,
            metadata=part.metadata,
        )

    @staticmethod
    def _token_is_waiting_for_terminal_input(token: dict[str, Any]) -> bool:
        control = token.get("control") if isinstance(token.get("control"), dict) else {}
        return token.get("status") == "waiting" and isinstance(control.get("wait"), dict)

    def _sync_terminal_events(self, session: Session, *, run: Run, token: dict[str, Any]) -> dict[str, Any]:
        if not self._token_is_waiting_for_terminal_input(token):
            return token
        cursor = int(_get_path(token, "metadata.terminal_cursor") or 0)
        events = self.repository.list_terminal_events(session, run.id, from_seq=cursor + 1)
        if not events:
            return token

        next_token = json.loads(json.dumps(token, ensure_ascii=False, default=str))
        terminal = next_token.setdefault("terminal", {})
        token_events = terminal.setdefault("events", [])
        input_envelope = next_token.setdefault("input_envelope", {})
        control = next_token.setdefault("control", {})
        wait = control.get("wait") if isinstance(control.get("wait"), dict) else None
        wait_accepting_input = isinstance(wait, dict) and next_token.get("status") == "waiting"
        if wait_accepting_input:
            self._ensure_wait_input_window(wait, accept_after_seq=cursor)
        consumed_evidence: list[dict[str, Any]] = []
        for event in events:
            event_payload = self._terminal_event_token_payload_with_parts(session, event)
            token_events.append(event_payload)
            terminal["latest_seq"] = event.seq_no
            next_token.setdefault("metadata", {})["terminal_cursor"] = event.seq_no
            if event.direction == "input":
                input_text = self._terminal_input_text(event, event_payload.get("parts") if isinstance(event_payload.get("parts"), list) else None)
                if input_text:
                    input_envelope["user_input"] = input_text
                    input_envelope.setdefault("text", input_text)
                if wait_accepting_input and isinstance(wait, dict) and self._can_consume_terminal_input_for_wait(
                    event_payload=event_payload,
                    wait=wait,
                    control=control,
                ):
                    evidence = {
                        **event_payload,
                        "text": input_text,
                        "input_bundle": event_payload.get("input_bundle")
                        or self._terminal_input_bundle_from_parts(event, event_payload.get("parts") or []),
                    }
                    wait.setdefault("evidence", []).append(evidence)
                    consumed_evidence.append(evidence)
                    self._record_terminal_consumption(control=control, wait=wait, evidence=evidence)
        if wait_accepting_input and consumed_evidence and isinstance(wait, dict):
            latest_evidence = consumed_evidence[-1]
            wait["latest_event_seq"] = latest_evidence.get("seq_no")
            wait["status"] = "received"
            control["latest_evidence"] = latest_evidence
            next_token["status"] = "running"
            next_token["phase"] = str(wait.get("resume_phase") or next_token.get("phase") or "start")
        return next_token

    @staticmethod
    def _ensure_wait_input_window(wait: dict[str, Any], *, accept_after_seq: int) -> None:
        input_window = wait.get("input_window")
        if isinstance(input_window, dict):
            input_window.setdefault("policy", "checkpoint_scoped")
            try:
                input_window["accept_after_seq"] = int(input_window.get("accept_after_seq") or 0)
            except (TypeError, ValueError):
                input_window["accept_after_seq"] = accept_after_seq
            return
        wait["input_window"] = {
            "accept_after_seq": accept_after_seq,
            "policy": "checkpoint_scoped",
        }

    @staticmethod
    def _can_consume_terminal_input_for_wait(
        *,
        event_payload: dict[str, Any],
        wait: dict[str, Any],
        control: dict[str, Any],
    ) -> bool:
        seq_no = event_payload.get("seq_no")
        if not isinstance(seq_no, int):
            return False
        input_window = wait.get("input_window") if isinstance(wait.get("input_window"), dict) else {}
        try:
            accept_after_seq = int(input_window.get("accept_after_seq") or 0)
        except (TypeError, ValueError):
            accept_after_seq = 0
        if seq_no <= accept_after_seq:
            return False
        consumed = control.get("terminal_consumption") if isinstance(control.get("terminal_consumption"), list) else []
        return not any(isinstance(item, dict) and item.get("seq_no") == seq_no for item in consumed)

    @staticmethod
    def _record_terminal_consumption(
        *,
        control: dict[str, Any],
        wait: dict[str, Any],
        evidence: dict[str, Any],
    ) -> None:
        seq_no = evidence.get("seq_no")
        if not isinstance(seq_no, int):
            return
        consumed = control.setdefault("terminal_consumption", [])
        if not isinstance(consumed, list):
            consumed = []
            control["terminal_consumption"] = consumed
        if any(isinstance(item, dict) and item.get("seq_no") == seq_no for item in consumed):
            return
        consumed.append(
            {
                "seq_no": seq_no,
                "event_id": str(evidence.get("id") or ""),
                "checkpoint_id": str(wait.get("checkpoint_id") or ""),
                "workflow_step_id": str(wait.get("workflow_step_id") or ""),
                "consumed_at": now_utc().isoformat(),
            }
        )

    def _ensure_default_run_bindings(
        self,
        session: Session,
        *,
        run: Run,
        terminal_session: TerminalSession,
        terminal_context: dict[str, Any],
        binding_preferences: list[dict[str, Any]],
    ) -> list[RunCapabilityBinding]:
        existing = self.repository.list_run_bindings(session, run.id)
        if existing:
            return existing

        policy_snapshot = {
            "source": "mvp_default",
            "terminal_context": terminal_context,
            "binding_preferences": binding_preferences,
        }
        bindings = [
            RunCapabilityBinding(
                run_id=run.id,
                compile_artifact_id=run.compile_artifact_id,
                requirement_key="terminal.input",
                binding_type="terminal",
                capability="terminal.text.input.v1",
                target_kind="web_terminal",
                target_ref=terminal_session.id,
                channel="input",
                schema_ref="terminal.text.input.v1",
                manifest_hash=self._hash_payload({"capability": "terminal.text.input.v1", "target": terminal_session.id}),
                policy_snapshot=policy_snapshot,
                status="active",
            ),
            RunCapabilityBinding(
                run_id=run.id,
                compile_artifact_id=run.compile_artifact_id,
                requirement_key="terminal.output",
                binding_type="terminal",
                capability="terminal.text.output.v1",
                target_kind="web_terminal",
                target_ref=terminal_session.id,
                channel="output",
                schema_ref="terminal.text.output.v1",
                manifest_hash=self._hash_payload({"capability": "terminal.text.output.v1", "target": terminal_session.id}),
                policy_snapshot=policy_snapshot,
                status="active",
            ),
        ]
        session.add_all(bindings)
        session.flush()
        return bindings

    def _validate_active_job_lease(self, session: Session) -> RuntimeJob | None:
        lease = self._active_job_lease
        if lease is None:
            return None
        if self._lease_is_healthy is not None and not self._lease_is_healthy():
            raise RuntimeLeaseLostError("Runtime advisory lock 已失效，丢弃本次 observation。")
        transaction = session.get_transaction()
        fence_marker = (lease.owner, id(transaction)) if transaction is not None else None
        if session.info.get("runtime_lease_fence") == fence_marker:
            return session.get(RuntimeJob, lease.job_id)
        job = session.scalar(
            select(RuntimeJob)
            .where(
                RuntimeJob.id == lease.job_id,
                RuntimeJob.status == "running",
                RuntimeJob.worker_name == lease.owner,
                RuntimeJob.lease_until.is_not(None),
                RuntimeJob.lease_until > now_utc(),
            )
            .with_for_update()
        )
        if job is None:
            raise RuntimeLeaseLostError("Runtime job lease 已失效，丢弃本次 observation。")
        transaction = session.get_transaction()
        session.info["runtime_lease_fence"] = (lease.owner, id(transaction))
        return job

    def _lock_runtime_write(
        self,
        session: Session,
        *,
        run_id: str,
        expected_snapshot_seq: int,
    ) -> Run:
        self._validate_active_job_lease(session)
        run = self.repository.get_run_for_update(session, run_id)
        if run is None:
            raise SkillNotFoundError("未找到 Run。", details={"run_id": run_id})
        if run.latest_snapshot_seq != expected_snapshot_seq:
            raise RuntimeLeaseLostError("Runtime snapshot 版本已变化，丢弃本次 observation。")
        return run

    def _commit_and_publish(
        self,
        session: Session,
        *,
        run_id: str,
        previous_terminal_seq: int,
        previous_trace_seq: int,
        terminal_directions: set[str] | None = None,
        publish_task_status: bool = False,
    ) -> tuple[int, int]:
        self._validate_active_job_lease(session)
        session.flush()
        run = self.repository.get_run(session, run_id)
        terminal_upper_seq = run.latest_terminal_seq if run else previous_terminal_seq
        trace_upper_seq = run.latest_trace_seq if run else previous_trace_seq
        session.commit()
        session.info.pop("runtime_lease_fence", None)
        try:
            self._publish_runtime_events_after(
                session,
                run_id=run_id,
                previous_terminal_seq=previous_terminal_seq,
                previous_trace_seq=previous_trace_seq,
                terminal_upper_seq=terminal_upper_seq,
                trace_upper_seq=trace_upper_seq,
                terminal_directions=terminal_directions,
                publish_task_status=publish_task_status,
            )
        except Exception:
            session.rollback()
            # Event delivery is a post-commit hint. REST/from_seq remains
            # authoritative and a transport failure must not fail the write.
            LOGGER.exception("runtime event publish failed after commit", extra={"run_id": run_id})
        else:
            # Event hydration uses a new read transaction after the formal
            # commit. Close it before the next node can perform external I/O.
            session.commit()
        return terminal_upper_seq, trace_upper_seq

    def _publish_runtime_events_after(
        self,
        session: Session,
        *,
        run_id: str,
        previous_terminal_seq: int,
        previous_trace_seq: int,
        terminal_upper_seq: int,
        trace_upper_seq: int,
        terminal_directions: set[str] | None = None,
        publish_task_status: bool = False,
    ) -> None:
        for event in self.repository.list_terminal_events(
            session,
            run_id,
            from_seq=previous_terminal_seq + 1,
            to_seq=terminal_upper_seq,
        ):
            if terminal_directions is not None and event.direction not in terminal_directions:
                continue
            response = self._build_terminal_event_response(session, event)
            self.runtime_event_sink.publish(
                {
                    "event_type": "terminal.event.appended",
                    "run_id": run_id,
                    "invocation_id": None,
                    "seq_no": response.seq_no,
                    "occurred_at": response.occurred_at.isoformat(),
                    "payload": response.model_dump(mode="json"),
                }
            )
        if publish_task_status:
            response = self.get_run_task_status(session, run_id)
            self.runtime_event_sink.publish(
                {
                    "event_type": "run.task_status.updated",
                    "run_id": run_id,
                    "invocation_id": None,
                    "seq_no": response.snapshot_seq,
                    "occurred_at": response.updated_at.isoformat(),
                    "payload": response.model_dump(mode="json"),
                }
            )
        for event in self.repository.list_trace_events(session, run_id):
            if event.seq_no <= previous_trace_seq or event.seq_no > trace_upper_seq:
                continue
            response = self._build_trace_event_response(event)
            self.runtime_event_sink.publish(
                {
                    "event_type": "trace.event.appended",
                    "run_id": run_id,
                    "invocation_id": None,
                    "seq_no": response.seq_no,
                    "occurred_at": response.occurred_at.isoformat(),
                    "payload": response.model_dump(mode="json"),
                }
            )

    def _finish_runtime_job_turn(
        self,
        *,
        session: Session,
        job: RuntimeJob | None,
        run: Run,
        token: dict[str, Any],
        status: str,
        last_error: str = "",
    ) -> None:
        if not job:
            return
        self._validate_active_job_lease(session)
        payload = dict(job.payload or {})
        payload.pop("rerun_requested", None)
        job.payload = payload or {"run_id": run.id}
        if run.status not in {"succeeded", "failed", "cancelled", "aborted"} and self._has_unsynced_terminal_inputs(
            session,
            run_id=run.id,
            token=token,
        ):
            job.status = "pending"
            job.available_at = now_utc()
            job.lease_until = None
            job.worker_name = ""
            job.finished_at = None
            job.last_error = ""
            return
        job.status = status
        job.lease_until = None
        job.worker_name = ""
        job.finished_at = now_utc() if status in {"succeeded", "failed", "cancelled"} else None
        job.last_error = last_error

    def _has_unsynced_terminal_inputs(self, session: Session, *, run_id: str, token: dict[str, Any]) -> bool:
        cursor = int(_get_path(token, "metadata.terminal_cursor") or 0)
        return any(
            event.direction == "input"
            for event in self.repository.list_terminal_events(session, run_id, from_seq=cursor + 1)
        )

    def _ensure_runtime_job_pending(
        self,
        session: Session,
        run: Run,
        *,
        existing_job: RuntimeJob | None = None,
    ) -> RuntimeJob:
        job = existing_job or self.job_repository.get_runtime_job_by_dedupe_key_for_update(
            session,
            f"job:runtime:{run.id}",
        )
        if job:
            payload = dict(job.payload or {})
            if job.status == "running":
                payload["rerun_requested"] = True
                job.payload = payload
                return job
            payload.pop("rerun_requested", None)
            job.payload = payload or {"run_id": run.id}
            job.status = "pending"
            job.available_at = now_utc()
            job.lease_until = None
            job.worker_name = ""
            job.attempt_no = 0
            job.last_error = ""
            return job
        job = RuntimeJob(
            job_type="runtime",
            status="pending",
            payload={"run_id": run.id},
            run_id=run.id,
            dedupe_key=f"job:runtime:{run.id}",
            available_at=now_utc(),
            max_attempts=self.settings.runtime_job_max_attempts,
        )
        session.add(job)
        return job

    @staticmethod
    def _terminal_event_token_payload(event: TerminalEvent) -> dict[str, Any]:
        return {
            "id": event.id,
            "seq_no": event.seq_no,
            "direction": event.direction,
            "event_kind": event.event_kind,
            "mime_type": event.mime_type,
            "payload_inline": event.payload_inline,
            "artifact_object_id": event.artifact_object_id,
            "binding_id": event.run_capability_binding_id,
            "source_ref": event.source_ref,
            "occurred_at": event.occurred_at.isoformat(),
        }

    def _terminal_event_token_payload_with_parts(self, session: Session, event: TerminalEvent) -> dict[str, Any]:
        payload = self._terminal_event_token_payload(event)
        parts = [self._terminal_event_part_token_payload(part) for part in self.repository.list_terminal_event_parts(session, event.id)]
        if parts:
            payload["parts"] = parts
            payload["input_bundle"] = self._terminal_input_bundle_from_parts(event, parts)
        return payload

    @staticmethod
    def _terminal_event_part_token_payload(part: TerminalEventPart) -> dict[str, Any]:
        return {
            "id": part.id,
            "part_id": part.part_id,
            "order_index": part.order_index,
            "kind": part.kind,
            "mime_type": part.mime_type,
            "text": part.text_inline,
            "artifact_object_id": part.artifact_object_id,
            "size_bytes": part.size_bytes,
            "checksum": part.checksum,
            "metadata": part.part_metadata,
        }

    @classmethod
    def _terminal_input_bundle_from_parts(cls, event: TerminalEvent, parts: list[dict[str, Any]]) -> dict[str, Any]:
        summary = cls._terminal_input_summary(parts, event.payload_inline)
        return {
            "event_id": event.id,
            "seq_no": event.seq_no,
            "event_kind": event.event_kind,
            "mime_type": event.mime_type,
            "text": summary,
            "parts": parts,
        }

    @classmethod
    def _terminal_input_text(cls, event: TerminalEvent, parts: list[dict[str, Any]] | None = None) -> str:
        if parts:
            return cls._terminal_input_summary(parts, event.payload_inline)
        return cls._terminal_input_text_from_payload(event.payload_inline)

    @staticmethod
    def _terminal_input_text_from_payload(value: Any) -> str:
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            for key in ("user_input", "text", "value", "content", "description", "summary", "name", "filename"):
                if value.get(key):
                    return str(value[key])
        if value is None:
            return ""
        return json.dumps(value, ensure_ascii=False)

    @staticmethod
    def _part_kind_for_mime_type(mime_type: str) -> str:
        if mime_type.startswith("image/"):
            return "image"
        if mime_type.startswith("audio/"):
            return "audio"
        if mime_type.startswith("video/"):
            return "video"
        return "text" if mime_type.startswith("text/") else "file"

    @classmethod
    def _terminal_input_summary(cls, parts: list[dict[str, Any]], payload_inline: Any | None = None) -> str:
        lines: list[str] = []
        for part in parts:
            kind = str(part.get("kind") or "")
            text = str(part.get("text") or "").strip()
            metadata = part.get("metadata") if isinstance(part.get("metadata"), dict) else {}
            filename = str(metadata.get("filename") or metadata.get("name") or "").strip()
            if kind == "text" and text:
                lines.append(text)
            elif filename:
                lines.append(f"{kind or 'attachment'}: {filename}")
            elif kind:
                lines.append(f"{kind} input")
        if lines:
            return "\n".join(lines)
        return cls._terminal_input_text_from_payload(payload_inline)

    @staticmethod
    def _capability_for_requirement(requirement_key: str) -> str:
        if requirement_key.endswith("output"):
            return "terminal.text.output.v1"
        return "terminal.text.input.v1"

    @staticmethod
    def _default_binding_id(bindings: list[RunCapabilityBinding], direction: str) -> str | None:
        suffix = "output" if direction == "output" else "input"
        for binding in bindings:
            if binding.requirement_key.endswith(suffix) and binding.status == "active":
                return binding.id
        return bindings[0].id if bindings else None

    @staticmethod
    def _binding_payload(binding: RunCapabilityBinding) -> dict[str, Any]:
        return {
            "binding_id": binding.id,
            "requirement_key": binding.requirement_key,
            "capability": binding.capability,
            "target_kind": binding.target_kind,
            "target_ref": binding.target_ref,
            "channel": binding.channel,
            "status": binding.status,
        }

    @staticmethod
    def _extract_user_input(input_envelope: dict[str, Any], artifact_payload: dict[str, Any]) -> str:
        input_name = artifact_payload.get("schema", {}).get("input_name") or artifact_payload.get("interface", {}).get("input_name", "user_input")
        if input_name in input_envelope:
            return str(input_envelope[input_name])
        if "user_input" in input_envelope:
            return str(input_envelope["user_input"])
        if "text" in input_envelope:
            return str(input_envelope["text"])
        return json.dumps(input_envelope, ensure_ascii=False)

    def _build_initial_token(self, input_envelope: dict[str, Any], artifact_payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "phase": self._initial_phase(artifact_payload),
            "input_envelope": input_envelope,
            "observations": {},
            "budgets": {"llm_calls": 0, "tool_calls": 0},
            "outputs": {},
            "control": {},
            "metadata": {"artifact_version": artifact_payload.get("artifact_version"), "terminal_cursor": 0},
            "terminal": {"events": [], "latest_seq": 0},
            "facts": {},
            "registers": {},
            "memory": {},
            "trace": [],
            "status": "running",
        }

    @staticmethod
    def _initial_phase(artifact_payload: dict[str, Any]) -> str:
        init = artifact_payload.get("init", {})
        if isinstance(init, dict) and isinstance(init.get("entry_node"), str):
            return init["entry_node"]
        return "start"

    def _enabled_nodes(self, artifact_payload: dict[str, Any], token: dict[str, Any]) -> list[dict[str, Any]]:
        nodes = [node for node in artifact_payload.get("nodes", []) if isinstance(node, dict)]
        return [node for node in nodes if self._evaluate_guard(node.get("guard", {"always": True}), token)]

    def _select_node(self, enabled_nodes: list[dict[str, Any]]) -> dict[str, Any]:
        return sorted(
            enabled_nodes,
            key=lambda node: (
                int(node.get("policy", {}).get("priority", node.get("priority", 100))) if isinstance(node.get("policy", {}), dict) else 100,
                str(node.get("id", "")),
            ),
        )[0]

    def _evaluate_guard(self, guard: Any, token: dict[str, Any]) -> bool:
        if isinstance(guard, bool):
            return guard
        if not isinstance(guard, dict):
            return False
        if guard.get("always") is True:
            return True
        if "phase_is" in guard and token.get("phase") != guard["phase_is"]:
            return False
        if "field_exists" in guard and _get_path(token, str(guard["field_exists"])) is None:
            return False
        if "field_equals" in guard:
            field_equals = guard["field_equals"]
            if not isinstance(field_equals, dict):
                return False
            if _get_path(token, str(field_equals.get("path"))) != field_equals.get("value"):
                return False
        if "all" in guard:
            values = guard["all"]
            if not isinstance(values, list) or not all(self._evaluate_guard(item, token) for item in values):
                return False
        if "any" in guard:
            values = guard["any"]
            if not isinstance(values, list) or not any(self._evaluate_guard(item, token) for item in values):
                return False
        if "not" in guard and self._evaluate_guard(guard["not"], token):
            return False
        return True

    def _execute_node(
        self,
        *,
        session: Session,
        run: Run,
        node: dict[str, Any],
        token: dict[str, Any],
        artifact_payload: dict[str, Any],
    ) -> dict[str, Any]:
        kind = node.get("kind")
        actor_name = _actor_name(node.get("actor"))
        if kind == "start" or actor_name == "runtime.start":
            return {"started": True, "summary": "Runtime 已初始化 Session Token。"}
        if kind == "input" or actor_name == "runtime.input":
            user_input = self._extract_user_input(token.get("input_envelope", {}), artifact_payload)
            return {"user_input": user_input, "summary": "已接收用户输入。"}
        if kind == "llm" or actor_name == "agent.llm":
            deadline_monotonic = time.monotonic() + self.settings.runtime_step_timeout_seconds
            return self._execute_runner_agent_node(
                session=session,
                run=run,
                node=node,
                token=token,
                artifact_payload=artifact_payload,
                deadline_monotonic=deadline_monotonic,
            )
        if kind == "tool" or actor_name == "capability.demo_tool":
            user_input = self._extract_user_input(token.get("input_envelope", {}), artifact_payload)
            llm_output = str(
                _get_path(token, "observations.llm.content")
                or _get_path(token, "observations.plan_repair.content")
                or _last_observation_content(token)
                or ""
            )
            token.setdefault("budgets", {})["tool_calls"] = int(token.setdefault("budgets", {}).get("tool_calls", 0)) + 1
            return self._run_demo_tool(user_input=user_input, llm_output=llm_output)
        if kind == "terminal" or actor_name == "runtime.terminal":
            llm_content = str(_get_path(token, "observations.llm.content") or _get_path(token, "observations.plan_repair.content") or "")
            tool_observation = _get_path(token, "observations.tool")
            if llm_content and isinstance(tool_observation, dict) and tool_observation.get("result"):
                final_response = self._compose_final_output(llm_content, tool_observation)
            else:
                final_response = (
                    _get_path(token, "outputs.final_response")
                    or llm_content
                    or _last_observation_content(token)
                    or "Run 已完成。"
                )
            return {"final_response": str(final_response), "summary": "Run 已完成。"}
        raise RuntimeError(f"Unsupported runtime node actor: {actor_name or kind}")

    def _execute_runner_agent_node(
        self,
        *,
        session: Session,
        run: Run,
        node: dict[str, Any],
        token: dict[str, Any],
        artifact_payload: dict[str, Any],
        deadline_monotonic: float,
    ) -> dict[str, Any]:
        invocation = self._build_runner_invocation(
            session=session,
            run=run,
            node=node,
            token=token,
            artifact_payload=artifact_payload,
            deadline_monotonic=deadline_monotonic,
        )
        with start_span("runtime.agent.invoke", agent_key="psop.runner", node_id=node.get("id"), run_id=run.id):
            result = self.agent_harness_service.invoke(
                invocation,
                persistence_session=session,
                persistence_context={
                    "related_runtime_run_id": run.id,
                    "related_skill_definition_id": run.skill_definition_id,
                    "live_events_enabled": "false",
                },
            )
        self._ensure_runtime_deadline(deadline_monotonic)
        if result.status != "succeeded":
            if result.structured_output.get("error_type") in {
                "AgentDeadlineExceededError",
                "TimeoutError",
                "APITimeoutError",
                "ReadTimeout",
                "ConnectTimeout",
                "TimeoutException",
            }:
                raise RuntimeStepTimeoutError("Runtime 节点执行超过总时限。")
            raise RuntimeError(f"psop.runner 执行失败：{result.error_message or result.final_output}")
        observation = self._read_runner_observation_artifact(result)
        observation = validate_runner_observation(
            observation,
            invocation_input=invocation.input,
            invocation_context=invocation.context,
        )
        usage = self._agent_result_usage(result)
        budgets = token.setdefault("budgets", {})
        budgets["llm_calls"] = int(budgets.get("llm_calls", 0)) + int(usage.get("llm_calls") or 0)
        self._accumulate_llm_usage(budgets, usage)
        return self._map_runner_observation_to_runtime_observation(
            observation,
            agent_result=result,
            usage=usage,
        )

    def _build_runner_invocation(
        self,
        *,
        session: Session,
        run: Run,
        node: dict[str, Any],
        token: dict[str, Any],
        artifact_payload: dict[str, Any],
        deadline_monotonic: float,
    ) -> AgentInvocation:
        node_id = str(node.get("id") or "")
        actor_name = _actor_name(node.get("actor"))
        mode = "evidence_evaluation" if self._node_is_evaluation(node) else "terminal_guidance"
        context = self._build_runner_context(run=run, node=node, token=token, artifact_payload=artifact_payload)
        runner_turn_context = self._build_runner_turn_context(
            run=run,
            node=node,
            mode=mode,
            context=context,
        )
        context["runner_turn_context"] = runner_turn_context
        text = (
            f"node_id={node_id} 协助 PSOP Runtime 节点 `{node_id}`，模式：{mode}。\n\n"
            "请先基于以下 RunnerTurnContext 判断；只有上下文不足时才按需调用 read tools。\n"
            "<RunnerTurnContext>\n"
            f"{json.dumps(runner_turn_context, ensure_ascii=False, default=str)}\n"
            "</RunnerTurnContext>"
        )
        attachments = self._build_runner_input_attachments(
            session=session,
            run=run,
            node=node,
            token=token,
            deadline_monotonic=deadline_monotonic,
        )
        if attachments:
            context["input_attachments"] = [attachment.redacted_metadata() for attachment in attachments]
        return AgentInvocation(
            agent_key="psop.runner",
            input={
                "task": "assist_psop_runtime_node",
                "run_id": run.id,
                "node": {
                    "id": node_id,
                    "kind": str(node.get("kind") or ""),
                    "actor": actor_name,
                    "mode": mode,
                },
                "output_contract": {
                    "schema": "psop.runner.observation.v1",
                    "required_artifact": RUNNER_OBSERVATION_ARTIFACT_REF,
                    "allowed_decisions": ["continue", "need_more_evidence", "retry", "abort", "complete"],
                    "runtime_controls_transition": True,
                    "transition_summary": context.get("transition_contract", {}),
                    "language": "zh-CN",
                },
                "text": text,
            },
            context=context,
            attachments=attachments,
            memory_scope=f"psop.runner:{run.id}",
            deadline_monotonic=deadline_monotonic,
        )

    def _build_runner_context(
        self,
        *,
        run: Run,
        node: dict[str, Any],
        token: dict[str, Any],
        artifact_payload: dict[str, Any],
    ) -> dict[str, Any]:
        runtime_contract = artifact_payload.get("runtime_contract") if isinstance(artifact_payload.get("runtime_contract"), dict) else {}
        terminal = token.get("terminal") if isinstance(token.get("terminal"), dict) else {}
        terminal_events = terminal.get("events") if isinstance(terminal.get("events"), list) else []
        control = token.get("control") if isinstance(token.get("control"), dict) else {}
        return {
            "trust_labels": {
                "task_identity": "trusted_compiled_skill_snapshot",
                "runtime_contract": "trusted",
                "prompt_view": "trusted_runtime_projection",
                "terminal_events": "untrusted_runtime_input",
                "input_attachments": "untrusted_runtime_input",
            },
            "prompt_view": self._runner_prompt_view(run=run, node=node, token=token),
            "task_identity": self._runner_task_identity(artifact_payload),
            "runtime_contract": runtime_contract,
            "current_checkpoint": self._runner_current_checkpoint(run=run, node=node, token=token),
            "evidence_progress": self._runner_evidence_progress(node=node, token=token, artifact_payload=artifact_payload),
            "transition_contract": self._runner_transition_contract(artifact_payload=artifact_payload, node=node),
            "terminal_events": terminal_events[-20:],
            "latest_evidence": control.get("latest_evidence") if isinstance(control.get("latest_evidence"), dict) else {},
            "trace_summary": token.get("trace") if isinstance(token.get("trace"), list) else [],
            "allowed_runtime": {
                "terminal_event_kinds": ["terminal.text.output.v1"],
                "input_part_kinds": ["text", "image", "audio", "video"],
                "output_part_kinds": ["text"],
                "max_terminal_message_chars": 2000,
            },
            "terminal_cursor": _get_path(token, "metadata.terminal_cursor") or 0,
        }

    def _build_runner_turn_context(
        self,
        *,
        run: Run,
        node: dict[str, Any],
        mode: str,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        prompt_view = context.get("prompt_view") if isinstance(context.get("prompt_view"), dict) else {}
        runtime_contract = context.get("runtime_contract") if isinstance(context.get("runtime_contract"), dict) else {}
        terminal_events = context.get("terminal_events") if isinstance(context.get("terminal_events"), list) else []
        interaction = node.get("interaction") if isinstance(node.get("interaction"), dict) else {}
        current_checkpoint = context.get("current_checkpoint") if isinstance(context.get("current_checkpoint"), dict) else {}
        workflow_steps = runtime_contract.get("workflow_steps") if isinstance(runtime_contract.get("workflow_steps"), list) else []
        workflow_step_id = str(current_checkpoint.get("workflow_step_id") or interaction.get("workflow_step_id") or "")
        current_workflow_step: dict[str, Any] = {}
        stage_index = 0
        for index, step in enumerate(workflow_steps, start=1):
            if isinstance(step, dict) and str(step.get("id") or "") == workflow_step_id:
                current_workflow_step = dict(step)
                stage_index = index
                break
        projected_control = prompt_view.get("control") if isinstance(prompt_view.get("control"), dict) else {}
        return {
            "run_id": run.id,
            "node": {
                "id": str(node.get("id") or ""),
                "kind": str(node.get("kind") or ""),
                "actor": _actor_name(node.get("actor")),
            },
            "mode": mode,
            "turn_kind": str(interaction.get("runner_turn_kind") or ""),
            "task_identity": context.get("task_identity") if isinstance(context.get("task_identity"), dict) else {},
            "stage_position": {
                "current": stage_index,
                "total": len(workflow_steps),
                "workflow_step_id": workflow_step_id,
            },
            "current_workflow_step": current_workflow_step,
            "previous_evaluation": (
                projected_control.get("latest_evaluation")
                if isinstance(projected_control.get("latest_evaluation"), dict)
                else {}
            ),
            "prompt_view": prompt_view,
            "current_checkpoint": current_checkpoint,
            "evidence_progress": context.get("evidence_progress") if isinstance(context.get("evidence_progress"), dict) else {},
            "latest_evidence": context.get("latest_evidence") if isinstance(context.get("latest_evidence"), dict) else {},
            "recent_terminal_events": [
                self._runner_terminal_event_summary(item)
                for item in terminal_events[-5:]
                if isinstance(item, dict)
            ],
            "runtime_contract_slice": self._runner_runtime_contract_slice(runtime_contract),
            "transition_contract": context.get("transition_contract") if isinstance(context.get("transition_contract"), dict) else {},
            "trust_labels": context.get("trust_labels") if isinstance(context.get("trust_labels"), dict) else {},
            "output_contract": {
                "schema": "psop.runner.observation.v1",
                "required_artifact": RUNNER_OBSERVATION_ARTIFACT_REF,
                "allowed_decisions": ["continue", "need_more_evidence", "retry", "abort", "complete"],
                "runtime_controls_transition": True,
                "transition_summary": context.get("transition_contract") if isinstance(context.get("transition_contract"), dict) else {},
                "language": "zh-CN",
            },
            "terminal_cursor": int(context.get("terminal_cursor") or 0),
        }

    def _runner_runtime_contract_slice(self, runtime_contract: dict[str, Any]) -> dict[str, Any]:
        return {
            "execution_goal": runtime_contract.get("execution_goal") or "",
            "applicability": runtime_contract.get("applicability") if isinstance(runtime_contract.get("applicability"), dict) else {},
            "workflow_steps": runtime_contract.get("workflow_steps") if isinstance(runtime_contract.get("workflow_steps"), list) else [],
            "expected_evidence": runtime_contract.get("expected_evidence") if isinstance(runtime_contract.get("expected_evidence"), dict) else {},
            "safety_constraints": runtime_contract.get("safety_constraints") if isinstance(runtime_contract.get("safety_constraints"), list) else [],
            "completion_criteria": runtime_contract.get("completion_criteria") if isinstance(runtime_contract.get("completion_criteria"), list) else [],
        }

    @staticmethod
    def _runner_task_identity(artifact_payload: dict[str, Any]) -> dict[str, Any]:
        skill = artifact_payload.get("skill") if isinstance(artifact_payload.get("skill"), dict) else {}
        return {
            "skill_key": str(skill.get("key") or ""),
            "name": str(skill.get("name") or ""),
            "description": str(skill.get("description") or ""),
            "version": skill.get("version_no") if skill.get("version_no") is not None else skill.get("version"),
        }

    def _runner_transition_contract(self, *, artifact_payload: dict[str, Any], node: dict[str, Any]) -> dict[str, Any]:
        decisions: dict[str, str] = {}
        if self._node_is_evaluation(node):
            for runner_decision, runtime_decision in {
                "continue": "proceed",
                "complete": "complete",
                "abort": "abort",
            }.items():
                next_phase = self._resolve_evaluation_transition(
                    artifact_payload=artifact_payload,
                    node=node,
                    decision=runtime_decision,
                )
                if next_phase:
                    decisions[runner_decision] = next_phase
        return {
            "runtime_controls_transition": True,
            "current_node_id": str(node.get("id") or ""),
            "decisions": decisions,
            "note": "runner 只提交 decision；Runtime 根据 EG transition 决定下一 Runtime phase。",
        }

    def _runner_terminal_event_summary(self, event: dict[str, Any]) -> dict[str, Any]:
        summary = {
            "seq_no": event.get("seq_no"),
            "direction": event.get("direction"),
            "event_kind": event.get("event_kind"),
            "mime_type": event.get("mime_type"),
            "source_ref": event.get("source_ref") if isinstance(event.get("source_ref"), dict) else {},
        }
        text = str(event.get("text") or event.get("payload_inline") or "")
        if text:
            summary["text_preview"] = text[:500]
        parts = event.get("parts") if isinstance(event.get("parts"), list) else []
        summary["parts"] = [
            {
                "part_id": str(part.get("part_id") or ""),
                "kind": str(part.get("kind") or ""),
                "mime_type": str(part.get("mime_type") or ""),
                "has_attachment": bool(part.get("artifact_object_id")),
            }
            for part in parts
            if isinstance(part, dict)
        ][:10]
        return summary

    def _runner_evidence_progress(
        self,
        *,
        node: dict[str, Any],
        token: dict[str, Any],
        artifact_payload: dict[str, Any],
    ) -> dict[str, Any]:
        control = token.get("control") if isinstance(token.get("control"), dict) else {}
        wait = control.get("wait") if isinstance(control.get("wait"), dict) else None
        progress = control.get("evidence_progress") if isinstance(control.get("evidence_progress"), dict) else {}
        if wait and self._evidence_progress_matches_wait(progress, wait):
            return dict(progress)
        if wait:
            return self._build_evidence_progress(
                wait=wait,
                artifact_payload=artifact_payload,
                updated_by_node=str(node.get("id") or ""),
            )
        return {}

    def _build_runner_input_attachments(
        self,
        *,
        session: Session,
        run: Run,
        node: dict[str, Any],
        token: dict[str, Any],
        deadline_monotonic: float,
    ) -> list[AgentInvocationAttachment]:
        latest_evidence = _get_path(token, "control.latest_evidence")
        if not isinstance(latest_evidence, dict):
            return []
        seq_no = latest_evidence.get("seq_no")
        if not isinstance(seq_no, int) or isinstance(seq_no, bool):
            return []
        parts = latest_evidence.get("parts")
        if not isinstance(parts, list):
            return []

        attachments: list[AgentInvocationAttachment] = []
        image_parts = [
            part
            for part in parts
            if isinstance(part, dict) and str(part.get("kind") or "").lower() == "image"
        ]
        for part in image_parts:
            self._ensure_runtime_deadline(deadline_monotonic)
            part_id = str(part.get("part_id") or "").strip()
            source_ref = f"terminal_event:{seq_no}:{part_id}" if part_id else f"terminal_event:{seq_no}"
            if len(attachments) >= RUNNER_MAX_IMAGE_ATTACHMENTS:
                self._append_runner_attachment_warning(
                    session,
                    run=run,
                    node=node,
                    source_ref=source_ref,
                    artifact_object_id=str(part.get("artifact_object_id") or ""),
                    reason="attachment_limit_exceeded",
                )
                continue
            attachment = self._resolve_runner_image_attachment(
                session=session,
                run=run,
                node=node,
                seq_no=seq_no,
                part=part,
                deadline_monotonic=deadline_monotonic,
            )
            if attachment is not None:
                attachments.append(attachment)
        self._commit_before_runtime_external_io(session)
        return attachments

    def _resolve_runner_image_attachment(
        self,
        *,
        session: Session,
        run: Run,
        node: dict[str, Any],
        seq_no: int,
        part: dict[str, Any],
        deadline_monotonic: float,
    ) -> AgentInvocationAttachment | None:
        part_id = str(part.get("part_id") or "").strip()
        source_ref = f"terminal_event:{seq_no}:{part_id}" if part_id else f"terminal_event:{seq_no}"
        artifact_object_id = str(part.get("artifact_object_id") or "")
        if not artifact_object_id:
            self._append_runner_attachment_warning(
                session,
                run=run,
                node=node,
                source_ref=source_ref,
                artifact_object_id="",
                reason="missing_artifact_object_id",
            )
            return None
        artifact_object = self.repository.get_artifact_object(session, artifact_object_id)
        if not artifact_object:
            self._append_runner_attachment_warning(
                session,
                run=run,
                node=node,
                source_ref=source_ref,
                artifact_object_id=artifact_object_id,
                reason="artifact_object_not_found",
            )
            return None
        if self.object_store is None:
            self._append_runner_attachment_warning(
                session,
                run=run,
                node=node,
                source_ref=source_ref,
                artifact_object_id=artifact_object_id,
                reason="object_store_unavailable",
            )
            return None
        metadata = part.get("metadata") if isinstance(part.get("metadata"), dict) else {}
        filename = str(metadata.get("filename") or metadata.get("name") or part_id or artifact_object_id)
        media_type = str(part.get("mime_type") or artifact_object.media_type or "application/octet-stream")
        if not media_type.lower().startswith("image/"):
            self._append_runner_attachment_warning(
                session,
                run=run,
                node=node,
                source_ref=source_ref,
                artifact_object_id=artifact_object_id,
                reason="unsupported_media_type",
            )
            return None
        self._commit_before_runtime_external_io(session)
        try:
            content = self._download_runner_attachment_bytes(
                bucket=artifact_object.bucket,
                object_key=artifact_object.object_key,
                deadline_monotonic=deadline_monotonic,
            )
        except RuntimeStepTimeoutError:
            raise
        except Exception as exc:  # pragma: no cover - exercised by integration fakes.
            self._append_runner_attachment_warning(
                session,
                run=run,
                node=node,
                source_ref=source_ref,
                artifact_object_id=artifact_object_id,
                reason=f"download_failed:{exc.__class__.__name__}",
            )
            return None
        self._ensure_runtime_deadline(deadline_monotonic)
        return AgentInvocationAttachment(
            attachment_id=source_ref,
            source_ref=source_ref,
            terminal_event_seq=seq_no,
            part_id=part_id,
            filename=filename,
            media_type=media_type,
            size_bytes=int(part.get("size_bytes") or len(content) or 0),
            checksum=str(part.get("checksum") or ""),
            artifact_object_id=artifact_object_id,
            content_base64=base64.b64encode(content).decode("ascii"),
        )

    @staticmethod
    def _ensure_runtime_deadline(deadline_monotonic: float) -> None:
        if time.monotonic() >= deadline_monotonic:
            raise RuntimeStepTimeoutError("Runtime 节点执行超过总时限。")

    def _download_runner_attachment_bytes(
        self,
        *,
        bucket: str,
        object_key: str,
        deadline_monotonic: float,
    ) -> bytes:
        def object_io(operation, /, *args, **kwargs):
            call_io = getattr(self.object_store, "call_io", None)
            if callable(call_io):
                return call_io(operation, *args, **kwargs)
            return operation(*args, **kwargs)

        self._ensure_runtime_deadline(deadline_monotonic)
        open_download = getattr(self.object_store, "open_download", None)
        if not callable(open_download):
            content = object_io(
                self.object_store.download_bytes,
                bucket=bucket,
                object_key=object_key,
            )
            self._ensure_runtime_deadline(deadline_monotonic)
            return content
        download = object_io(open_download, bucket=bucket, object_key=object_key)
        chunks: list[bytes] = []
        try:
            while True:
                self._ensure_runtime_deadline(deadline_monotonic)
                chunk = object_io(download.read, 256 * 1024)
                self._ensure_runtime_deadline(deadline_monotonic)
                if not chunk:
                    return b"".join(chunks)
                chunks.append(chunk)
        finally:
            object_io(download.close)

    def _commit_before_runtime_external_io(self, session: Session) -> None:
        if not session.in_transaction():
            return
        self._validate_active_job_lease(session)
        session.commit()
        session.info.pop("runtime_lease_fence", None)

    def _runner_prompt_view(self, *, run: Run, node: dict[str, Any], token: dict[str, Any]) -> dict[str, Any]:
        control = token.get("control") if isinstance(token.get("control"), dict) else {}
        projected_control = {
            key: control[key]
            for key in ("wait", "latest_evidence", "latest_evaluation")
            if isinstance(control, dict) and key in control
        }
        return {
            "token_projection_ref": f"runtime://runs/{run.id}/snapshots/{run.latest_snapshot_seq}/projection/{node.get('id')}",
            "phase": token.get("phase"),
            "input": token.get("input_envelope") if isinstance(token.get("input_envelope"), dict) else {},
            "facts": token.get("facts") if isinstance(token.get("facts"), dict) else {},
            "control": projected_control,
            "observations": token.get("observations") if isinstance(token.get("observations"), dict) else {},
            "node": {
                "id": node.get("id"),
                "kind": node.get("kind"),
                "actor": node.get("actor"),
                "interaction": node.get("interaction") if isinstance(node.get("interaction"), dict) else {},
                "policy": node.get("policy") if isinstance(node.get("policy"), dict) else {},
            },
        }

    def _runner_current_checkpoint(self, *, run: Run, node: dict[str, Any], token: dict[str, Any]) -> dict[str, Any]:
        control = token.get("control") if isinstance(token.get("control"), dict) else {}
        wait = control.get("wait") if isinstance(control.get("wait"), dict) else None
        if wait:
            return dict(wait)
        interaction = node.get("interaction") if isinstance(node.get("interaction"), dict) else {}
        return {
            "checkpoint_id": str(interaction.get("checkpoint_id") or f"{node.get('id')}:wait"),
            "workflow_step_id": str(interaction.get("workflow_step_id") or node.get("id") or ""),
            "reason": str(interaction.get("wait_reason") or ""),
            "expected_inputs": interaction.get("expected_inputs") if isinstance(interaction.get("expected_inputs"), list) else [],
            "resume_phase": str(interaction.get("resume_phase") or f"evaluate_{node.get('id')}"),
            "run_id": run.id,
            "evidence": [],
        }

    @staticmethod
    def _read_runner_observation_artifact(result: AgentResult) -> dict[str, Any]:
        if not result.sandbox_path:
            raise RuntimeError("psop.runner 未返回 sandbox_path。")
        path = Path(result.sandbox_path) / "outputs" / "runner-observation.json"
        if not path.exists():
            raise RuntimeError(f"psop.runner 未生成必需 artifact：{RUNNER_OBSERVATION_VIRTUAL_PATH}")
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise RuntimeError("runner-observation.json 必须是 JSON object。")
        return payload

    @staticmethod
    def _map_runner_observation_to_runtime_observation(
        observation: dict[str, Any],
        *,
        agent_result: AgentResult,
        usage: dict[str, Any],
    ) -> dict[str, Any]:
        terminal_message = str(observation.get("terminal_message") or observation.get("final_response") or "")
        runtime_decision = str(observation.get("runtime_decision") or observation.get("decision") or "")
        return {
            "content": terminal_message,
            "decision": runtime_decision,
            "reason": str(observation.get("reason") or ""),
            "next_phase": str(observation.get("next_phase") or ""),
            "terminal_message": terminal_message,
            "wait_reason": str(observation.get("wait_reason") or ""),
            "expected_inputs": observation.get("expected_inputs") if isinstance(observation.get("expected_inputs"), list) else [],
            "evidence_assessment": observation.get("evidence_assessment") if isinstance(observation.get("evidence_assessment"), dict) else {},
            "safety_flags": observation.get("safety_flags") if isinstance(observation.get("safety_flags"), list) else [],
            "final_response": str(observation.get("final_response") or ""),
            "runner": {
                "agent_key": "psop.runner",
                "agent_run_id": agent_result.agent_run_id,
                "artifact_ref": RUNNER_OBSERVATION_ARTIFACT_REF,
                "source_refs": observation.get("source_refs") if isinstance(observation.get("source_refs"), list) else [],
                "safety_flags": observation.get("safety_flags") if isinstance(observation.get("safety_flags"), list) else [],
                "original_decision": str(observation.get("decision") or ""),
                "suggested_next_phase": str(observation.get("next_phase") or ""),
            },
            "usage": usage,
            "summary": f"Runner decision: {runtime_decision}",
        }

    @staticmethod
    def _agent_result_usage(result: AgentResult) -> dict[str, Any]:
        usage = {"llm_calls": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        for event in result.events:
            if event.event_type == "agent.model.completed":
                usage["llm_calls"] += 1
            if event.event_type != "agent.token.usage":
                continue
            item = event.payload.get("usage")
            if not isinstance(item, dict):
                continue
            for key in ("input_tokens", "output_tokens", "total_tokens"):
                value = item.get(key)
                if isinstance(value, int) and not isinstance(value, bool):
                    usage[key] += value
        return usage

    def _merge_observation(self, *, node: dict[str, Any], token: dict[str, Any], observation: dict[str, Any]) -> dict[str, Any]:
        next_token = json.loads(json.dumps(token, ensure_ascii=False))
        token_observation = self._observation_for_token(observation)
        for operation in node.get("merge", []):
            if not isinstance(operation, dict) or operation.get("op") != "set":
                continue
            target_path = operation.get("path")
            if not isinstance(target_path, str):
                continue
            value = operation.get("value") if "value" in operation else self._resolve_merge_source(
                str(operation.get("from")),
                token=next_token,
                observation=token_observation,
            )
            _set_path(next_token, target_path, value)
        next_token.setdefault("observations", {}).setdefault(str(node.get("id")), token_observation)
        return next_token

    @staticmethod
    def _observation_for_trace(observation: dict[str, Any]) -> dict[str, Any]:
        trace_observation = dict(observation)
        request = trace_observation.pop("_trace_request", None)
        if request:
            trace_observation["request"] = request
        return trace_observation

    @staticmethod
    def _observation_for_token(observation: dict[str, Any]) -> dict[str, Any]:
        token_observation = dict(observation)
        token_observation.pop("_trace_request", None)
        return token_observation

    @staticmethod
    def _resolve_merge_source(source: str, *, token: dict[str, Any], observation: dict[str, Any]) -> Any:
        if source.startswith("observation."):
            return _get_path(observation, source.removeprefix("observation."))
        if source == "observation":
            return observation
        if source.startswith("token."):
            return _get_path(token, source.removeprefix("token."))
        if source.startswith("input."):
            return _get_path(token.get("input_envelope", {}), source.removeprefix("input."))
        return None

    def _render_llm_prompts(
        self,
        session: Session,
        node: dict[str, Any],
        token: dict[str, Any],
        artifact_payload: dict[str, Any],
    ) -> tuple[str, str, dict[str, Any] | None]:
        projection = node.get("projection") if isinstance(node.get("projection"), dict) else {}
        runtime_contract = artifact_payload.get("runtime_contract", {}) if isinstance(artifact_payload.get("runtime_contract"), dict) else {}
        skill = artifact_payload.get("skill", {}) if isinstance(artifact_payload.get("skill"), dict) else {}
        skill_instruction = str(runtime_contract.get("skill_instruction") or "")
        prompt_metadata = None
        if projection.get("system_template") and projection.get("user_template"):
            system_template = str(projection.get("system_template") or "")
            user_template = str(projection.get("user_template") or "")
        else:
            prompt_pack = self.agent_prompt_service.resolve_prompt_pack(
                session,
                usage_key="runtime.llm_node_fallback",
                fallback_ref="runtime_execution/llm_node_fallback/v1",
            )
            system_template = str(projection.get("system_template") or prompt_pack.system_prompt)
            user_template = str(projection.get("user_template") or prompt_pack.files.get("user_template.md") or "")
            prompt_metadata = prompt_pack.metadata()
        context = {
            "token": token,
            "input": token.get("input_envelope", {}),
            "skill": {
                **skill,
                "instruction": skill_instruction,
            },
        }
        system_prompt = _append_runtime_llm_language_policy(_render_template(system_template, context))
        user_prompt = _render_template(user_template, context)
        return system_prompt, user_prompt, prompt_metadata

    def _resolve_llm_attachments(self, session: Session, token: dict[str, Any]) -> list[LlmAttachment]:
        parts = self._latest_input_parts(token)
        attachments: list[LlmAttachment] = []
        for part in parts:
            kind = str(part.get("kind") or "").lower()
            artifact_object_id = str(part.get("artifact_object_id") or "")
            if kind == "text" or not artifact_object_id:
                continue
            artifact_object = self.repository.get_artifact_object(session, artifact_object_id)
            if not artifact_object:
                raise RuntimeError(f"Terminal input attachment `{artifact_object_id}` not found.")
            if self.object_store is None:
                raise RuntimeError("RuntimeService 未配置对象存储，无法读取多模态输入。")
            content = self.object_store.download_bytes(bucket=artifact_object.bucket, object_key=artifact_object.object_key)
            metadata = part.get("metadata") if isinstance(part.get("metadata"), dict) else {}
            filename = str(metadata.get("filename") or metadata.get("name") or f"{part.get('part_id') or artifact_object_id}")
            media_type = str(part.get("mime_type") or artifact_object.media_type or "application/octet-stream")
            attachments.append(
                LlmAttachment(
                    filename=filename,
                    media_type=media_type,
                    content_base64=base64.b64encode(content).decode("ascii"),
                )
            )
        return attachments

    @staticmethod
    def _latest_input_parts(token: dict[str, Any]) -> list[dict[str, Any]]:
        control = token.get("control") if isinstance(token.get("control"), dict) else {}
        latest_evidence = control.get("latest_evidence") if isinstance(control, dict) else None
        if isinstance(latest_evidence, dict) and isinstance(latest_evidence.get("parts"), list):
            return [part for part in latest_evidence["parts"] if isinstance(part, dict)]
        terminal = token.get("terminal") if isinstance(token.get("terminal"), dict) else {}
        events = terminal.get("events") if isinstance(terminal, dict) else []
        if isinstance(events, list):
            for event in reversed(events):
                if isinstance(event, dict) and event.get("direction") == "input" and isinstance(event.get("parts"), list):
                    return [part for part in event["parts"] if isinstance(part, dict)]
        return []

    @staticmethod
    def _llm_attachment_summary(attachments: list[LlmAttachment]) -> list[dict[str, Any]]:
        return [
            {
                "filename": attachment.filename,
                "media_type": attachment.media_type,
                "content_base64_chars": len(attachment.content_base64),
            }
            for attachment in attachments
        ]

    @staticmethod
    def _parse_evaluation_observation(content: str, *, node: dict[str, Any]) -> dict[str, Any]:
        raw = content.strip()
        if raw.startswith("```"):
            import re

            match = re.search(r"```(?:json)?\s*(.*?)```", raw, flags=re.DOTALL | re.IGNORECASE)
            if match:
                raw = match.group(1).strip()
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Evaluation node `{node.get('id')}` must return JSON decision: {exc.msg}") from exc
        if not isinstance(parsed, dict):
            raise RuntimeError(f"Evaluation node `{node.get('id')}` must return a JSON object.")
        decision = str(parsed.get("decision") or "").strip().lower()
        if decision not in {"proceed", "retry", "need_more_evidence", "abort", "complete"}:
            raise RuntimeError(
                f"Evaluation node `{node.get('id')}` returned unsupported decision `{decision or '<missing>'}`."
            )
        return {
            **parsed,
            "decision": decision,
            "reason": str(parsed.get("reason") or ""),
            "next_phase": str(parsed.get("next_phase") or ""),
            "terminal_message": str(parsed.get("terminal_message") or ""),
            "summary": f"Evaluation decision: {decision}",
        }

    @staticmethod
    def _event_type_for_node(node: dict[str, Any]) -> str:
        kind = node.get("kind")
        mapping = {
            "start": "runtime.start.completed",
            "input": "runtime.input.accepted",
            "llm": "gateway.inference.completed",
            "tool": "gateway.tool.completed",
            "terminal": "runtime.final.completed",
        }
        if str(node.get("kind")) == "terminal" and "abort" in str(node.get("id") or ""):
            return "runtime.aborted"
        return mapping.get(str(kind), f"runtime.node.{node.get('id')}.completed")

    @staticmethod
    def _halt_success(artifact_payload: dict[str, Any], token: dict[str, Any]) -> bool:
        halt = artifact_payload.get("halt", {})
        success = halt.get("success") if isinstance(halt, dict) else None
        if isinstance(success, dict) and "field_equals" in success:
            condition = success["field_equals"]
            if isinstance(condition, dict):
                return _get_path(token, str(condition.get("path"))) == condition.get("value")
        return token.get("status") == "success"

    @staticmethod
    def _halt_aborted(artifact_payload: dict[str, Any], token: dict[str, Any]) -> bool:
        halt = artifact_payload.get("halt", {})
        aborted = halt.get("aborted") if isinstance(halt, dict) else None
        if isinstance(aborted, dict) and "field_equals" in aborted:
            condition = aborted["field_equals"]
            if isinstance(condition, dict):
                return _get_path(token, str(condition.get("path"))) == condition.get("value")
        return token.get("status") == "aborted"

    @staticmethod
    def _halt_wait(artifact_payload: dict[str, Any], token: dict[str, Any]) -> bool:
        halt = artifact_payload.get("halt", {})
        wait = halt.get("wait") if isinstance(halt, dict) else None
        if isinstance(wait, dict) and "field_equals" in wait:
            condition = wait["field_equals"]
            if isinstance(condition, dict):
                return _get_path(token, str(condition.get("path"))) == condition.get("value")
        return token.get("status") == "waiting"

    @staticmethod
    def _run_demo_tool(*, user_input: str, llm_output: str) -> dict[str, Any]:
        return {
            "tool_name": "psop.demo.inspect_input",
            "input_length": len(user_input),
            "llm_output_length": len(llm_output),
            "contains_question": "?" in user_input or "？" in user_input,
            "result": "demo tool 已完成输入检查。",
        }

    @staticmethod
    def _compose_final_output(llm_output: str, tool_result: dict[str, Any]) -> str:
        return (
            f"{llm_output.strip()}\n\n"
            f"---\n"
            f"工具检查：{tool_result['result']}输入长度 {tool_result['input_length']}，"
            f"是否包含问题：{'是' if tool_result['contains_question'] else '否'}。"
        )

    @staticmethod
    def _accumulate_llm_usage(budgets: dict[str, Any], usage: dict[str, Any]) -> None:
        fields = {
            "input_tokens": "llm_input_tokens",
            "output_tokens": "llm_output_tokens",
            "total_tokens": "llm_total_tokens",
        }
        for source_key, budget_key in fields.items():
            value = usage.get(source_key)
            if isinstance(value, int) and not isinstance(value, bool):
                budgets[budget_key] = int(budgets.get(budget_key, 0)) + value

    @staticmethod
    def _hash_payload(payload: dict[str, Any]) -> str:
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _build_invocation_response(self, session: Session, invocation: SkillInvocation) -> InvocationResponse:
        run = self.repository.get_run_for_invocation(session, invocation.id)
        return InvocationResponse(
            id=invocation.id,
            skill_definition_id=invocation.skill_definition_id,
            skill_version_id=invocation.skill_version_id,
            compile_artifact_id=invocation.compile_artifact_id,
            gateway_type=invocation.gateway_type,
            input_envelope=invocation.input_envelope,
            terminal_context=invocation.terminal_context,
            binding_preferences=invocation.binding_preferences,
            status=invocation.status,
            idempotency_key=invocation.idempotency_key,
            run_id=run.id if run else None,
            terminal_session_id=run.terminal_session_id if run else None,
            created_at=invocation.created_at,
            updated_at=invocation.updated_at,
        )

    def _build_run_response(self, session: Session, run: Run) -> RunResponse:
        wait_context = self._run_wait_context(session, run)
        return RunResponse(
            id=run.id,
            invocation_id=run.invocation_id,
            skill_definition_id=run.skill_definition_id,
            skill_version_id=run.skill_version_id,
            compile_artifact_id=run.compile_artifact_id,
            status=run.status,
            runtime_phase=run.runtime_phase,
            latest_snapshot_seq=run.latest_snapshot_seq,
            latest_terminal_seq=run.latest_terminal_seq,
            latest_trace_seq=run.latest_trace_seq,
            terminal_session_id=run.terminal_session_id,
            binding_summary=[self._binding_payload(item) for item in self.repository.list_run_bindings(session, run.id)],
            current_step=wait_context["current_step"],
            wait_reason=wait_context["wait_reason"],
            expected_inputs=wait_context["expected_inputs"],
            checkpoint_id=wait_context["checkpoint_id"],
            resume_phase=wait_context["resume_phase"],
            latest_evaluation=wait_context["latest_evaluation"],
            final_output=run.final_output,
            exit_reason=run.exit_reason,
            created_at=run.created_at,
            started_at=run.started_at,
            ended_at=run.ended_at,
            updated_at=run.updated_at,
        )

    def _build_run_task_status_response(
        self,
        *,
        session: Session,
        run: Run,
        artifact_payload: dict[str, Any],
        token: dict[str, Any],
        snapshot: SessionTokenSnapshot | None,
    ) -> RunTaskStatusResponse:
        runtime_contract = (
            artifact_payload.get("runtime_contract")
            if isinstance(artifact_payload.get("runtime_contract"), dict)
            else {}
        )
        raw_steps = runtime_contract.get("workflow_steps")
        workflow_steps = (
            [item for item in raw_steps if isinstance(item, dict) and str(item.get("id") or "")]
            if isinstance(raw_steps, list)
            else []
        )
        observations = token.get("observations") if isinstance(token.get("observations"), dict) else {}
        control = token.get("control") if isinstance(token.get("control"), dict) else {}
        wait = control.get("wait") if isinstance(control.get("wait"), dict) else {}
        active_wait = bool(wait) and (
            str(token.get("status") or "") == "waiting" or run.status == "waiting_input"
        )
        current_stage_id = str(wait.get("workflow_step_id") or "") if active_wait else ""
        step_ids = {str(item.get("id") or "") for item in workflow_steps}
        if current_stage_id not in step_ids:
            current_stage_id = self._workflow_step_id_from_phase(
                str(token.get("phase") or run.runtime_phase or ""),
                step_ids,
            )

        completed_ids: set[str] = set()
        for step_id in step_ids:
            evaluation = observations.get(f"evaluate_{step_id}")
            if isinstance(evaluation, dict) and str(evaluation.get("decision") or "").lower() in {
                "proceed",
                "complete",
            }:
                completed_ids.add(step_id)
        if not current_stage_id and run.status in {"failed", "aborted", "cancelled", "canceled"}:
            started_incomplete = [
                str(item.get("id") or "")
                for item in workflow_steps
                if str(item.get("id") or "") not in completed_ids
                and (
                    f"instruct_{item.get('id')}" in observations
                    or f"evaluate_{item.get('id')}" in observations
                )
            ]
            current_stage_id = started_incomplete[-1] if started_incomplete else ""

        latest_evaluation = (
            control.get("latest_evaluation")
            if isinstance(control.get("latest_evaluation"), dict)
            else {}
        )
        terminal_stage_status = {
            "failed": "failed",
            "aborted": "aborted",
            "cancelled": "cancelled",
            "canceled": "cancelled",
        }.get(run.status)
        stages: list[dict[str, Any]] = []
        for index, step in enumerate(workflow_steps, start=1):
            step_id = str(step.get("id") or "")
            if step_id in completed_ids:
                status = "completed"
            elif step_id == current_stage_id and terminal_stage_status:
                status = terminal_stage_status
            elif step_id == current_stage_id and active_wait:
                status = "waiting_input"
            elif step_id == current_stage_id:
                status = "in_progress"
            else:
                status = "pending"

            status_reason = ""
            if step_id == current_stage_id:
                if terminal_stage_status:
                    status_reason = run.exit_reason
                elif active_wait:
                    status_reason = str(wait.get("reason") or "")
                elif str(latest_evaluation.get("node_id") or "") == f"evaluate_{step_id}":
                    status_reason = str(latest_evaluation.get("reason") or "")
            stages.append(
                {
                    "id": step_id,
                    "index": index,
                    "title": str(step.get("title") or step_id),
                    "goal": str(step.get("goal") or ""),
                    "status": status,
                    "status_reason": status_reason,
                }
            )

        completed = len(completed_ids & step_ids)
        total = len(workflow_steps)
        current_checkpoint = self._build_run_task_checkpoint(
            wait=wait if active_wait else {},
            control=control,
            artifact_payload=artifact_payload,
            current_stage_id=current_stage_id,
        )
        skill_snapshot = artifact_payload.get("skill") if isinstance(artifact_payload.get("skill"), dict) else {}
        skill_definition = self.repository.get_skill_definition(session, run.skill_definition_id)
        skill_version = self.repository.get_skill_version(session, run.skill_version_id)
        activity_status = self._run_task_activity_status(
            run_status=run.status,
            token_status=str(token.get("status") or ""),
            has_current_stage=bool(current_stage_id),
            completed=completed,
            total=total,
        )
        return RunTaskStatusResponse(
            run_id=run.id,
            snapshot_seq=snapshot.seq_no if snapshot else run.latest_snapshot_seq,
            updated_at=run.updated_at,
            run_status=run.status,
            activity_status=activity_status,
            task={
                "skill_key": str(skill_snapshot.get("key") or (skill_definition.key if skill_definition else "")),
                "skill_name": str(skill_snapshot.get("name") or (skill_definition.name if skill_definition else "")),
                "version_no": skill_snapshot.get("version_no") or (skill_version.version_no if skill_version else None),
                "execution_goal": str(runtime_contract.get("execution_goal") or ""),
            },
            progress={
                "completed": completed,
                "total": total,
                "percent": round(completed * 100 / total) if total else 0,
            },
            current_stage_id=current_stage_id,
            stages=stages,
            current_checkpoint=current_checkpoint,
        )

    def _build_run_task_checkpoint(
        self,
        *,
        wait: dict[str, Any],
        control: dict[str, Any],
        artifact_payload: dict[str, Any],
        current_stage_id: str,
    ) -> dict[str, Any] | None:
        if not wait or not current_stage_id:
            return None
        progress = control.get("evidence_progress") if isinstance(control.get("evidence_progress"), dict) else {}
        if not self._evidence_progress_matches_wait(progress, wait):
            raw_requirements = self._evidence_requirements_for_workflow_step(
                artifact_payload=artifact_payload,
                workflow_step_id=current_stage_id,
            )
            requirements = [{**item, "status": "missing", "reason": ""} for item in raw_requirements]
        else:
            requirements = [item for item in progress.get("requirements", []) if isinstance(item, dict)]
        normalized_requirements = [
            {
                "requirement_key": str(item.get("requirement_key") or ""),
                "description": str(item.get("description") or item.get("requirement_key") or ""),
                "kind": str(item.get("kind") or ""),
                "status": str(item.get("status") or "missing"),
                "reason": str(item.get("reason") or ""),
            }
            for item in requirements
            if str(item.get("requirement_key") or "")
        ]
        return {
            "checkpoint_id": str(wait.get("checkpoint_id") or ""),
            "reason": str(wait.get("reason") or ""),
            "expected_inputs": wait.get("expected_inputs") if isinstance(wait.get("expected_inputs"), list) else [],
            "accepted_requirements": sum(item["status"] == "accepted" for item in normalized_requirements),
            "total_requirements": len(normalized_requirements),
            "requirements": normalized_requirements,
        }

    @staticmethod
    def _workflow_step_id_from_phase(phase: str, step_ids: set[str]) -> str:
        for prefix in ("instruct_", "evaluate_"):
            if phase.startswith(prefix) and phase.removeprefix(prefix) in step_ids:
                return phase.removeprefix(prefix)
        return ""

    @staticmethod
    def _run_task_activity_status(
        *,
        run_status: str,
        token_status: str,
        has_current_stage: bool,
        completed: int,
        total: int,
    ) -> str:
        terminal_status = {
            "succeeded": "succeeded",
            "failed": "failed",
            "aborted": "aborted",
            "cancelled": "cancelled",
            "canceled": "cancelled",
        }.get(run_status)
        if terminal_status:
            return terminal_status
        if run_status == "waiting_input" or token_status == "waiting":
            return "waiting_input"
        if total and completed == total:
            return "finalizing"
        if has_current_stage:
            return "running"
        if run_status in {"waiting_runtime", "queued"}:
            return "initializing"
        return "running"

    def _run_wait_context(self, session: Session, run: Run) -> dict[str, Any]:
        snapshots = self.repository.list_snapshots(session, run.id)
        token = snapshots[-1].token_payload if snapshots else {}
        control = token.get("control") if isinstance(token, dict) else {}
        control = control if isinstance(control, dict) else {}
        wait = control.get("wait")
        wait = wait if isinstance(wait, dict) else {}
        latest_evaluation = control.get("latest_evaluation")
        latest_evaluation = latest_evaluation if isinstance(latest_evaluation, dict) else {}
        return {
            "current_step": str(wait.get("workflow_step_id") or token.get("current_step") or ""),
            "wait_reason": str(wait.get("reason") or ""),
            "expected_inputs": wait.get("expected_inputs") if isinstance(wait.get("expected_inputs"), list) else [],
            "checkpoint_id": str(wait.get("checkpoint_id") or ""),
            "resume_phase": str(wait.get("resume_phase") or ""),
            "latest_evaluation": latest_evaluation,
        }

    @staticmethod
    def _build_terminal_session_response(terminal_session: TerminalSession) -> TerminalSessionResponse:
        return TerminalSessionResponse(
            id=terminal_session.id,
            run_id=terminal_session.run_id,
            mode=terminal_session.mode,
            status=terminal_session.status,
            opened_at=terminal_session.opened_at,
            closed_at=terminal_session.closed_at,
            created_at=terminal_session.created_at,
        )

    def _build_terminal_event_response(self, session: Session, event: TerminalEvent) -> TerminalEventResponse:
        return TerminalEventResponse(
            id=event.id,
            terminal_session_id=event.terminal_session_id,
            run_id=event.run_id,
            trace_event_id=event.trace_event_id,
            artifact_object_id=event.artifact_object_id,
            run_capability_binding_id=event.run_capability_binding_id,
            direction=event.direction,
            event_kind=event.event_kind,
            mime_type=event.mime_type,
            payload_inline=event.payload_inline,
            seq_no=event.seq_no,
            external_event_id=event.external_event_id,
            source_ref=event.source_ref,
            parts=[
                self._build_terminal_event_part_response(part)
                for part in self.repository.list_terminal_event_parts(session, event.id)
            ],
            occurred_at=event.occurred_at,
            created_at=event.created_at,
        )

    @staticmethod
    def _build_terminal_event_part_response(part: TerminalEventPart) -> TerminalEventPartResponse:
        return TerminalEventPartResponse(
            id=part.id,
            terminal_event_id=part.terminal_event_id,
            run_id=part.run_id,
            artifact_object_id=part.artifact_object_id,
            part_id=part.part_id,
            order_index=part.order_index,
            kind=part.kind,
            mime_type=part.mime_type,
            text=part.text_inline,
            size_bytes=part.size_bytes,
            checksum=part.checksum,
            metadata=part.part_metadata,
            created_at=part.created_at,
        )

    @staticmethod
    def _build_run_binding_response(binding: RunCapabilityBinding) -> RunCapabilityBindingResponse:
        return RunCapabilityBindingResponse(
            id=binding.id,
            run_id=binding.run_id,
            compile_artifact_id=binding.compile_artifact_id,
            source_capability_binding_id=binding.source_capability_binding_id,
            requirement_key=binding.requirement_key,
            binding_type=binding.binding_type,
            capability=binding.capability,
            target_kind=binding.target_kind,
            target_ref=binding.target_ref,
            channel=binding.channel,
            schema_ref=binding.schema_ref,
            manifest_hash=binding.manifest_hash,
            policy_snapshot=binding.policy_snapshot,
            status=binding.status,
            created_at=binding.created_at,
            updated_at=binding.updated_at,
        )

    @staticmethod
    def _build_snapshot_response(snapshot: SessionTokenSnapshot) -> SessionTokenSnapshotResponse:
        return SessionTokenSnapshotResponse(
            id=snapshot.id,
            run_id=snapshot.run_id,
            seq_no=snapshot.seq_no,
            token_payload=snapshot.token_payload,
            enabled_set=snapshot.enabled_set,
            selection_summary=snapshot.selection_summary,
            snapshot_hash=snapshot.snapshot_hash,
            created_at=snapshot.created_at,
        )

    @staticmethod
    def _build_trace_event_response(event: TraceEvent) -> TraceEventResponse:
        return TraceEventResponse(
            id=event.id,
            run_id=event.run_id,
            seq_no=event.seq_no,
            phase=event.phase,
            event_type=event.event_type,
            span_id=event.span_id,
            parent_span_id=event.parent_span_id,
            payload=event.payload,
            occurred_at=event.occurred_at,
        )

    @staticmethod
    def _build_runtime_job_response(job: RuntimeJob) -> RuntimeJobResponse:
        return RuntimeJobResponse(
            id=job.id,
            job_type=job.job_type,
            status=job.status,
            payload=job.payload,
            dedupe_key=job.dedupe_key,
            run_id=job.run_id,
            compile_request_id=job.compile_request_id,
            worker_name=job.worker_name or "",
            metrics=dict(job.metrics or {}),
            lease_until=job.lease_until,
            available_at=job.available_at,
            attempt_no=job.attempt_no,
            max_attempts=job.max_attempts,
            last_error=job.last_error,
            started_at=job.started_at,
            finished_at=job.finished_at,
            created_at=job.created_at,
            updated_at=job.updated_at,
        )

    @staticmethod
    def _build_timeline_item(event: TraceEventResponse) -> ReplayTimelineItem:
        titles = {
            "binding.resolved": "绑定解析",
            "binding.updated": "绑定更新",
            "runtime.input.accepted": "输入",
            "runtime.wait_checkpoint.entered": "等待现场证据",
            "runtime.agent.completed": "Runner 输出",
            "gateway.inference.completed": "LLM 输出",
            "gateway.tool.completed": "工具调用",
            "runtime.final.completed": "最终结果",
            "runtime.aborted": "已中止",
            "runtime.runner.attachment.warning": "Runner 附件告警",
            "runtime.message_processing.failed": "消息处理失败",
            "runtime.failed": "运行失败",
        }
        observation = event.payload.get("observation", {})
        if isinstance(observation, dict):
            summary = (
                observation.get("final_response")
                or observation.get("content")
                or observation.get("result")
                or observation.get("user_input")
                or event.payload.get("summary")
                or event.event_type
            )
        else:
            summary = event.payload.get("summary") or event.event_type
        return ReplayTimelineItem(
            seq_no=event.seq_no,
            phase=event.phase,
            event_type=event.event_type,
            title=titles.get(event.event_type, event.event_type),
            summary=str(summary),
            payload=event.payload,
            occurred_at=event.occurred_at,
        )

    @staticmethod
    def _build_terminal_timeline_item(event: TerminalEventResponse) -> ReplayTimelineItem:
        title = "终端输入" if event.direction == "input" else "终端输出"
        if event.parts:
            summary = "\n".join(
                filter(
                    None,
                    [
                        part.text or part.metadata.get("filename") or part.part_id
                        for part in event.parts
                    ],
                )
            )
        elif isinstance(event.payload_inline, str):
            summary = event.payload_inline
        elif event.payload_inline is None:
            summary = event.event_kind
        else:
            summary = json.dumps(event.payload_inline, ensure_ascii=False)
        return ReplayTimelineItem(
            seq_no=event.seq_no,
            phase="terminal",
            event_type="terminal.event.appended",
            title=title,
            summary=summary,
            payload=event.model_dump(mode="json"),
            occurred_at=event.occurred_at,
        )


def _actor_name(actor: Any) -> str:
    if isinstance(actor, str):
        return actor
    if isinstance(actor, dict):
        if isinstance(actor.get("name"), str):
            return str(actor["name"])
        actor_type = actor.get("type")
        if actor_type == "llm":
            return "agent.llm"
        if actor_type == "tool":
            return "capability.demo_tool"
        if actor_type == "runtime" and isinstance(actor.get("operation"), str):
            return f"runtime.{actor['operation']}"
    return ""


def _get_path(payload: Any, path: str | None) -> Any:
    if not path:
        return None
    current = payload
    for part in path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return None
    return current


def _set_path(payload: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    current = payload
    for part in parts[:-1]:
        next_value = current.get(part)
        if not isinstance(next_value, dict):
            next_value = {}
            current[part] = next_value
        current = next_value
    current[parts[-1]] = value


def _render_template(template: str, context: dict[str, Any]) -> str:
    def replace(match):
        expression = match.group(1).strip()
        return str(_get_path(context, expression) or "")

    import re

    return re.sub(r"\{\{\s*([^}]+?)\s*\}\}", replace, template)


def _append_runtime_llm_language_policy(system_prompt: str) -> str:
    prompt = system_prompt.strip()
    if "平台级输出语言要求" in prompt:
        return prompt
    if not prompt:
        return RUNTIME_LLM_LANGUAGE_POLICY
    return f"{prompt}\n\n{RUNTIME_LLM_LANGUAGE_POLICY}"


def _last_observation_content(token: dict[str, Any]) -> str:
    observations = token.get("observations", {})
    if not isinstance(observations, dict):
        return ""
    for value in reversed(list(observations.values())):
        if isinstance(value, dict):
            content = value.get("content") or value.get("final_response") or value.get("summary")
            if content:
                return str(content)
    return ""
