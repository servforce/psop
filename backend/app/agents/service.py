from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy.orm import Session

from app.agents.models import (
    AgentBinding,
    AgentDefinition,
    AgentEvent,
    AgentModelCall,
    AgentRun,
    AgentSession,
    AgentToolAuthorization,
    AgentToolCall,
    AgentVersion,
)
from app.agents.repository import AgentRepository
from app.agents.schemas import (
    AgentBindingResponse,
    AgentDefinitionDetailResponse,
    AgentDefinitionSummaryResponse,
    AgentEventResponse,
    AgentModelCallResponse,
    AgentRunResponse,
    AgentSessionResponse,
    AgentToolAuthorizationResponse,
    AgentVersionSummaryResponse,
    AppendAgentEventRequest,
    CreateAgentRunRequest,
    CreateAgentToolCallRequest,
    CreateToolAuthorizationRequest,
    ToolAuthorizationDecisionRequest,
    AgentToolCallResponse,
)
from app.pskills.exceptions import SkillNotFoundError, SkillValidationError
from app.pskills.models import now_utc


DEFAULT_AGENT_SPECS: list[dict[str, Any]] = [
    {
        "key": "pskill.builder",
        "name": "PSkill Builder",
        "role": "builder",
        "goal": "将人类知识、多模态资料和专家经验构建为 PSkill draft。",
        "usage_keys": ["pskill.build.default"],
        "allowed_tools": ["psop.pskills.read", "psop.materials.read"],
        "allowed_skill_names": ["pskill-builder", "ffmpeg-video-processing", "document-ocr-processing"],
        "output_schema": {"name": "PSkillBuilderResult"},
    },
    {
        "key": "pskill.compiler",
        "name": "PSkill Compiler",
        "role": "compiler",
        "goal": "将 PSkill 编译为 formal-v5 Execution Graph。",
        "usage_keys": ["pskill.compile.formal_v5"],
        "allowed_tools": ["psop.pskills.read", "psop.compiler.validate_formal_v5"],
        "allowed_skill_names": ["pskill-compiler-formal-v5"],
        "output_schema": {"name": "PSkillCompilerResult"},
    },
    {
        "key": "pskill.tester",
        "name": "PSkill Tester",
        "role": "tester",
        "goal": "发布前测试 PSkill、执行图、交互、安全和回归。",
        "usage_keys": ["pskill.test.pre_publish"],
        "allowed_tools": ["psop.pskills.read", "psop.testing.write_diagnostics"],
        "allowed_skill_names": ["pskill-tester"],
        "output_schema": {"name": "PSkillTestResult"},
    },
    {
        "key": "pskill.runner",
        "name": "PSkill Runner",
        "role": "runner",
        "goal": "在 RuntimeService 主权边界内为运行节点生成 observation。",
        "usage_keys": ["pskill.run.node"],
        "allowed_tools": ["psop.runtime.read", "psop.run_events.write_low"],
        "allowed_skill_names": ["pskill-runner-field-assistant"],
        "output_schema": {"name": "RuntimeAgentObservation"},
    },
    {
        "key": "pskill.evaluator",
        "name": "PSkill Evaluator",
        "role": "evaluator",
        "goal": "评估已完成 Run，进行质量归因并给出优化建议。",
        "usage_keys": ["pskill.evaluate.run"],
        "allowed_tools": ["psop.runtime.read", "psop.evaluations.write_diagnostics"],
        "allowed_skill_names": ["pskill-run-evaluator"],
        "output_schema": {"name": "RunEvaluationResult"},
    },
    {
        "key": "psop.governance",
        "name": "PSOP Governance",
        "role": "governance",
        "goal": "将评估结果转为可验证、可审批、可回滚的系统改进提案。",
        "usage_keys": ["psop.governance.proposal"],
        "allowed_tools": [
            "psop.evaluations.read",
            "psop.governance.write_proposal",
            "psop.agent_version.activate",
            "psop.skill_version.activate",
        ],
        "allowed_skill_names": ["psop-governance-manager"],
        "output_schema": {"name": "GovernanceProposalResult"},
    },
]


class AgentService:
    def __init__(self, repository: AgentRepository | None = None) -> None:
        self.repository = repository or AgentRepository()

    def ensure_seed_data(self, session: Session) -> bool:
        changed = False
        for seed in DEFAULT_AGENT_SPECS:
            spec = self._seed_spec(seed)
            content_hash = self._hash_spec(spec)
            definition = self.repository.get_definition_by_key(session, str(seed["key"]))
            if not definition:
                definition = AgentDefinition(
                    key=str(seed["key"]),
                    name=str(seed["name"]),
                    role=str(seed["role"]),
                    description=str(seed["goal"]),
                    status="active",
                )
                session.add(definition)
                session.flush()
                changed = True
            for field in ("name", "role"):
                value = str(seed[field])
                if getattr(definition, field) != value:
                    setattr(definition, field, value)
                    changed = True
            if definition.description != str(seed["goal"]):
                definition.description = str(seed["goal"])
                changed = True

            version = self.repository.get_version_by_hash(
                session,
                definition_id=definition.id,
                content_hash=content_hash,
            )
            if not version:
                version_no = self.repository.next_version_no(session, definition.id)
                version = AgentVersion(
                    definition_id=definition.id,
                    version_no=version_no,
                    version_label=f"seed-v{version_no}",
                    status="published",
                    spec_json=spec,
                    content_hash=content_hash,
                    published_at=now_utc(),
                )
                session.add(version)
                session.flush()
                changed = True
            active_version = self.repository.get_version(session, definition.active_version_id)
            should_activate_seed = (
                not active_version
                or (
                    active_version.version_label.startswith("seed-v")
                    and active_version.content_hash != content_hash
                )
            )
            if should_activate_seed:
                definition.active_version_id = version.id
                changed = True

            for usage_key in seed["usage_keys"]:
                binding = self.repository.get_binding(session, str(usage_key))
                if not binding:
                    session.add(
                        AgentBinding(
                            usage_key=str(usage_key),
                            definition_id=definition.id,
                            active_version_id=version.id,
                        )
                    )
                    changed = True
        if changed:
            session.flush()
        return changed

    def list_definitions(self, session: Session) -> list[AgentDefinitionSummaryResponse]:
        if self.ensure_seed_data(session):
            session.commit()
        return [self._build_definition_summary(session, item) for item in self.repository.list_definitions(session)]

    def get_definition(self, session: Session, agent_key: str) -> AgentDefinitionDetailResponse:
        if self.ensure_seed_data(session):
            session.commit()
        definition = self.repository.get_definition_by_key(session, agent_key)
        if not definition:
            raise SkillNotFoundError("未找到 Agent。", details={"agent_key": agent_key})
        versions = self.repository.list_versions(session, definition.id)
        active_version = self.repository.get_version(session, definition.active_version_id)
        return AgentDefinitionDetailResponse(
            **self._build_definition_summary(session, definition).model_dump(),
            versions=[self._build_version_response(item) for item in versions],
            active_version=self._build_version_response(active_version) if active_version else None,
        )

    def list_versions(self, session: Session, agent_key: str) -> list[AgentVersionSummaryResponse]:
        if self.ensure_seed_data(session):
            session.commit()
        definition = self.repository.get_definition_by_key(session, agent_key)
        if not definition:
            raise SkillNotFoundError("未找到 Agent。", details={"agent_key": agent_key})
        return [self._build_version_response(item) for item in self.repository.list_versions(session, definition.id)]

    def create_run(
        self,
        session: Session,
        payload: CreateAgentRunRequest,
        *,
        commit: bool = True,
    ) -> AgentRunResponse:
        if self.ensure_seed_data(session):
            session.flush()
        definition = self.repository.get_definition_by_key(session, payload.agent_key)
        if not definition:
            raise SkillNotFoundError("未找到 Agent。", details={"agent_key": payload.agent_key})
        agent_run = AgentRun(
            definition_id=definition.id,
            agent_version_id=definition.active_version_id,
            agent_session_id=None,
            agent_key=definition.key,
            status="queued",
            owner_type=payload.owner_type,
            owner_id=payload.owner_id,
            run_id=payload.run_id,
            input_payload=payload.input_payload,
        )
        session.add(agent_run)
        session.flush()
        agent_session = self._ensure_session_for_run(session, agent_run)
        agent_run.agent_session_id = agent_session.id
        self.append_event(
            session,
            agent_run.id,
            AppendAgentEventRequest(
                event_type="agent.run.created",
                phase="created",
                payload={"agent_key": definition.key, "owner_type": payload.owner_type, "owner_id": payload.owner_id},
            ),
            commit=False,
        )
        if commit:
            session.commit()
        return self._build_run_response(agent_run)

    def list_runs(
        self,
        session: Session,
        *,
        agent_key: str | None = None,
        status: str | None = None,
        owner_type: str | None = None,
        owner_id: str | None = None,
    ) -> list[AgentRunResponse]:
        if self.ensure_seed_data(session):
            session.commit()
        return [
            self._build_run_response(item)
            for item in self.repository.list_runs(
                session,
                agent_key=agent_key,
                status=status,
                owner_type=owner_type,
                owner_id=owner_id,
            )
        ]

    def get_run(self, session: Session, agent_run_id: str) -> AgentRunResponse:
        agent_run = self._get_run(session, agent_run_id)
        return self._build_run_response(agent_run)

    def get_run_model(self, session: Session, agent_run_id: str) -> AgentRun:
        return self._get_run(session, agent_run_id)

    def append_event(
        self,
        session: Session,
        agent_run_id: str,
        payload: AppendAgentEventRequest,
        *,
        commit: bool = True,
    ) -> AgentEventResponse:
        agent_run = self._get_run(session, agent_run_id)
        event = AgentEvent(
            agent_run_id=agent_run.id,
            seq_no=self.repository.next_event_seq(session, agent_run.id),
            event_type=payload.event_type,
            phase=payload.phase,
            payload=payload.payload,
        )
        session.add(event)
        session.flush()
        if commit:
            session.commit()
        return self._build_event_response(event)

    def list_events(self, session: Session, agent_run_id: str) -> list[AgentEventResponse]:
        self._get_run(session, agent_run_id)
        return [self._build_event_response(item) for item in self.repository.list_events(session, agent_run_id)]

    def list_model_calls(self, session: Session, agent_run_id: str) -> list[AgentModelCallResponse]:
        self._get_run(session, agent_run_id)
        return [self._build_model_call_response(item) for item in self.repository.list_model_calls(session, agent_run_id)]

    def record_model_call(
        self,
        session: Session,
        *,
        agent_run_id: str,
        provider: str,
        route_key: str,
        model_name: str,
        status: str,
        request_payload: dict[str, Any],
        response_payload: dict[str, Any],
        usage_json: dict[str, Any] | None = None,
        error_message: str = "",
        commit: bool = True,
    ) -> AgentModelCallResponse:
        agent_run = self._get_run(session, agent_run_id)
        now = now_utc()
        model_call = AgentModelCall(
            agent_run_id=agent_run.id,
            provider=provider,
            route_key=route_key,
            model_name=model_name,
            status=status,
            request_payload=request_payload,
            response_payload=response_payload,
            usage_json=usage_json or {},
            error_message=error_message,
            started_at=now,
            ended_at=now,
        )
        session.add(model_call)
        session.flush()
        if commit:
            session.commit()
        return self._build_model_call_response(model_call)

    def create_tool_call(
        self,
        session: Session,
        agent_run_id: str,
        payload: CreateAgentToolCallRequest,
        *,
        commit: bool = True,
    ) -> AgentToolCallResponse:
        agent_run = self._get_run(session, agent_run_id)
        tool_call = AgentToolCall(
            agent_run_id=agent_run.id,
            tool_name=payload.tool_name,
            tool_provider=payload.tool_provider,
            status="planned",
            arguments_summary=payload.arguments_summary,
            side_effect_level=payload.side_effect_level,
            idempotency_key=payload.idempotency_key,
        )
        session.add(tool_call)
        session.flush()
        self.append_event(
            session,
            agent_run.id,
            AppendAgentEventRequest(
                event_type="agent.tool_call.planned",
                phase="tool",
                payload={"tool_call_id": tool_call.id, "tool_name": tool_call.tool_name},
            ),
            commit=False,
        )
        if commit:
            session.commit()
        return self._build_tool_call_response(tool_call)

    def list_tool_calls(self, session: Session, agent_run_id: str) -> list[AgentToolCallResponse]:
        self._get_run(session, agent_run_id)
        return [self._build_tool_call_response(item) for item in self.repository.list_tool_calls(session, agent_run_id)]

    def create_tool_authorization(
        self,
        session: Session,
        payload: CreateToolAuthorizationRequest,
    ) -> AgentToolAuthorizationResponse:
        agent_run = self._get_run(session, payload.agent_run_id)
        tool_call = self.repository.get_tool_call(session, payload.agent_tool_call_id)
        if payload.agent_tool_call_id and not tool_call:
            raise SkillNotFoundError("未找到 Agent Tool Call。", details={"agent_tool_call_id": payload.agent_tool_call_id})
        if tool_call and tool_call.agent_run_id != agent_run.id:
            raise SkillValidationError(
                "Agent Tool Call 不属于当前 AgentRun。",
                details={"agent_run_id": agent_run.id, "agent_tool_call_id": tool_call.id},
            )
        authorization = AgentToolAuthorization(
            agent_run_id=agent_run.id,
            agent_tool_call_id=payload.agent_tool_call_id,
            run_id=payload.run_id,
            run_event_id=payload.run_event_id,
            tool_name=payload.tool_name,
            tool_provider=payload.tool_provider,
            mcp_server_name=payload.mcp_server_name,
            side_effect_level=payload.side_effect_level,
            risk_level=payload.risk_level,
            authorization_reason=payload.authorization_reason,
            tool_arguments_summary=payload.tool_arguments_summary,
            expected_effect_summary=payload.expected_effect_summary,
            reversible=payload.reversible,
            idempotency_key=payload.idempotency_key,
            status="pending",
            request_payload=payload.request_payload,
        )
        session.add(authorization)
        if tool_call:
            tool_call.status = "waiting_authorization"
        agent_run.status = "waiting_tool_authorization"
        session.flush()
        self.append_event(
            session,
            agent_run.id,
            AppendAgentEventRequest(
                event_type="agent.waiting_tool_authorization",
                phase="tool_authorization",
                payload={"authorization_id": authorization.id, "tool_name": authorization.tool_name},
            ),
            commit=False,
        )
        session.commit()
        return self._build_tool_authorization_response(authorization)

    def list_tool_authorizations(
        self,
        session: Session,
        *,
        agent_run_id: str | None = None,
        status: str | None = None,
    ) -> list[AgentToolAuthorizationResponse]:
        return [
            self._build_tool_authorization_response(item)
            for item in self.repository.list_tool_authorizations(session, agent_run_id=agent_run_id, status=status)
        ]

    def get_tool_authorization(self, session: Session, authorization_id: str) -> AgentToolAuthorizationResponse:
        authorization = self._get_tool_authorization(session, authorization_id)
        return self._build_tool_authorization_response(authorization)

    def approve_tool_authorization(
        self,
        session: Session,
        authorization_id: str,
        payload: ToolAuthorizationDecisionRequest,
    ) -> AgentToolAuthorizationResponse:
        authorization = self._get_pending_tool_authorization(session, authorization_id)
        authorization.status = "approved"
        authorization.response_payload = payload.response_payload
        authorization.responded_at = now_utc()
        agent_run = self._get_run(session, authorization.agent_run_id)
        agent_run.status = "queued"
        tool_call = self.repository.get_tool_call(session, authorization.agent_tool_call_id)
        if tool_call:
            tool_call.status = "authorized"
        self.append_event(
            session,
            agent_run.id,
            AppendAgentEventRequest(
                event_type="agent.resumed_after_tool_authorization",
                phase="tool_authorization",
                payload={"authorization_id": authorization.id, "decision": "approved"},
            ),
            commit=False,
        )
        session.commit()
        return self._build_tool_authorization_response(authorization)

    def reject_tool_authorization(
        self,
        session: Session,
        authorization_id: str,
        payload: ToolAuthorizationDecisionRequest,
    ) -> AgentToolAuthorizationResponse:
        authorization = self._get_pending_tool_authorization(session, authorization_id)
        authorization.status = "rejected"
        authorization.response_payload = payload.response_payload
        authorization.responded_at = now_utc()
        agent_run = self._get_run(session, authorization.agent_run_id)
        agent_run.status = "failed"
        agent_run.error_message = "tool_authorization_denied"
        agent_run.ended_at = now_utc()
        tool_call = self.repository.get_tool_call(session, authorization.agent_tool_call_id)
        if tool_call:
            tool_call.status = "denied"
        self.append_event(
            session,
            agent_run.id,
            AppendAgentEventRequest(
                event_type="agent.failed_tool_authorization_denied",
                phase="tool_authorization",
                payload={"authorization_id": authorization.id, "decision": "rejected"},
            ),
            commit=False,
        )
        session.commit()
        return self._build_tool_authorization_response(authorization)

    def _get_run(self, session: Session, agent_run_id: str) -> AgentRun:
        agent_run = self.repository.get_run(session, agent_run_id)
        if not agent_run:
            raise SkillNotFoundError("未找到 AgentRun。", details={"agent_run_id": agent_run_id})
        return agent_run

    def _get_tool_authorization(self, session: Session, authorization_id: str) -> AgentToolAuthorization:
        authorization = self.repository.get_tool_authorization(session, authorization_id)
        if not authorization:
            raise SkillNotFoundError("未找到工具授权请求。", details={"authorization_id": authorization_id})
        return authorization

    def _get_pending_tool_authorization(self, session: Session, authorization_id: str) -> AgentToolAuthorization:
        authorization = self._get_tool_authorization(session, authorization_id)
        if authorization.status != "pending":
            raise SkillValidationError(
                "工具授权请求已处理，不能重复响应。",
                details={"authorization_id": authorization_id, "status": authorization.status},
            )
        return authorization

    def _ensure_session_for_run(self, session: Session, agent_run: AgentRun) -> AgentSession:
        agent_session = self.repository.get_session(session, agent_run.agent_session_id)
        if agent_session:
            return agent_session
        agent_session = self.repository.get_session_by_owner(
            session,
            agent_key=agent_run.agent_key,
            owner_type=agent_run.owner_type,
            owner_id=agent_run.owner_id,
        )
        if not agent_session:
            agent_session = AgentSession(
                definition_id=agent_run.definition_id,
                agent_key=agent_run.agent_key,
                owner_type=agent_run.owner_type,
                owner_id=agent_run.owner_id,
                status="active",
                summary_json={},
            )
            session.add(agent_session)
            session.flush()
        return agent_session

    def _build_definition_summary(self, session: Session, definition: AgentDefinition) -> AgentDefinitionSummaryResponse:
        versions = self.repository.list_versions(session, definition.id)
        active_version = self.repository.get_version(session, definition.active_version_id)
        return AgentDefinitionSummaryResponse(
            id=definition.id,
            key=definition.key,
            name=definition.name,
            role=definition.role,
            description=definition.description,
            status=definition.status,
            active_version_id=definition.active_version_id,
            active_version_label=active_version.version_label if active_version else None,
            version_count=len(versions),
            bindings=[
                self._build_binding_response(item)
                for item in self.repository.list_bindings_for_definition(session, definition.id)
            ],
            created_at=definition.created_at,
            updated_at=definition.updated_at,
        )

    @staticmethod
    def _build_version_response(version: AgentVersion) -> AgentVersionSummaryResponse:
        return AgentVersionSummaryResponse(
            id=version.id,
            definition_id=version.definition_id,
            version_no=version.version_no,
            version_label=version.version_label,
            status=version.status,
            spec_json=version.spec_json,
            content_hash=version.content_hash,
            published_at=version.published_at,
            created_at=version.created_at,
            updated_at=version.updated_at,
        )

    @staticmethod
    def _build_binding_response(binding: AgentBinding) -> AgentBindingResponse:
        return AgentBindingResponse(
            id=binding.id,
            usage_key=binding.usage_key,
            definition_id=binding.definition_id,
            active_version_id=binding.active_version_id,
            created_at=binding.created_at,
            updated_at=binding.updated_at,
        )

    @staticmethod
    def _build_run_response(agent_run: AgentRun) -> AgentRunResponse:
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
    def _build_session_response(agent_session: AgentSession) -> AgentSessionResponse:
        return AgentSessionResponse(
            id=agent_session.id,
            definition_id=agent_session.definition_id,
            agent_key=agent_session.agent_key,
            owner_type=agent_session.owner_type,
            owner_id=agent_session.owner_id,
            status=agent_session.status,
            summary_json=agent_session.summary_json,
            created_at=agent_session.created_at,
            updated_at=agent_session.updated_at,
        )

    @staticmethod
    def _build_event_response(event: AgentEvent) -> AgentEventResponse:
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
    def _build_model_call_response(model_call: AgentModelCall) -> AgentModelCallResponse:
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
    def _build_tool_call_response(tool_call: AgentToolCall) -> AgentToolCallResponse:
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
    def _build_tool_authorization_response(authorization: AgentToolAuthorization) -> AgentToolAuthorizationResponse:
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
            request_payload=authorization.request_payload,
            response_payload=authorization.response_payload,
            created_at=authorization.created_at,
            responded_at=authorization.responded_at,
            executed_at=authorization.executed_at,
        )

    @staticmethod
    def _seed_spec(seed: dict[str, Any]) -> dict[str, Any]:
        return {
            "key": seed["key"],
            "name": seed["name"],
            "role": seed["role"],
            "goal": seed["goal"],
            "instructions": {},
            "model_policy": {"route_key": "text"},
            "runtime_policy": {},
            "allowed_tools": seed.get("allowed_tools", []),
            "allowed_skill_names": seed.get("allowed_skill_names", []),
            "memory_policy": {},
            "planner_policy": {},
            "sandbox_policy": {},
            "guardrail_policy": {},
            "output_schema": seed["output_schema"],
        }

    @staticmethod
    def _hash_spec(spec: dict[str, Any]) -> str:
        encoded = json.dumps(spec, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()
