from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from app.agent_harness.events import AgentEventWriter
from app.agent_harness.skills.spec import AgentSkill


class SkillLoader:
    def __init__(self, skills_root: Path) -> None:
        self.skills_root = skills_root

    def load_metadata(self, skill_name: str) -> AgentSkill:
        metadata, _, skill_path = self._read(skill_name)
        return _skill_from_parts(metadata=metadata, body="", skill_path=skill_path, skill_name=skill_name)

    def load(self, skill_name: str, event_writer: AgentEventWriter | None = None) -> AgentSkill:
        metadata, body, skill_path = self._read(skill_name)
        skill = _skill_from_parts(metadata=metadata, body=body.strip(), skill_path=skill_path, skill_name=skill_name)
        if event_writer:
            event_writer.record(
                "agent.skill.loaded",
                {"skill_name": skill.name, "description": skill.description, "allowed_tools": skill.allowed_tools},
            )
        return skill

    def _read(self, skill_name: str) -> tuple[dict[str, Any], str, Path]:
        skill_path = self.skills_root / skill_name / "SKILL.md"
        if not skill_path.exists():
            raise FileNotFoundError(f"Agent Skill 不存在：{skill_name}")
        metadata, body = _parse_skill_md(skill_path.read_text(encoding="utf-8"))
        return metadata, body, skill_path


def _parse_skill_md(content: str) -> tuple[dict[str, Any], str]:
    if not content.startswith("---\n"):
        raise ValueError("Agent Skill 必须包含 YAML frontmatter。")
    parts = content.split("---", 2)
    if len(parts) < 3:
        raise ValueError("Agent Skill frontmatter 格式无效。")
    _, raw_metadata, body = parts
    metadata = yaml.safe_load(raw_metadata) or {}
    if not isinstance(metadata, dict):
        raise ValueError("Agent Skill frontmatter 必须是 YAML 对象。")
    return metadata, body


def _skill_from_parts(*, metadata: dict[str, Any], body: str, skill_path: Path, skill_name: str) -> AgentSkill:
    name = str(metadata.get("name") or "").strip()
    if name != skill_name:
        raise ValueError(f"Agent Skill name 必须匹配目录名：{name!r} != {skill_name!r}")
    description = str(metadata.get("description") or "").strip()
    if not description:
        raise ValueError(f"{skill_path} 必须声明 description。")
    return AgentSkill(
        name=name,
        description=description,
        allowed_tools=_parse_allowed_tools(metadata, skill_path),
        instruction=body,
        path=str(skill_path),
    )


def _parse_allowed_tools(metadata: dict[str, Any], skill_path: Path) -> list[str]:
    if "allowed-tools" not in metadata:
        raise ValueError(f"{skill_path} 必须声明 allowed-tools。")
    raw = metadata["allowed-tools"]
    if not isinstance(raw, list):
        raise ValueError(f"{skill_path} 的 allowed-tools 必须是字符串列表。")
    allowed_tools: list[str] = []
    for item in raw:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{skill_path} 的 allowed-tools 只能包含非空字符串。")
        allowed_tools.append(item.strip())
    return allowed_tools
