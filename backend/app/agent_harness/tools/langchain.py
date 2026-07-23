from __future__ import annotations

from typing import Any

from app.agent_harness.tools.registry import ToolExecutionContext, ToolRegistry


def to_langchain_tools(*, tool_names: list[str], registry: ToolRegistry, context: ToolExecutionContext) -> list[Any]:
    from langchain_core.tools import StructuredTool
    from pydantic import Field, create_model

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
        args_schema = (
            definition.spec.input_schema
            if definition.spec.input_schema_mode == "raw_json_schema"
            else _args_schema_from_json_schema(
                create_model=create_model,
                field=Field,
                tool_name=tool_name,
                schema=definition.spec.input_schema,
            )
        )
        tools.append(
            StructuredTool.from_function(
                _run_tool,
                name=tool_name,
                description=definition.spec.description,
                args_schema=args_schema,
                return_direct=definition.spec.return_direct,
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
        return list[_python_type_from_json_schema(item_schema)]
    if schema_type == "object":
        return dict[str, Any]
    return Any


def _safe_model_name(tool_name: str) -> str:
    return "".join(part.capitalize() for part in tool_name.replace("-", "_").split("_") if part) or "Tool"
