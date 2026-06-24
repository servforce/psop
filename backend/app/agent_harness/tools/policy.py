from __future__ import annotations

from app.agent_harness.skills.spec import AgentSkill


def filter_tools_by_skill_allowed_tools(declared_tool_names: list[str], skills: list[AgentSkill]) -> list[str]:
    declared = _ordered_unique(declared_tool_names)
    if not skills:
        return declared
    allowed: set[str] = set()
    for skill in skills:
        allowed.update(skill.allowed_tools)
    unauthorized = sorted(allowed - set(declared))
    if unauthorized:
        raise ValueError(f"Agent Skill 请求了未授权工具：{unauthorized}")
    return [tool for tool in declared if tool in allowed]


def _ordered_unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result
