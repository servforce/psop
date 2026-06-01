from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from app.agents.registry import (
    AgentPromptPack,
    DomainPack,
    DomainPackRegistry,
    DomainPackResolution,
    PromptRegistry,
)
from app.domain.agent_prompts.service import AgentPromptService
from app.domain.compiler.formal_v5 import FormalDiagnostic
from app.domain.skills.manifest import SkillDocument
from app.domain.skills.models import SkillDefinition, SkillVersion
from app.gateway.inference import LlmInferenceGateway
from app.gateway.gitlab import SkillSourceBundle
from sqlalchemy.orm import Session


@dataclass(slots=True)
class CompileAgentCandidate:
    artifact: dict[str, Any] | None
    diagnostics: list[FormalDiagnostic]
    context_diagnostics: list[FormalDiagnostic]
    compiler_metadata: dict[str, Any]
    raw_content: str
    usage: dict[str, Any]


class SkillCompileAgent:
    """LLM-backed compiler agent for turning Skill source into formal-v5 EG candidates."""

    def __init__(
        self,
        inference_gateway: LlmInferenceGateway,
        *,
        prompt_registry: PromptRegistry | None = None,
        domain_pack_registry: DomainPackRegistry | None = None,
        agent_prompt_service: AgentPromptService | None = None,
    ) -> None:
        self.inference_gateway = inference_gateway
        self.prompt_registry = prompt_registry or PromptRegistry()
        self.domain_pack_registry = domain_pack_registry or DomainPackRegistry()
        self.agent_prompt_service = agent_prompt_service or AgentPromptService(prompt_registry=self.prompt_registry)

    def compile(
        self,
        *,
        skill_definition: SkillDefinition,
        skill_version: SkillVersion,
        document: SkillDocument,
        source: SkillSourceBundle,
        repair_diagnostics: list[FormalDiagnostic] | None = None,
        session: Session | None = None,
    ) -> CompileAgentCandidate:
        prompt_pack = self.agent_prompt_service.resolve_prompt_pack(
            session,
            usage_key="default.compile_agent",
            fallback_ref="skill_compilation/formal_v5_compile/v1",
        )
        domain_resolution = self.domain_pack_registry.resolve(_domain_pack_ref(document))
        compiler_metadata = _compiler_metadata(prompt_pack, domain_resolution)
        context_diagnostics = _context_diagnostics(compiler_metadata, domain_resolution)
        completion = self.inference_gateway.complete(
            system_prompt=prompt_pack.system_prompt,
            user_prompt=self._user_prompt(
                skill_definition=skill_definition,
                skill_version=skill_version,
                document=document,
                source=source,
                prompt_pack=prompt_pack,
                domain_pack=domain_resolution.pack,
                compiler_metadata=compiler_metadata,
                repair_diagnostics=repair_diagnostics or [],
            ),
            route_key=prompt_pack.route_key,
        )
        candidate = self._parse_candidate(completion.content)
        candidate.context_diagnostics = context_diagnostics
        candidate.compiler_metadata = compiler_metadata
        candidate.usage = dict(completion.usage or {})
        return candidate

    @staticmethod
    def _parse_candidate(content: str) -> CompileAgentCandidate:
        json_text = _extract_json(content)
        try:
            artifact = json.loads(json_text)
        except json.JSONDecodeError as exc:
            return CompileAgentCandidate(
                artifact=None,
                diagnostics=[
                    FormalDiagnostic(
                        severity="error",
                        code="compile.agent.invalid_json",
                        message=f"编译智能体未返回合法 JSON：{exc.msg}",
                        location={"line": exc.lineno, "column": exc.colno},
                    )
                ],
                context_diagnostics=[],
                compiler_metadata={},
                raw_content=content,
                usage={},
            )

        if not isinstance(artifact, dict):
            return CompileAgentCandidate(
                artifact=None,
                diagnostics=[
                    FormalDiagnostic(
                        severity="error",
                        code="compile.agent.invalid_json",
                        message="编译智能体 JSON 顶层必须是对象。",
                        location={"path": "$"},
                    )
                ],
                context_diagnostics=[],
                compiler_metadata={},
                raw_content=content,
                usage={},
            )
        return CompileAgentCandidate(
            artifact=artifact,
            diagnostics=[],
            context_diagnostics=[],
            compiler_metadata={},
            raw_content=content,
            usage={},
        )

    @staticmethod
    def _user_prompt(
        *,
        skill_definition: SkillDefinition,
        skill_version: SkillVersion,
        document: SkillDocument,
        source: SkillSourceBundle,
        prompt_pack: AgentPromptPack,
        domain_pack: DomainPack,
        compiler_metadata: dict[str, Any],
        repair_diagnostics: list[FormalDiagnostic],
    ) -> str:
        payload = {
            "task": "compile_skill_to_psop_execution_graph_formal_v5",
            "skill": {
                "id": skill_definition.id,
                "key": skill_definition.key,
                "name": skill_definition.name,
                "description": skill_definition.description,
                "version_id": skill_version.id,
                "version_no": skill_version.version_no,
                "source_commit_sha": skill_version.source_commit_sha,
            },
            "manifest_snapshot": document.skill.model_dump(mode="json"),
            "source": {
                "README.md": source.readme_content,
                "SKILL.md": source.skill_md_content,
            },
            "agent_prompt": prompt_pack.metadata(),
            "domain_pack": {
                **domain_pack.metadata(),
                "guidance": domain_pack.guidance,
            },
            "allowed_runtime": {
                "node_kinds": ["start", "input", "llm", "tool", "terminal"],
                "actors": [
                    "runtime.start",
                    "runtime.input",
                    "agent.llm",
                    "capability.demo_tool",
                    "runtime.terminal",
                ],
                "tools": ["psop.demo.inspect_input"],
                "guard_ops": ["always", "phase_is", "field_exists", "field_equals", "all", "any", "not"],
                "merge_ops": ["set"],
            },
            "workflow_compilation_contract": {
                "must_extract_workflow_from": ["SKILL.md", "README.md"],
                "runtime_contract_required_fields": [
                    "execution_goal",
                    "applicability",
                    "workflow_steps",
                    "expected_evidence",
                    "safety_constraints",
                    "wait_checkpoints",
                    "completion_criteria",
                    "recovery_paths",
                ],
                "workflow_step_required_fields": ["id", "title", "goal", "source_evidence"],
                "business_node_rule": (
                    "每个 workflow step 必须编译为 instruct_<step_id> 和 evaluate_<step_id> 两个节点。"
                    "instruct 节点必须输出到终端并进入 wait checkpoint；evaluate 节点必须消费 terminal evidence 并输出 JSON decision。"
                ),
                "node_sequence_rule": (
                    "start -> instruct_<first_step> -> wait -> evaluate_<first_step> -> "
                    "instruct_<next_step> / recover_or_retry -> final_verify -> terminal"
                ),
                "llm_projection_rule": (
                    "指令型 llm 节点输出 terminal_message；评估型 llm 节点必须只输出 JSON object，"
                    "包含 decision/proceed|retry|need_more_evidence|abort|complete、reason、next_phase、terminal_message。"
                ),
                "runtime_language_rule": (
                    "所有 Runtime LLM 节点的用户可见自然语言必须使用简体中文。"
                    "instruct 节点的终端输出必须是简体中文。"
                    "evaluate/final_verify 节点输出 JSON 时，字段名与 decision/next_phase 枚举保持英文协议值，"
                    "但 reason、terminal_message 等自然语言字段值必须是简体中文。"
                ),
                "policy_budget_rule": (
                    "不要生成固定模板值 max_llm_calls=8。LLM 调用预算必须按 workflow_steps 动态推导："
                    "happy path 至少为 2 * workflow_steps.length + 1；当前阶段优先不设置 hard limit，"
                    "如必须设置也不得低于 happy path 调用数并需为 retry/need_more_evidence 留出弹性。"
                ),
                "view_graph_rule": (
                    "dependency_graph_for_view 只表达 guard/merge/next_phase 真实可达的展示边；"
                    "不得添加 artifact 中没有明确 phase 写入路径的 speculative recovery edge。"
                ),
                "domain_pack_rule": (
                    "domain_pack 只用于理解行业术语、常见步骤和质量标准；"
                    "不得改变 formal v5、actor/tool 白名单、guard DSL、merge DSL 或状态主权边界。"
                ),
            },
            "repair_diagnostics": [item.as_dict() for item in repair_diagnostics],
            "output_hint": {
                "final_output_path": "outputs.final_response",
                "initial_phase": "start",
                "success_status": "success",
            },
            "compiler_metadata": compiler_metadata,
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)


def _domain_pack_ref(document: SkillDocument) -> str | None:
    value = getattr(document.skill.compile_config, "domain_pack", None)
    if isinstance(value, str) and value.strip():
        return value.strip()
    extra = getattr(document.skill.compile_config, "__pydantic_extra__", None) or {}
    extra_value = extra.get("domain_pack")
    return extra_value.strip() if isinstance(extra_value, str) and extra_value.strip() else None


def _compiler_metadata(prompt_pack: AgentPromptPack, domain_resolution: DomainPackResolution) -> dict[str, Any]:
    return {
        "agent_prompt": prompt_pack.metadata(),
        "domain_pack": {
            **domain_resolution.pack.metadata(),
            "requested_ref": domain_resolution.requested_ref,
            "used_default": domain_resolution.used_default,
        },
    }


def _context_diagnostics(
    compiler_metadata: dict[str, Any],
    domain_resolution: DomainPackResolution,
) -> list[FormalDiagnostic]:
    diagnostics = [
        FormalDiagnostic(
            severity="info",
            code="compile.agent.prompt_pack",
            message="使用 repo 版本化 Agent Prompt Pack 与 Domain Pack 调用 SKILL 编译智能体。",
            location=compiler_metadata,
        )
    ]
    if domain_resolution.used_default:
        diagnostics.append(
            FormalDiagnostic(
                severity="warning",
                code="compile.agent.domain_pack_fallback",
                message=(
                    f"未找到 domain_pack `{domain_resolution.requested_ref}`，"
                    f"已回退到 `{domain_resolution.pack.key}`。"
                ),
                location={
                    "requested_ref": domain_resolution.requested_ref,
                    "fallback_domain_pack": domain_resolution.pack.metadata(),
                    "reason": domain_resolution.fallback_reason,
                },
            )
        )
    return diagnostics


def _extract_json(content: str) -> str:
    stripped = content.strip()
    fence_match = re.search(r"```(?:json)?\s*(.*?)```", stripped, flags=re.DOTALL | re.IGNORECASE)
    if fence_match:
        return fence_match.group(1).strip()
    return stripped
