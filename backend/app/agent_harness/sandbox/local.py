from __future__ import annotations

import fnmatch
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.agent_harness.sandbox.base import (
    PSOP_OUTPUTS_VIRTUAL_ROOT,
    PSOP_WORKSPACE_VIRTUAL_ROOT,
    AgentSandbox,
    AgentSandboxProvider,
)
from app.core.config import Settings


@dataclass(slots=True)
class LocalAgentSandbox(AgentSandbox):
    sandbox_id: str
    agent_run_id: str
    root_path: Path
    workspace_path: Path
    outputs_path: Path
    input_path: Path
    output_path: Path
    events_path: Path
    memory_path: Path

    def resolve_virtual_path(self, virtual_path: str) -> Path:
        if not virtual_path or "\x00" in virtual_path:
            raise ValueError("sandbox 路径不能为空。")
        if os.path.isabs(virtual_path) and not virtual_path.startswith("/mnt/psop/"):
            raise ValueError("sandbox 禁止访问 host 绝对路径。")
        if not virtual_path.startswith("/mnt/psop/"):
            raise ValueError("sandbox 路径必须使用 /mnt/psop 虚拟路径。")
        if _has_parent_traversal(virtual_path):
            raise ValueError("sandbox 路径不能包含父目录穿越。")

        if virtual_path == PSOP_WORKSPACE_VIRTUAL_ROOT:
            return self.workspace_path.resolve()
        if virtual_path.startswith(f"{PSOP_WORKSPACE_VIRTUAL_ROOT}/"):
            relative = virtual_path[len(PSOP_WORKSPACE_VIRTUAL_ROOT) :].lstrip("/")
            return _resolve_inside(self.workspace_path, relative)
        if virtual_path == PSOP_OUTPUTS_VIRTUAL_ROOT:
            return self.outputs_path.resolve()
        if virtual_path.startswith(f"{PSOP_OUTPUTS_VIRTUAL_ROOT}/"):
            relative = virtual_path[len(PSOP_OUTPUTS_VIRTUAL_ROOT) :].lstrip("/")
            return _resolve_inside(self.outputs_path, relative)
        raise ValueError("不支持的 sandbox 虚拟路径。")

    def virtualize_path(self, path: Path) -> str:
        resolved = path.resolve()
        for host_root, virtual_root in (
            (self.workspace_path.resolve(), PSOP_WORKSPACE_VIRTUAL_ROOT),
            (self.outputs_path.resolve(), PSOP_OUTPUTS_VIRTUAL_ROOT),
        ):
            try:
                relative = resolved.relative_to(host_root)
            except ValueError:
                continue
            suffix = relative.as_posix()
            return virtual_root if suffix == "." else f"{virtual_root}/{suffix}"
        raise ValueError("host 路径不在 sandbox 可见目录内。")

    def read_text(self, virtual_path: str) -> str:
        return self.resolve_virtual_path(virtual_path).read_text(encoding="utf-8")

    def write_text(self, virtual_path: str, content: str, *, append: bool = False) -> str:
        path = self.resolve_virtual_path(virtual_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        mode = "a" if append else "w"
        with path.open(mode, encoding="utf-8") as handle:
            handle.write(content)
        return self.virtualize_path(path)

    def write_json(self, path: Path, payload: dict[str, Any]) -> None:
        _ensure_run_path(self.root_path, path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def write_output(self, payload: dict[str, Any]) -> None:
        self.write_json(self.output_path, payload)

    def list_dir(self, virtual_path: str) -> list[str]:
        path = self.resolve_virtual_path(virtual_path)
        return sorted(self.virtualize_path(child) for child in path.iterdir())

    def glob(self, virtual_path: str, pattern: str) -> list[str]:
        path = self.resolve_virtual_path(virtual_path)
        matches = []
        for child in path.rglob("*"):
            if fnmatch.fnmatch(child.name, pattern) or fnmatch.fnmatch(child.relative_to(path).as_posix(), pattern):
                matches.append(self.virtualize_path(child))
        return sorted(matches)

    def grep(self, virtual_path: str, pattern: str) -> list[dict[str, Any]]:
        path = self.resolve_virtual_path(virtual_path)
        files = [path] if path.is_file() else [child for child in path.rglob("*") if child.is_file()]
        matches: list[dict[str, Any]] = []
        for file_path in files:
            try:
                lines = file_path.read_text(encoding="utf-8").splitlines()
            except UnicodeDecodeError:
                continue
            for line_no, line in enumerate(lines, start=1):
                if pattern in line:
                    matches.append({"path": self.virtualize_path(file_path), "line": line_no, "text": line})
        return matches


class LocalAgentSandboxProvider(AgentSandboxProvider):
    def __init__(self, settings: Settings) -> None:
        root = Path(settings.agent_harness_sandbox_root)
        if not root.is_absolute():
            root = settings.repo_root / root
        self.root_path = root
        self._sandboxes: dict[str, LocalAgentSandbox] = {}

    def acquire(self, *, agent_run_id: str | None = None, input_payload: dict[str, Any] | None = None) -> LocalAgentSandbox:
        run_id = agent_run_id or str(uuid4())
        root = self.root_path / run_id
        sandbox = LocalAgentSandbox(
            sandbox_id=f"local:{run_id}",
            agent_run_id=run_id,
            root_path=root,
            workspace_path=root / "workspace",
            outputs_path=root / "outputs",
            input_path=root / "input.json",
            output_path=root / "output.json",
            events_path=root / "events.jsonl",
            memory_path=root / "memory.json",
        )
        sandbox.workspace_path.mkdir(parents=True, exist_ok=True)
        sandbox.outputs_path.mkdir(parents=True, exist_ok=True)
        sandbox.write_json(sandbox.input_path, input_payload or {})
        if not sandbox.memory_path.exists():
            sandbox.write_json(sandbox.memory_path, {})
        self._sandboxes[sandbox.sandbox_id] = sandbox
        return sandbox

    def get(self, sandbox_id: str) -> LocalAgentSandbox | None:
        return self._sandboxes.get(sandbox_id)

    def release(self, sandbox_id: str) -> None:
        self._sandboxes.pop(sandbox_id, None)


def _has_parent_traversal(path: str) -> bool:
    return any(part == ".." for part in Path(path).parts)


def _resolve_inside(root: Path, relative_path: str) -> Path:
    if not relative_path:
        return root.resolve()
    target = (root / relative_path).resolve()
    return _ensure_run_path(root, target)


def _ensure_run_path(root: Path, path: Path) -> Path:
    resolved_root = root.resolve()
    resolved_path = path.resolve()
    try:
        resolved_path.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError("sandbox 文件路径越界。") from exc
    return resolved_path
