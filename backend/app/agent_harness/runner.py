from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.agent_harness.agent_decision import AgentDecision
from app.agent_harness.tools import ToolPolicy
from app.agents.models import AgentRun
from app.agents.schemas import (
    AppendAgentEventRequest,
    CreateAgentToolCallRequest,
    CreateToolAuthorizationRequest,
    AgentRunResponse,
)
from app.agents.service import AgentService
from app.memory.service import MemoryService
from app.pskills.exceptions import SkillNotFoundError, SkillValidationError
from app.pskills.models import now_utc
from app.skills.models import SkillActivation
from app.skills.repository import SkillPackageRepository
from app.skills.service import SkillPackageService


DEFAULT_AGENT_SKILLS: dict[str, list[str]] = {
    "pskill.builder": ["pskill-builder", "ffmpeg-video-processing", "document-ocr-processing"],
    "pskill.compiler": ["pskill-compiler-formal-v5"],
    "pskill.tester": ["pskill-tester"],
    "pskill.runner": ["pskill-runner-field-assistant"],
    "pskill.evaluator": ["pskill-run-evaluator"],
    "psop.governance": ["psop-governance-manager"],
}


class AgentRunner:
    def __init__(
        self,
        *,
        agent_service: AgentService | None = None,
        skill_service: SkillPackageService | None = None,
        skill_repository: SkillPackageRepository | None = None,
        tool_policy: ToolPolicy | None = None,
        memory_service: MemoryService | None = None,
    ) -> None:
        self.agent_service = agent_service or AgentService()
        self.skill_service = skill_service or SkillPackageService()
        self.skill_repository = skill_repository or SkillPackageRepository()
        self.tool_policy = tool_policy or ToolPolicy()
        self.memory_service = memory_service or MemoryService()

    def run_once(self, session: Session, agent_run_id: str) -> AgentRunResponse:
        agent_run = self.agent_service.get_run_model(session, agent_run_id)
        if agent_run.status not in {"queued", "running"}:
            raise SkillValidationError(
                "AgentRun 当前状态不可执行。",
                details={"agent_run_id": agent_run.id, "status": agent_run.status},
            )

        version = self.agent_service.repository.get_version(session, agent_run.agent_version_id)
        if not version:
            raise SkillNotFoundError("未找到 AgentVersion。", details={"agent_run_id": agent_run.id})
        resumed = self._execute_authorized_tool_call(session, agent_run)
        if resumed:
            return resumed
        spec = version.spec_json
        agent_run.status = "running"
        agent_run.started_at = agent_run.started_at or now_utc()
        self.agent_service.append_event(
            session,
            agent_run.id,
            AppendAgentEventRequest(event_type="agent.runner.started", phase="runner", payload={"agent_key": agent_run.agent_key}),
            commit=False,
        )

        active_tools = self._activate_skills(session, agent_run=agent_run, spec=spec)
        decision = self._decision_from_input(agent_run.input_payload)
        self.agent_service.record_model_call(
            session,
            agent_run_id=agent_run.id,
            provider="deterministic",
            route_key=str(spec.get("model_policy", {}).get("route_key") or "json"),
            model_name="agent-harness-deterministic",
            status="succeeded",
            request_payload={"input_payload": agent_run.input_payload, "agent_key": agent_run.agent_key},
            response_payload=decision.model_dump(mode="json"),
            usage_json={"input_tokens": 0, "output_tokens": 0},
            commit=False,
        )
        self.agent_service.append_event(
            session,
            agent_run.id,
            AppendAgentEventRequest(
                event_type="agent.model_call.completed",
                phase="model",
                payload={"decision_type": decision.decision_type},
            ),
            commit=False,
        )

        if decision.decision_type == "final_output":
            agent_run.output_payload = decision.output_payload
            memory_count = self._write_memory_candidates(session, agent_run=agent_run, output_payload=decision.output_payload)
            agent_run.status = "succeeded"
            agent_run.ended_at = now_utc()
            self.agent_service.append_event(
                session,
                agent_run.id,
                AppendAgentEventRequest(
                    event_type="agent.final_output",
                    phase="output",
                    payload={"output_schema": spec.get("output_schema", {}), "memory_candidate_count": memory_count},
                ),
                commit=False,
            )
            session.commit()
            return self.agent_service._build_run_response(agent_run)

        if decision.decision_type == "fail":
            agent_run.status = "failed"
            agent_run.error_message = decision.error_message or "agent_decision_failed"
            agent_run.ended_at = now_utc()
            self.agent_service.append_event(
                session,
                agent_run.id,
                AppendAgentEventRequest(event_type="agent.failed", phase="output", payload={"error": agent_run.error_message}),
                commit=False,
            )
            session.commit()
            return self.agent_service._build_run_response(agent_run)

        return self._handle_tool_call(session, agent_run=agent_run, spec=spec, decision=decision, active_tools=active_tools)

    def _execute_authorized_tool_call(self, session: Session, agent_run: AgentRun) -> AgentRunResponse | None:
        tool_calls = self.agent_service.repository.list_tool_calls(session, agent_run.id)
        tool_call = next((item for item in tool_calls if item.status == "authorized"), None)
        if not tool_call:
            return None
        authorization = next(
            (
                item
                for item in self.agent_service.repository.list_tool_authorizations(
                    session,
                    agent_run_id=agent_run.id,
                    status="approved",
                )
                if item.agent_tool_call_id == tool_call.id
            ),
            None,
        )
        agent_run.status = "running"
        agent_run.started_at = agent_run.started_at or now_utc()
        tool_call.status = "executing"
        self.agent_service.append_event(
            session,
            agent_run.id,
            AppendAgentEventRequest(
                event_type="agent.runner.resumed_authorized_tool",
                phase="tool",
                payload={"tool_call_id": tool_call.id, "tool_name": tool_call.tool_name},
            ),
            commit=False,
        )
        try:
            tool_result = self._execute_native_tool_call(session, tool_call=tool_call)
        except (SkillNotFoundError, SkillValidationError) as error:
            tool_call.status = "failed"
            tool_call.result_summary = {"executed": False, "error": error.message, "details": error.details}
            if authorization:
                authorization.status = "failed"
                authorization.executed_at = now_utc()
            agent_run.status = "failed"
            agent_run.error_message = error.message
            agent_run.ended_at = now_utc()
            self.agent_service.append_event(
                session,
                agent_run.id,
                AppendAgentEventRequest(
                    event_type="agent.tool_call.failed",
                    phase="tool",
                    payload={"tool_call_id": tool_call.id, "tool_name": tool_call.tool_name, "error": error.message},
                ),
                commit=False,
            )
            session.commit()
            return self.agent_service._build_run_response(agent_run)
        tool_call.status = "succeeded"
        tool_call.result_summary = {
            "executed": True,
            "authorization_id": authorization.id if authorization else "",
            **tool_result,
        }
        if authorization:
            authorization.status = "executed"
            authorization.executed_at = now_utc()
        agent_run.status = "succeeded"
        agent_run.output_payload = {
            "tool_result": {"tool_name": tool_call.tool_name, "status": "succeeded", **tool_result}
        }
        agent_run.ended_at = now_utc()
        self.agent_service.append_event(
            session,
            agent_run.id,
            AppendAgentEventRequest(
                event_type="agent.tool_call.succeeded",
                phase="tool",
                payload={"tool_call_id": tool_call.id, "tool_name": tool_call.tool_name, "result": tool_result},
            ),
            commit=False,
        )
        session.commit()
        return self.agent_service._build_run_response(agent_run)

    def _execute_native_tool_call(self, session: Session, *, tool_call: Any) -> dict[str, Any]:
        if tool_call.tool_name != "psop.agent_version.activate":
            return {"result": {"authorized_execution": True}}
        arguments = tool_call.arguments_summary or {}
        agent_key = str(arguments.get("agent_key") or "").strip()
        version_id = str(arguments.get("version_id") or "").strip()
        if not agent_key or not version_id:
            raise SkillValidationError(
                "psop.agent_version.activate 缺少 agent_key 或 version_id。",
                details={"arguments_summary": arguments},
            )
        activation = self.agent_service.activate_version_from_tool(
            session,
            agent_key=agent_key,
            version_id=version_id,
            commit=False,
        )
        return {"result": activation}

    def _activate_skills(self, session: Session, *, agent_run: AgentRun, spec: dict[str, Any]) -> set[str]:
        self.skill_service.sync_packages(session)
        selected_names = list(spec.get("allowed_skill_names") or DEFAULT_AGENT_SKILLS.get(agent_run.agent_key, []))
        active_tools: set[str] = set()
        for package_name in selected_names:
            package = self.skill_repository.get_package_by_name(session, package_name)
            if not package or not package.active_version_id:
                continue
            version = self.skill_repository.get_version(session, package.active_version_id)
            if not version:
                continue
            active_tools.update(str(tool) for tool in version.allowed_tools)
            activation = self.skill_repository.get_activation(
                session,
                agent_run_id=agent_run.id,
                version_id=version.id,
            )
            if not activation:
                session.add(
                    SkillActivation(
                        agent_run_id=agent_run.id,
                        package_id=package.id,
                        version_id=version.id,
                        activation_context={
                            "agent_key": agent_run.agent_key,
                            "package_name": package.name,
                            "content_hash": version.content_hash,
                        },
                    )
                )
        self.agent_service.append_event(
            session,
            agent_run.id,
            AppendAgentEventRequest(
                event_type="agent.skills.activated",
                phase="skills",
                payload={"skill_names": selected_names, "allowed_tools": sorted(active_tools)},
            ),
            commit=False,
        )
        return active_tools

    def _handle_tool_call(
        self,
        session: Session,
        *,
        agent_run: AgentRun,
        spec: dict[str, Any],
        decision: AgentDecision,
        active_tools: set[str],
    ) -> AgentRunResponse:
        if not decision.tool_name:
            raise SkillValidationError("tool_call decision 缺少 tool_name。", details={"agent_run_id": agent_run.id})
        effective_allowed_tools = set(str(tool) for tool in spec.get("allowed_tools") or [])
        effective_allowed_tools &= active_tools
        effective_allowed_tools &= self.tool_policy.allowed_tools
        policy_decision = self.tool_policy.check(
            tool_name=decision.tool_name,
            tool_provider=decision.tool_provider,
            requested_side_effect_level=decision.side_effect_level,
            effective_allowed_tools=effective_allowed_tools,
        )
        tool_call = self.agent_service.create_tool_call(
            session,
            agent_run.id,
            CreateAgentToolCallRequest(
                tool_name=decision.tool_name,
                tool_provider=decision.tool_provider,
                arguments_summary=decision.arguments_summary,
                side_effect_level=policy_decision.side_effect_level,
                idempotency_key=decision.idempotency_key,
            ),
            commit=False,
        )
        if not policy_decision.allowed:
            model = self.agent_service.repository.get_tool_call(session, tool_call.id)
            if model:
                model.status = "blocked"
            agent_run.status = "failed"
            agent_run.error_message = policy_decision.reason
            agent_run.ended_at = now_utc()
            self.agent_service.append_event(
                session,
                agent_run.id,
                AppendAgentEventRequest(
                    event_type="agent.tool_call.blocked",
                    phase="tool",
                    payload={"tool_call_id": tool_call.id, "reason": policy_decision.reason},
                ),
                commit=False,
            )
            session.commit()
            return self.agent_service._build_run_response(agent_run)

        if policy_decision.requires_authorization:
            authorization_reason = decision.authorization_reason or f"{decision.tool_name} requires authorization."
            return self._request_tool_authorization(
                session,
                agent_run=agent_run,
                tool_call_id=tool_call.id,
                decision=decision,
                side_effect_level=policy_decision.side_effect_level,
                authorization_reason=authorization_reason,
            )

        model = self.agent_service.repository.get_tool_call(session, tool_call.id)
        if model:
            model.status = "succeeded"
            model.result_summary = {"executed": True, "policy": policy_decision.reason}
        agent_run.status = "succeeded"
        agent_run.output_payload = {"tool_result": {"tool_name": decision.tool_name, "status": "succeeded"}}
        agent_run.ended_at = now_utc()
        self.agent_service.append_event(
            session,
            agent_run.id,
            AppendAgentEventRequest(
                event_type="agent.tool_call.succeeded",
                phase="tool",
                payload={"tool_call_id": tool_call.id, "tool_name": decision.tool_name},
            ),
            commit=False,
        )
        session.commit()
        return self.agent_service._build_run_response(agent_run)

    def _request_tool_authorization(
        self,
        session: Session,
        *,
        agent_run: AgentRun,
        tool_call_id: str,
        decision: AgentDecision,
        side_effect_level: str,
        authorization_reason: str,
    ) -> AgentRunResponse:
        self.agent_service.create_tool_authorization(
            session,
            CreateToolAuthorizationRequest(
                agent_run_id=agent_run.id,
                agent_tool_call_id=tool_call_id,
                run_id=agent_run.run_id,
                tool_name=decision.tool_name,
                tool_provider=decision.tool_provider,
                side_effect_level=side_effect_level,
                risk_level="high" if side_effect_level == "high_write" else "medium",
                authorization_reason=authorization_reason,
                tool_arguments_summary=decision.arguments_summary,
                expected_effect_summary=decision.expected_effect_summary,
                reversible=decision.reversible,
                idempotency_key=decision.idempotency_key,
                request_payload={"decision": decision.model_dump(mode="json")},
            ),
        )
        return self.agent_service.get_run(session, agent_run.id)

    def _write_memory_candidates(
        self,
        session: Session,
        *,
        agent_run: AgentRun,
        output_payload: dict[str, Any],
    ) -> int:
        candidates = output_payload.get("memory_candidates") if isinstance(output_payload, dict) else None
        if not isinstance(candidates, list) or not candidates:
            return 0
        entries = self.memory_service.write_candidates(
            session,
            agent_key=agent_run.agent_key,
            created_by_agent_run_id=agent_run.id,
            candidates=candidates,
            commit=False,
        )
        self.agent_service.append_event(
            session,
            agent_run.id,
            AppendAgentEventRequest(
                event_type="agent.memory_candidates.written",
                phase="memory",
                payload={"memory_entry_ids": [item.id for item in entries]},
            ),
            commit=False,
        )
        return len(entries)

    @staticmethod
    def _decision_from_input(input_payload: dict[str, Any]) -> AgentDecision:
        payload = input_payload.get("agent_decision") or {"decision_type": "final_output", "output_payload": input_payload.get("expected_output", {})}
        if isinstance(payload, AgentDecision):
            return payload
        if not isinstance(payload, dict):
            raise SkillValidationError("agent_decision 必须是对象。")
        return AgentDecision(**payload)
