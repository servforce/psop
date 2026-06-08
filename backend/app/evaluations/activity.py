from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.agents.service import AgentService
from app.evaluations.service import EvaluationService


ACTIVE_EVALUATOR_AGENT_STATUSES = {"queued", "running", "waiting_tool_authorization"}


class EvaluationActivityService:
    """Builds RunEvaluation snapshots from persisted evaluation and evaluator-agent facts."""

    def __init__(
        self,
        *,
        evaluation_service: EvaluationService | None = None,
        agent_service: AgentService | None = None,
    ) -> None:
        self.evaluation_service = evaluation_service or EvaluationService()
        self.agent_service = agent_service or AgentService()

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
            "active": active,
            "terminal": not active,
        }
