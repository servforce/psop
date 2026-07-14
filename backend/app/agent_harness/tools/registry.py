from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from app.agent_harness.events import AgentEventWriter
from app.agent_harness.memory.store import MemoryStore
from app.agent_harness.sandbox.base import AgentSandbox
from app.agent_harness.skills.loader import SkillLoader
from app.agent_harness.tools.spec import ToolSpec


@dataclass(slots=True)
class ToolExecutionContext:
    sandbox: AgentSandbox
    memory_store: MemoryStore
    memory_scope: str
    event_writer: AgentEventWriter
    invocation_context: dict[str, Any]
    invocation_input: dict[str, Any] = field(default_factory=dict)
    settings: Any | None = None
    skill_loader: SkillLoader | None = None
    allowed_skill_names: set[str] | None = None


ToolHandler = Callable[[dict[str, Any], ToolExecutionContext], dict[str, Any]]


@dataclass(slots=True)
class ToolDefinition:
    spec: ToolSpec
    handler: ToolHandler


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, spec: ToolSpec, handler: ToolHandler) -> None:
        self._tools[spec.name] = ToolDefinition(spec=spec, handler=handler)

    def get(self, name: str) -> ToolDefinition:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise KeyError(f"未注册 Agent Harness tool：{name}") from exc

    def resolve(self, names: list[str]) -> list[ToolDefinition]:
        return [self.get(name) for name in names]

    def execute(self, name: str, arguments: dict[str, Any], context: ToolExecutionContext) -> dict[str, Any]:
        definition = self.get(name)
        return definition.handler(arguments, context)

    def openai_tools(self, names: list[str]) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": definition.spec.name,
                    "description": definition.spec.description,
                    "parameters": definition.spec.input_schema or {"type": "object", "properties": {}},
                },
            }
            for definition in self.resolve(names)
        ]


def _summarize_result(result: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for key, value in result.items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            summary[key] = value if not isinstance(value, str) or len(value) <= 240 else value[:237] + "..."
        elif isinstance(value, list):
            summary[key] = {"type": "list", "count": len(value)}
        elif isinstance(value, dict):
            summary[key] = {"type": "object", "keys": sorted(value.keys())[:12]}
        else:
            summary[key] = str(type(value).__name__)
    return summary
