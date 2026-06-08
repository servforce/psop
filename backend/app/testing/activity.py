from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.testing.service import OPEN_SCENARIO_RUN_STATUSES, SkillTestService
from app.testing.schemas import SkillTestScenarioReviewResponse


class SkillTestRunActivityService:
    """Builds test-run review snapshots from persisted testing and runtime facts."""

    def __init__(self, *, testing_service: SkillTestService) -> None:
        self.testing_service = testing_service

    def build_snapshot(self, session: Session, test_run_id: str) -> dict[str, Any]:
        review = self.testing_service.get_review(session, test_run_id)
        agent_events = self.testing_service.list_run_events(session, test_run_id)
        active = self._is_active_review(review)
        return {
            "test_run": review.scenario_run.model_dump(mode="json"),
            "scenario": review.scenario.model_dump(mode="json"),
            "active": active,
            "terminal": not active,
            "review": review.model_dump(mode="json"),
            "agent_events": [item.model_dump(mode="json") for item in agent_events],
        }

    @staticmethod
    def _is_active_review(review: SkillTestScenarioReviewResponse) -> bool:
        scenario_run = review.scenario_run
        if scenario_run.status == "cancelled":
            return False
        if scenario_run.status in OPEN_SCENARIO_RUN_STATUSES:
            return True
        expectation_ids = {
            str(item.get("id"))
            for item in (review.scenario_timeline or {}).get("events", [])
            if str(item.get("lane_id") or "").startswith("expected.") and item.get("id")
        }
        evaluated_ids = {item.expectation_id for item in review.expectation_evaluations}
        return bool(expectation_ids - evaluated_ids)
