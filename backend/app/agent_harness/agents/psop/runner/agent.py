from __future__ import annotations

from app.agent_harness.agents.context import AgentBuildContext
from app.agent_harness.agents.factory import create_psop_agent
from app.agent_harness.agents.psop.runner.prompt import apply_prompt_template
from app.agent_harness.middlewares import build_middlewares
from app.agent_harness.tools.builtin.runner import register_runner_tools
from app.agent_harness.tools.builtin.workspace import register_workspace_tools
from app.agent_harness.tools.framework import LOAD_SKILL_RESOURCE_TOOL_NAME, LOAD_SKILL_TOOL_NAME, register_framework_tools
from app.agent_harness.tools.langchain import to_langchain_tools
from app.agent_harness.tools.policy import filter_tools_by_skill_allowed_tools
from app.agent_harness.tools.registry import ToolExecutionContext, ToolRegistry


def make_runner_agent(context: AgentBuildContext):
    registry = ToolRegistry()
    register_runner_tools(registry)
    register_workspace_tools(registry)
    register_framework_tools(registry)

    business_tool_names = filter_tools_by_skill_allowed_tools(
        context.definition.tools,
        context.skill_metadata,
    )
    tool_context = ToolExecutionContext(
        sandbox=context.sandbox,
        memory_store=context.memory_store,
        memory_scope=context.memory_scope,
        event_writer=context.event_writer,
        invocation_context=context.invocation.context,
        invocation_input=context.invocation.input,
        settings=context.settings,
        skill_loader=context.skill_loader,
        allowed_skill_names=set(context.definition.skills),
    )
    tools = to_langchain_tools(
        tool_names=[LOAD_SKILL_TOOL_NAME, LOAD_SKILL_RESOURCE_TOOL_NAME, *business_tool_names],
        registry=registry,
        context=tool_context,
    )
    return create_psop_agent(
        model=context.create_model(),
        tools=tools,
        middleware=build_middlewares(
            context.definition,
            context.event_writer,
            deadline_monotonic=context.invocation.deadline_monotonic,
            before_model_call=context.refresh_provider_deadline,
        ),
        system_prompt=apply_prompt_template(
            system_prompt=context.system_prompt,
            memory_prompt=context.memory_prompt,
            skill_metadata=context.skill_metadata,
            memory_payload=context.memory_payload,
        ),
        name=context.definition.agent_key,
    )
