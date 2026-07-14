from __future__ import annotations

from collections.abc import Callable

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


def build_middlewares(
    definition: AgentDefinition,
    event_writer: AgentEventWriter,
    *,
    deadline_monotonic: float | None = None,
    before_model_call: Callable[[], None] | None = None,
) -> list[AgentMiddleware]:
    configured = _enabled_middleware(definition)
    middlewares: list[AgentMiddleware] = []
    for name, config in configured:
        if name == "dangling_tool_call":
            middlewares.append(DanglingToolCallMiddleware())
        elif name == "model_events":
            middlewares.append(
                ModelCallEventMiddleware(
                    event_writer,
                    max_model_calls=_optional_positive_int(config.get("max_model_calls")),
                    deadline_monotonic=deadline_monotonic,
                    before_model_call=before_model_call,
                )
            )
        elif name == "token_usage":
            middlewares.append(TokenUsageMiddleware(event_writer))
        elif name == "tool_calls":
            middlewares.append(
                ToolCallMiddleware(
                    event_writer,
                    max_error_counts=_tool_error_limits(config.get("max_error_counts")),
                    deadline_monotonic=deadline_monotonic,
                )
            )
        else:
            raise ValueError(f"不支持的 Agent Harness middleware：{name}")
    return middlewares


def _enabled_middleware_names(definition: AgentDefinition) -> list[str]:
    return [name for name, _config in _enabled_middleware(definition)]


def _enabled_middleware(definition: AgentDefinition) -> list[tuple[str, dict]]:
    if not definition.middleware:
        return [(name, {}) for name in DEFAULT_MIDDLEWARE_ORDER]
    items: list[tuple[str, dict]] = []
    for item in definition.middleware:
        if isinstance(item, str):
            items.append((item, {}))
        elif item.enabled:
            items.append((item.name, dict(item.config or {})))
    return items


def _optional_positive_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _tool_error_limits(value: object) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    limits: dict[str, int] = {}
    for tool_name, raw_limit in value.items():
        limit = _optional_positive_int(raw_limit)
        if limit:
            limits[str(tool_name)] = limit
    return limits
