from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.core.config import Settings


@dataclass(slots=True)
class AgentWorkspace:
    agent_run_id: str
    root_path: Path
    workspace_path: Path
    input_path: Path
    output_path: Path
    events_path: Path
    memory_path: Path

    def write_json(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def write_output(self, payload: dict[str, Any]) -> None:
        self.write_json(self.output_path, payload)

    def resolve_workspace_file(self, relative_path: str) -> Path:
        if not relative_path or relative_path.strip() in {".", "/"}:
            raise ValueError("workspace 文件名不能为空。")
        candidate = (self.workspace_path / relative_path).resolve()
        workspace_root = self.workspace_path.resolve()
        if candidate != workspace_root and workspace_root not in candidate.parents:
            raise ValueError("workspace 文件路径越界。")
        return candidate


class WorkspaceManager:
    def __init__(self, settings: Settings) -> None:
        root = Path(settings.agent_harness_workspace_root)
        self.root_path = root if root.is_absolute() else settings.repo_root / root

    def create(self, *, agent_run_id: str | None = None, input_payload: dict[str, Any] | None = None) -> AgentWorkspace:
        run_id = agent_run_id or str(uuid4())
        root = self.root_path / run_id
        workspace = AgentWorkspace(
            agent_run_id=run_id,
            root_path=root,
            workspace_path=root / "workspace",
            input_path=root / "input.json",
            output_path=root / "output.json",
            events_path=root / "events.jsonl",
            memory_path=root / "memory.json",
        )
        workspace.workspace_path.mkdir(parents=True, exist_ok=True)
        workspace.write_json(workspace.input_path, input_payload or {})
        if not workspace.memory_path.exists():
            workspace.write_json(workspace.memory_path, {})
        return workspace
