from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TYPE_CHECKING

from sqlalchemy.orm import Session

from app.agents.schemas import AgentToolCallResponse, CreateAgentToolCallRequest
from app.pskills.exceptions import SkillValidationError

if TYPE_CHECKING:
    from app.agent_harness.agent_decision import AgentDecision
    from app.agents.models import AgentRun
    from app.agents.service import AgentService


AUTH_REQUIRED_LEVELS = {"high_write", "external_action", "physical_action"}


DEFAULT_TOOL_SIDE_EFFECTS: dict[str, str] = {
    "psop.pskills.get": "read",
    "psop.pskills.read": "read",
    "psop.materials.list": "read",
    "psop.materials.read_analysis": "read",
    "psop.repository.read_file": "read",
    "psop.repository.propose_patch": "low_write",
    "psop.pskill_manifest.parse": "compute",
    "psop.pskill_manifest.render": "compute",
    "psop.compiler.validate_formal_v5": "compute",
    "psop.testing.write_diagnostics": "low_write",
    "psop.runtime.read": "read",
    "psop.evaluations.read": "read",
    "psop.evaluations.write_diagnostics": "low_write",
    "psop.governance.write_proposal": "low_write",
    "psop.memory.search": "read",
    "psop.memory.write_candidate": "low_write",
    "psop.media.compute": "compute",
    "psop.document.compute": "compute",
    "psop.repository.commit_patch": "high_write",
    "psop.agent_version.activate": "high_write",
    "psop.skill_version.activate": "high_write",
}


NATIVE_TOOL_EXECUTORS: set[str] = {
    "psop.pskills.get",
    "psop.pskills.read",
    "psop.materials.list",
    "psop.materials.read_analysis",
    "psop.repository.read_file",
    "psop.repository.propose_patch",
    "psop.pskill_manifest.parse",
    "psop.pskill_manifest.render",
    "psop.compiler.validate_formal_v5",
    "psop.testing.write_diagnostics",
    "psop.runtime.read",
    "psop.evaluations.read",
    "psop.evaluations.write_diagnostics",
    "psop.governance.write_proposal",
    "psop.memory.search",
    "psop.memory.write_candidate",
    "psop.agent_version.activate",
    "psop.skill_version.activate",
}


@dataclass(frozen=True)
class ToolPolicyDecision:
    allowed: bool
    side_effect_level: str
    requires_authorization: bool
    reason: str


class ToolPolicy:
    def __init__(self, side_effects: dict[str, str] | None = None) -> None:
        self.side_effects = side_effects or DEFAULT_TOOL_SIDE_EFFECTS

    @property
    def allowed_tools(self) -> set[str]:
        return set(self.side_effects)

    def check(
        self,
        *,
        tool_name: str,
        tool_provider: str,
        requested_side_effect_level: str | None,
        effective_allowed_tools: set[str],
    ) -> ToolPolicyDecision:
        if tool_name not in self.allowed_tools:
            return ToolPolicyDecision(
                allowed=False,
                side_effect_level=requested_side_effect_level or "unknown",
                requires_authorization=False,
                reason="tool_not_registered",
            )
        if tool_name not in effective_allowed_tools:
            return ToolPolicyDecision(
                allowed=False,
                side_effect_level=requested_side_effect_level or self.side_effects[tool_name],
                requires_authorization=False,
                reason="tool_not_allowed_by_agent_or_skill",
            )
        side_effect_level = requested_side_effect_level or self.side_effects[tool_name]
        requires_authorization = side_effect_level in AUTH_REQUIRED_LEVELS or tool_provider == "mcp"
        return ToolPolicyDecision(
            allowed=True,
            side_effect_level=side_effect_level,
            requires_authorization=requires_authorization,
            reason="requires_authorization" if requires_authorization else "auto_allowed",
        )


@dataclass(frozen=True)
class AgentToolCallPlan:
    tool_call: AgentToolCallResponse
    policy_decision: ToolPolicyDecision
    effective_allowed_tools: set[str]


class AgentToolHarness:
    def __init__(self, tool_policy: ToolPolicy | None = None) -> None:
        self.tool_policy = tool_policy or ToolPolicy()

    def prepare_tool_call(
        self,
        session: Session,
        *,
        agent_service: "AgentService",
        agent_run: "AgentRun",
        spec: dict[str, Any],
        decision: "AgentDecision",
        active_tools: set[str],
    ) -> AgentToolCallPlan:
        if not decision.tool_name:
            raise SkillValidationError("tool_call decision 缺少 tool_name。", details={"agent_run_id": agent_run.id})
        effective_allowed_tools = self.effective_allowed_tools(spec=spec, active_tools=active_tools)
        policy_decision = self.tool_policy.check(
            tool_name=decision.tool_name,
            tool_provider=decision.tool_provider,
            requested_side_effect_level=decision.side_effect_level,
            effective_allowed_tools=effective_allowed_tools,
        )
        tool_call = agent_service.create_tool_call(
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
        return AgentToolCallPlan(
            tool_call=tool_call,
            policy_decision=policy_decision,
            effective_allowed_tools=effective_allowed_tools,
        )

    def effective_allowed_tools(self, *, spec: dict[str, Any], active_tools: set[str]) -> set[str]:
        agent_allowed_tools = {str(tool) for tool in spec.get("allowed_tools") or []}
        return agent_allowed_tools & active_tools & self.tool_policy.allowed_tools
