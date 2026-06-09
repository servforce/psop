from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.agent_harness.definitions import DEFAULT_AGENT_SKILLS
from app.skills.service import SkillPackageService


class AgentSkillHarness:
    def __init__(self, skill_service: SkillPackageService) -> None:
        self.skill_service = skill_service

    @staticmethod
    def selected_skill_names(*, agent_key: str, spec: dict[str, Any]) -> list[str]:
        configured_names = spec.get("allowed_skill_names")
        if isinstance(configured_names, list):
            return [str(item).strip() for item in configured_names if str(item).strip()]
        return list(DEFAULT_AGENT_SKILLS.get(agent_key, []))

    def activate_run_skills(
        self,
        session: Session,
        *,
        agent_run_id: str,
        agent_key: str,
        spec: dict[str, Any],
    ) -> tuple[set[str], list[str], list[str]]:
        selected_names = self.selected_skill_names(agent_key=agent_key, spec=spec)
        active_tools, active_skill_names = self.skill_service.activate_agent_run_skills(
            session,
            agent_run_id=agent_run_id,
            agent_key=agent_key,
            selected_names=selected_names,
            sync=True,
        )
        return active_tools, active_skill_names, selected_names

    def hydrate_context(self, session: Session, *, agent_run_id: str) -> list[dict[str, Any]]:
        return self.skill_service.hydrate_agent_run_skill_context(session, agent_run_id=agent_run_id)

    def activate_version_from_tool(
        self,
        session: Session,
        *,
        package_name: str,
        version_id: str,
        commit: bool = False,
    ) -> dict[str, Any]:
        return self.skill_service.activate_version_from_tool(
            session,
            package_name=package_name,
            version_id=version_id,
            commit=commit,
        )
