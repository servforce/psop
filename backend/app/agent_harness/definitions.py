from __future__ import annotations

import re
import importlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import yaml

from app.agent_harness.schemas import AgentDefinition


_AGENT_KEY_SEGMENT_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


@dataclass(frozen=True, slots=True)
class AgentPackage:
    definition: AgentDefinition
    agent_root: Path
    module_name: str

    def read_system_prompt(self) -> str:
        return self._read_optional_text(self.definition.system_prompt_file)

    def read_memory_prompt(self) -> str:
        return self._read_optional_text(self.definition.memory_file)

    def load_factory(self) -> Callable[..., Any]:
        module = importlib.import_module(f"{self.module_name}.agent")
        factory = getattr(module, self.definition.factory, None)
        if not callable(factory):
            raise ValueError(f"{self.module_name}.agent 中不存在可调用 factory：{self.definition.factory}")
        return factory

    def _read_optional_text(self, relative_path: str) -> str:
        target = _resolve_package_file(self.agent_root, relative_path)
        if not target.exists():
            return ""
        return target.read_text(encoding="utf-8")


class FileAgentDefinitionRegistry:
    def __init__(self, root: Path) -> None:
        self.root = root

    def load(self, agent_key: str) -> AgentPackage:
        segments = _safe_agent_key_segments(agent_key)
        agent_root = self.root.joinpath(*segments)
        config_path = agent_root / "agent.yaml"
        if not config_path.exists():
            raise FileNotFoundError(f"未找到 AgentDefinition：{agent_key}")
        payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        if not isinstance(payload, dict):
            raise ValueError(f"{config_path} 顶层必须是对象。")
        definition = AgentDefinition.model_validate(payload)
        if definition.agent_key != agent_key:
            raise ValueError(
                f"{config_path} 中的 agent_key 与请求不一致：{definition.agent_key!r} != {agent_key!r}"
            )
        return AgentPackage(
            definition=definition,
            agent_root=agent_root,
            module_name="app.agent_harness.agents." + ".".join(segments),
        )

    def _agent_root(self, agent_key: str) -> Path:
        segments = _safe_agent_key_segments(agent_key)
        return self.root.joinpath(*segments)


def default_agent_registry(backend_root: Path) -> FileAgentDefinitionRegistry:
    return FileAgentDefinitionRegistry(backend_root / "app" / "agent_harness" / "agents")


def _safe_agent_key_segments(agent_key: str) -> list[str]:
    segments = agent_key.split(".")
    if len(segments) < 2:
        raise ValueError("Agent key 必须包含 namespace，例如 demo.psop_harness_agent。")
    for segment in segments:
        if not _AGENT_KEY_SEGMENT_PATTERN.fullmatch(segment):
            raise ValueError(f"非法 Agent key segment：{segment!r}")
    return segments


def _resolve_package_file(root: Path, relative_path: str) -> Path:
    if not relative_path or Path(relative_path).is_absolute():
        raise ValueError("Agent package 文件路径必须是相对路径。")
    candidate = (root / relative_path).resolve()
    resolved_root = root.resolve()
    try:
        candidate.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError("Agent package 文件路径越界。") from exc
    return candidate
