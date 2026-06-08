from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.agents.service import AgentService
from app.governance.service import GovernanceService
from app.memory.service import MemoryService
from app.skills.service import SkillPackageService


ACTIVE_GOVERNANCE_AGENT_STATUSES = {"queued", "running", "waiting_tool_authorization"}


class GovernanceProposalActivityService:
    """Builds governance proposal snapshots from persisted proposal and agent facts."""

    def __init__(
        self,
        *,
        governance_service: GovernanceService | None = None,
        agent_service: AgentService | None = None,
        skill_service: SkillPackageService | None = None,
        memory_service: MemoryService | None = None,
    ) -> None:
        self.governance_service = governance_service or GovernanceService()
        self.agent_service = agent_service or AgentService()
        self.skill_service = skill_service or SkillPackageService()
        self.memory_service = memory_service or MemoryService()

    def build_snapshot(self, session: Session, proposal_id: str) -> dict[str, Any]:
        proposal = self.governance_service.get_proposal(session, proposal_id)
        agent_run = self.agent_service.get_run(session, proposal.agent_run_id)
        active = agent_run.status in ACTIVE_GOVERNANCE_AGENT_STATUSES
        return {
            "proposal": proposal.model_dump(mode="json"),
            "agent_run": agent_run.model_dump(mode="json"),
            "agent_events": [
                item.model_dump(mode="json")
                for item in self.agent_service.list_events(session, proposal.agent_run_id)
            ],
            "model_calls": [
                item.model_dump(mode="json")
                for item in self.agent_service.list_model_calls(session, proposal.agent_run_id)
            ],
            "tool_calls": [
                item.model_dump(mode="json")
                for item in self.agent_service.list_tool_calls(session, proposal.agent_run_id)
            ],
            "skill_activations": [
                item.model_dump(mode="json")
                for item in self.skill_service.list_activations(session, proposal.agent_run_id)
            ],
            "tool_authorizations": [
                item.model_dump(mode="json")
                for item in self.agent_service.list_tool_authorizations(session, agent_run_id=proposal.agent_run_id)
            ],
            "memory_entries": [
                item.model_dump(mode="json")
                for item in self.memory_service.list_entries_for_agent_run(session, proposal.agent_run_id, limit=100)
            ],
            "active": active,
            "terminal": not active,
        }
