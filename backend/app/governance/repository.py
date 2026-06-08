from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.evaluations.models import RunEvaluation, RunEvaluationFinding
from app.governance.models import PsopImprovementExperiment, PsopImprovementProposal


class GovernanceRepository:
    """Database access for PSOP improvement proposals and experiments."""

    def get_proposal(self, session: Session, proposal_id: str) -> PsopImprovementProposal | None:
        return session.get(PsopImprovementProposal, proposal_id)

    def list_proposals(self, session: Session, *, status: str | None = None) -> list[PsopImprovementProposal]:
        query = select(PsopImprovementProposal).order_by(
            PsopImprovementProposal.created_at.desc(),
            PsopImprovementProposal.id.desc(),
        )
        if status:
            query = query.where(PsopImprovementProposal.status == status)
        return list(session.scalars(query).all())

    def get_experiment(self, session: Session, experiment_id: str) -> PsopImprovementExperiment | None:
        return session.get(PsopImprovementExperiment, experiment_id)

    def list_experiments(
        self,
        session: Session,
        *,
        proposal_id: str | None = None,
        status: str | None = None,
        experiment_type: str | None = None,
    ) -> list[PsopImprovementExperiment]:
        query = select(PsopImprovementExperiment).order_by(
            PsopImprovementExperiment.created_at.desc(),
            PsopImprovementExperiment.id.desc(),
        )
        if proposal_id:
            query = query.where(PsopImprovementExperiment.proposal_id == proposal_id)
        if status:
            query = query.where(PsopImprovementExperiment.status == status)
        if experiment_type:
            query = query.where(PsopImprovementExperiment.experiment_type == experiment_type)
        return list(session.scalars(query).all())

    def list_experiments_for_proposal(
        self,
        session: Session,
        proposal_id: str,
    ) -> list[PsopImprovementExperiment]:
        return list(
            session.scalars(
                select(PsopImprovementExperiment)
                .where(PsopImprovementExperiment.proposal_id == proposal_id)
                .order_by(PsopImprovementExperiment.created_at.asc(), PsopImprovementExperiment.id.asc())
            ).all()
        )

    def get_finding(self, session: Session, finding_id: str) -> RunEvaluationFinding | None:
        return session.get(RunEvaluationFinding, finding_id)

    def get_evaluation(self, session: Session, evaluation_id: str) -> RunEvaluation | None:
        return session.get(RunEvaluation, evaluation_id)
