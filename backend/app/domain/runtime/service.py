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
    Run,
    SessionTokenSnapshot,
    SkillInvocation,
    TraceEvent,
)
from app.domain.runtime.repository import RuntimeRepository
from app.domain.runtime.schemas import (
    CreateInvocationRequest,
    InvocationResponse,
    ReplayDetailResponse,
    ReplayTimelineItem,
    RunResponse,
    SessionTokenSnapshotResponse,
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

        skill_version = self.repository.get_skill_version(session, skill_definition.latest_published_version_id)
        if not skill_version or skill_version.status != "published":
            raise SkillValidationError("当前 Skill 尚无已发布版本，无法发起运行。")

        artifact = self.repository.get_latest_ready_artifact(session, skill_version.id)
        if not artifact:
            raise SkillValidationError("当前 Skill 尚无成功编译产物，无法发起运行。")
        artifact_object = self.repository.get_artifact_object(session, artifact.artifact_object_id)
        artifact_payload = artifact_object.content_json if artifact_object else {}

        invocation = SkillInvocation(
            skill_definition_id=skill_definition.id,
            skill_version_id=skill_version.id,
            compile_artifact_id=artifact.id,
            gateway_type=payload.gateway_type,
            input_envelope=payload.input_envelope,
            status="queued",
        )
        session.add(invocation)
        session.flush()

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
        self.process_run(session, run.id)
        refreshed = self.repository.get_invocation(session, invocation.id)
        if not refreshed:
            raise SkillNotFoundError("Invocation 创建后无法读取。")
        return self._build_invocation_response(session, refreshed)

    def process_run(self, session: Session, run_id: str) -> Run:
        run = self.repository.get_run(session, run_id)
        if not run:
            raise SkillNotFoundError("未找到 Run。", details={"run_id": run_id})
        if run.status == "succeeded":
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

        run.status = "running"
        run.runtime_phase = self._initial_phase(artifact_payload)
        run.started_at = run.started_at or now_utc()
        invocation.status = "running"
        session.flush()

        try:
            token = self.repository.list_snapshots(session, run.id)[-1].token_payload
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
                            run.runtime_phase = "waiting"
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
                    token = self._append_runtime_step(
                        session,
                        run=run,
                        token=token,
                        node=node,
                        observation=observation,
                        enabled_after=[item["id"] for item in self._enabled_nodes(artifact_payload, token)],
                    )
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
        return [self._build_run_response(item) for item in self.repository.list_runs(session, status=status, skill_id=skill_id)]

    def get_run(self, session: Session, run_id: str) -> RunResponse:
        run = self.repository.get_run(session, run_id)
        if not run:
            raise SkillNotFoundError("未找到 Run。", details={"run_id": run_id})
        return self._build_run_response(run)

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
        timeline = [self._build_timeline_item(event) for event in trace_events]
        return ReplayDetailResponse(
            run=self._build_run_response(run),
            timeline=timeline,
            snapshots=snapshots,
            trace_events=trace_events,
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
        session.flush()
        return token

    def _append_trace_event(
        self,
        session: Session,
        *,
        run: Run,
        phase: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        seq_no = len(self.repository.list_trace_events(session, run.id)) + 1
        session.add(
            TraceEvent(
                run_id=run.id,
                seq_no=seq_no,
                phase=phase,
                event_type=event_type,
                span_id=f"{run.id[:8]}-{seq_no:04d}",
                parent_span_id="",
                payload=payload,
            )
        )

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
            "metadata": {"artifact_version": artifact_payload.get("artifact_version")},
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
            token.setdefault("budgets", {})["llm_calls"] = int(token.setdefault("budgets", {}).get("llm_calls", 0)) + 1
            return {
                "content": llm_completion.content,
                "provider": llm_completion.provider,
                "model": llm_completion.model,
                "summary": "LLM 节点执行完成。",
            }
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
        return token.get("status") == "success" or bool(_get_path(token, "outputs.final_response"))

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
            status=invocation.status,
            idempotency_key=invocation.idempotency_key,
            run_id=run.id if run else None,
            created_at=invocation.created_at,
            updated_at=invocation.updated_at,
        )

    @staticmethod
    def _build_run_response(run: Run) -> RunResponse:
        return RunResponse(
            id=run.id,
            invocation_id=run.invocation_id,
            skill_definition_id=run.skill_definition_id,
            skill_version_id=run.skill_version_id,
            compile_artifact_id=run.compile_artifact_id,
            status=run.status,
            runtime_phase=run.runtime_phase,
            latest_snapshot_seq=run.latest_snapshot_seq,
            final_output=run.final_output,
            exit_reason=run.exit_reason,
            created_at=run.created_at,
            started_at=run.started_at,
            ended_at=run.ended_at,
            updated_at=run.updated_at,
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
            "runtime.input.accepted": "输入",
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
