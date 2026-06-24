from __future__ import annotations

from typing import Any

from app.agent_harness.tools.registry import ToolExecutionContext, ToolRegistry
from app.agent_harness.tools.spec import ToolSpec


LOAD_SKILL_TOOL_NAME = "load_skill"


def register_framework_tools(registry: ToolRegistry) -> None:
    registry.register(
        ToolSpec(
            name=LOAD_SKILL_TOOL_NAME,
            description="Load the full instruction body for an Agent Skill declared by the current agent.",
            input_schema={
                "type": "object",
                "properties": {"skill_name": {"type": "string"}},
                "required": ["skill_name"],
            },
        ),
        _load_skill,
    )


def _load_skill(arguments: dict[str, Any], context: ToolExecutionContext) -> dict[str, Any]:
    skill_name = arguments.get("skill_name")
    if not isinstance(skill_name, str) or not skill_name.strip():
        raise ValueError("skill_name 必须是非空字符串。")
    skill_name = skill_name.strip()
    if context.allowed_skill_names is None or skill_name not in context.allowed_skill_names:
        raise ValueError(f"Agent 未声明 Skill：{skill_name}")
    if context.skill_loader is None:
        raise RuntimeError("当前工具上下文未配置 SkillLoader。")
    skill = context.skill_loader.load(skill_name, context.event_writer)
    return {
        "name": skill.name,
        "description": skill.description,
        "allowed_tools": skill.allowed_tools,
        "content": skill.instruction,
        "path": skill.path,
    }
