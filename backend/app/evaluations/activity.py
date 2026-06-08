from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.agents.service import AgentService
from app.evaluations.service import EvaluationService
from app.memory.service import MemoryService


ACTIVE_EVALUATOR_AGENT_STATUSES = {"queued", "running", "waiting_tool_authorization"}


class EvaluationActivityService:
    """Builds RunEvaluation snapshots from persisted evaluation and evaluator-agent facts."""

    def __init__(
        self,
        *,
        evaluation_service: EvaluationService | None = None,
        agent_service: AgentService | None = None,
        memory_service: MemoryService | None = None,
    ) -> None:
        self.evaluation_service = evaluation_service or EvaluationService()
        self.agent_service = agent_service or AgentService()
        self.memory_service = memory_service or MemoryService()

    def build_snapshot(self, session: Session, evaluation_id: str) -> dict[str, Any]:
        evaluation = self.evaluation_service.get_evaluation(session, evaluation_id)
        agent_run = self.agent_service.get_run(session, evaluation.agent_run_id)
        active = agent_run.status in ACTIVE_EVALUATOR_AGENT_STATUSES
        return {
            "evaluation": evaluation.model_dump(mode="json"),
            "findings": [item.model_dump(mode="json") for item in evaluation.findings],
            "agent_run": agent_run.model_dump(mode="json"),
            "agent_events": [
                item.model_dump(mode="json")
                for item in self.agent_service.list_events(session, evaluation.agent_run_id)
            ],
            "model_calls": [
                item.model_dump(mode="json")
                for item in self.agent_service.list_model_calls(session, evaluation.agent_run_id)
            ],
            "memory_entries": [
                item.model_dump(mode="json")
                for item in self.memory_service.list_entries_for_agent_run(session, evaluation.agent_run_id, limit=100)
            ],
            "active": active,
            "terminal": not active,
        }
