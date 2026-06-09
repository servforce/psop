from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.agent_harness.tools import AUTH_REQUIRED_LEVELS, DEFAULT_TOOL_SIDE_EFFECTS, NATIVE_TOOL_EXECUTORS, ToolPolicy
from app.agents.models import AgentToolCall
from app.agents.schemas import AgentToolCallResponse
from app.agents.service import AgentService
from app.pskills.exceptions import SkillNotFoundError, SkillValidationError
from app.pskills.models import now_utc
from app.skills.repository import SkillPackageRepository
from app.skills.service import SkillPackageService
from app.tools.models import ToolDefinition
from app.tools.repository import ToolRepository
from app.tools.schemas import ToolDefinitionResponse, ToolTestRequest, ToolTestResponse


VALID_SIDE_EFFECT_LEVELS = {"read", "compute", "low_write", "high_write", "external_action", "physical_action"}
VALID_TOOL_STATUSES = {"active", "deprecated", "disabled"}

DEFAULT_TOOL_DESCRIPTIONS: dict[str, str] = {
    "psop.pskills.get": "读取 PSkill 定义、版本和 source 摘要。",
    "psop.pskills.read": "读取 PSkill 定义、版本和 source 摘要。",
    "psop.materials.list": "列出 PSkill materials。",
    "psop.materials.read_analysis": "读取 PSkill material 分析结果。",
    "psop.repository.read_file": "读取 PSkill source 仓库文件。",
    "psop.repository.propose_patch": "生成可 review 的 PSkill draft patch。",
    "psop.pskill_manifest.parse": "解析 pskill.yaml manifest。",
    "psop.pskill_manifest.render": "渲染 pskill.yaml manifest。",
    "psop.compiler.validate_formal_v5": "校验 EG Compile Artifact 是否符合 formal-v5。",
    "psop.testing.write_diagnostics": "写入测试诊断和发布门禁内部记录。",
    "psop.runtime.read": "读取 Runtime Run、Session Token snapshot、RunEvent 和 RunTrace。",
    "psop.evaluations.read": "读取 RunEvaluation 和 finding 归因结果。",
    "psop.evaluations.write_diagnostics": "写入评估诊断和候选 finding。",
    "psop.governance.write_proposal": "写入治理提案和实验业务记录。",
    "psop.memory.search": "检索 Agent 记忆、领域知识和历史经验。",
    "psop.memory.write_candidate": "写入待审核 Agent memory candidate。",
    "psop.media.compute": "执行本地媒体处理和摘要计算。",
    "psop.document.compute": "执行本文档处理、OCR 或结构化摘要计算。",
    "psop.repository.commit_patch": "向 Git 仓库提交 patch。",
    "psop.agent_version.activate": "激活生产 AgentVersion。",
    "psop.skill_version.activate": "激活生产 SkillVersion。",
}


class ToolService:
    def __init__(
        self,
        *,
        repository: ToolRepository | None = None,
        agent_service: AgentService | None = None,
        skill_service: SkillPackageService | None = None,
        skill_repository: SkillPackageRepository | None = None,
    ) -> None:
        self.repository = repository or ToolRepository()
        self.agent_service = agent_service or AgentService()
        self.skill_service = skill_service or SkillPackageService(repository=skill_repository)
        self.skill_repository = self.skill_service.repository
        self.tool_policy = ToolPolicy()

    def ensure_seed_data(self, session: Session) -> bool:
        changed = False
        for tool_name, side_effect_level in DEFAULT_TOOL_SIDE_EFFECTS.items():
            requires_authorization = side_effect_level in AUTH_REQUIRED_LEVELS
            tool = self.repository.get_tool_by_name(session, tool_name)
            tool_changed = False
            if not tool:
                session.add(
                    ToolDefinition(
                        name=tool_name,
                        provider="native",
                        side_effect_level=side_effect_level,
                        requires_authorization=requires_authorization,
                        description=DEFAULT_TOOL_DESCRIPTIONS.get(tool_name, tool_name),
                        input_schema_json={},
                        output_schema_json={},
                        metadata_json={"seed_source": "ToolPolicy.DEFAULT_TOOL_SIDE_EFFECTS"},
                        status="active",
                    )
                )
                changed = True
                continue
            if tool.provider != "native":
                tool.provider = "native"
                tool_changed = True
            if tool.side_effect_level != side_effect_level:
                tool.side_effect_level = side_effect_level
                tool_changed = True
            if tool.requires_authorization != requires_authorization:
                tool.requires_authorization = requires_authorization
                tool_changed = True
            description = DEFAULT_TOOL_DESCRIPTIONS.get(tool_name, tool.description)
            if tool.description != description:
                tool.description = description
                tool_changed = True
            if tool.status != "active":
                tool.status = "active"
                tool_changed = True
            if "seed_source" not in (tool.metadata_json or {}):
                tool.metadata_json = {**(tool.metadata_json or {}), "seed_source": "ToolPolicy.DEFAULT_TOOL_SIDE_EFFECTS"}
                tool_changed = True
            if tool_changed:
                tool.updated_at = now_utc()
                changed = True
        if changed:
            session.flush()
        return changed

    def list_tools(
        self,
        session: Session,
        *,
        side_effect_level: str | None = None,
        requires_authorization: bool | None = None,
        status: str | None = None,
    ) -> list[ToolDefinitionResponse]:
        changed = self.ensure_seed_data(session)
        changed = self.agent_service.ensure_seed_data(session) or changed
        if changed:
            session.commit()
        self.skill_service.sync_packages(session)
        self._validate_optional_filter("side_effect_level", side_effect_level, VALID_SIDE_EFFECT_LEVELS)
        self._validate_optional_filter("status", status, VALID_TOOL_STATUSES)
        return [
            self._build_tool_response(session, item)
            for item in self.repository.list_tools(
                session,
                side_effect_level=self._normalize_optional(side_effect_level),
                requires_authorization=requires_authorization,
                status=self._normalize_optional(status),
            )
        ]

    def get_tool(self, session: Session, tool_name: str) -> ToolDefinitionResponse:
        changed = self.ensure_seed_data(session)
        changed = self.agent_service.ensure_seed_data(session) or changed
        if changed:
            session.commit()
        self.skill_service.sync_packages(session)
        tool = self.repository.get_tool_by_name(session, tool_name)
        if not tool:
            raise SkillNotFoundError("未找到工具定义。", details={"tool_name": tool_name})
        return self._build_tool_response(session, tool)

    def test_tool(self, session: Session, tool_name: str, payload: ToolTestRequest) -> ToolTestResponse:
        changed = self.ensure_seed_data(session)
        changed = self.agent_service.ensure_seed_data(session) or changed
        if changed:
            session.commit()
        self.skill_service.sync_packages(session)
        tool = self.repository.get_tool_by_name(session, tool_name)
        if not tool:
            raise SkillNotFoundError("未找到工具定义。", details={"tool_name": tool_name})
        if payload.requested_side_effect_level:
            self._validate_optional_filter(
                "requested_side_effect_level",
                payload.requested_side_effect_level,
                VALID_SIDE_EFFECT_LEVELS,
            )

        effective_allowed_tools = set(DEFAULT_TOOL_SIDE_EFFECTS)
        agent_key = self._normalize_optional(payload.agent_key)
        if agent_key:
            effective_allowed_tools = self._effective_allowed_tools_for_agent(session, agent_key)
        policy_decision = self.tool_policy.check(
            tool_name=tool.name,
            tool_provider=tool.provider,
            requested_side_effect_level=self._normalize_optional(payload.requested_side_effect_level),
            effective_allowed_tools=effective_allowed_tools,
        )
        native_implemented = tool.name in NATIVE_TOOL_EXECUTORS
        executable = (
            tool.status == "active"
            and policy_decision.allowed
            and not policy_decision.requires_authorization
            and native_implemented
            and policy_decision.side_effect_level in {"read", "compute"}
        )
        policy_reason = self._tool_test_policy_reason(
            tool,
            policy_decision.reason,
            executable=executable,
            native_implemented=native_implemented,
        )
        return ToolTestResponse(
            tool_name=tool.name,
            executable=executable,
            dry_run=True,
            side_effect_level=policy_decision.side_effect_level,
            requires_authorization=policy_decision.requires_authorization,
            policy_reason=policy_reason,
            input_echo=dict(payload.arguments_summary or {}),
            output_preview=self._build_tool_test_output_preview(
                tool,
                executable=executable,
                arguments_summary=payload.arguments_summary or {},
            ),
            policy_decision={
                "allowed": policy_decision.allowed,
                "reason": policy_decision.reason,
                "agent_key": agent_key or None,
                "console_test_supported_levels": ["read", "compute"],
                "dry_run_only": True,
                "native_implemented": native_implemented,
            },
        )

    def list_recent_tool_calls(self, session: Session, tool_name: str, *, limit: int = 10) -> list[AgentToolCallResponse]:
        changed = self.ensure_seed_data(session)
        changed = self.agent_service.ensure_seed_data(session) or changed
        if changed:
            session.commit()
        tool = self.repository.get_tool_by_name(session, tool_name)
        if not tool:
            raise SkillNotFoundError("未找到工具定义。", details={"tool_name": tool_name})
        return [
            self._build_tool_call_response(item)
            for item in self.repository.list_recent_tool_calls(session, tool.name, limit=limit)
        ]

    def _build_tool_response(self, session: Session, tool: ToolDefinition) -> ToolDefinitionResponse:
        recent_call_count = self.repository.count_tool_calls(session, tool.name)
        failed_call_count = self.repository.count_failed_tool_calls(session, tool.name)
        failure_rate = round(failed_call_count / recent_call_count, 4) if recent_call_count else 0.0
        policy_decision = self.tool_policy.check(
            tool_name=tool.name,
            tool_provider=tool.provider,
            requested_side_effect_level=tool.side_effect_level,
            effective_allowed_tools=set(DEFAULT_TOOL_SIDE_EFFECTS),
        )
        native_implemented = tool.name in NATIVE_TOOL_EXECUTORS
        policy_reason = self._tool_definition_policy_reason(
            tool,
            policy_decision.reason,
            native_implemented=native_implemented,
        )
        return ToolDefinitionResponse(
            id=tool.id,
            name=tool.name,
            provider=tool.provider,
            side_effect_level=tool.side_effect_level,
            requires_authorization=tool.requires_authorization,
            description=tool.description,
            input_schema=tool.input_schema_json,
            output_schema=tool.output_schema_json,
            metadata=tool.metadata_json,
            status=tool.status,
            allowed_agent_keys=self._allowed_agent_keys(session, tool.name),
            recent_call_count=recent_call_count,
            failed_call_count=failed_call_count,
            failure_rate=failure_rate,
            policy_summary={
                "registered": True,
                "native_implemented": native_implemented,
                "auto_executable": not tool.requires_authorization and native_implemented,
                "policy_reason": policy_reason,
                "policy_decision": {
                    "allowed": policy_decision.allowed,
                    "reason": policy_decision.reason,
                    "side_effect_level": policy_decision.side_effect_level,
                    "requires_authorization": policy_decision.requires_authorization,
                },
                "auth_required_levels": sorted(AUTH_REQUIRED_LEVELS),
                "permission_rule": "AgentSpec.allowed_tools ∩ SkillPackage.allowed_tools ∩ ToolPolicy.allowed_tools",
            },
            created_at=tool.created_at,
            updated_at=tool.updated_at,
        )

    def _effective_allowed_tools_for_agent(self, session: Session, agent_key: str) -> set[str]:
        definition = self.agent_service.repository.get_definition_by_key(session, agent_key)
        if not definition:
            raise SkillNotFoundError("未找到 Agent。", details={"agent_key": agent_key})
        version = self.agent_service.repository.get_version(session, definition.active_version_id)
        if not version:
            return set()
        spec = version.spec_json if isinstance(version.spec_json, dict) else {}
        agent_allowed_tools = set(str(item) for item in spec.get("allowed_tools") or [])
        skill_allowed_tools, _ = self.skill_service.active_skill_allowed_tools_for_agent(
            session,
            agent_key=agent_key,
            sync=False,
        )
        return agent_allowed_tools & skill_allowed_tools

    @staticmethod
    def _tool_test_policy_reason(
        tool: ToolDefinition,
        policy_reason: str,
        *,
        executable: bool,
        native_implemented: bool,
    ) -> str:
        if executable:
            return "console_test_allowed"
        if tool.status != "active":
            return "tool_not_active"
        if policy_reason == "requires_authorization":
            return "requires_tool_authorization"
        if tool.side_effect_level not in {"read", "compute"}:
            return "unsupported_side_effect_for_console_test"
        if not native_implemented:
            return "native_tool_not_implemented"
        return policy_reason

    @staticmethod
    def _tool_definition_policy_reason(
        tool: ToolDefinition,
        policy_reason: str,
        *,
        native_implemented: bool,
    ) -> str:
        if tool.status != "active":
            return "tool_not_active"
        if policy_reason == "requires_authorization":
            return "requires_tool_authorization"
        if not native_implemented:
            return "native_tool_not_implemented"
        return policy_reason

    @staticmethod
    def _build_tool_test_output_preview(
        tool: ToolDefinition,
        *,
        executable: bool,
        arguments_summary: dict[str, Any],
    ) -> dict[str, Any]:
        if not executable:
            return {
                "status": "not_executed",
                "message": "Console tool test is dry-run only and did not execute this tool.",
            }
        return {
            "status": "dry_run_succeeded",
            "tool_name": tool.name,
            "provider": tool.provider,
            "accepted_argument_keys": sorted(str(key) for key in arguments_summary),
        }

    def _allowed_agent_keys(self, session: Session, tool_name: str) -> list[str]:
        keys: list[str] = []
        for definition in self.repository.list_agent_definitions(session):
            version = self.agent_service.repository.get_version(session, definition.active_version_id)
            if not version:
                continue
            spec = version.spec_json if isinstance(version.spec_json, dict) else {}
            agent_allowed_tools = set(str(item) for item in spec.get("allowed_tools") or [])
            skill_allowed_tools, _ = self.skill_service.active_skill_allowed_tools_for_agent(
                session,
                agent_key=definition.key,
                sync=False,
            )
            if tool_name in agent_allowed_tools and tool_name in skill_allowed_tools:
                keys.append(definition.key)
        return keys

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
    def _validate_optional_filter(name: str, value: str | None, allowed: set[str]) -> None:
        if value is not None and value.strip() and value.strip() not in allowed:
            raise SkillValidationError(f"{name} filter 无效。", details={name: value})

    @staticmethod
    def _normalize_optional(value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None
