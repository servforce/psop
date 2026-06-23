from __future__ import annotations

import json
from typing import Any

from app.agent_harness.events import AgentEventWriter
from app.agent_harness.memory.file_store import FileMemoryStore
from app.agent_harness.models.psop_gateway_chat_model import PsopGatewayChatModel
from app.agent_harness.models.scripted_chat_model import ScriptedToolCallingChatModel
from app.agent_harness.schemas import AgentArtifact, AgentDefinition, AgentInvocation, AgentResult
from app.agent_harness.skills.spec import AgentSkill
from app.agent_harness.tools.registry import ToolExecutionContext, ToolRegistry
from app.agent_harness.workspace.manager import AgentWorkspace
from app.gateway.inference import LlmChatMessage, LlmInferenceGateway


class DeepAgentsRunner:
    def __init__(self, inference_gateway: LlmInferenceGateway) -> None:
        self.inference_gateway = inference_gateway

    def invoke(
        self,
        *,
        invocation: AgentInvocation,
        definition: AgentDefinition,
        system_prompt: str,
        memory_text: str,
        skills: list[AgentSkill],
        tool_registry: ToolRegistry,
        workspace: AgentWorkspace,
        event_writer: AgentEventWriter,
    ) -> AgentResult:
        memory_scope = invocation.memory_scope or definition.memory_scope or definition.agent_key
        memory_store = FileMemoryStore(workspace.memory_path)
        memory_payload = memory_store.read(memory_scope)
        event_writer.record("agent.memory.read", {"scope": memory_scope, "keys": sorted(memory_payload.keys())})
        tool_names = _ordered_unique(definition.tools + [tool for skill in skills for tool in skill.tools])
        context = ToolExecutionContext(
            workspace=workspace,
            memory_store=memory_store,
            memory_scope=memory_scope,
            event_writer=event_writer,
        )
        rendered_system_prompt = _render_system_prompt(system_prompt, memory_text, skills, memory_payload)
        if invocation.use_mock_model:
            return self._invoke_tool_loop(
                invocation=invocation,
                definition=definition,
                system_prompt=rendered_system_prompt,
                model=ScriptedToolCallingChatModel(),
                tool_names=tool_names,
                tool_registry=tool_registry,
                tool_context=context,
                workspace=workspace,
                event_writer=event_writer,
            )
        return self._invoke_deepagents(
            invocation=invocation,
            definition=definition,
            system_prompt=rendered_system_prompt,
            tool_names=tool_names,
            tool_registry=tool_registry,
            tool_context=context,
            workspace=workspace,
            event_writer=event_writer,
        )

    def _invoke_tool_loop(
        self,
        *,
        invocation: AgentInvocation,
        definition: AgentDefinition,
        system_prompt: str,
        model: ScriptedToolCallingChatModel,
        tool_names: list[str],
        tool_registry: ToolRegistry,
        tool_context: ToolExecutionContext,
        workspace: AgentWorkspace,
        event_writer: AgentEventWriter,
    ) -> AgentResult:
        messages = [
            LlmChatMessage(role="system", content=system_prompt),
            LlmChatMessage(role="user", content=str(invocation.input.get("text") or "")),
        ]
        tool_results: dict[str, Any] = {}
        final_output = ""
        for _ in range(12):
            event_writer.record("agent.model.requested", {"model": model.model, "tool_count": len(tool_names)})
            completion = model.complete_chat(messages=messages, tools=tool_registry.openai_tools(tool_names))
            event_writer.record(
                "agent.model.completed",
                {
                    "model": completion.model,
                    "tool_call_count": len(completion.message.tool_calls),
                    "content_length": len(completion.message.content or ""),
                },
            )
            if completion.message.tool_calls:
                messages.append(completion.message)
                for tool_call in completion.message.tool_calls:
                    result = tool_registry.execute(tool_call.name, tool_call.arguments, tool_context)
                    tool_results[tool_call.name] = result
                    messages.append(
                        LlmChatMessage(
                            role="tool",
                            name=tool_call.name,
                            tool_call_id=tool_call.id,
                            content=json.dumps(result, ensure_ascii=False),
                        )
                    )
                continue
            final_output = completion.message.content or ""
            break
        if not final_output:
            final_output = "Agent Harness demo 未生成最终输出。"
        return AgentResult(
            agent_run_id=workspace.agent_run_id,
            agent_key=definition.agent_key,
            status="succeeded",
            final_output=final_output,
            structured_output={"tool_results": tool_results},
            events=event_writer.events,
            artifacts=[AgentArtifact(artifact_type="demo_report", path=str(workspace.workspace_path / "result.md"))],
            workspace_path=str(workspace.workspace_path),
        )

    def _invoke_deepagents(
        self,
        *,
        invocation: AgentInvocation,
        definition: AgentDefinition,
        system_prompt: str,
        tool_names: list[str],
        tool_registry: ToolRegistry,
        tool_context: ToolExecutionContext,
        workspace: AgentWorkspace,
        event_writer: AgentEventWriter,
    ) -> AgentResult:
        try:
            from deepagents import create_deep_agent
        except ImportError as exc:
            raise RuntimeError("未安装 deepagents，无法执行真实 Agent Harness。") from exc

        model = PsopGatewayChatModel(inference_gateway=self.inference_gateway, route_key=definition.route_key)
        agent = create_deep_agent(
            model=model,
            tools=_langchain_tools(tool_names=tool_names, registry=tool_registry, context=tool_context),
            system_prompt=system_prompt,
        )
        event_writer.record("agent.model.requested", {"runner": "deepagents", "tool_count": len(tool_names)})
        result = agent.invoke({"messages": [{"role": "user", "content": str(invocation.input.get("text") or "")}]})
        event_writer.record("agent.model.completed", {"runner": "deepagents"})
        final_output = _extract_final_output(result)
        return AgentResult(
            agent_run_id=workspace.agent_run_id,
            agent_key=definition.agent_key,
            status="succeeded",
            final_output=final_output,
            structured_output={"raw_result": _jsonable(result)},
            events=event_writer.events,
            workspace_path=str(workspace.workspace_path),
        )


def _render_system_prompt(
    system_prompt: str,
    memory_text: str,
    skills: list[AgentSkill],
    memory_payload: dict[str, Any],
) -> str:
    parts = [system_prompt.strip()]
    if memory_text.strip():
        parts.append("# Agent Memory\n" + memory_text.strip())
    if memory_payload:
        parts.append("# Current Memory Snapshot\n" + json.dumps(memory_payload, ensure_ascii=False, indent=2))
    for skill in skills:
        parts.append(f"# Loaded Skill: {skill.name}\n{skill.instruction}")
    return "\n\n".join(part for part in parts if part)


def _langchain_tools(*, tool_names: list[str], registry: ToolRegistry, context: ToolExecutionContext) -> list[Any]:
    try:
        from langchain_core.tools import StructuredTool
        from pydantic import Field, create_model
    except ImportError:
        return []

    tools = []
    for tool_name in tool_names:
        definition = registry.get(tool_name)

        def _make_tool(name: str) -> Any:
            def _run_tool(**kwargs: Any) -> dict[str, Any]:
                return registry.execute(name, kwargs, context)

            return _run_tool

        _run_tool = _make_tool(tool_name)

        _run_tool.__name__ = tool_name
        _run_tool.__doc__ = definition.spec.description
        tools.append(
            StructuredTool.from_function(
                _run_tool,
                name=tool_name,
                description=definition.spec.description,
                args_schema=_args_schema_from_json_schema(
                    create_model=create_model,
                    field=Field,
                    tool_name=tool_name,
                    schema=definition.spec.input_schema,
                ),
            )
        )
    return tools


def _args_schema_from_json_schema(*, create_model: Any, field: Any, tool_name: str, schema: dict[str, Any]) -> Any:
    properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    required = set(schema.get("required") if isinstance(schema.get("required"), list) else [])
    fields: dict[str, tuple[Any, Any]] = {}
    for name, property_schema in properties.items():
        if not isinstance(name, str) or not isinstance(property_schema, dict):
            continue
        annotation = _python_type_from_json_schema(property_schema)
        default = ... if name in required else None
        description = str(property_schema.get("description") or "")
        fields[name] = (annotation, field(default, description=description))
    return create_model(f"{_safe_model_name(tool_name)}Args", **fields)


def _python_type_from_json_schema(schema: dict[str, Any]) -> Any:
    schema_type = schema.get("type")
    if schema_type == "string":
        return str
    if schema_type == "integer":
        return int
    if schema_type == "number":
        return float
    if schema_type == "boolean":
        return bool
    if schema_type == "array":
        item_schema = schema.get("items") if isinstance(schema.get("items"), dict) else {}
        return list[_python_type_from_json_schema(item_schema)] | str
    if schema_type == "object":
        return dict[str, Any]
    return Any


def _safe_model_name(tool_name: str) -> str:
    return "".join(part.capitalize() for part in tool_name.replace("-", "_").split("_") if part) or "Tool"


def _extract_final_output(result: Any) -> str:
    if isinstance(result, dict):
        messages = result.get("messages")
        if isinstance(messages, list) and messages:
            content = getattr(messages[-1], "content", None)
            if content is not None:
                return str(content)
        if result.get("output"):
            return str(result["output"])
    return str(result)


def _jsonable(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except TypeError:
        return str(value)


def _ordered_unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result
