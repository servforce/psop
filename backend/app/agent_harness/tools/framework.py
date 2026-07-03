from __future__ import annotations

from typing import Any

from app.agent_harness.tools.registry import ToolExecutionContext, ToolRegistry
from app.agent_harness.tools.spec import ToolSpec


LOAD_SKILL_TOOL_NAME = "load_skill"
LOAD_SKILL_RESOURCE_TOOL_NAME = "load_skill_resource"


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
    registry.register(
        ToolSpec(
            name=LOAD_SKILL_RESOURCE_TOOL_NAME,
            description="Load a Markdown resource file inside an Agent Skill directory declared by the current agent.",
            input_schema={
                "type": "object",
                "properties": {
                    "skill_name": {"type": "string"},
                    "resource_path": {"type": "string"},
                    "max_chars": {"type": "integer"},
                },
                "required": ["skill_name", "resource_path"],
            },
        ),
        _load_skill_resource,
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


def _load_skill_resource(arguments: dict[str, Any], context: ToolExecutionContext) -> dict[str, Any]:
    skill_name = arguments.get("skill_name")
    if not isinstance(skill_name, str) or not skill_name.strip():
        raise ValueError("skill_name 必须是非空字符串。")
    skill_name = skill_name.strip()
    if context.allowed_skill_names is None or skill_name not in context.allowed_skill_names:
        raise ValueError(f"Agent 未声明 Skill：{skill_name}")
    resource_path = arguments.get("resource_path")
    if not isinstance(resource_path, str) or not resource_path.strip():
        raise ValueError("resource_path 必须是非空字符串。")
    max_chars = arguments.get("max_chars")
    if max_chars is None:
        max_chars = 60_000
    if not isinstance(max_chars, int):
        raise ValueError("max_chars 必须是正整数。")
    if context.skill_loader is None:
        raise RuntimeError("当前工具上下文未配置 SkillLoader。")
    return context.skill_loader.load_resource(
        skill_name,
        resource_path,
        context.event_writer,
        max_chars=max_chars,
    )
