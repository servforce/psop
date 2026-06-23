from __future__ import annotations

from app.agent_harness.skills.spec import AgentSkill


class SkillRegistry:
    def __init__(self) -> None:
        self._skills: dict[str, AgentSkill] = {}

    def add(self, skill: AgentSkill) -> None:
        self._skills[skill.name] = skill

    def get(self, name: str) -> AgentSkill | None:
        return self._skills.get(name)

    def all(self) -> list[AgentSkill]:
        return list(self._skills.values())
