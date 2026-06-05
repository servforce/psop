from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.api.dependencies import get_db_session, get_governance_service
from app.governance.schemas import (
    GovernanceExperimentResponse,
    GovernanceProposalCreateRequest,
    GovernanceProposalResponse,
    GovernanceReviewRequest,
)
from app.governance.service import GovernanceService


router = APIRouter(prefix="/governance", tags=["governance"])


@router.get("/proposals", response_model=list[GovernanceProposalResponse])
def list_proposals(
    status: str | None = Query(default=None),
    session: Session = Depends(get_db_session),
    service: GovernanceService = Depends(get_governance_service),
) -> list[GovernanceProposalResponse]:
    return service.list_proposals(session, status=status)


@router.post("/proposals", response_model=GovernanceProposalResponse, status_code=status.HTTP_201_CREATED)
def create_proposal(
    payload: GovernanceProposalCreateRequest,
    session: Session = Depends(get_db_session),
    service: GovernanceService = Depends(get_governance_service),
) -> GovernanceProposalResponse:
    return service.create_proposal(session, payload)


@router.get("/proposals/{proposal_id}", response_model=GovernanceProposalResponse)
def get_proposal(
    proposal_id: str,
    session: Session = Depends(get_db_session),
    service: GovernanceService = Depends(get_governance_service),
) -> GovernanceProposalResponse:
    return service.get_proposal(session, proposal_id)


@router.post("/proposals/{proposal_id}/run-tests", response_model=GovernanceProposalResponse)
def run_proposal_tests(
    proposal_id: str,
    session: Session = Depends(get_db_session),
    service: GovernanceService = Depends(get_governance_service),
) -> GovernanceProposalResponse:
    return service.run_tests(session, proposal_id)


@router.post("/proposals/{proposal_id}/submit-review", response_model=GovernanceProposalResponse)
def submit_proposal_review(
    proposal_id: str,
    payload: GovernanceReviewRequest | None = None,
    session: Session = Depends(get_db_session),
    service: GovernanceService = Depends(get_governance_service),
) -> GovernanceProposalResponse:
    return service.submit_review(session, proposal_id, payload or GovernanceReviewRequest())


@router.post("/proposals/{proposal_id}/activate-canary", response_model=GovernanceProposalResponse)
def activate_proposal_canary(
    proposal_id: str,
    session: Session = Depends(get_db_session),
    service: GovernanceService = Depends(get_governance_service),
) -> GovernanceProposalResponse:
    return service.activate_canary(session, proposal_id)


@router.post("/proposals/{proposal_id}/rollback", response_model=GovernanceProposalResponse)
def rollback_proposal(
    proposal_id: str,
    session: Session = Depends(get_db_session),
    service: GovernanceService = Depends(get_governance_service),
) -> GovernanceProposalResponse:
    return service.rollback(session, proposal_id)


@router.get("/experiments/{experiment_id}", response_model=GovernanceExperimentResponse)
def get_experiment(
    experiment_id: str,
    session: Session = Depends(get_db_session),
    service: GovernanceService = Depends(get_governance_service),
) -> GovernanceExperimentResponse:
    return service.get_experiment(session, experiment_id)
