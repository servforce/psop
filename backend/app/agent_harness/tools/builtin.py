from __future__ import annotations

import re
import json
from typing import Any

from app.agent_harness.sandbox.base import PSOP_WORKSPACE_VIRTUAL_ROOT
from app.agent_harness.tools.registry import ToolExecutionContext, ToolRegistry
from app.agent_harness.tools.spec import ToolSpec


def register_builtin_tools(registry: ToolRegistry) -> None:
    registry.register(
        ToolSpec(
            name="demo_extract_check_items",
            description="Extract checklist items from a field operation description.",
            input_schema={
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
        ),
        _demo_extract_check_items,
    )
    registry.register(
        ToolSpec(
            name="demo_score_checklist",
            description="Score extracted checklist items and return a simple risk level.",
            input_schema={
                "type": "object",
                "properties": {"items": {"type": "array", "items": {"type": "string"}}},
                "required": ["items"],
            },
        ),
        _demo_score_checklist,
    )
    registry.register(
        ToolSpec(
            name="memory_put",
            description="Write a key/value pair to the current agent memory scope.",
            input_schema={
                "type": "object",
                "properties": {"key": {"type": "string"}, "value": {"type": "string"}},
                "required": ["key", "value"],
            },
        ),
        _memory_put,
    )
    registry.register(
        ToolSpec(
            name="memory_get",
            description="Read a value from the current agent memory scope.",
            input_schema={
                "type": "object",
                "properties": {"key": {"type": "string"}},
                "required": ["key"],
            },
        ),
        _memory_get,
    )
    registry.register(
        ToolSpec(
            name="write_demo_report",
            description="Write a demo markdown report into the agent workspace.",
            input_schema={
                "type": "object",
                "properties": {
                    "filename": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["filename", "content"],
            },
        ),
        _write_demo_report,
    )


def _demo_extract_check_items(arguments: dict[str, Any], _: ToolExecutionContext) -> dict[str, Any]:
    text = _require_str(arguments, "text")
    parts = [item.strip(" -\t\r\n") for item in re.split(r"[，。；;,.\n]+", text) if item.strip()]
    items = []
    for part in parts:
        normalized = re.sub(r"^(并|然后|同时|以及|再|先|请)", "", part).strip()
        if normalized:
            items.append(normalized)
    return {"items": items, "item_count": len(items)}


def _demo_score_checklist(arguments: dict[str, Any], _: ToolExecutionContext) -> dict[str, Any]:
    raw_items = _coerce_items(arguments.get("items"))
    items = [str(item).strip() for item in raw_items if str(item).strip()]
    high_keywords = ("高压", "动火", "受限空间", "吊装", "泄漏")
    medium_keywords = ("ppe", "PPE", "阀门", "压力", "泵房", "电源")
    risk_level = "low"
    if any(keyword in item for item in items for keyword in high_keywords):
        risk_level = "high"
    elif len(items) >= 3 or any(keyword in item for item in items for keyword in medium_keywords):
        risk_level = "medium"
    return {
        "item_count": len(items),
        "risk_level": risk_level,
        "scores": [{"item": item, "score": 1} for item in items],
    }


def _memory_put(arguments: dict[str, Any], context: ToolExecutionContext) -> dict[str, Any]:
    key = _require_str(arguments, "key")
    value = _require_str(arguments, "value")
    context.memory_store.write(context.memory_scope, key, value)
    context.event_writer.record("agent.memory.write", {"scope": context.memory_scope, "key": key})
    return {"ok": True, "scope": context.memory_scope, "key": key}


def _memory_get(arguments: dict[str, Any], context: ToolExecutionContext) -> dict[str, Any]:
    key = _require_str(arguments, "key")
    memory = context.memory_store.read(context.memory_scope)
    context.event_writer.record("agent.memory.read", {"scope": context.memory_scope, "key": key})
    return {"key": key, "value": memory.get(key)}


def _write_demo_report(arguments: dict[str, Any], context: ToolExecutionContext) -> dict[str, Any]:
    filename = _require_str(arguments, "filename")
    content = _require_str(arguments, "content")
    virtual_path = filename if filename.startswith("/mnt/psop/") else f"{PSOP_WORKSPACE_VIRTUAL_ROOT}/{filename}"
    written_path = context.sandbox.write_text(virtual_path, content)
    context.event_writer.record("agent.file.written", {"path": written_path})
    return {"path": written_path, "bytes": len(content.encode("utf-8"))}


def _require_str(arguments: dict[str, Any], key: str) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} 必须是非空字符串。")
    return value.strip()


def _coerce_items(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value.strip():
        stripped = value.strip()
        if stripped.startswith("["):
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, list):
                return parsed
        return [item.strip() for item in re.split(r"[|,，;；\n]+", stripped) if item.strip()]
    raise ValueError("items 必须是数组或可解析为数组的字符串。")
