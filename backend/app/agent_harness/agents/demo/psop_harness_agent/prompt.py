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
        parts.append("# Current Memory Snapshot\n" + json.dumps(memory_payload, ensure_ascii=False, indent=2))
    parts.append(_build_skill_section(skill_metadata))
    return "\n\n".join(part for part in parts if part)


def _build_skill_section(skills: list[AgentSkill]) -> str:
    lines = [
        "<skill_system>",
        "Agent Skills 位于仓库根目录 skills/。",
        "系统提示词只注入 Skill 元信息；开始执行相关工作前，必须先调用 load_skill 读取完整 SKILL.md。",
        "只能调用当前 AgentDefinition 声明的 skills。",
        "",
        "Available Skills:",
    ]
    for skill in skills:
        allowed_tools = ", ".join(skill.allowed_tools) if skill.allowed_tools else "(none)"
        lines.append(f"- {skill.name}: {skill.description}; allowed-tools: {allowed_tools}")
    lines.append("</skill_system>")
    return "\n".join(lines)
