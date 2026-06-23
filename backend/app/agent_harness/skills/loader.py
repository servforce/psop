from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from app.agent_harness.events import AgentEventWriter
from app.agent_harness.skills.spec import AgentSkill


class SkillLoader:
    def __init__(self, skills_root: Path) -> None:
        self.skills_root = skills_root

    def load(self, skill_name: str, event_writer: AgentEventWriter | None = None) -> AgentSkill:
        skill_path = self.skills_root / skill_name / "SKILL.md"
        if not skill_path.exists():
            raise FileNotFoundError(f"Agent Skill 不存在：{skill_name}")
        metadata, body = _parse_skill_md(skill_path.read_text(encoding="utf-8"))
        skill = AgentSkill(
            name=str(metadata.get("name") or skill_name),
            description=str(metadata.get("description") or ""),
            tools=[str(item) for item in metadata.get("tools") or []],
            instruction=body.strip(),
            path=str(skill_path),
        )
        if event_writer:
            event_writer.record(
                "agent.skill.loaded",
                {"skill_name": skill.name, "description": skill.description, "tools": skill.tools},
            )
        return skill


def _parse_skill_md(content: str) -> tuple[dict[str, Any], str]:
    stripped = content.lstrip()
    if not stripped.startswith("---"):
        return {}, content
    parts = stripped.split("---", 2)
    if len(parts) < 3:
        return {}, content
    _, raw_metadata, body = parts
    metadata = yaml.safe_load(raw_metadata) or {}
    return (metadata if isinstance(metadata, dict) else {}), body
