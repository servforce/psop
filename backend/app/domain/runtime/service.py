from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.logging import log_context
from app.core.observability import record_span_exception, start_span
from app.domain.jobs.models import RuntimeJob
from app.domain.jobs.repository import JobRepository
from app.domain.jobs.schemas import RuntimeJobResponse
from app.domain.runtime.models import (
    RunCapabilityBinding,
    Run,
    SessionTokenSnapshot,
    SkillInvocation,
    TerminalEvent,
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
    SessionTokenSnapshotResponse,
    TerminalEventAppendResponse,
    TerminalEventResponse,
    TerminalSessionDetailResponse,
    TerminalSessionResponse,
    TerminalTranscriptSummary,
    TraceEventResponse,
)
from app.domain.skills.exceptions import SkillNotFoundError, SkillValidationError
from app.domain.skills.models import now_utc
from app.gateway.inference import LlmInferenceGateway

LOGGER = logging.getLogger(__name__)


class RuntimeService:
    """Invocation, RuntimeKernel and Replay service for the issue #1 MVP slice."""

    def __init__(
        self,
        *,
        settings: Settings,
        inference_gateway: LlmInferenceGateway,
        repository: RuntimeRepository | None = None,
        job_repository: JobRepository | None = None,
    ) -> None:
        self.settings = settings
        self.inference_gateway = inference_gateway
        self.repository = repository or RuntimeRepository()
        self.job_repository = job_repository or JobRepository()

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
            status="queued",
        )
        session.add(invocation)
        session.flush()

        initial_input = self._extract_initial_terminal_input(payload.input_envelope)
        run = Run(
            invocation_id=invocation.id,
            skill_definition_id=skill_definition.id,
            skill_version_id=skill_version.id,
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
        self._append_trace_event(
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
            self._append_terminal_event(
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
                job_type="runtime",
                status="pending",
                payload={"run_id": run.id},
                run_id=run.id,
                dedupe_key=f"job:runtime:{run.id}",
                max_attempts=self.settings.runtime_job_max_attempts,
            )
        )
        session.commit()
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

        # MVP local drain: keeps the issue #1 flow runnable without a separate worker process.
        # Real-world runs start even without user_input so Runtime can introduce the task,
        # output the first actionable step, and enter a durable wait checkpoint.
        self.process_run(session, run.id)
        refreshed = self.repository.get_invocation(session, invocation.id)
        if not refreshed:
            raise SkillNotFoundError("Invocation 创建后无法读取。")
        return self._build_invocation_response(session, refreshed)

    def process_run(self, session: Session, run_id: str) -> Run:
        run = self.repository.get_run(session, run_id)
        if not run:
            raise SkillNotFoundError("未找到 Run。", details={"run_id": run_id})
        if run.status in {"succeeded", "failed", "cancelled"}:
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
            skill_id=run.skill_definition_id,
            skill_version_id=run.skill_version_id,
        ):
            LOGGER.info("runtime loop started")

        job = self.job_repository.get_runtime_job_by_dedupe_key(session, f"job:runtime:{run.id}")
        if job:
            job.status = "running"
            job.attempt_no += 1

        run.started_at = run.started_at or now_utc()
        invocation.status = "running"
        session.flush()

        try:
            token = self.repository.list_snapshots(session, run.id)[-1].token_payload
            token = self._sync_terminal_events(session, run=run, token=token)
            run.status = "running"
            run.runtime_phase = str(token.get("phase") or self._initial_phase(artifact_payload))
            if token.get("status") == "waiting":
                run.status = "waiting_input"
                run.runtime_phase = self._runtime_phase_from_token(token)
                invocation.status = "running"
                if job:
                    job.status = "succeeded"
                session.commit()
                LOGGER.info("runtime loop waiting for terminal evidence")
                return run
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
                        break

                    enabled_nodes = self._enabled_nodes(artifact_payload, token)
                    if not enabled_nodes:
                        if self._halt_wait(artifact_payload, token):
                            run.status = "waiting_input"
                            run.runtime_phase = self._runtime_phase_from_token(token)
                            invocation.status = "running"
                            if job:
                                job.status = "succeeded"
                            session.commit()
                            LOGGER.info("runtime loop waiting for input")
                            return run
                        raise RuntimeError("Runtime deadlock: no enabled nodes and no wait condition matched.")

                    node = self._select_node(enabled_nodes)
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
                            observation = self._execute_node(node=node, token=token, artifact_payload=artifact_payload)
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
                        if job:
                            job.status = "succeeded"
                        session.commit()
                        LOGGER.info("runtime loop entered wait checkpoint")
                        return run
                else:
                    raise RuntimeError(f"Runtime exceeded max_steps={max_steps}.")

                if self._halt_success(artifact_payload, token):
                    loop_span.set_attribute("runtime.exit_reason", "halt_success")

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
            session.commit()
            LOGGER.info("runtime loop succeeded", extra={"final_output_length": len(run.final_output or "")})
            return run
        except Exception as exc:
            run.status = "failed"
            run.runtime_phase = "failed"
            run.exit_reason = str(exc)
            run.ended_at = now_utc()
            invocation.status = "failed"
            self._close_terminal_session(session, run)
            if job:
                job.status = "failed"
                job.last_error = str(exc)
            self._append_trace_event(
                session,
                run=run,
                phase="failed",
                event_type="runtime.failed",
                payload={"error": str(exc)},
            )
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
            self._build_terminal_event_response(item)
            for item in self.repository.list_terminal_events(session, run_id, from_seq=from_seq, to_seq=to_seq)
        ]

    def append_terminal_event(
        self,
        session: Session,
        run_id: str,
        payload: AppendTerminalEventRequest,
        *,
        idempotency_key: str | None = None,
    ) -> TerminalEventAppendResponse:
        run = self.repository.get_run(session, run_id)
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
                event_response = self._build_terminal_event_response(existing)
                return TerminalEventAppendResponse(
                    accepted=True,
                    event_id=existing.id,
                    seq_no=existing.seq_no,
                    event=event_response,
                )

        if run.status in {"succeeded", "failed", "cancelled"}:
            raise SkillValidationError("Run 已结束，不能继续追加终端输入。", details={"run_id": run_id, "status": run.status})
        terminal_session = self.repository.get_terminal_session_for_run(session, run_id)
        if not terminal_session or terminal_session.status != "open":
            raise SkillValidationError("当前 Run 没有可用的 Terminal Session。", details={"run_id": run_id})

        event = self._append_terminal_event(
            session,
            run=run,
            terminal_session=terminal_session,
            direction=payload.direction,
            event_kind=payload.event_kind,
            mime_type=payload.mime_type,
            payload_inline=payload.payload_inline,
            artifact_object_id=payload.artifact_object_id,
            binding_id=payload.binding_id,
            source_ref=payload.source.model_dump(),
            external_event_id=external_event_id,
            occurred_at=payload.occurred_at,
        )
        if run.status == "waiting_input":
            self._ensure_runtime_job_pending(session, run)
            session.commit()
            self.process_run(session, run.id)
            event = session.get(TerminalEvent, event.id) or event
        else:
            session.commit()
        event_response = self._build_terminal_event_response(event)
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
        self._append_trace_event(
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
        self._append_trace_event(
            session,
            run=run,
            phase=str(node.get("id")),
            event_type=self._event_type_for_node(node),
            payload={
                "node_id": node.get("id"),
                "node_kind": node.get("kind"),
                "observation": observation,
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
                self._append_terminal_event(
                    session,
                    run=run,
                    terminal_session=terminal_session,
                    direction="output",
                    event_kind=str(interaction.get("output_event_kind") or "terminal.text.output.v1"),
                    mime_type=str(interaction.get("output_mime_type") or "text/markdown"),
                    payload_inline=terminal_message,
                    binding_id=None,
                    source_ref={"kind": "runtime", "node_id": str(node.get("id"))},
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
                next_token["status"] = "failure"
                next_token["phase"] = "failed"
                next_token.setdefault("outputs", {})["final_response"] = terminal_message or str(
                    observation.get("reason") or "任务已终止。"
                )

        if interaction.get("wait_after_output"):
            next_token = self._enter_wait_checkpoint(run=run, token=next_token, node=node, observation=observation)
            entered_wait = True
            self._append_trace_event(
                session,
                run=run,
                phase=str(node.get("id")),
                event_type="runtime.wait_checkpoint.entered",
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
        source_ref: dict[str, Any] | None = None,
        external_event_id: str | None = None,
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

        next_seq = run.latest_terminal_seq + 1
        run.latest_terminal_seq = next_seq
        event = TerminalEvent(
            terminal_session_id=terminal_session.id,
            run_id=run.id,
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
        return event

    def _sync_terminal_events(self, session: Session, *, run: Run, token: dict[str, Any]) -> dict[str, Any]:
        cursor = int(_get_path(token, "metadata.terminal_cursor") or 0)
        events = self.repository.list_terminal_events(session, run.id, from_seq=cursor + 1)
        if not events:
            return token

        next_token = json.loads(json.dumps(token, ensure_ascii=False, default=str))
        terminal = next_token.setdefault("terminal", {})
        token_events = terminal.setdefault("events", [])
        input_envelope = next_token.setdefault("input_envelope", {})
        for event in events:
            event_payload = self._terminal_event_token_payload(event)
            token_events.append(event_payload)
            terminal["latest_seq"] = event.seq_no
            next_token.setdefault("metadata", {})["terminal_cursor"] = event.seq_no
            if event.direction == "input":
                input_text = self._terminal_input_text(event)
                if input_text:
                    input_envelope["user_input"] = input_text
                    input_envelope.setdefault("text", input_text)
                wait = next_token.setdefault("control", {}).get("wait")
                if isinstance(wait, dict) and next_token.get("status") == "waiting":
                    evidence = {
                        **event_payload,
                        "text": input_text,
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
            job.status = "pending"
            job.available_at = now_utc()
            job.last_error = ""
            return job
        job = RuntimeJob(
            job_type="runtime",
            status="pending",
            payload={"run_id": run.id},
            run_id=run.id,
            dedupe_key=f"job:runtime:{run.id}",
            max_attempts=self.settings.runtime_job_max_attempts,
        )
        session.add(job)
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

    @staticmethod
    def _terminal_input_text(event: TerminalEvent) -> str:
        value = event.payload_inline
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            for key in ("user_input", "text", "value", "content"):
                if value.get(key):
                    return str(value[key])
        if value is None:
            return ""
        return json.dumps(value, ensure_ascii=False)

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

    def _execute_node(self, *, node: dict[str, Any], token: dict[str, Any], artifact_payload: dict[str, Any]) -> dict[str, Any]:
        kind = node.get("kind")
        actor_name = _actor_name(node.get("actor"))
        if kind == "start" or actor_name == "runtime.start":
            return {"started": True, "summary": "Runtime 已初始化 Session Token。"}
        if kind == "input" or actor_name == "runtime.input":
            user_input = self._extract_user_input(token.get("input_envelope", {}), artifact_payload)
            return {"user_input": user_input, "summary": "已接收用户输入。"}
        if kind == "llm" or actor_name == "agent.llm":
            system_prompt, user_prompt = self._render_llm_prompts(node, token, artifact_payload)
            route_key = artifact_payload.get("capability_summary", {}).get("llm_route_key", "default")
            with start_span("gateway.inference", route_key=route_key, node_id=node.get("id")):
                llm_completion = self.inference_gateway.complete(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    route_key=route_key,
                )
            budgets = token.setdefault("budgets", {})
            budgets["llm_calls"] = int(budgets.get("llm_calls", 0)) + 1
            self._accumulate_llm_usage(budgets, llm_completion.usage)
            observation = {
                "content": llm_completion.content,
                "provider": llm_completion.provider,
                "model": llm_completion.model,
                "input": {
                    "system_prompt": system_prompt,
                    "user_prompt": user_prompt,
                },
                "output": {"content": llm_completion.content},
                "usage": llm_completion.usage,
                "summary": "LLM 节点执行完成。",
            }
            if self._node_is_evaluation(node):
                observation.update(self._parse_evaluation_observation(llm_completion.content, node=node))
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

    def _merge_observation(self, *, node: dict[str, Any], token: dict[str, Any], observation: dict[str, Any]) -> dict[str, Any]:
        next_token = json.loads(json.dumps(token, ensure_ascii=False))
        for operation in node.get("merge", []):
            if not isinstance(operation, dict) or operation.get("op") != "set":
                continue
            target_path = operation.get("path")
            if not isinstance(target_path, str):
                continue
            value = operation.get("value") if "value" in operation else self._resolve_merge_source(
                str(operation.get("from")),
                token=next_token,
                observation=observation,
            )
            _set_path(next_token, target_path, value)
        next_token.setdefault("observations", {}).setdefault(str(node.get("id")), observation)
        return next_token

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

    def _render_llm_prompts(self, node: dict[str, Any], token: dict[str, Any], artifact_payload: dict[str, Any]) -> tuple[str, str]:
        projection = node.get("projection") if isinstance(node.get("projection"), dict) else {}
        runtime_contract = artifact_payload.get("runtime_contract", {}) if isinstance(artifact_payload.get("runtime_contract"), dict) else {}
        skill = artifact_payload.get("skill", {}) if isinstance(artifact_payload.get("skill"), dict) else {}
        skill_instruction = str(runtime_contract.get("skill_instruction") or "")
        system_template = str(
            projection.get("system_template")
            or f"你正在执行 PSOP Skill：{skill.get('name', 'Unnamed Skill')}。\n请遵循 Skill 执行说明，输出清晰、可执行的中文结果。"
        )
        user_template = str(
            projection.get("user_template")
            or "用户输入：{{input.user_input}}\n\nSkill 执行说明：\n{{skill.instruction}}"
        )
        context = {
            "token": token,
            "input": token.get("input_envelope", {}),
            "skill": {
                **skill,
                "instruction": skill_instruction,
            },
        }
        return _render_template(system_template, context), _render_template(user_template, context)

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

    @staticmethod
    def _build_terminal_event_response(event: TerminalEvent) -> TerminalEventResponse:
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
            occurred_at=event.occurred_at,
            created_at=event.created_at,
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
            run_id=job.run_id,
            compile_request_id=job.compile_request_id,
            attempt_no=job.attempt_no,
            max_attempts=job.max_attempts,
            last_error=job.last_error,
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
            "gateway.inference.completed": "LLM 输出",
            "gateway.tool.completed": "工具调用",
            "runtime.final.completed": "最终结果",
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
        if isinstance(event.payload_inline, str):
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
