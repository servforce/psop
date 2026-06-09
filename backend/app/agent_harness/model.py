from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from app.agent_harness.agent_decision import AgentDecision
from app.gateway.inference import LlmInferenceGateway, TEXT_ROUTE_KEY
from app.pskills.exceptions import SkillValidationError


@dataclass(frozen=True)
class AgentModelDecisionResult:
    decision: AgentDecision
    provider: str
    route_key: str
    model_name: str
    request_payload: dict[str, Any]
    response_payload: dict[str, Any]
    usage_json: dict[str, Any]


class AgentModelClient:
    """Turn an AgentRun context into an AgentDecision through the LLM gateway."""

    def __init__(self, inference_gateway: LlmInferenceGateway | None = None) -> None:
        self.inference_gateway = inference_gateway

    def complete_decision(
        self,
        *,
        agent_key: str,
        spec: dict[str, Any],
        input_payload: dict[str, Any],
        active_skill_names: list[str],
        skill_context: list[dict[str, Any]],
        memory_context: list[dict[str, Any]],
        plan_payload: dict[str, Any],
        allowed_tools: list[str] | None = None,
        system_prompt: str | None = None,
        agent_prompt: dict[str, Any] | None = None,
        route_key: str | None = None,
    ) -> AgentModelDecisionResult:
        if not self.inference_gateway:
            raise SkillValidationError("AgentRunner 未配置 LLM Inference Gateway，无法执行 LLM AgentDecision。")

        resolved_route_key = route_key or self._route_key(spec)
        prompt_metadata = dict(agent_prompt or {})
        prompt_payload = {
            "agent_key": agent_key,
            "goal": spec.get("goal") or "",
            "role": spec.get("role") or "",
            "input_payload": input_payload,
            "active_skill_names": active_skill_names,
            "skill_context": skill_context,
            "allowed_tools": list(allowed_tools if allowed_tools is not None else spec.get("allowed_tools") or []),
            "memory_context": memory_context,
            "plan": plan_payload,
            "output_schema": spec.get("output_schema") or {},
            "agent_prompt": prompt_metadata,
        }
        system_prompt = self._system_prompt(agent_key=agent_key, spec=spec, prompt_system_prompt=system_prompt)
        user_prompt = json.dumps(prompt_payload, ensure_ascii=False, sort_keys=True)
        completion = self.inference_gateway.complete(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            route_key=resolved_route_key,
        )
        parsed = self._parse_json_object(completion.content)
        decision = self._decision_from_parsed(parsed)
        response_payload = {
            **decision.model_dump(mode="json"),
            "parsed": parsed,
            "raw_content": completion.content,
        }
        return AgentModelDecisionResult(
            decision=decision,
            provider=str(getattr(completion, "provider", "") or "llm_inference_gateway"),
            route_key=resolved_route_key,
            model_name=str(getattr(completion, "model", "") or ""),
            request_payload={
                "mode": "llm",
                "agent_key": agent_key,
                "agent_prompt": prompt_metadata,
                "prompt_payload": prompt_payload,
                "gateway_request": dict(getattr(completion, "request", {}) or {}),
            },
            response_payload=response_payload,
            usage_json=dict(getattr(completion, "usage", {}) or {}),
        )

    @staticmethod
    def uses_llm_model(spec: dict[str, Any]) -> bool:
        policy = spec.get("model_policy")
        if not isinstance(policy, dict):
            return False
        mode = str(policy.get("mode") or "").strip().lower()
        provider = str(policy.get("provider") or "").strip().lower()
        return mode in {"llm", "gateway", "llm_inference_gateway"} or provider == "llm_inference_gateway"

    @staticmethod
    def _route_key(spec: dict[str, Any]) -> str:
        policy = spec.get("model_policy")
        if not isinstance(policy, dict):
            return TEXT_ROUTE_KEY
        return str(policy.get("route_key") or TEXT_ROUTE_KEY)

    @staticmethod
    def _system_prompt(*, agent_key: str, spec: dict[str, Any], prompt_system_prompt: str | None = None) -> str:
        base_prompt = str(prompt_system_prompt or "").strip()
        if not base_prompt:
            base_prompt = "\n".join(
                [
                    f"你是 PSOP Agent Harness 中的 {agent_key}。",
                    str(spec.get("goal") or "根据输入上下文生成下一步 AgentDecision。"),
                ]
            )
        decision_contract = "\n".join(
            [
                "## Agent Harness Decision Contract",
                "只输出 JSON decision 对象，不要输出 Markdown、解释或额外文本。",
                "JSON decision 必须符合 AgentDecision：decision_type 为 final_output、tool_call 或 fail。",
                "tool_call 只能选择 prompt payload allowed_tools 中的工具；高副作用工具只提出 tool_call，由 Harness 负责授权。",
                "clarifying_questions、need_more_evidence、proposal_review_required 等人类等待应作为 final_output 的业务状态，不是 HITL。",
            ]
        )
        return f"{base_prompt}\n\n{decision_contract}"

    @classmethod
    def _decision_from_parsed(cls, parsed: dict[str, Any]) -> AgentDecision:
        decision_payload = parsed.get("agent_decision") or parsed.get("decision_payload") or parsed
        if not isinstance(decision_payload, dict):
            raise SkillValidationError(
                "LLM AgentDecision 响应必须是对象。",
                details={"parsed": parsed},
            )
        if "decision_type" not in decision_payload:
            decision_payload = {
                "decision_type": "final_output",
                "output_payload": parsed,
            }
        try:
            return AgentDecision(**decision_payload)
        except ValidationError as error:
            raise SkillValidationError(
                "LLM AgentDecision 响应不符合 AgentDecision schema。",
                details={"parsed": parsed, "error": str(error)},
            ) from error

    @staticmethod
    def _parse_json_object(content: str) -> dict[str, Any]:
        text = str(content or "").strip()
        if text.startswith("```"):
            lines = text.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            text = "\n".join(lines).strip()
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            start = text.find("{")
            end = text.rfind("}")
            if start < 0 or end <= start:
                raise SkillValidationError(
                    "LLM AgentDecision 响应不是 JSON 对象。",
                    details={"raw_content": content},
                )
            try:
                parsed = json.loads(text[start : end + 1])
            except json.JSONDecodeError as error:
                raise SkillValidationError(
                    "LLM AgentDecision 响应不是合法 JSON。",
                    details={"raw_content": content, "error": str(error)},
                ) from error
        if not isinstance(parsed, dict):
            raise SkillValidationError(
                "LLM AgentDecision 响应必须是 JSON 对象。",
                details={"raw_content": content},
            )
        return parsed
