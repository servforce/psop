from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.agents.service import AgentService
from app.memory.service import MemoryService
from app.skills.service import SkillPackageService


ACTIVE_AGENT_RUN_STATUSES = {"queued", "running", "waiting_tool_authorization"}


class AgentRunActivityService:
    """Builds AgentRun activity snapshots from persisted agent observability facts."""

    def __init__(
        self,
        *,
        agent_service: AgentService | None = None,
        skill_service: SkillPackageService | None = None,
        memory_service: MemoryService | None = None,
    ) -> None:
        self.agent_service = agent_service or AgentService()
        self.skill_service = skill_service or SkillPackageService()
        self.memory_service = memory_service or MemoryService()

    def build_snapshot(self, session: Session, agent_run_id: str) -> dict[str, Any]:
        run = self.agent_service.get_run(session, agent_run_id)
        status = str(run.status or "")
        active = status in ACTIVE_AGENT_RUN_STATUSES
        return {
            "agent_run": run.model_dump(mode="json"),
            "active": active,
            "terminal": not active,
            "events": [
                item.model_dump(mode="json")
                for item in self.agent_service.list_events(session, run.id)
            ],
            "model_calls": [
                item.model_dump(mode="json")
                for item in self.agent_service.list_model_calls(session, run.id)
            ],
            "tool_calls": [
                item.model_dump(mode="json")
                for item in self.agent_service.list_tool_calls(session, run.id)
            ],
            "skill_activations": [
                item.model_dump(mode="json")
                for item in self.skill_service.list_activations(session, run.id)
            ],
            "tool_authorizations": [
                item.model_dump(mode="json")
                for item in self.agent_service.list_tool_authorizations(session, agent_run_id=run.id)
            ],
            "memory_entries": [
                item.model_dump(mode="json")
                for item in self.memory_service.list_entries_for_agent_run(session, run.id, limit=100)
            ],
        }
