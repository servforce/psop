from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.evaluations.models import RunEvaluation, RunEvaluationFinding
from app.runtime.models import Run, RunEvent, RunTrace, SessionTokenSnapshot


class EvaluationRepository:
    """Database access for run evaluations and findings."""

    def get_run(self, session: Session, run_id: str) -> Run | None:
        return session.get(Run, run_id)

    def get_evaluation(self, session: Session, evaluation_id: str) -> RunEvaluation | None:
        return session.get(RunEvaluation, evaluation_id)

    def get_finding(self, session: Session, finding_id: str) -> RunEvaluationFinding | None:
        return session.get(RunEvaluationFinding, finding_id)

    def list_evaluations_by_ids(self, session: Session, evaluation_ids: set[str]) -> list[RunEvaluation]:
        if not evaluation_ids:
            return []
        return list(
            session.scalars(select(RunEvaluation).where(RunEvaluation.id.in_(sorted(evaluation_ids)))).all()
        )

    def list_evaluations(
        self,
        session: Session,
        *,
        run_id: str | None = None,
        pskill_definition_id: str | None = None,
        overall_outcome: str | None = None,
        limit: int = 50,
    ) -> list[RunEvaluation]:
        query = select(RunEvaluation)
        if run_id:
            query = query.where(RunEvaluation.run_id == run_id)
        if pskill_definition_id:
            query = query.where(RunEvaluation.pskill_definition_id == pskill_definition_id)
        if overall_outcome:
            query = query.where(RunEvaluation.overall_outcome == overall_outcome)
        query = query.order_by(RunEvaluation.created_at.desc(), RunEvaluation.id.desc()).limit(limit)
        return list(session.scalars(query).all())

    def list_snapshots(self, session: Session, run_id: str) -> list[SessionTokenSnapshot]:
        return list(
            session.scalars(
                select(SessionTokenSnapshot)
                .where(SessionTokenSnapshot.run_id == run_id)
                .order_by(SessionTokenSnapshot.seq_no.asc())
            ).all()
        )

    def list_run_events(self, session: Session, run_id: str) -> list[RunEvent]:
        return list(
            session.scalars(select(RunEvent).where(RunEvent.run_id == run_id).order_by(RunEvent.seq_no.asc())).all()
        )

    def list_run_traces(self, session: Session, run_id: str) -> list[RunTrace]:
        return list(
            session.scalars(select(RunTrace).where(RunTrace.run_id == run_id).order_by(RunTrace.seq_no.asc())).all()
        )

    def list_evaluation_findings(self, session: Session, evaluation_id: str) -> list[RunEvaluationFinding]:
        return list(
            session.scalars(
                select(RunEvaluationFinding)
                .where(RunEvaluationFinding.evaluation_id == evaluation_id)
                .order_by(RunEvaluationFinding.created_at.asc(), RunEvaluationFinding.id.asc())
            ).all()
        )

    def list_findings(
        self,
        session: Session,
        *,
        status: str | None = None,
        category: str | None = None,
        severity: str | None = None,
        run_id: str | None = None,
        pskill_definition_id: str | None = None,
    ) -> list[RunEvaluationFinding]:
        query = select(RunEvaluationFinding).join(
            RunEvaluation,
            RunEvaluation.id == RunEvaluationFinding.evaluation_id,
        )
        if status:
            query = query.where(RunEvaluationFinding.status == status)
        if category:
            query = query.where(RunEvaluationFinding.category == category)
        if severity:
            query = query.where(RunEvaluationFinding.severity == severity)
        if run_id:
            query = query.where(RunEvaluation.run_id == run_id)
        if pskill_definition_id:
            query = query.where(RunEvaluation.pskill_definition_id == pskill_definition_id)
        query = query.order_by(RunEvaluationFinding.created_at.desc(), RunEvaluationFinding.id.desc())
        return list(session.scalars(query).all())
