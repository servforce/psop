from __future__ import annotations

from pathlib import Path
from typing import Any

from app.agent_harness.sandbox.base import PSOP_OUTPUTS_VIRTUAL_ROOT, PSOP_WORKSPACE_VIRTUAL_ROOT
from app.agent_harness.tools.registry import ToolExecutionContext, ToolRegistry
from app.agent_harness.tools.spec import ToolSpec


def register_workspace_tools(registry: ToolRegistry) -> None:
    registry.register(
        ToolSpec(
            name="workspace.list",
            description="列出当前 agent workspace 中的文件或目录。",
            purpose="用于查看 /mnt/psop/workspace 内的中间草稿和调试产物。",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "default": "."},
                    "max_entries": {"type": "integer", "minimum": 1, "maximum": 200},
                },
                "additionalProperties": False,
            },
            max_result_chars=12000,
        ),
        _workspace_list,
    )
    registry.register(
        ToolSpec(
            name="workspace.read_text",
            description="读取当前 agent workspace 中的文本文件。",
            purpose="用于读取 /mnt/psop/workspace 内由本次 agent run 生成的中间文件。",
            input_schema={
                "type": "object",
                "required": ["path"],
                "properties": {
                    "path": {"type": "string", "minLength": 1, "maxLength": 500},
                    "max_chars": {"type": "integer", "minimum": 1000, "maximum": 40000},
                },
                "additionalProperties": False,
            },
            max_result_chars=42000,
        ),
        _workspace_read_text,
    )
    registry.register(
        ToolSpec(
            name="workspace.write_text",
            description="向当前 agent workspace 写入文本文件。",
            purpose="用于保存 builder 的证据映射、参考资产选择和标准引用等中间草稿。",
            input_schema={
                "type": "object",
                "required": ["path", "content"],
                "properties": {
                    "path": {"type": "string", "minLength": 1, "maxLength": 500},
                    "content": {"type": "string", "minLength": 1, "maxLength": 200000},
                    "mode": {"type": "string", "enum": ["create", "overwrite"], "default": "overwrite"},
                },
                "additionalProperties": False,
            },
            risk_class="write_local",
            side_effect_class="write_sandbox_file",
            resource_scope="sandbox_workspace",
            audit_event="agent.file.written",
            max_result_chars=4000,
        ),
        _workspace_write_text,
    )


def _workspace_list(arguments: dict[str, Any], context: ToolExecutionContext) -> dict[str, Any]:
    try:
        virtual_path = _workspace_path(str(arguments.get("path") or "."))
        max_entries = _bounded_int(arguments.get("max_entries"), default=200, minimum=1, maximum=200)
        entries = context.sandbox.list_dir(virtual_path)[:max_entries]
        return {
            "status": "success",
            "summary": f"列出 {len(entries)} 个 workspace 条目。",
            "items": entries,
            "virtual_path": virtual_path,
            "truncated": len(entries) >= max_entries,
            "next_valid_actions": ["workspace.read_text", "workspace.write_text"],
        }
    except Exception as exc:
        return _error_result(exc, ["workspace.list", "workspace.read_text", "workspace.write_text"])


def _workspace_read_text(arguments: dict[str, Any], context: ToolExecutionContext) -> dict[str, Any]:
    try:
        virtual_path = _workspace_path(_require_str(arguments, "path"))
        max_chars = _bounded_int(arguments.get("max_chars"), default=40000, minimum=1000, maximum=40000)
        content = context.sandbox.read_text(virtual_path)
        truncated = len(content) > max_chars
        if truncated:
            content = content[:max_chars]
        return {
            "status": "success",
            "summary": f"读取 workspace 文件 {virtual_path}。",
            "virtual_path": virtual_path,
            "content": content,
            "bytes": len(content.encode("utf-8")),
            "truncated": truncated,
            "artifact_ref": f"sandbox://workspace/{Path(virtual_path).name}",
            "next_valid_actions": ["workspace.write_text", "psop.builder.submit_candidate"],
        }
    except Exception as exc:
        return _error_result(exc, ["workspace.list", "workspace.read_text"])


def _workspace_write_text(arguments: dict[str, Any], context: ToolExecutionContext) -> dict[str, Any]:
    try:
        raw_path = _require_str(arguments, "path")
        if raw_path.startswith(PSOP_OUTPUTS_VIRTUAL_ROOT):
            raise ValueError("workspace.write_text 不允许写入 outputs 目录。")
        virtual_path = _workspace_path(raw_path)
        content = _require_str(arguments, "content")
        mode = str(arguments.get("mode") or "overwrite")
        if mode not in {"create", "overwrite"}:
            raise ValueError("mode 必须是 create 或 overwrite。")
        resolved = context.sandbox.resolve_virtual_path(virtual_path)
        if mode == "create" and resolved.exists():
            raise ValueError("目标文件已存在，不能以 create 模式覆盖。")
        written_path = context.sandbox.write_text(virtual_path, content)
        context.event_writer.record("agent.file.written", {"path": written_path, "tool_name": "workspace.write_text"})
        return {
            "status": "success",
            "summary": f"已写入 workspace 文件 {written_path}。",
            "virtual_path": written_path,
            "bytes": len(content.encode("utf-8")),
            "truncated": False,
            "artifact_ref": f"sandbox://workspace/{Path(written_path).name}",
            "next_valid_actions": ["workspace.read_text", "psop.builder.submit_candidate"],
        }
    except Exception as exc:
        return _error_result(exc, ["workspace.write_text", "workspace.list"])


def _workspace_path(path: str) -> str:
    if not path or "\x00" in path:
        raise ValueError("workspace 路径不能为空。")
    if path in {".", "/mnt/psop/workspace"}:
        return PSOP_WORKSPACE_VIRTUAL_ROOT
    if path.startswith("/mnt/psop/") and not path.startswith(f"{PSOP_WORKSPACE_VIRTUAL_ROOT}/"):
        raise ValueError("workspace 工具只能访问 /mnt/psop/workspace。")
    if path.startswith(f"{PSOP_WORKSPACE_VIRTUAL_ROOT}/"):
        return path
    return f"{PSOP_WORKSPACE_VIRTUAL_ROOT}/{path.lstrip('/')}"


def _require_str(arguments: dict[str, Any], key: str) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} 必须是非空字符串。")
    return value.strip()


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    if value is None:
        return default
    parsed = int(value)
    if parsed < minimum or parsed > maximum:
        raise ValueError(f"数值必须在 {minimum} 到 {maximum} 之间。")
    return parsed


def _error_result(exc: Exception, next_valid_actions: list[str]) -> dict[str, Any]:
    return {
        "status": "error",
        "type": exc.__class__.__name__,
        "message": str(exc),
        "retryable": False,
        "next_valid_actions": next_valid_actions,
    }
