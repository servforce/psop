from __future__ import annotations

from langchain.agents.middleware import AgentMiddleware

from app.agent_harness.events import AgentEventWriter
from app.agent_harness.middlewares.dangling_tool_call import DanglingToolCallMiddleware
from app.agent_harness.middlewares.model_events import ModelCallEventMiddleware
from app.agent_harness.middlewares.token_usage import TokenUsageMiddleware
from app.agent_harness.middlewares.tool_calls import ToolCallMiddleware
from app.agent_harness.schemas import AgentDefinition


DEFAULT_MIDDLEWARE_ORDER = [
    "dangling_tool_call",
    "model_events",
    "token_usage",
    "tool_calls",
]


def build_middlewares(definition: AgentDefinition, event_writer: AgentEventWriter) -> list[AgentMiddleware]:
    configured = _enabled_middleware_names(definition)
    middlewares: list[AgentMiddleware] = []
    for name in configured:
        if name == "dangling_tool_call":
            middlewares.append(DanglingToolCallMiddleware())
        elif name == "model_events":
            middlewares.append(ModelCallEventMiddleware(event_writer))
        elif name == "token_usage":
            middlewares.append(TokenUsageMiddleware(event_writer))
        elif name == "tool_calls":
            middlewares.append(ToolCallMiddleware(event_writer))
        else:
            raise ValueError(f"不支持的 Agent Harness middleware：{name}")
    return middlewares


def _enabled_middleware_names(definition: AgentDefinition) -> list[str]:
    if not definition.middleware:
        return list(DEFAULT_MIDDLEWARE_ORDER)
    names: list[str] = []
    for item in definition.middleware:
        if isinstance(item, str):
            names.append(item)
        elif item.enabled:
            names.append(item.name)
    return names
