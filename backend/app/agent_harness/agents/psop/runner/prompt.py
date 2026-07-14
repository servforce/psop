from __future__ import annotations

import json

from app.agent_harness.skills.spec import AgentSkill


def apply_prompt_template(
    *,
    system_prompt: str,
    memory_prompt: str,
    skill_metadata: list[AgentSkill],
    memory_payload: dict[str, object],
) -> str:
    parts = [system_prompt.strip()]
    if memory_prompt.strip():
        parts.append("# Agent Memory\n" + memory_prompt.strip())
    if memory_payload:
        parts.append("# 当前 Memory Snapshot\n" + json.dumps(memory_payload, ensure_ascii=False, indent=2))
    parts.append(_build_skill_section(skill_metadata))
    return "\n\n".join(part for part in parts if part)


def _build_skill_section(skills: list[AgentSkill]) -> str:
    lines = [
        "<skill_system>",
        "Agent Skills 位于仓库根目录 skills/。",
        "Runner 的核心规则已经预加载在 system prompt 中；不要为了形式完整而固定调用 load_skill。",
        "当前节点上下文可能命名为 RunnerContext 或 RunnerTurnContext；如果它不足以判断，才按需调用 load_skill 或 load_skill_resource 补充 Skill 细节。",
        "只能调用当前 AgentDefinition 声明的 skills。",
        "",
        "可用 Skills:",
    ]
    for skill in skills:
        allowed_tools = ", ".join(skill.allowed_tools) if skill.allowed_tools else "(none)"
        lines.append(f"- {skill.name}: {skill.description}; allowed-tools: {allowed_tools}")
    lines.append("</skill_system>")
    return "\n".join(lines)
