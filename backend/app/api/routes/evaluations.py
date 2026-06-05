from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.api.dependencies import get_db_session, get_evaluation_service, get_governance_service
from app.evaluations.schemas import (
    RunEvaluationFindingResponse,
    RunEvaluationResponse,
    UpdateRunEvaluationFindingRequest,
)
from app.evaluations.service import EvaluationService
from app.governance.schemas import GovernanceProposalResponse
from app.governance.service import GovernanceService


router = APIRouter(prefix="/evaluations", tags=["evaluations"])


@router.post("/runs/{run_id}", response_model=RunEvaluationResponse, status_code=status.HTTP_201_CREATED)
def create_run_evaluation(
    run_id: str,
    session: Session = Depends(get_db_session),
    service: EvaluationService = Depends(get_evaluation_service),
) -> RunEvaluationResponse:
    return service.create_run_evaluation(session, run_id)


@router.get("/findings", response_model=list[RunEvaluationFindingResponse])
def list_findings(
    status: str | None = Query(default=None),
    category: str | None = Query(default=None),
    severity: str | None = Query(default=None),
    run_id: str | None = Query(default=None),
    pskill_definition_id: str | None = Query(default=None),
    session: Session = Depends(get_db_session),
    service: EvaluationService = Depends(get_evaluation_service),
) -> list[RunEvaluationFindingResponse]:
    return service.list_findings(
        session,
        status=status,
        category=category,
        severity=severity,
        run_id=run_id,
        pskill_definition_id=pskill_definition_id,
    )


@router.patch("/findings/{finding_id}", response_model=RunEvaluationFindingResponse)
def update_finding_status(
    finding_id: str,
    payload: UpdateRunEvaluationFindingRequest,
    session: Session = Depends(get_db_session),
    service: EvaluationService = Depends(get_evaluation_service),
) -> RunEvaluationFindingResponse:
    return service.update_finding_status(session, finding_id, payload)


@router.post(
    "/findings/{finding_id}/create-proposal",
    response_model=GovernanceProposalResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_proposal_from_finding(
    finding_id: str,
    session: Session = Depends(get_db_session),
    governance_service: GovernanceService = Depends(get_governance_service),
) -> GovernanceProposalResponse:
    return governance_service.create_proposal_from_finding(session, finding_id)


@router.get("/{evaluation_id}", response_model=RunEvaluationResponse)
def get_evaluation(
    evaluation_id: str,
    session: Session = Depends(get_db_session),
    service: EvaluationService = Depends(get_evaluation_service),
) -> RunEvaluationResponse:
    return service.get_evaluation(session, evaluation_id)


@router.get("/{evaluation_id}/findings", response_model=list[RunEvaluationFindingResponse])
def list_evaluation_findings(
    evaluation_id: str,
    session: Session = Depends(get_db_session),
    service: EvaluationService = Depends(get_evaluation_service),
) -> list[RunEvaluationFindingResponse]:
    return service.list_evaluation_findings(session, evaluation_id)
