from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any


PSOP_WORKSPACE_VIRTUAL_ROOT = "/mnt/psop/workspace"
PSOP_OUTPUTS_VIRTUAL_ROOT = "/mnt/psop/outputs"


class AgentSandbox(ABC):
    sandbox_id: str
    agent_run_id: str
    root_path: Path
    workspace_path: Path
    outputs_path: Path
    input_path: Path
    output_path: Path
    events_path: Path
    memory_path: Path

    @abstractmethod
    def resolve_virtual_path(self, virtual_path: str) -> Path:
        ...

    @abstractmethod
    def virtualize_path(self, path: Path) -> str:
        ...

    @abstractmethod
    def read_text(self, virtual_path: str) -> str:
        ...

    @abstractmethod
    def write_text(self, virtual_path: str, content: str, *, append: bool = False) -> str:
        ...

    @abstractmethod
    def write_json(self, path: Path, payload: dict[str, Any]) -> None:
        ...

    @abstractmethod
    def write_output(self, payload: dict[str, Any]) -> None:
        ...

    @abstractmethod
    def list_dir(self, virtual_path: str) -> list[str]:
        ...

    @abstractmethod
    def glob(self, virtual_path: str, pattern: str) -> list[str]:
        ...

    @abstractmethod
    def grep(self, virtual_path: str, pattern: str) -> list[dict[str, Any]]:
        ...


class AgentSandboxProvider(ABC):
    @abstractmethod
    def acquire(self, *, agent_run_id: str | None = None, input_payload: dict[str, Any] | None = None) -> AgentSandbox:
        ...

    @abstractmethod
    def get(self, sandbox_id: str) -> AgentSandbox | None:
        ...

    @abstractmethod
    def release(self, sandbox_id: str) -> None:
        ...
