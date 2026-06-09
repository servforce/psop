from __future__ import annotations

import base64
import hashlib
import json
import logging
from datetime import timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent_harness.guardrails import InputGuardrail
from app.agent_harness.planning import AgentPlanner
from app.agents.models import AgentEvent, AgentModelCall, AgentRun, AgentToolAuthorization, AgentToolCall
from app.agents.schemas import (
    AgentEventResponse,
    AgentModelCallResponse,
    AgentRunResponse,
    AgentToolAuthorizationResponse,
    AgentToolCallResponse,
    AppendAgentEventRequest,
    CreateAgentRunRequest,
)
from app.agents.service import AgentService
from app.core.config import Settings
from app.core.logging import log_context
from app.core.observability import current_trace_context, record_span_exception, start_span
from app.agent_prompts.service import AgentPromptService
from app.agents.tool_authorization_context import tool_authorization_business_context
from app.evaluations.evidence import finding_evidence_refs
from app.evaluations.models import RunEvaluation, RunEvaluationFinding
from app.evaluations.schemas import RunEvaluationFindingResponse, RunEvaluationResponse
from app.governance.models import PsopImprovementExperiment, PsopImprovementProposal
from app.governance.schemas import GovernanceExperimentResponse, GovernanceProposalResponse
from app.jobs.models import RuntimeJob
from app.jobs.repository import JobRepository
from app.jobs.schemas import RuntimeJobResponse
from app.jobs.types import RUNTIME_STEP_JOB_TYPE, RUN_EVALUATION_JOB_TYPE
from app.runtime.models import (
    RunCapabilityBinding,
    Run,
    SessionTokenSnapshot,
    SkillInvocation,
    RunEvent,
    RunEventPart,
    TerminalSession,
    RunTrace,
)
from app.runtime.repository import RuntimeRepository
from app.runtime.schemas import (
    AppendRunEventRequest,
    BindingRequirementResponse,
    CreateInvocationRequest,
    InvocationResponse,
    ReplayDetailResponse,
    ReplayEgNodePathItem,
    ReplayProvenanceResponse,
    ReplayTraceLookupResponse,
    ReplayTimelineItem,
    ResolveRunBindingsRequest,
    RunCapabilityBindingResponse,
    RunResponse,
    SessionTokenSnapshotResponse,
    RunEventAppendResponse,
    RunEventResponse,
    TerminalSessionDetailResponse,
    TerminalSessionResponse,
    TerminalTranscriptSummary,
    RunTraceResponse,
    RunEventPartInput,
    RunEventPartResponse,
)
from app.pskills.exceptions import SkillsError, SkillNotFoundError, SkillValidationError
from app.pskills.models import now_utc
from app.gateway.inference import LlmAttachment, LlmInferenceGateway, MULTIMODAL_ROUTE_KEY, TEXT_ROUTE_KEY
from app.infra.object_store import ObjectStoreService
from app.memory.service import MemoryService

LOGGER = logging.getLogger(__name__)

RUNTIME_LLM_LANGUAGE_POLICY = """平台级输出语言要求：
- 所有面向终端用户展示的自然语言必须使用简体中文。
- 如果当前节点要求输出 JSON，JSON 字段名和 decision/next_phase 等协议枚举值保持英文协议值。
- reason、terminal_message、final_response、summary 等自然语言字段值必须使用简体中文。
- 不要因为附件内容、文件名、模型默认行为或上游 prompt 语言而改用英文。
- 如果当前节点要求只输出 JSON，不要在 JSON 外追加任何说明。"""


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
        agent_service: AgentService | None = None,
        input_guardrail: InputGuardrail | None = None,
        memory_service: MemoryService | None = None,
        planner: AgentPlanner | None = None,
        object_store: ObjectStoreService | None = None,
    ) -> None:
        self.settings = settings
        self.inference_gateway = inference_gateway
        self.repository = repository or RuntimeRepository()
        self.job_repository = job_repository or JobRepository()
        self.agent_prompt_service = agent_prompt_service or AgentPromptService()
        self.agent_service = agent_service or AgentService()
        self.input_guardrail = input_guardrail or InputGuardrail()
        self.memory_service = memory_service or MemoryService()
        self.planner = planner or AgentPlanner()
        self.object_store = object_store

    def create_invocation(self, session: Session, payload: CreateInvocationRequest) -> InvocationResponse:
        pskill_definition = self.repository.get_pskill_definition_by_key(session, payload.skill_key)
        if not pskill_definition or pskill_definition.status == "archived":
            raise SkillNotFoundError("未找到可调用的 Skill。", details={"skill_key": payload.skill_key})

        if payload.compile_artifact_id:
            artifact = self.repository.get_artifact(session, payload.compile_artifact_id)
            if not artifact:
                raise SkillValidationError("指定编译产物不存在。", details={"compile_artifact_id": payload.compile_artifact_id})
            if artifact.status != "ready":
                raise SkillValidationError("指定编译产物尚不可运行。", details={"compile_artifact_id": payload.compile_artifact_id})
            pskill_version = self.repository.get_pskill_version(session, artifact.pskill_version_id)
            if not pskill_version or pskill_version.pskill_definition_id != pskill_definition.id:
                raise SkillValidationError("指定编译产物不属于当前 Skill。", details={"compile_artifact_id": payload.compile_artifact_id})
        else:
            pskill_version = self.repository.get_pskill_version(session, pskill_definition.latest_published_version_id)
            if not pskill_version or pskill_version.status != "published":
                raise SkillValidationError("当前 Skill 尚无已发布版本，无法发起运行。")

            artifact = self.repository.get_latest_ready_artifact(session, pskill_version.id)
            if not artifact:
                raise SkillValidationError("当前 Skill 尚无成功编译产物，无法发起运行。")
        artifact_object = self.repository.get_artifact_object(session, artifact.artifact_object_id)
        artifact_payload = artifact_object.content_json if artifact_object else {}
        gateway_type = "terminal" if payload.gateway_type in {"web", "terminal"} else payload.gateway_type
        terminal_context = payload.terminal_context or {
            "terminal_kind": "web" if payload.gateway_type in {"web", "terminal"} else payload.gateway_type
        }

        invocation = SkillInvocation(
            pskill_definition_id=pskill_definition.id,
            pskill_version_id=pskill_version.id,
            compile_artifact_id=artifact.id,
            gateway_type=gateway_type,
            input_envelope=payload.input_envelope,
            terminal_context=terminal_context,
            binding_preferences=payload.binding_preferences,
            status="queued",
        )
        session.add(invocation)
        session.flush()

        initial_input = self._extract_initial_terminal_input(payload.input_envelope)
        run = Run(
            invocation_id=invocation.id,
            pskill_definition_id=pskill_definition.id,
            pskill_version_id=pskill_version.id,
            compile_artifact_id=artifact.id,
            status="queued",
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
        self._append_run_trace(
            session,
            run=run,
            phase="binding",
            event_type="binding.resolved",
            payload={"bindings": [self._binding_payload(item) for item in bindings]},
        )

        initial_token = self._build_initial_token(payload.input_envelope, artifact_payload)
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
        if initial_input is not None:
            self._append_run_event(
                session,
                run=run,
                terminal_session=terminal_session,
                direction="input",
                event_kind="terminal.text.input.v1",
                mime_type="text/plain",
                payload_inline=initial_input,
                binding_id=self._default_binding_id(bindings, "input"),
                source_ref={"kind": "web", "connection_id": "invocation"},
                external_event_id=f"invocation:{invocation.id}:initial-input",
            )
        session.add(
            RuntimeJob(
                job_type=RUNTIME_STEP_JOB_TYPE,
                status="pending",
                payload={"run_id": run.id},
                run_id=run.id,
                dedupe_key=f"job:runtime:{run.id}",
                available_at=now_utc() + timedelta(seconds=2),
                max_attempts=self.settings.runtime_job_max_attempts,
            )
        )
        session.commit()
        LOGGER.info(
            "runtime invocation created",
            extra={
                "skill_id": pskill_definition.id,
                "skill_key": pskill_definition.key,
                "pskill_version_id": pskill_version.id,
                "invocation_id": invocation.id,
                "run_id": run.id,
                "artifact_id": artifact.id,
            },
        )

        # MVP local drain: keeps the issue #1 flow runnable without a separate worker process.
        # Real-world runs start even without user_input so Runtime can introduce the task,
        # output the first actionable step, and enter a durable wait checkpoint.
        self.process_run(session, run.id)
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
            pskill_definition_id=source_run.pskill_definition_id,
            pskill_version_id=source_run.pskill_version_id,
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
            pskill_definition_id=source_run.pskill_definition_id,
            pskill_version_id=source_run.pskill_version_id,
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
        self._append_run_trace(
            session,
            run=run,
            phase="binding",
            event_type="binding.resolved",
            payload={"bindings": [self._binding_payload(item) for item in bindings]},
        )

        copied_run_events: list[dict[str, Any]] = []
        for source_event in self.repository.list_run_events(session, source_run_id, to_seq=terminal_seq):
            source_parts = [
                RunEventPartInput(
                    part_id=part.part_id,
                    kind=part.kind,
                    mime_type=part.mime_type,
                    text=part.text_inline,
                    artifact_object_id=part.artifact_object_id,
                    size_bytes=part.size_bytes,
                    checksum=part.checksum,
                    metadata=part.part_metadata,
                )
                for part in self.repository.list_run_event_parts(session, source_event.id)
            ]
            copied = self._append_run_event(
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
                        "run_event_id": source_event.id,
                        "seq_no": source_event.seq_no,
                    },
                },
                external_event_id=f"fork:{run.id}:terminal:{source_event.seq_no}",
                occurred_at=source_event.occurred_at,
            )
            copied_run_events.append(self._run_event_token_payload_with_parts(session, copied))

        terminal = token.setdefault("terminal", {})
        token["run_events"] = list(copied_run_events)
        terminal["events"] = list(copied_run_events)
        terminal["latest_seq"] = run.latest_run_event_seq
        metadata = token.setdefault("metadata", {})
        metadata["run_event_cursor"] = run.latest_run_event_seq
        metadata["terminal_cursor"] = run.latest_run_event_seq
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
        self._append_run_trace(
            session,
            run=run,
            phase="fork",
            event_type="runtime.fork.created",
            payload={
                "source_run_id": source_run_id,
                "source_snapshot_seq": snapshot_seq,
                "source_terminal_seq": terminal_seq,
                "terminal_prefix_count": len(copied_run_events),
            },
        )
        if run.status != "waiting_input":
            session.add(
                RuntimeJob(
                    job_type=RUNTIME_STEP_JOB_TYPE,
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

    def process_run(self, session: Session, run_id: str) -> Run:
        run = self.repository.get_run(session, run_id)
        if not run:
            raise SkillNotFoundError("未找到 Run。", details={"run_id": run_id})
        if run.status in {"succeeded", "failed", "cancelled", "aborted"}:
            return run

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
            skill_id=run.pskill_definition_id,
            pskill_version_id=run.pskill_version_id,
        ):
            LOGGER.info("runtime loop started")

        job = self.job_repository.get_runtime_job_by_dedupe_key(session, f"job:runtime:{run.id}")
        if job:
            if job.status != "running":
                job.attempt_no += 1
            job.status = "running"

        run.started_at = run.started_at or now_utc()
        invocation.status = "running"
        session.flush()

        token: dict[str, Any] = {}
        latest_snapshot: SessionTokenSnapshot | None = None
        try:
            snapshots = self.repository.list_snapshots(session, run.id)
            latest_snapshot = snapshots[-1] if snapshots else None
            token = latest_snapshot.token_payload if latest_snapshot else {}
            token = self._compact_runtime_token(token)
            token = self._sync_run_events(session, run=run, token=token)
            run.status = "running"
            run.runtime_phase = str(token.get("phase") or self._initial_phase(artifact_payload))
            if token.get("status") == "waiting":
                run.status = "waiting_input"
                run.runtime_phase = self._runtime_phase_from_token(token)
                invocation.status = "running"
                if job:
                    job.status = "succeeded"
                self._sync_runtime_job_metrics(job, token)
                session.commit()
                LOGGER.info("runtime loop waiting for terminal evidence")
                return run
            max_steps = int(artifact_payload.get("policies", {}).get("max_steps") or 16)
            with start_span(
                "runtime.loop",
                **self._runtime_span_attributes(
                    run=run,
                    invocation=invocation,
                    artifact=artifact,
                    latest_snapshot=latest_snapshot,
                ),
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
                            if job:
                                job.status = "succeeded"
                            self._sync_runtime_job_metrics(job, token)
                            session.commit()
                            LOGGER.info("runtime loop waiting for input")
                            return run
                        raise RuntimeError("Runtime deadlock: no enabled nodes and no wait condition matched.")

                    node = self._select_node(enabled_nodes)
                    actor_span_attributes = self._runtime_span_attributes(
                        run=run,
                        invocation=invocation,
                        artifact=artifact,
                        latest_snapshot=latest_snapshot,
                        node=node,
                    )
                    with start_span(
                        "runtime.actor",
                        **actor_span_attributes,
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
                                span_attributes=actor_span_attributes,
                            )
                        except Exception as exc:
                            record_span_exception(actor_span, exc)
                            raise
                    token = self._merge_observation(node=node, token=token, observation=observation)
                    token, entered_wait = self._apply_node_interaction(
                        session,
                        run=run,
                        token=token,
                        node=node,
                        observation=observation,
                        agent_run_id=self._agent_run_id_from_observation(observation),
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
                        agent_run_id=self._agent_run_id_from_observation(observation),
                    )
                    snapshots = self.repository.list_snapshots(session, run.id)
                    latest_snapshot = snapshots[-1] if snapshots else latest_snapshot
                    if entered_wait:
                        run.status = "waiting_input"
                        run.runtime_phase = self._runtime_phase_from_token(token)
                        invocation.status = "running"
                        if job:
                            job.status = "succeeded"
                        self._sync_runtime_job_metrics(job, token)
                        session.commit()
                        LOGGER.info("runtime loop entered wait checkpoint")
                        return run
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
                if job:
                    job.status = "succeeded"
                    job.last_error = ""
                self._sync_runtime_job_metrics(job, token)
                self._enqueue_run_evaluation_job(session, run=run, trigger="runtime.aborted")
                session.commit()
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
            if job:
                job.status = "succeeded"
                job.last_error = ""
            self._sync_runtime_job_metrics(job, token)
            self._enqueue_run_evaluation_job(session, run=run, trigger="runtime.succeeded")
            session.commit()
            LOGGER.info("runtime loop succeeded", extra={"final_output_length": len(run.final_output or "")})
            return run
        except Exception as exc:
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
                session.commit()
                LOGGER.exception("runtime turn processing failed; returned to waiting input", extra={"error": str(exc)})
                return run

            run.status = "failed"
            run.runtime_phase = "failed"
            run.exit_reason = str(exc)
            run.ended_at = now_utc()
            invocation.status = "failed"
            if job:
                job.status = "failed"
                job.last_error = str(exc)
            self._sync_runtime_job_metrics(job, token)
            failure_trace = self._append_run_trace(
                session,
                run=run,
                phase="failed",
                event_type="runtime.failed",
                payload=self._runtime_exception_trace_payload(exc, recoverable=False),
            )
            session.flush()
            self._append_runtime_failure_run_event(
                session,
                run=run,
                error=str(exc),
                run_trace=failure_trace,
            )
            self._close_terminal_session(session, run)
            self._enqueue_run_evaluation_job(session, run=run, trigger="runtime.failed")
            session.commit()
            LOGGER.exception("runtime loop failed", extra={"error": str(exc)})
            return run

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

    def list_runs(self, session: Session, *, status: str | None = None, skill_id: str | None = None) -> list[RunResponse]:
        return [
            self._build_run_response(session, item)
            for item in self.repository.list_runs(session, status=status, skill_id=skill_id)
        ]

    def get_run(self, session: Session, run_id: str) -> RunResponse:
        run = self.repository.get_run(session, run_id)
        if not run:
            raise SkillNotFoundError("未找到 Run。", details={"run_id": run_id})
        return self._build_run_response(session, run)

    def cancel_run(self, session: Session, run_id: str, *, reason: str = "cancelled by user") -> RunResponse:
        run = self.repository.get_run(session, run_id)
        if not run:
            raise SkillNotFoundError("未找到 Run。", details={"run_id": run_id})
        if run.status == "cancelled":
            return self._build_run_response(session, run)
        if run.status in {"succeeded", "failed", "aborted"}:
            raise SkillValidationError("Run 已结束，不能取消。", details={"run_id": run_id, "status": run.status})

        invocation = self.repository.get_invocation(session, run.invocation_id)
        run.status = "cancelled"
        run.runtime_phase = "cancelled"
        run.exit_reason = reason
        run.ended_at = now_utc()
        if invocation:
            invocation.status = "cancelled"
        self._close_terminal_session(session, run)
        self._append_cancel_snapshot(session, run=run, reason=reason)
        self._append_run_trace(
            session,
            run=run,
            phase="cancelled",
            event_type="runtime.cancelled",
            payload={"reason": reason},
        )
        job = self.job_repository.get_runtime_job_by_dedupe_key(session, f"job:runtime:{run.id}")
        if job and job.status not in {"succeeded", "failed", "cancelled"}:
            job.status = "cancelled"
            job.last_error = reason
        self._enqueue_run_evaluation_job(session, run=run, trigger="runtime.cancelled")
        session.commit()
        return self._build_run_response(session, run)

    def list_snapshots(self, session: Session, run_id: str) -> list[SessionTokenSnapshotResponse]:
        return [self._build_snapshot_response(item) for item in self.repository.list_snapshots(session, run_id)]

    def list_run_traces(
        self,
        session: Session,
        run_id: str,
        *,
        event_type: str | None = None,
    ) -> list[RunTraceResponse]:
        return [
            self._build_run_trace_response(item)
            for item in self.repository.list_run_traces(session, run_id, event_type=event_type)
        ]

    def get_terminal_session(self, session: Session, run_id: str) -> TerminalSessionDetailResponse:
        terminal_session = self.repository.get_terminal_session_for_run(session, run_id)
        if not terminal_session:
            raise SkillNotFoundError("未找到 Terminal Session。", details={"run_id": run_id})
        events = self.repository.list_run_events(session, run_id)
        return TerminalSessionDetailResponse(
            terminal_session=self._build_terminal_session_response(terminal_session),
            transcript_summary=TerminalTranscriptSummary(
                latest_seq=events[-1].seq_no if events else 0,
                event_count=len(events),
            ),
        )

    def list_run_events(
        self,
        session: Session,
        run_id: str,
        *,
        from_seq: int | None = None,
        to_seq: int | None = None,
    ) -> list[RunEventResponse]:
        if not self.repository.get_run(session, run_id):
            raise SkillNotFoundError("未找到 Run。", details={"run_id": run_id})
        return [
            self._build_run_event_response(session, item)
            for item in self.repository.list_run_events(session, run_id, from_seq=from_seq, to_seq=to_seq)
        ]

    def get_run_event(self, session: Session, run_id: str, event_id: str) -> RunEventResponse:
        event = self.repository.get_run_event(session, event_id)
        if not event or event.run_id != run_id:
            raise SkillNotFoundError("未找到 RunEvent。", details={"run_id": run_id, "event_id": event_id})
        return self._build_run_event_response(session, event)

    def list_run_event_parts(self, session: Session, run_id: str) -> list[RunEventPartResponse]:
        if not self.repository.get_run(session, run_id):
            raise SkillNotFoundError("未找到 Run。", details={"run_id": run_id})
        return [
            self._build_run_event_part_response(part)
            for part in self.repository.list_run_event_parts_for_run(session, run_id)
        ]

    def get_run_event_part(
        self,
        session: Session,
        run_id: str,
        event_id: str,
        part_id: str,
    ) -> RunEventPartResponse:
        event = self.repository.get_run_event(session, event_id)
        if not event or event.run_id != run_id:
            raise SkillNotFoundError("未找到 RunEvent。", details={"run_id": run_id, "event_id": event_id})
        part = self.repository.get_run_event_part_by_public_id(session, run_event_id=event_id, part_id=part_id)
        if not part or part.run_id != run_id:
            raise SkillNotFoundError(
                "未找到 RunEventPart。",
                details={"run_id": run_id, "event_id": event_id, "part_id": part_id},
            )
        return self._build_run_event_part_response(part)

    def append_run_event(
        self,
        session: Session,
        run_id: str,
        payload: AppendRunEventRequest,
        *,
        idempotency_key: str | None = None,
        process_after_append: bool = True,
    ) -> RunEventAppendResponse:
        run = self.repository.get_run(session, run_id)
        if not run:
            raise SkillNotFoundError("未找到 Run。", details={"run_id": run_id})

        external_event_id = payload.external_event_id or idempotency_key
        if external_event_id:
            existing = self.repository.get_run_event_by_external_id(
                session,
                run_id=run_id,
                external_event_id=external_event_id,
            )
            if existing:
                event_response = self._build_run_event_response(session, existing)
                return RunEventAppendResponse(
                    accepted=True,
                    event_id=existing.id,
                    seq_no=existing.seq_no,
                    event=event_response,
                )

        if run.status in {"succeeded", "failed", "cancelled", "aborted"}:
            raise SkillValidationError("Run 已结束，不能继续追加 RunEvent。", details={"run_id": run_id, "status": run.status})
        terminal_session = self.repository.get_terminal_session_for_run(session, run_id)
        if not terminal_session or terminal_session.status != "open":
            raise SkillValidationError("当前 Run 没有可用的 Terminal Session。", details={"run_id": run_id})

        event = self._append_run_event(
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
            self._ensure_runtime_job_pending(session, run)
        if run.status == "waiting_input" and process_after_append:
            session.commit()
            self.process_run(session, run.id)
            event = session.get(RunEvent, event.id) or event
        else:
            session.commit()
        event_response = self._build_run_event_response(session, event)
        return RunEventAppendResponse(
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
        run = self.repository.get_run(session, run_id)
        if not run:
            raise SkillNotFoundError("未找到 Run。", details={"run_id": run_id})
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
        bindings = self.repository.list_run_bindings(session, run_id)
        self._append_run_trace(
            session,
            run=run,
            phase="binding",
            event_type="binding.updated",
            payload={"bindings": [self._binding_payload(item) for item in bindings]},
        )
        session.commit()
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
        run_traces = self.list_run_traces(session, run_id)
        run_events = self.list_run_events(session, run_id)
        agent_runs = self._list_replay_agent_runs(session, run_id=run_id, run_traces=run_traces, run_events=run_events)
        agent_run_ids = [item.id for item in agent_runs]
        agent_events = self._list_replay_agent_events(session, agent_run_ids=agent_run_ids)
        agent_tool_calls = self._list_replay_agent_tool_calls(session, agent_run_ids=agent_run_ids)
        agent_model_calls = self._list_replay_agent_model_calls(session, agent_run_ids=agent_run_ids)
        agent_tool_authorizations = self._list_replay_agent_tool_authorizations(
            session,
            run_id=run_id,
            agent_run_ids=agent_run_ids,
        )
        run_evaluations = self._list_replay_run_evaluations(session, run_id=run_id)
        run_evaluations_by_id = {evaluation.id: evaluation for evaluation in run_evaluations}
        run_evaluation_findings = self._list_replay_run_evaluation_findings(session, run_id=run_id)
        governance_proposals = self._list_replay_governance_proposals(
            session,
            run_id=run_id,
            run_evaluation_ids=set(run_evaluations_by_id),
            run_evaluation_finding_ids={finding.id for finding in run_evaluation_findings},
        )
        governance_experiments = self._list_replay_governance_experiments(
            session,
            proposal_ids={proposal.id for proposal in governance_proposals},
        )
        governance_proposals_by_id = {proposal.id: proposal for proposal in governance_proposals}
        governance_experiments_by_proposal = self._group_governance_experiments_by_proposal(governance_experiments)
        timeline = [self._build_timeline_item(event) for event in run_traces]
        timeline.extend(self._build_terminal_timeline_item(event) for event in run_events)
        timeline.sort(key=lambda item: (self._timeline_occurred_at_sort_key(item.occurred_at), item.seq_no, item.event_type))
        return ReplayDetailResponse(
            run=self._build_run_response(session, run),
            provenance=self._build_replay_provenance(session, run=run, snapshots=snapshots),
            timeline=timeline,
            eg_node_path=self._build_eg_node_path(run_traces),
            snapshots=snapshots,
            run_traces=run_traces,
            run_events=run_events,
            bindings=self.list_run_bindings(session, run_id),
            agent_runs=[self._build_agent_run_response(item) for item in agent_runs],
            agent_events=[self._build_agent_event_response(item) for item in agent_events],
            agent_tool_calls=[self._build_agent_tool_call_response(item) for item in agent_tool_calls],
            tool_calls=[self._build_agent_tool_call_response(item) for item in agent_tool_calls],
            agent_model_calls=[self._build_agent_model_call_response(item) for item in agent_model_calls],
            model_calls=[self._build_agent_model_call_response(item) for item in agent_model_calls],
            agent_tool_authorizations=[
                self._build_agent_tool_authorization_response(item) for item in agent_tool_authorizations
            ],
            tool_authorizations=[self._build_agent_tool_authorization_response(item) for item in agent_tool_authorizations],
            run_evaluations=[
                self._build_run_evaluation_response(evaluation, findings=run_evaluation_findings)
                for evaluation in run_evaluations
            ],
            run_evaluation_findings=[
                self._build_run_evaluation_finding_response(
                    item,
                    evaluation=run_evaluations_by_id.get(item.evaluation_id),
                )
                for item in run_evaluation_findings
            ],
            governance_proposals=[
                self._build_governance_proposal_response(
                    session,
                    proposal,
                    experiments=governance_experiments_by_proposal.get(proposal.id, []),
                )
                for proposal in governance_proposals
            ],
            governance_experiments=[
                self._build_governance_experiment_response(
                    experiment,
                    proposal=governance_proposals_by_id.get(experiment.proposal_id),
                )
                for experiment in governance_experiments
            ],
        )

    @staticmethod
    def _timeline_occurred_at_sort_key(value: Any) -> float:
        if hasattr(value, "tzinfo") and hasattr(value, "timestamp"):
            normalized = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
            return float(normalized.astimezone(timezone.utc).timestamp())
        return 0.0

    def _build_replay_provenance(
        self,
        session: Session,
        *,
        run: Run,
        snapshots: list[SessionTokenSnapshotResponse],
    ) -> ReplayProvenanceResponse:
        artifact = self.repository.get_artifact(session, run.compile_artifact_id)
        latest_snapshot = snapshots[-1] if snapshots else None
        return ReplayProvenanceResponse(
            invocation_id=run.invocation_id,
            run_id=run.id,
            pskill_definition_id=run.pskill_definition_id,
            pskill_version_id=run.pskill_version_id,
            compile_artifact_id=run.compile_artifact_id,
            compile_request_id=artifact.compile_request_id if artifact else "",
            latest_session_token_snapshot_id=latest_snapshot.id if latest_snapshot else "",
            latest_session_token_seq=latest_snapshot.seq_no if latest_snapshot else 0,
        )

    @staticmethod
    def _runtime_span_attributes(
        *,
        run: Run,
        invocation: SkillInvocation,
        artifact: Any,
        latest_snapshot: SessionTokenSnapshot | None,
        node: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        attributes: dict[str, Any] = {
            "run_id": run.id,
            "invocation_id": invocation.id,
            "skill_id": run.pskill_definition_id,
            "pskill_definition_id": run.pskill_definition_id,
            "pskill_version_id": run.pskill_version_id,
            "skill_version_id": run.pskill_version_id,
            "compile_artifact_id": run.compile_artifact_id,
            "compile_request_id": getattr(artifact, "compile_request_id", ""),
            "session_token_id": latest_snapshot.id if latest_snapshot else "",
            "session_token_seq": latest_snapshot.seq_no if latest_snapshot else 0,
        }
        if node is not None:
            attributes.update(
                {
                    "node_id": node.get("id"),
                    "node_kind": node.get("kind"),
                }
            )
        return attributes

    def build_replay_for_trace(self, session: Session, trace_id: str) -> ReplayTraceLookupResponse:
        trace = self.repository.get_run_trace(session, trace_id)
        if not trace:
            trace = self.repository.get_latest_run_trace_by_otel_trace_id(session, trace_id)
        if not trace:
            raise SkillNotFoundError("未找到 RunTrace。", details={"trace_id": trace_id})
        trace_response = self._build_run_trace_response(trace)
        replay = self.build_replay(session, trace.run_id)
        timeline_item = next(
            (
                item
                for item in replay.timeline
                if item.source_kind == "run_trace" and item.source_id == trace.id
            ),
            self._build_timeline_item(trace_response),
        )
        return ReplayTraceLookupResponse(
            trace=trace_response,
            run=replay.run,
            timeline_item=timeline_item,
            replay=replay,
        )

    def _build_eg_node_path(self, run_traces: list[RunTraceResponse]) -> list[ReplayEgNodePathItem]:
        path: list[ReplayEgNodePathItem] = []
        for trace in run_traces:
            node_id = self._eg_node_id_from_trace(trace)
            if not node_id:
                continue
            timeline_item = self._build_timeline_item(trace)
            path.append(
                ReplayEgNodePathItem(
                    seq_no=trace.seq_no,
                    trace_id=trace.id,
                    node_id=node_id,
                    node_kind=self._eg_node_kind_from_trace(trace),
                    phase=trace.phase,
                    event_type=trace.event_type,
                    title=timeline_item.title,
                    summary=timeline_item.summary,
                    checkpoint_id=self._eg_checkpoint_id_from_trace(trace),
                    agent_run_id=trace.agent_run_id,
                    occurred_at=trace.occurred_at,
                )
            )
        return path

    @staticmethod
    def _eg_node_id_from_trace(trace: RunTraceResponse) -> str:
        if trace.event_type in {"runtime.evaluation.queued"}:
            return ""
        payload = trace.payload if isinstance(trace.payload, dict) else {}
        node_id = payload.get("node_id")
        if isinstance(node_id, str) and node_id:
            return node_id
        wait = payload.get("wait") if isinstance(payload.get("wait"), dict) else {}
        entered_by_node = wait.get("entered_by_node") if isinstance(wait, dict) else None
        if isinstance(entered_by_node, str) and entered_by_node:
            return entered_by_node
        if (
            trace.phase
            and trace.phase not in {"binding", "fork", "failed", "cancelled"}
            and trace.event_type.startswith(("runtime.", "gateway."))
        ):
            return trace.phase
        return ""

    @staticmethod
    def _eg_node_kind_from_trace(trace: RunTraceResponse) -> str:
        payload = trace.payload if isinstance(trace.payload, dict) else {}
        node_kind = payload.get("node_kind")
        return str(node_kind or "")

    @staticmethod
    def _eg_checkpoint_id_from_trace(trace: RunTraceResponse) -> str:
        payload = trace.payload if isinstance(trace.payload, dict) else {}
        checkpoint_id = payload.get("checkpoint_id")
        if isinstance(checkpoint_id, str) and checkpoint_id:
            return checkpoint_id
        wait = payload.get("wait") if isinstance(payload.get("wait"), dict) else {}
        wait_checkpoint_id = wait.get("checkpoint_id") if isinstance(wait, dict) else None
        return str(wait_checkpoint_id or "")

    def _list_replay_agent_runs(
        self,
        session: Session,
        *,
        run_id: str,
        run_traces: list[RunTraceResponse],
        run_events: list[RunEventResponse],
    ) -> list[AgentRun]:
        agent_run_ids = {
            item.agent_run_id
            for item in [*run_traces, *run_events]
            if item.agent_run_id
        }
        direct_agent_runs = list(
            session.scalars(
                select(AgentRun)
                .where(AgentRun.run_id == run_id)
                .order_by(AgentRun.created_at.asc(), AgentRun.id.asc())
            ).all()
        )
        agent_run_ids.update(item.id for item in direct_agent_runs)
        agent_run_ids.update(
            item
            for item in session.scalars(
                select(AgentToolAuthorization.agent_run_id).where(AgentToolAuthorization.run_id == run_id)
            ).all()
            if item
        )
        agent_run_ids.update(
            item
            for item in session.scalars(select(RunEvaluation.agent_run_id).where(RunEvaluation.run_id == run_id)).all()
            if item
        )
        if not agent_run_ids:
            return []
        return list(
            session.scalars(
                select(AgentRun)
                .where(AgentRun.id.in_(sorted(agent_run_ids)))
                .order_by(AgentRun.created_at.asc(), AgentRun.id.asc())
            ).all()
        )

    @staticmethod
    def _list_replay_agent_events(session: Session, *, agent_run_ids: list[str]) -> list[AgentEvent]:
        if not agent_run_ids:
            return []
        return list(
            session.scalars(
                select(AgentEvent)
                .where(AgentEvent.agent_run_id.in_(agent_run_ids))
                .order_by(AgentEvent.occurred_at.asc(), AgentEvent.agent_run_id.asc(), AgentEvent.seq_no.asc())
            ).all()
        )

    @staticmethod
    def _list_replay_agent_tool_calls(session: Session, *, agent_run_ids: list[str]) -> list[AgentToolCall]:
        if not agent_run_ids:
            return []
        return list(
            session.scalars(
                select(AgentToolCall)
                .where(AgentToolCall.agent_run_id.in_(agent_run_ids))
                .order_by(AgentToolCall.created_at.asc(), AgentToolCall.agent_run_id.asc(), AgentToolCall.id.asc())
            ).all()
        )

    @staticmethod
    def _list_replay_agent_model_calls(session: Session, *, agent_run_ids: list[str]) -> list[AgentModelCall]:
        if not agent_run_ids:
            return []
        return list(
            session.scalars(
                select(AgentModelCall)
                .where(AgentModelCall.agent_run_id.in_(agent_run_ids))
                .order_by(AgentModelCall.created_at.asc(), AgentModelCall.agent_run_id.asc(), AgentModelCall.id.asc())
            ).all()
        )

    @staticmethod
    def _list_replay_agent_tool_authorizations(
        session: Session,
        *,
        run_id: str,
        agent_run_ids: list[str],
    ) -> list[AgentToolAuthorization]:
        by_id: dict[str, AgentToolAuthorization] = {
            item.id: item
            for item in session.scalars(
                select(AgentToolAuthorization).where(AgentToolAuthorization.run_id == run_id)
            ).all()
        }
        if agent_run_ids:
            for item in session.scalars(
                select(AgentToolAuthorization).where(AgentToolAuthorization.agent_run_id.in_(agent_run_ids))
            ).all():
                by_id[item.id] = item
        return sorted(by_id.values(), key=lambda item: (item.created_at, item.id))

    @staticmethod
    def _list_replay_run_evaluations(session: Session, *, run_id: str) -> list[RunEvaluation]:
        return list(
            session.scalars(
                select(RunEvaluation)
                .where(RunEvaluation.run_id == run_id)
                .order_by(RunEvaluation.created_at.asc(), RunEvaluation.id.asc())
            ).all()
        )

    @staticmethod
    def _list_replay_run_evaluation_findings(session: Session, *, run_id: str) -> list[RunEvaluationFinding]:
        return list(
            session.scalars(
                select(RunEvaluationFinding)
                .join(RunEvaluation, RunEvaluation.id == RunEvaluationFinding.evaluation_id)
                .where(RunEvaluation.run_id == run_id)
                .order_by(RunEvaluationFinding.created_at.asc(), RunEvaluationFinding.id.asc())
            ).all()
        )

    @staticmethod
    def _list_replay_governance_proposals(
        session: Session,
        *,
        run_id: str,
        run_evaluation_ids: set[str],
        run_evaluation_finding_ids: set[str],
    ) -> list[PsopImprovementProposal]:
        proposals = list(
            session.scalars(
                select(PsopImprovementProposal)
                .order_by(PsopImprovementProposal.created_at.asc(), PsopImprovementProposal.id.asc())
            ).all()
        )
        return [
            proposal
            for proposal in proposals
            if RuntimeService._governance_proposal_references_replay_run(
                proposal,
                run_id=run_id,
                run_evaluation_ids=run_evaluation_ids,
                run_evaluation_finding_ids=run_evaluation_finding_ids,
            )
        ]

    @staticmethod
    def _governance_proposal_references_replay_run(
        proposal: PsopImprovementProposal,
        *,
        run_id: str,
        run_evaluation_ids: set[str],
        run_evaluation_finding_ids: set[str],
    ) -> bool:
        if proposal.source_run_id == run_id:
            return True
        if proposal.source_evaluation_id and proposal.source_evaluation_id in run_evaluation_ids:
            return True
        source_finding_ids = {str(item) for item in list(proposal.source_finding_ids or []) if item}
        if source_finding_ids.intersection(run_evaluation_finding_ids):
            return True
        return RuntimeService._governance_evidence_refs_replay_run(
            list(proposal.evidence_refs or []),
            run_id=run_id,
            run_evaluation_ids=run_evaluation_ids,
            run_evaluation_finding_ids=run_evaluation_finding_ids,
        )

    @staticmethod
    def _governance_evidence_refs_replay_run(
        evidence_refs: list[Any],
        *,
        run_id: str,
        run_evaluation_ids: set[str],
        run_evaluation_finding_ids: set[str],
    ) -> bool:
        for ref in evidence_refs:
            if not isinstance(ref, dict):
                continue
            ref_id = str(ref.get("id") or ref.get("source_id") or "").strip()
            ref_run_id = str(ref.get("run_id") or ref.get("source_run_id") or "").strip()
            ref_evaluation_id = str(ref.get("evaluation_id") or ref.get("run_evaluation_id") or "").strip()
            ref_finding_id = str(ref.get("finding_id") or ref.get("run_evaluation_finding_id") or "").strip()
            kind = str(ref.get("kind") or ref.get("source_kind") or "").strip()
            if ref_run_id == run_id:
                return True
            if ref_evaluation_id in run_evaluation_ids or ref_finding_id in run_evaluation_finding_ids:
                return True
            if kind in {"run", "run_replay"} and ref_id == run_id:
                return True
            if kind == "run_evaluation" and ref_id in run_evaluation_ids:
                return True
            if kind == "run_evaluation_finding" and ref_id in run_evaluation_finding_ids:
                return True
        return False

    @staticmethod
    def _list_replay_governance_experiments(
        session: Session,
        *,
        proposal_ids: set[str],
    ) -> list[PsopImprovementExperiment]:
        if not proposal_ids:
            return []
        return list(
            session.scalars(
                select(PsopImprovementExperiment)
                .where(PsopImprovementExperiment.proposal_id.in_(proposal_ids))
                .order_by(PsopImprovementExperiment.created_at.asc(), PsopImprovementExperiment.id.asc())
            ).all()
        )

    @staticmethod
    def _group_governance_experiments_by_proposal(
        experiments: list[PsopImprovementExperiment],
    ) -> dict[str, list[PsopImprovementExperiment]]:
        grouped: dict[str, list[PsopImprovementExperiment]] = {}
        for experiment in experiments:
            grouped.setdefault(experiment.proposal_id, []).append(experiment)
        return grouped

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
        self._append_run_trace(
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
        agent_run_id: str | None = None,
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
        self._append_run_trace(
            session,
            run=run,
            phase=str(node.get("id")),
            event_type=self._event_type_for_node(node),
            agent_run_id=agent_run_id,
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
                self._append_run_event(
                    session,
                    run=run,
                    terminal_session=terminal_session,
                    direction="output",
                    event_kind="terminal.text.output.v1",
                    mime_type="text/plain",
                    payload_inline=str(observation["final_response"]),
                    binding_id=None,
                    source_ref={"kind": "runtime", "node_id": str(node.get("id"))},
                    agent_run_id=agent_run_id,
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
        agent_run_id: str | None = None,
    ) -> tuple[dict[str, Any], bool]:
        interaction = node.get("interaction") if isinstance(node.get("interaction"), dict) else {}
        next_token = token
        entered_wait = False

        terminal_message = self._terminal_message_from_observation(observation)
        should_output = bool(interaction.get("output_to_terminal")) or (
            self._node_is_evaluation(node) and bool(terminal_message)
        )
        if should_output and terminal_message:
            terminal_session = self.repository.get_terminal_session_for_run(session, run.id)
            if terminal_session and terminal_session.status == "open":
                self._append_run_event(
                    session,
                    run=run,
                    terminal_session=terminal_session,
                    direction="output",
                    event_kind=str(interaction.get("output_event_kind") or "terminal.text.output.v1"),
                    mime_type=str(interaction.get("output_mime_type") or "text/markdown"),
                    payload_inline=terminal_message,
                    binding_id=None,
                    source_ref={"kind": "runtime", "node_id": str(node.get("id"))},
                    agent_run_id=agent_run_id,
                )

        if self._node_is_evaluation(node):
            next_token.setdefault("control", {})["latest_evaluation"] = self._evaluation_summary(node, observation)
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
                    "occurred_at": now_utc().isoformat(),
                }
                if requested_next_phase:
                    next_token["phase"] = requested_next_phase
                else:
                    next_token["status"] = "aborted"
                    next_token["phase"] = "aborted"
            elif decision in {"proceed", "complete"}:
                requested_next_phase = str(observation.get("next_phase") or "").strip()
                if requested_next_phase:
                    next_token["phase"] = requested_next_phase

        if interaction.get("wait_after_output"):
            next_token = self._enter_wait_checkpoint(run=run, token=next_token, node=node, observation=observation)
            entered_wait = True
            self._append_run_trace(
                session,
                run=run,
                phase=str(node.get("id")),
                event_type="runtime.wait_checkpoint.entered",
                agent_run_id=agent_run_id,
                payload={"wait": next_token.get("control", {}).get("wait", {})},
            )

        return next_token, entered_wait

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
        return {
            "node_id": str(node.get("id") or ""),
            "decision": str(observation.get("decision") or ""),
            "reason": str(observation.get("reason") or ""),
            "next_phase": str(observation.get("next_phase") or ""),
            "terminal_message": str(observation.get("terminal_message") or ""),
        }

    @staticmethod
    def _enter_wait_checkpoint(
        *,
        run: Run,
        token: dict[str, Any],
        node: dict[str, Any],
        observation: dict[str, Any],
    ) -> dict[str, Any]:
        interaction = node.get("interaction") if isinstance(node.get("interaction"), dict) else {}
        checkpoint_id = str(interaction.get("checkpoint_id") or f"{node.get('id')}:wait")
        wait = {
            "status": "waiting",
            "checkpoint_id": checkpoint_id,
            "workflow_step_id": str(interaction.get("workflow_step_id") or node.get("id") or ""),
            "reason": str(interaction.get("wait_reason") or observation.get("wait_reason") or "等待用户提交现场证据。"),
            "expected_inputs": interaction.get("expected_inputs") if isinstance(interaction.get("expected_inputs"), list) else [],
            "resume_phase": str(interaction.get("resume_phase") or f"evaluate_{node.get('id')}"),
            "entered_by_node": str(node.get("id") or ""),
            "entered_at": now_utc().isoformat(),
            "run_id": run.id,
            "evidence": [],
        }
        token.setdefault("control", {})["wait"] = wait
        token.setdefault("control", {}).setdefault("wait_checkpoints", []).append(
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
        failure_trace = self._append_run_trace(
            session,
            run=run,
            phase="waiting",
            event_type="runtime.message_processing.failed",
            payload=self._runtime_exception_trace_payload(
                error,
                recoverable=True,
                latest_input_event_seq=latest_evidence.get("seq_no") if isinstance(latest_evidence, dict) else None,
            ),
        )
        session.flush()
        run_event = self._append_runtime_recoverable_failure_run_event(
            session,
            run=run,
            run_trace=failure_trace,
        )
        recovered_token = self._recover_terminal_turn_token(
            token,
            error=error_text,
            run_trace=failure_trace,
            run_event=run_event,
        )
        run.status = "waiting_input"
        run.runtime_phase = self._runtime_phase_from_token(recovered_token)
        run.exit_reason = ""
        run.ended_at = None
        invocation.status = "running"
        if job:
            job.status = "succeeded"
            job.last_error = ""
        self._append_recoverable_failure_snapshot(
            session,
            run=run,
            token=recovered_token,
            run_trace=failure_trace,
        )

    def _append_runtime_recoverable_failure_run_event(
        self,
        session: Session,
        *,
        run: Run,
        run_trace: RunTrace,
    ) -> RunEvent | None:
        terminal_session = self.repository.get_terminal_session_for_run(session, run.id)
        if not terminal_session or terminal_session.status != "open":
            return None

        try:
            return self._append_run_event(
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
                    "run_trace_id": run_trace.id,
                },
                external_event_id=f"runtime:{run.id}:message-processing-failed:{run_trace.seq_no}",
                run_trace_id=run_trace.id,
                agent_run_id=run_trace.agent_run_id,
            )
        except Exception:
            LOGGER.exception("failed to append recoverable runtime failure RunEvent", extra={"run_id": run.id})
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
        run_trace: RunTrace,
        run_event: RunEvent | None,
    ) -> dict[str, Any]:
        recovered = json.loads(json.dumps(token, ensure_ascii=False, default=str))
        control = recovered.setdefault("control", {})
        wait = control.get("wait")
        if isinstance(wait, dict):
            wait["status"] = "waiting"
            wait["reason"] = self._runtime_recoverable_failure_terminal_message()
            wait.setdefault("recoverable_errors", []).append(
                {
                    "run_trace_id": run_trace.id,
                    "trace_seq_no": run_trace.seq_no,
                    "error": error,
                    "occurred_at": now_utc().isoformat(),
                }
            )
        control["latest_processing_error"] = {
            "run_trace_id": run_trace.id,
            "trace_seq_no": run_trace.seq_no,
            "recoverable": True,
            "occurred_at": now_utc().isoformat(),
        }
        recovered["status"] = "waiting"
        recovered["phase"] = "waiting"
        if run_event:
            event_payload = self._run_event_token_payload(run_event)
            self._append_run_event_payload_to_token(recovered, event_payload=event_payload, seq_no=run_event.seq_no)
        return recovered

    def _append_recoverable_failure_snapshot(
        self,
        session: Session,
        *,
        run: Run,
        token: dict[str, Any],
        run_trace: RunTrace,
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
                    "run_trace_id": run_trace.id,
                },
                snapshot_hash=self._hash_payload(token),
            )
        )

    def _append_runtime_failure_run_event(
        self,
        session: Session,
        *,
        run: Run,
        error: str,
        run_trace: RunTrace,
    ) -> None:
        terminal_session = self.repository.get_terminal_session_for_run(session, run.id)
        if not terminal_session or terminal_session.status != "open":
            return

        try:
            self._append_run_event(
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
                    "run_trace_id": run_trace.id,
                },
                external_event_id=f"runtime:{run.id}:failed",
                run_trace_id=run_trace.id,
                agent_run_id=run_trace.agent_run_id,
            )
        except Exception:
            LOGGER.exception("failed to append runtime failure RunEvent", extra={"run_id": run.id})

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

    def _append_run_trace(
        self,
        session: Session,
        *,
        run: Run,
        phase: str,
        event_type: str,
        payload: dict[str, Any],
        agent_run_id: str | None = None,
    ) -> RunTrace:
        seq_no = run.latest_trace_seq + 1
        run.latest_trace_seq = seq_no
        trace_context = current_trace_context()
        event = RunTrace(
            run_id=run.id,
            agent_run_id=agent_run_id,
            seq_no=seq_no,
            phase=phase,
            event_type=event_type,
            trace_id=trace_context.get("trace_id", ""),
            span_id=trace_context.get("span_id") or f"{run.id[:8]}-{seq_no:04d}",
            parent_span_id="",
            payload=payload,
        )
        session.add(event)
        return event

    def _append_run_event(
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
        parts: list[RunEventPartInput] | None = None,
        source_ref: dict[str, Any] | None = None,
        external_event_id: str | None = None,
        run_trace_id: str | None = None,
        agent_run_id: str | None = None,
        occurred_at=None,
    ) -> RunEvent:
        normalized_direction = direction.strip().lower()
        if normalized_direction not in {"input", "output"}:
            raise SkillValidationError("RunEvent direction 只能是 input 或 output。", details={"direction": direction})
        if not event_kind:
            raise SkillValidationError("RunEvent 必须包含 event_kind。")
        if not mime_type:
            raise SkillValidationError("RunEvent 必须包含 mime_type。")
        if terminal_session.run_id != run.id:
            raise SkillValidationError("Terminal Session 与 Run 不匹配。")

        resolved_binding_id = binding_id or self._default_binding_id(
            self.repository.list_run_bindings(session, run.id),
            normalized_direction,
        )
        if resolved_binding_id:
            binding = self.repository.get_run_capability_binding(session, resolved_binding_id)
            if not binding or binding.run_id != run.id or binding.status != "active":
                raise SkillValidationError("RunEvent binding 无效。", details={"binding_id": resolved_binding_id})

        next_seq = run.latest_run_event_seq + 1
        run.latest_run_event_seq = next_seq
        event = RunEvent(
            terminal_session_id=terminal_session.id,
            run_id=run.id,
            run_trace_id=run_trace_id,
            agent_run_id=agent_run_id,
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
        for order_index, part in enumerate(self._normalize_run_event_parts(event, parts or []), start=1):
            if part.artifact_object_id and not self.repository.get_artifact_object(session, part.artifact_object_id):
                raise SkillValidationError(
                    "RunEventPart artifact_object_id 无效。",
                    details={"part_id": part.part_id, "artifact_object_id": part.artifact_object_id},
                )
            session.add(
                RunEventPart(
                    run_event_id=event.id,
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

    def _normalize_run_event_parts(
        self,
        event: RunEvent,
        parts: list[RunEventPartInput],
    ) -> list[RunEventPartInput]:
        if parts:
            normalized_parts: list[RunEventPartInput] = []
            part_counts: dict[str, int] = {}
            for index, part in enumerate(parts):
                normalized_parts.append(self._validate_run_event_part(part, index, part_counts))
            return normalized_parts
        if event.direction != "input":
            return []
        if event.artifact_object_id:
            return []
        text = self._terminal_input_text_from_payload(event.payload_inline)
        if text:
            return [
                RunEventPartInput(
                    part_id="text_1",
                    kind="text",
                    mime_type="text/plain",
                    text=text,
                )
            ]
        return []

    def _validate_run_event_part(
        self,
        part: RunEventPartInput,
        index: int,
        part_counts: dict[str, int] | None = None,
    ) -> RunEventPartInput:
        kind = (part.kind or "").strip().lower()
        if not kind:
            kind = self._part_kind_for_mime_type((part.mime_type or "").strip().lower())
        if kind not in {"text", "image", "video", "audio"}:
            raise SkillValidationError("RunEventPart kind 仅支持 text/image/video/audio。", details={"kind": part.kind})
        if kind == "text" and not str(part.text or "").strip():
            raise SkillValidationError("text part 必须包含 text。", details={"part_id": part.part_id})
        if kind != "text" and not part.artifact_object_id:
            raise SkillValidationError("多模态 part 必须绑定 artifact_object_id。", details={"part_id": part.part_id})
        mime_type = (part.mime_type or "").strip().lower()
        if kind != "text" and not mime_type.startswith(f"{kind}/"):
            raise SkillValidationError(
                "RunEventPart kind 与 mime_type 不匹配。",
                details={"part_id": part.part_id, "kind": kind, "mime_type": part.mime_type},
            )
        part_id = (part.part_id or "").strip()
        kind_index = index + 1
        if part_counts is not None:
            part_counts[kind] = part_counts.get(kind, 0) + 1
            kind_index = part_counts[kind]
        if not part_id:
            part_id = f"{kind}_{kind_index}"
        return RunEventPartInput(
            part_id=part_id,
            kind=kind,
            mime_type=part.mime_type or ("text/plain" if kind == "text" else "application/octet-stream"),
            text=part.text,
            artifact_object_id=part.artifact_object_id,
            size_bytes=part.size_bytes,
            checksum=part.checksum,
            metadata=part.metadata,
        )

    def _sync_run_events(self, session: Session, *, run: Run, token: dict[str, Any]) -> dict[str, Any]:
        cursor = int(_get_path(token, "metadata.run_event_cursor") or _get_path(token, "metadata.terminal_cursor") or 0)
        next_token = json.loads(json.dumps(token, ensure_ascii=False, default=str))
        self._ensure_run_event_projection(next_token)
        events = self.repository.list_run_events(session, run.id, from_seq=cursor + 1)
        if not events:
            return next_token

        input_envelope = next_token.setdefault("input_envelope", {})
        for event in events:
            event_payload = self._run_event_token_payload_with_parts(session, event)
            self._append_run_event_payload_to_token(next_token, event_payload=event_payload, seq_no=event.seq_no)
            if event.direction == "input":
                input_text = self._terminal_input_text(event, event_payload.get("parts") if isinstance(event_payload.get("parts"), list) else None)
                if input_text:
                    input_envelope["user_input"] = input_text
                    input_envelope.setdefault("text", input_text)
                wait = next_token.setdefault("control", {}).get("wait")
                if isinstance(wait, dict) and next_token.get("status") == "waiting":
                    evidence = {
                        **event_payload,
                        "text": input_text,
                        "input_bundle": event_payload.get("input_bundle")
                        or self._terminal_input_bundle_from_parts(event, event_payload.get("parts") or []),
                    }
                    wait.setdefault("evidence", []).append(evidence)
                    wait["latest_event_seq"] = event.seq_no
                    wait["status"] = "received"
                    next_token.setdefault("control", {})["latest_evidence"] = evidence
                    next_token["status"] = "running"
                    next_token["phase"] = str(wait.get("resume_phase") or next_token.get("phase") or "start")
        return next_token

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

    def _ensure_runtime_job_pending(self, session: Session, run: Run) -> RuntimeJob:
        job = self.job_repository.get_runtime_job_by_dedupe_key(session, f"job:runtime:{run.id}")
        if job:
            job.job_type = RUNTIME_STEP_JOB_TYPE
            job.status = "pending"
            job.available_at = now_utc() + timedelta(seconds=2)
            job.last_error = ""
            return job
        job = RuntimeJob(
            job_type=RUNTIME_STEP_JOB_TYPE,
            status="pending",
            payload={"run_id": run.id},
            run_id=run.id,
            dedupe_key=f"job:runtime:{run.id}",
            available_at=now_utc() + timedelta(seconds=2),
            max_attempts=self.settings.runtime_job_max_attempts,
        )
        session.add(job)
        return job

    def _enqueue_run_evaluation_job(self, session: Session, *, run: Run, trigger: str) -> RuntimeJob | None:
        if run.status not in {"succeeded", "failed", "aborted", "cancelled"}:
            return None
        dedupe_key = f"job:run-evaluation:{run.id}"
        existing = self.job_repository.get_runtime_job_by_dedupe_key(session, dedupe_key)
        if existing:
            return existing
        job = RuntimeJob(
            job_type=RUN_EVALUATION_JOB_TYPE,
            status="pending",
            payload={
                "operation": "run_evaluation",
                "run_id": run.id,
                "queued_by": "runtime",
                "trigger": trigger,
                "trigger_status": run.status,
            },
            run_id=run.id,
            dedupe_key=dedupe_key,
        )
        session.add(job)
        session.flush()
        self._append_run_trace(
            session,
            run=run,
            phase=run.runtime_phase,
            event_type="runtime.evaluation.queued",
            payload={
                "job_id": job.id,
                "job_type": RUN_EVALUATION_JOB_TYPE,
                "dedupe_key": dedupe_key,
                "run_status": run.status,
                "trigger": trigger,
            },
        )
        return job

    @staticmethod
    def _extract_initial_terminal_input(input_envelope: dict[str, Any]) -> Any | None:
        if "user_input" in input_envelope:
            return input_envelope["user_input"]
        if "text" in input_envelope:
            return input_envelope["text"]
        if input_envelope:
            return input_envelope
        return None

    @staticmethod
    def _run_event_token_payload(event: RunEvent) -> dict[str, Any]:
        return {
            "id": event.id,
            "seq_no": event.seq_no,
            "direction": event.direction,
            "event_kind": event.event_kind,
            "mime_type": event.mime_type,
            "payload_inline": event.payload_inline,
            "artifact_object_id": event.artifact_object_id,
            "binding_id": event.run_capability_binding_id,
            "agent_run_id": event.agent_run_id,
            "source_ref": event.source_ref,
            "occurred_at": event.occurred_at.isoformat(),
        }

    def _run_event_token_payload_with_parts(self, session: Session, event: RunEvent) -> dict[str, Any]:
        payload = self._run_event_token_payload(event)
        parts = [self._run_event_part_token_payload(part) for part in self.repository.list_run_event_parts(session, event.id)]
        if parts:
            payload["parts"] = parts
            payload["input_bundle"] = self._terminal_input_bundle_from_parts(event, parts)
        return payload

    @staticmethod
    def _run_event_part_token_payload(part: RunEventPart) -> dict[str, Any]:
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
    def _terminal_input_bundle_from_parts(cls, event: RunEvent, parts: list[dict[str, Any]]) -> dict[str, Any]:
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
    def _terminal_input_text(cls, event: RunEvent, parts: list[dict[str, Any]] | None = None) -> str:
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
            "metadata": {
                "artifact_version": artifact_payload.get("artifact_version"),
                "run_event_cursor": 0,
                "terminal_cursor": 0,
            },
            "run_events": [],
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
        span_attributes: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        kind = node.get("kind")
        actor_name = _actor_name(node.get("actor"))
        if kind == "start" or actor_name == "runtime.start":
            return {"started": True, "summary": "Runtime 已初始化 Session Token。"}
        if kind == "input" or actor_name == "runtime.input":
            user_input = self._extract_user_input(token.get("input_envelope", {}), artifact_payload)
            return {"user_input": user_input, "summary": "已接收用户输入。"}
        if kind == "llm" or actor_name == "agent.llm":
            system_prompt, user_prompt, prompt_metadata = self._render_llm_prompts(
                session,
                node,
                token,
                artifact_payload,
            )
            attachments = self._resolve_llm_attachments(session, token)
            route_key = (
                MULTIMODAL_ROUTE_KEY
                if attachments
                else artifact_payload.get("capability_summary", {}).get("llm_route_key", TEXT_ROUTE_KEY)
            )
            input_parts = self._llm_attachment_summary(attachments)
            agent_run_id = self._create_runtime_agent_run(
                session,
                run=run,
                node=node,
                token=token,
                input_parts=input_parts,
                agent_prompt=prompt_metadata,
            )
            skill_context = self._hydrate_runtime_agent_skill_context(
                session,
                agent_run_id=agent_run_id,
            )
            agent_spec = self._runtime_agent_spec(session, agent_run_id=agent_run_id)
            memory_context = self._retrieve_runtime_agent_memory_context(
                session,
                agent_run_id=agent_run_id,
                spec=agent_spec,
            )
            plan_payload = self._create_runtime_agent_plan(
                session,
                agent_run_id=agent_run_id,
                spec=agent_spec,
                skill_context=skill_context,
                memory_context=memory_context,
            )
            system_prompt, user_prompt = self._append_runtime_agent_skill_context_to_prompts(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                skill_context=skill_context,
            )
            system_prompt, user_prompt = self._append_runtime_agent_memory_and_plan_to_prompts(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                memory_context=memory_context,
                plan_payload=plan_payload,
            )
            input_summary = self._llm_prompt_summary(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                route_key=route_key,
                agent_prompt=prompt_metadata,
            )
            self._update_runtime_agent_run_prompt_context(
                session,
                agent_run_id=agent_run_id,
                input_summary=input_summary,
                input_parts=input_parts,
                agent_prompt=prompt_metadata,
                skill_context=skill_context,
                memory_context=memory_context,
                plan_payload=plan_payload,
            )
            try:
                with start_span(
                    "gateway.inference",
                    **{
                        **(span_attributes or {}),
                        "route_key": route_key,
                        "node_id": node.get("id"),
                    },
                ):
                    if attachments:
                        if not hasattr(self.inference_gateway, "complete_multimodal"):
                            raise RuntimeError("当前 LLM Inference Gateway 不支持多模态输入。")
                        llm_completion = self.inference_gateway.complete_multimodal(
                            system_prompt=system_prompt,
                            user_prompt=user_prompt,
                            attachments=attachments,
                            route_key=route_key,
                        )
                    else:
                        llm_completion = self.inference_gateway.complete(
                            system_prompt=system_prompt,
                            user_prompt=user_prompt,
                            route_key=route_key,
                        )
            except Exception as exc:
                error_payload = self._runtime_exception_trace_payload(exc)
                failed_model_call = self._record_runtime_agent_model_call_failed(
                    session,
                    agent_run_id=agent_run_id,
                    node=node,
                    route_key=route_key,
                    input_summary=input_summary,
                    input_parts=input_parts,
                    agent_prompt=prompt_metadata,
                    skill_context=skill_context,
                    memory_context=memory_context,
                    plan_payload=plan_payload,
                    error_payload=error_payload,
                )
                self._mark_runtime_agent_failed(
                    session,
                    agent_run_id=agent_run_id,
                    node=node,
                    error_message=str(exc),
                    model_call_id=failed_model_call.id,
                )
                self._append_run_trace(
                    session,
                    run=run,
                    phase=str(node.get("id") or ""),
                    event_type="gateway.inference.failed",
                    agent_run_id=agent_run_id,
                    payload={
                        "node_id": str(node.get("id") or ""),
                        "node_kind": str(node.get("kind") or ""),
                        "route_key": route_key,
                        "model_call_id": failed_model_call.id,
                        **error_payload,
                    },
                )
                raise
            budgets = token.setdefault("budgets", {})
            budgets["llm_calls"] = int(budgets.get("llm_calls", 0)) + 1
            self._accumulate_llm_usage(budgets, llm_completion.usage)
            observation = {
                "schema": "RuntimeAgentObservation",
                "content": llm_completion.content,
                "provider": llm_completion.provider,
                "model": llm_completion.model,
                "input_summary": input_summary,
                "input_parts": input_parts,
                "output": {"content": llm_completion.content},
                "usage": llm_completion.usage,
                "summary": "LLM 节点执行完成。",
                "_agent_run_id": agent_run_id,
            }
            if llm_completion.request:
                observation["_trace_request"] = llm_completion.request
            if self._node_is_evaluation(node):
                observation.update(self._parse_evaluation_observation(llm_completion.content, node=node))
            self._record_runtime_agent_model_call(
                session,
                agent_run_id=agent_run_id,
                node=node,
                route_key=route_key,
                observation=observation,
                agent_prompt=prompt_metadata,
                skill_context=skill_context,
                memory_context=memory_context,
                plan_payload=plan_payload,
            )
            self._mark_runtime_agent_succeeded(
                session,
                agent_run_id=agent_run_id,
                node=node,
                output_payload=self._runtime_agent_observation_payload(node=node, observation=observation),
            )
            return observation
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

    def _create_runtime_agent_run(
        self,
        session: Session,
        *,
        run: Run,
        node: dict[str, Any],
        token: dict[str, Any],
        input_parts: list[dict[str, Any]],
        agent_prompt: dict[str, Any] | None,
    ) -> str:
        agent_run = self.agent_service.create_run(
            session,
            CreateAgentRunRequest(
                agent_key="pskill.runner",
                owner_type="runtime_run",
                owner_id=run.id,
                run_id=run.id,
                input_payload={
                    "schema": "RuntimeAgentObservation",
                    "run_id": run.id,
                    "compile_artifact_id": run.compile_artifact_id,
                    "node": self._runtime_agent_node_payload(node),
                    "runtime_context": self._runtime_agent_context_payload(run=run, token=token),
                    "input_parts": input_parts,
                    "agent_prompt": dict(agent_prompt or {}),
                    "skill_context": [],
                },
            ),
            commit=False,
        )
        agent_run_model = self.agent_service.get_run_model(session, agent_run.id)
        input_guardrail_result = self.input_guardrail.check(
            agent_key=agent_run_model.agent_key,
            input_payload=agent_run_model.input_payload,
        )
        self.agent_service.append_event(
            session,
            agent_run.id,
            AppendAgentEventRequest(
                event_type="agent.input_guardrail.checked",
                phase="guardrails",
                payload=input_guardrail_result.as_event_payload(),
            ),
            commit=False,
        )
        if not input_guardrail_result.passed:
            agent_run_model.status = "failed"
            agent_run_model.error_message = "input_guardrail_failed"
            agent_run_model.ended_at = now_utc()
            self.agent_service.append_event(
                session,
                agent_run.id,
                AppendAgentEventRequest(
                    event_type="agent.input_guardrail.failed",
                    phase="guardrails",
                    payload={"findings": [item.as_dict() for item in input_guardrail_result.findings]},
                ),
                commit=False,
            )
            raise SkillValidationError(
                "Runtime AgentRun 输入未通过输入护栏。",
                details={"agent_run_id": agent_run.id, "findings": input_guardrail_result.as_event_payload()},
            )
        agent_run_model.status = "running"
        agent_run_model.started_at = agent_run_model.started_at or now_utc()
        self.agent_service.append_event(
            session,
            agent_run.id,
            AppendAgentEventRequest(
                event_type="runtime.node.started",
                phase=str(node.get("id") or ""),
                payload={
                    "run_id": run.id,
                    "node_id": str(node.get("id") or ""),
                    "node_kind": str(node.get("kind") or ""),
                    "output_schema": "RuntimeAgentObservation",
                },
            ),
            commit=False,
        )
        return agent_run.id

    def _hydrate_runtime_agent_skill_context(
        self,
        session: Session,
        *,
        agent_run_id: str,
    ) -> list[dict[str, Any]]:
        skill_context = self.agent_service.skill_service.hydrate_agent_run_skill_context(
            session,
            agent_run_id=agent_run_id,
        )
        self.agent_service.append_event(
            session,
            agent_run_id,
            AppendAgentEventRequest(
                event_type="agent.skills.hydrated",
                phase="skills",
                payload={
                    "skill_context_count": len(skill_context),
                    "package_names": [str(item.get("package_name") or "") for item in skill_context],
                    "content_hashes": {
                        str(item.get("package_name") or ""): str(item.get("content_hash") or "")
                        for item in skill_context
                        if item.get("package_name")
                    },
                    "reference_counts": {
                        str(item.get("package_name") or ""): len(item.get("references") or [])
                        for item in skill_context
                        if item.get("package_name")
                    },
                },
            ),
            commit=False,
        )
        return skill_context

    def _runtime_agent_spec(self, session: Session, *, agent_run_id: str) -> dict[str, Any]:
        agent_run = self.agent_service.get_run_model(session, agent_run_id)
        version = self.agent_service.repository.get_version(session, agent_run.agent_version_id)
        if not version or not isinstance(version.spec_json, dict):
            return {}
        return dict(version.spec_json)

    def _retrieve_runtime_agent_memory_context(
        self,
        session: Session,
        *,
        agent_run_id: str,
        spec: dict[str, Any],
    ) -> list[dict[str, Any]]:
        agent_run = self.agent_service.get_run_model(session, agent_run_id)
        memory_context = self.memory_service.retrieve_context_for_agent(
            session,
            agent_key=agent_run.agent_key,
            limit=self._runtime_agent_memory_context_limit(spec),
        )
        self.agent_service.append_event(
            session,
            agent_run_id,
            AppendAgentEventRequest(
                event_type="agent.memory.retrieved",
                phase="memory",
                payload={
                    "memory_entry_ids": [str(item.get("id")) for item in memory_context],
                    "memory_entry_count": len(memory_context),
                    "status": "active",
                    "used_as_runtime_state": False,
                },
            ),
            commit=False,
        )
        return memory_context

    def _create_runtime_agent_plan(
        self,
        session: Session,
        *,
        agent_run_id: str,
        spec: dict[str, Any],
        skill_context: list[dict[str, Any]],
        memory_context: list[dict[str, Any]],
    ) -> dict[str, Any]:
        agent_run = self.agent_service.get_run_model(session, agent_run_id)
        active_skill_names = [
            str(item.get("package_name") or "")
            for item in skill_context
            if item.get("package_name")
        ]
        plan = self.planner.create_plan(
            agent_key=agent_run.agent_key,
            spec=spec,
            input_payload=agent_run.input_payload,
            active_skill_names=active_skill_names,
            memory_context=memory_context,
        )
        plan_payload = plan.as_dict()
        self.agent_service.append_event(
            session,
            agent_run_id,
            AppendAgentEventRequest(
                event_type="agent.plan.created",
                phase="planning",
                payload=plan_payload,
            ),
            commit=False,
        )
        return plan_payload

    @staticmethod
    def _runtime_agent_memory_context_limit(spec: dict[str, Any]) -> int:
        policy = spec.get("memory_policy")
        if not isinstance(policy, dict):
            return 5
        try:
            return max(1, min(20, int(policy.get("context_limit") or 5)))
        except (TypeError, ValueError):
            return 5

    @staticmethod
    def _append_runtime_agent_skill_context_to_prompts(
        *,
        system_prompt: str,
        user_prompt: str,
        skill_context: list[dict[str, Any]],
    ) -> tuple[str, str]:
        if not skill_context:
            return system_prompt, user_prompt
        context_json = json.dumps(skill_context, ensure_ascii=False, sort_keys=True)
        context_prompt = "\n".join(
            [
                "PSOP Runner Skill Package Context:",
                context_json,
                "以上 Skill package context 只能作为 pskill.runner 的过程规程和能力边界；正式运行状态只能来自 Session Token、RunEvent 和 RunTrace。",
            ]
        )
        return system_prompt, f"{user_prompt}\n\n{context_prompt}"

    @staticmethod
    def _append_runtime_agent_memory_and_plan_to_prompts(
        *,
        system_prompt: str,
        user_prompt: str,
        memory_context: list[dict[str, Any]],
        plan_payload: dict[str, Any],
    ) -> tuple[str, str]:
        prompt_blocks: list[str] = []
        if memory_context:
            memory_json = json.dumps(memory_context, ensure_ascii=False, sort_keys=True)
            prompt_blocks.append(
                "\n".join(
                    [
                        "PSOP Runner Memory Context:",
                        memory_json,
                        "以上 memory context 只能作为 pskill.runner 的非正式参考；正式运行状态只能来自 Session Token、RunEvent 和 RunTrace。",
                    ]
                )
            )
        if plan_payload:
            plan_json = json.dumps(plan_payload, ensure_ascii=False, sort_keys=True)
            prompt_blocks.append(
                "\n".join(
                    [
                        "PSOP Runner Execution Plan:",
                        plan_json,
                        "以上 plan 只描述本次 AgentRun 的过程安排，不是 Runtime 状态，也不能直接修改 Session Token。",
                    ]
                )
            )
        if not prompt_blocks:
            return system_prompt, user_prompt
        return system_prompt, f"{user_prompt}\n\n" + "\n\n".join(prompt_blocks)

    def _update_runtime_agent_run_prompt_context(
        self,
        session: Session,
        *,
        agent_run_id: str,
        input_summary: dict[str, Any],
        input_parts: list[dict[str, Any]],
        agent_prompt: dict[str, Any] | None,
        skill_context: list[dict[str, Any]],
        memory_context: list[dict[str, Any]],
        plan_payload: dict[str, Any],
    ) -> None:
        agent_run = self.agent_service.get_run_model(session, agent_run_id)
        payload = dict(agent_run.input_payload or {})
        payload["input_summary"] = input_summary
        payload["input_parts"] = input_parts
        payload["agent_prompt"] = dict(agent_prompt or {})
        payload["skill_context"] = skill_context
        payload["memory_context"] = memory_context
        payload["plan"] = plan_payload
        agent_run.input_payload = payload

    def _mark_runtime_agent_succeeded(
        self,
        session: Session,
        *,
        agent_run_id: str,
        node: dict[str, Any],
        output_payload: dict[str, Any],
    ) -> None:
        agent_run = self.agent_service.get_run_model(session, agent_run_id)
        agent_run.status = "succeeded"
        agent_run.output_payload = output_payload
        agent_run.error_message = ""
        agent_run.ended_at = now_utc()
        self.agent_service.append_event(
            session,
            agent_run_id,
            AppendAgentEventRequest(
                event_type="runtime.agent.observation.returned",
                phase=str(node.get("id") or ""),
                payload={
                    "node_id": str(node.get("id") or ""),
                    "output_schema": "RuntimeAgentObservation",
                    "decision": str(output_payload.get("observation", {}).get("decision") or ""),
                    "has_terminal_message": bool(output_payload.get("observation", {}).get("terminal_message")),
                },
            ),
            commit=False,
        )

    def _mark_runtime_agent_failed(
        self,
        session: Session,
        *,
        agent_run_id: str,
        node: dict[str, Any],
        error_message: str,
        model_call_id: str | None = None,
    ) -> None:
        agent_run = self.agent_service.get_run_model(session, agent_run_id)
        agent_run.status = "failed"
        agent_run.error_message = error_message
        agent_run.ended_at = now_utc()
        self.agent_service.append_event(
            session,
            agent_run_id,
            AppendAgentEventRequest(
                event_type="runtime.agent.failed",
                phase=str(node.get("id") or ""),
                payload={
                    "node_id": str(node.get("id") or ""),
                    "error_message": error_message,
                    "model_call_id": model_call_id or "",
                },
            ),
            commit=False,
        )

    def _record_runtime_agent_model_call_failed(
        self,
        session: Session,
        *,
        agent_run_id: str,
        node: dict[str, Any],
        route_key: str,
        input_summary: dict[str, Any],
        input_parts: list[dict[str, Any]],
        agent_prompt: dict[str, Any] | None,
        skill_context: list[dict[str, Any]],
        memory_context: list[dict[str, Any]],
        plan_payload: dict[str, Any],
        error_payload: dict[str, Any],
    ) -> AgentModelCallResponse:
        error_details = error_payload.get("error_details") if isinstance(error_payload.get("error_details"), dict) else {}
        return self.agent_service.record_model_call(
            session,
            agent_run_id=agent_run_id,
            provider="llm_inference_gateway",
            route_key=route_key,
            model_name=str(error_details.get("model") or ""),
            status="failed",
            request_payload={
                "node": self._runtime_agent_node_payload(node),
                "input_summary": input_summary,
                "input_parts": input_parts,
                "agent_prompt": dict(agent_prompt or {}),
                "skill_context": skill_context,
                "memory_context": memory_context,
                "plan": plan_payload,
            },
            response_payload={
                "schema": "RuntimeAgentObservation",
                "status": "failed",
                **error_payload,
            },
            usage_json={},
            error_message=str(error_payload.get("error") or ""),
            commit=False,
        )

    def _record_runtime_agent_model_call(
        self,
        session: Session,
        *,
        agent_run_id: str,
        node: dict[str, Any],
        route_key: str,
        observation: dict[str, Any],
        agent_prompt: dict[str, Any] | None,
        skill_context: list[dict[str, Any]],
        memory_context: list[dict[str, Any]],
        plan_payload: dict[str, Any],
    ) -> None:
        self.agent_service.record_model_call(
            session,
            agent_run_id=agent_run_id,
            provider="llm_inference_gateway",
            route_key=route_key,
            model_name=str(observation.get("model") or ""),
            status="succeeded",
            request_payload={
                "node": self._runtime_agent_node_payload(node),
                "input_summary": observation.get("input_summary") if isinstance(observation.get("input_summary"), dict) else {},
                "input_parts": observation.get("input_parts") if isinstance(observation.get("input_parts"), list) else [],
                "agent_prompt": dict(agent_prompt or {}),
                "skill_context": skill_context,
                "memory_context": memory_context,
                "plan": plan_payload,
            },
            response_payload=self._runtime_agent_observation_payload(node=node, observation=observation),
            usage_json=dict(observation.get("usage") or {}),
            commit=False,
        )
        self.agent_service.append_event(
            session,
            agent_run_id,
            AppendAgentEventRequest(
                event_type="runtime.agent.model_call.completed",
                phase=str(node.get("id") or ""),
                payload={"node_id": str(node.get("id") or ""), "route_key": route_key},
            ),
            commit=False,
        )

    @staticmethod
    def _runtime_agent_node_payload(node: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": str(node.get("id") or ""),
            "kind": str(node.get("kind") or ""),
            "actor": node.get("actor") if isinstance(node.get("actor"), dict) else {},
        }

    @staticmethod
    def _runtime_agent_context_payload(*, run: Run, token: dict[str, Any]) -> dict[str, Any]:
        events = RuntimeService._token_run_events(token)
        latest_event = events[-1] if isinstance(events, list) and events else {}
        control = token.get("control") if isinstance(token.get("control"), dict) else {}
        wait = control.get("wait") if isinstance(control, dict) and isinstance(control.get("wait"), dict) else {}
        return {
            "status": str(token.get("status") or ""),
            "phase": str(token.get("phase") or ""),
            "runtime_phase": run.runtime_phase,
            "latest_snapshot_seq": run.latest_snapshot_seq,
            "latest_run_event_seq": run.latest_run_event_seq,
            "latest_trace_seq": run.latest_trace_seq,
            "wait": wait,
            "latest_run_event": latest_event if isinstance(latest_event, dict) else {},
            "latest_terminal_event": latest_event if isinstance(latest_event, dict) else {},
        }

    def _runtime_agent_observation_payload(self, *, node: dict[str, Any], observation: dict[str, Any]) -> dict[str, Any]:
        return {
            "schema": "RuntimeAgentObservation",
            "node": self._runtime_agent_node_payload(node),
            "observation": self._observation_for_agent_output(observation),
        }

    @staticmethod
    def _agent_run_id_from_observation(observation: dict[str, Any]) -> str | None:
        value = observation.get("_agent_run_id")
        return str(value) if value else None

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
        trace_observation.pop("_agent_run_id", None)
        request = trace_observation.pop("_trace_request", None)
        if request:
            trace_observation["request"] = request
        return trace_observation

    @staticmethod
    def _observation_for_agent_output(observation: dict[str, Any]) -> dict[str, Any]:
        agent_observation = dict(observation)
        agent_observation.pop("_agent_run_id", None)
        agent_observation.pop("_trace_request", None)
        return agent_observation

    @staticmethod
    def _observation_for_token(observation: dict[str, Any]) -> dict[str, Any]:
        token_observation = dict(observation)
        token_observation.pop("_agent_run_id", None)
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
        prompt_pack = self.agent_prompt_service.resolve_prompt_pack(
            session,
            usage_key="pskill.run.node",
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
        events = RuntimeService._token_run_events(token)
        if isinstance(events, list):
            for event in reversed(events):
                if isinstance(event, dict) and event.get("direction") == "input" and isinstance(event.get("parts"), list):
                    return [part for part in event["parts"] if isinstance(part, dict)]
        return []

    @staticmethod
    def _token_run_events(token: dict[str, Any]) -> list[Any]:
        run_events = token.get("run_events")
        if isinstance(run_events, list):
            return run_events
        terminal = token.get("terminal") if isinstance(token.get("terminal"), dict) else {}
        terminal_events = terminal.get("events") if isinstance(terminal, dict) else []
        return terminal_events if isinstance(terminal_events, list) else []

    @staticmethod
    def _ensure_run_event_projection(token: dict[str, Any]) -> tuple[list[Any], list[Any], dict[str, Any]]:
        terminal = token.setdefault("terminal", {})
        terminal_events = terminal.get("events")
        if not isinstance(terminal_events, list):
            terminal_events = []
            terminal["events"] = terminal_events

        run_events = token.get("run_events")
        if not isinstance(run_events, list):
            run_events = list(terminal_events)
            token["run_events"] = run_events
        elif terminal_events and not run_events:
            run_events.extend(terminal_events)
        elif run_events and not terminal_events:
            terminal_events.extend(run_events)

        metadata = token.setdefault("metadata", {})
        run_event_cursor = metadata.get("run_event_cursor")
        terminal_cursor = metadata.get("terminal_cursor")
        latest_seq = terminal.get("latest_seq")
        if run_event_cursor is None:
            metadata["run_event_cursor"] = terminal_cursor if terminal_cursor is not None else latest_seq or 0
        if terminal_cursor is None:
            metadata["terminal_cursor"] = metadata.get("run_event_cursor") or latest_seq or 0

        return run_events, terminal_events, terminal

    @staticmethod
    def _append_run_event_payload_to_token(token: dict[str, Any], *, event_payload: dict[str, Any], seq_no: int) -> None:
        run_events, terminal_events, terminal = RuntimeService._ensure_run_event_projection(token)
        run_events.append(event_payload)
        if terminal_events is not run_events:
            terminal_events.append(event_payload)
        terminal["latest_seq"] = seq_no
        metadata = token.setdefault("metadata", {})
        metadata["run_event_cursor"] = seq_no
        metadata["terminal_cursor"] = seq_no

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
        if decision in {"proceed", "complete"} and not str(parsed.get("next_phase") or "").strip():
            raise RuntimeError(f"Evaluation node `{node.get('id')}` must include next_phase for decision `{decision}`.")
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
        artifact = self.repository.get_artifact(session, invocation.compile_artifact_id)
        return InvocationResponse(
            id=invocation.id,
            pskill_definition_id=invocation.pskill_definition_id,
            pskill_version_id=invocation.pskill_version_id,
            compile_artifact_id=invocation.compile_artifact_id,
            compile_request_id=artifact.compile_request_id if artifact else "",
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
        artifact = self.repository.get_artifact(session, run.compile_artifact_id)
        return RunResponse(
            id=run.id,
            invocation_id=run.invocation_id,
            pskill_definition_id=run.pskill_definition_id,
            pskill_version_id=run.pskill_version_id,
            compile_artifact_id=run.compile_artifact_id,
            compile_request_id=artifact.compile_request_id if artifact else "",
            status=run.status,
            runtime_phase=run.runtime_phase,
            latest_snapshot_seq=run.latest_snapshot_seq,
            latest_run_event_seq=run.latest_run_event_seq,
            latest_terminal_seq=run.latest_run_event_seq,
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

    def _build_run_event_response(self, session: Session, event: RunEvent) -> RunEventResponse:
        return RunEventResponse(
            id=event.id,
            terminal_session_id=event.terminal_session_id,
            run_id=event.run_id,
            run_trace_id=event.run_trace_id,
            trace_event_id=event.run_trace_id,
            agent_run_id=event.agent_run_id,
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
                self._build_run_event_part_response(part)
                for part in self.repository.list_run_event_parts(session, event.id)
            ],
            occurred_at=event.occurred_at,
            created_at=event.created_at,
        )

    @staticmethod
    def _build_run_event_part_response(part: RunEventPart) -> RunEventPartResponse:
        return RunEventPartResponse(
            id=part.id,
            run_event_id=part.run_event_id,
            terminal_event_id=part.run_event_id,
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
    def _build_agent_run_response(agent_run: AgentRun) -> AgentRunResponse:
        return AgentRunResponse(
            id=agent_run.id,
            definition_id=agent_run.definition_id,
            agent_version_id=agent_run.agent_version_id,
            agent_session_id=agent_run.agent_session_id,
            agent_key=agent_run.agent_key,
            status=agent_run.status,
            owner_type=agent_run.owner_type,
            owner_id=agent_run.owner_id,
            run_id=agent_run.run_id,
            input_payload=agent_run.input_payload,
            output_payload=agent_run.output_payload,
            error_message=agent_run.error_message,
            started_at=agent_run.started_at,
            ended_at=agent_run.ended_at,
            created_at=agent_run.created_at,
            updated_at=agent_run.updated_at,
        )

    @staticmethod
    def _build_agent_event_response(event: AgentEvent) -> AgentEventResponse:
        return AgentEventResponse(
            id=event.id,
            agent_run_id=event.agent_run_id,
            seq_no=event.seq_no,
            event_type=event.event_type,
            phase=event.phase,
            payload=event.payload,
            occurred_at=event.occurred_at,
        )

    @staticmethod
    def _build_agent_tool_call_response(tool_call: AgentToolCall) -> AgentToolCallResponse:
        return AgentToolCallResponse(
            id=tool_call.id,
            agent_run_id=tool_call.agent_run_id,
            tool_name=tool_call.tool_name,
            tool_provider=tool_call.tool_provider,
            status=tool_call.status,
            arguments_summary=tool_call.arguments_summary,
            result_summary=tool_call.result_summary,
            side_effect_level=tool_call.side_effect_level,
            idempotency_key=tool_call.idempotency_key,
            created_at=tool_call.created_at,
            updated_at=tool_call.updated_at,
        )

    @staticmethod
    def _build_agent_model_call_response(model_call: AgentModelCall) -> AgentModelCallResponse:
        return AgentModelCallResponse(
            id=model_call.id,
            agent_run_id=model_call.agent_run_id,
            provider=model_call.provider,
            route_key=model_call.route_key,
            model_name=model_call.model_name,
            status=model_call.status,
            request_payload=model_call.request_payload,
            response_payload=model_call.response_payload,
            usage_json=model_call.usage_json,
            error_message=model_call.error_message,
            started_at=model_call.started_at,
            ended_at=model_call.ended_at,
            created_at=model_call.created_at,
        )

    @staticmethod
    def _build_agent_tool_authorization_response(
        authorization: AgentToolAuthorization,
    ) -> AgentToolAuthorizationResponse:
        return AgentToolAuthorizationResponse(
            id=authorization.id,
            agent_run_id=authorization.agent_run_id,
            agent_tool_call_id=authorization.agent_tool_call_id,
            run_id=authorization.run_id,
            run_event_id=authorization.run_event_id,
            tool_name=authorization.tool_name,
            tool_provider=authorization.tool_provider,
            mcp_server_name=authorization.mcp_server_name,
            side_effect_level=authorization.side_effect_level,
            risk_level=authorization.risk_level,
            authorization_reason=authorization.authorization_reason,
            tool_arguments_summary=authorization.tool_arguments_summary,
            expected_effect_summary=authorization.expected_effect_summary,
            reversible=authorization.reversible,
            idempotency_key=authorization.idempotency_key,
            status=authorization.status,
            business_context=tool_authorization_business_context(authorization),
            request_payload=authorization.request_payload,
            response_payload=authorization.response_payload,
            created_at=authorization.created_at,
            responded_at=authorization.responded_at,
            executed_at=authorization.executed_at,
        )

    def _build_run_evaluation_response(
        self,
        evaluation: RunEvaluation,
        *,
        findings: list[RunEvaluationFinding],
    ) -> RunEvaluationResponse:
        return RunEvaluationResponse(
            id=evaluation.id,
            run_id=evaluation.run_id,
            pskill_definition_id=evaluation.pskill_definition_id,
            pskill_version_id=evaluation.pskill_version_id,
            artifact_id=evaluation.artifact_id,
            agent_run_id=evaluation.agent_run_id,
            overall_outcome=evaluation.overall_outcome,
            quality_score=evaluation.quality_score,
            summary=evaluation.summary,
            attribution=evaluation.attribution_json,
            findings=[
                self._build_run_evaluation_finding_response(item, evaluation=evaluation)
                for item in findings
                if item.evaluation_id == evaluation.id
            ],
            created_at=evaluation.created_at,
        )

    @staticmethod
    def _build_run_evaluation_finding_response(
        finding: RunEvaluationFinding,
        *,
        evaluation: RunEvaluation | None = None,
    ) -> RunEvaluationFindingResponse:
        return RunEvaluationFindingResponse(
            id=finding.id,
            evaluation_id=finding.evaluation_id,
            run_id=evaluation.run_id if evaluation else "",
            pskill_definition_id=evaluation.pskill_definition_id if evaluation else "",
            pskill_version_id=evaluation.pskill_version_id if evaluation else "",
            overall_outcome=evaluation.overall_outcome if evaluation else "",
            quality_score=evaluation.quality_score if evaluation else None,
            category=finding.category,
            severity=finding.severity,
            confidence=finding.confidence,
            description=finding.description,
            evidence_refs=finding_evidence_refs(finding, evaluation),
            recommended_action=finding.recommended_action,
            status=finding.status,
            evaluation_created_at=evaluation.created_at if evaluation else None,
            created_at=finding.created_at,
        )

    def _build_governance_proposal_response(
        self,
        session: Session,
        proposal: PsopImprovementProposal,
        *,
        experiments: list[PsopImprovementExperiment],
    ) -> GovernanceProposalResponse:
        return GovernanceProposalResponse(
            id=proposal.id,
            agent_run_id=proposal.agent_run_id,
            source_finding_ids=list(proposal.source_finding_ids or []),
            source_findings=[
                response
                for finding_id in list(proposal.source_finding_ids or [])
                if (response := self._build_governance_source_finding_response(session, str(finding_id))) is not None
            ],
            source_evaluation_id=proposal.source_evaluation_id,
            source_run_id=proposal.source_run_id,
            proposal_type=proposal.proposal_type,
            target=proposal.target_json,
            problem_statement=proposal.problem_statement,
            evidence_refs=list(proposal.evidence_refs or []),
            proposed_changes=list(proposal.proposed_changes or []),
            risk_assessment=proposal.risk_assessment,
            required_tests=list(proposal.required_tests or []),
            activation_plan=proposal.activation_plan,
            status=proposal.status,
            experiments=[
                self._build_governance_experiment_response(experiment, proposal=proposal)
                for experiment in experiments
            ],
            created_at=proposal.created_at,
            updated_at=proposal.updated_at,
        )

    def _build_governance_source_finding_response(
        self,
        session: Session,
        finding_id: str,
    ) -> RunEvaluationFindingResponse | None:
        finding = session.get(RunEvaluationFinding, finding_id)
        if not finding:
            return None
        evaluation = session.get(RunEvaluation, finding.evaluation_id)
        return self._build_run_evaluation_finding_response(finding, evaluation=evaluation)

    def _build_governance_experiment_response(
        self,
        experiment: PsopImprovementExperiment,
        *,
        proposal: PsopImprovementProposal | None = None,
    ) -> GovernanceExperimentResponse:
        return GovernanceExperimentResponse(
            id=experiment.id,
            proposal_id=experiment.proposal_id,
            proposal_status=proposal.status if proposal else "",
            proposal_type=proposal.proposal_type if proposal else "",
            problem_statement=proposal.problem_statement if proposal else "",
            source_run_id=proposal.source_run_id if proposal else None,
            experiment_type=experiment.experiment_type,
            status=experiment.status,
            summary=experiment.summary,
            before_metrics=experiment.before_metrics,
            after_metrics=experiment.after_metrics,
            result=experiment.result_json,
            canary_scope=self._governance_experiment_canary_scope(experiment, proposal=proposal),
            rollback_conditions=self._governance_experiment_rollback_conditions(experiment, proposal=proposal),
            started_at=experiment.started_at,
            finished_at=experiment.finished_at,
            created_at=experiment.created_at,
        )

    @staticmethod
    def _governance_canary_scope(proposal: PsopImprovementProposal) -> dict[str, Any]:
        activation_plan = proposal.activation_plan or {}
        nested = activation_plan.get("canary")
        if isinstance(nested, dict) and isinstance(nested.get("scope"), dict):
            return dict(nested["scope"])
        direct = activation_plan.get("canary_scope")
        if isinstance(direct, dict):
            return dict(direct)
        return {
            "strategy": activation_plan.get("strategy", "test_review_canary_rollback"),
            "source_run_id": proposal.source_run_id,
            "proposal_type": proposal.proposal_type,
        }

    @staticmethod
    def _governance_rollback_conditions(proposal: PsopImprovementProposal) -> list[Any]:
        activation_plan = proposal.activation_plan or {}
        nested = activation_plan.get("rollback")
        if isinstance(nested, dict) and isinstance(nested.get("conditions"), list):
            return list(nested["conditions"])
        direct = activation_plan.get("rollback_conditions")
        if isinstance(direct, list):
            return list(direct)
        return [
            "canary_metric_regression",
            "manual_review_rejects_canary",
            "unexpected_runtime_or_tool_authorization_failure",
        ]

    def _governance_experiment_canary_scope(
        self,
        experiment: PsopImprovementExperiment,
        *,
        proposal: PsopImprovementProposal | None = None,
    ) -> dict[str, Any]:
        result = experiment.result_json or {}
        result_scope = result.get("canary_scope")
        if isinstance(result_scope, dict):
            return dict(result_scope)
        return self._governance_canary_scope(proposal) if proposal else {}

    def _governance_experiment_rollback_conditions(
        self,
        experiment: PsopImprovementExperiment,
        *,
        proposal: PsopImprovementProposal | None = None,
    ) -> list[Any]:
        result = experiment.result_json or {}
        result_conditions = result.get("rollback_conditions")
        if isinstance(result_conditions, list):
            return list(result_conditions)
        return self._governance_rollback_conditions(proposal) if proposal else []

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
    def _build_run_trace_response(event: RunTrace) -> RunTraceResponse:
        return RunTraceResponse(
            id=event.id,
            run_id=event.run_id,
            agent_run_id=event.agent_run_id,
            seq_no=event.seq_no,
            phase=event.phase,
            event_type=event.event_type,
            trace_id=event.trace_id,
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
    def _build_timeline_item(event: RunTraceResponse) -> ReplayTimelineItem:
        titles = {
            "binding.resolved": "绑定解析",
            "binding.updated": "绑定更新",
            "runtime.input.accepted": "输入",
            "runtime.wait_checkpoint.entered": "等待现场证据",
            "gateway.inference.completed": "LLM 输出",
            "gateway.inference.failed": "LLM 失败",
            "gateway.tool.completed": "工具调用",
            "runtime.final.completed": "最终结果",
            "runtime.aborted": "已中止",
            "runtime.evaluation.queued": "评估已入队",
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
            source_kind="run_trace",
            source_id=event.id,
            agent_run_id=event.agent_run_id,
        )

    @staticmethod
    def _build_terminal_timeline_item(event: RunEventResponse) -> ReplayTimelineItem:
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
            event_type="run.event.appended",
            title=title,
            summary=summary,
            payload=event.model_dump(mode="json"),
            occurred_at=event.occurred_at,
            source_kind="run_event",
            source_id=event.id,
            agent_run_id=event.agent_run_id,
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
