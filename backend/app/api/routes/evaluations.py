from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect, status
from sqlalchemy.orm import Session

from app.api.dependencies import get_db_session, get_evaluation_service, get_governance_service, get_job_query_service
from app.evaluations.activity import EvaluationActivityService
from app.evaluations.schemas import (
    RunEvaluationFindingResponse,
    RunEvaluationResponse,
    UpdateRunEvaluationFindingRequest,
)
from app.evaluations.service import EvaluationService
from app.governance.schemas import GovernanceProposalResponse
from app.governance.service import GovernanceService
from app.jobs.schemas import RuntimeJobResponse
from app.jobs.service import JobQueryService
from app.pskills.exceptions import SkillsError


router = APIRouter(prefix="/evaluations", tags=["evaluations"])
evaluation_activity_ws_router = APIRouter(prefix="/ws", tags=["ws"])


@router.post("/runs/{run_id}", response_model=RunEvaluationResponse, status_code=status.HTTP_201_CREATED)
def create_run_evaluation(
    run_id: str,
    session: Session = Depends(get_db_session),
    service: EvaluationService = Depends(get_evaluation_service),
) -> RunEvaluationResponse:
    return service.create_run_evaluation(session, run_id)


@router.post("/runs/{run_id}/queue", response_model=RuntimeJobResponse, status_code=status.HTTP_202_ACCEPTED)
def queue_run_evaluation(
    run_id: str,
    session: Session = Depends(get_db_session),
    service: EvaluationService = Depends(get_evaluation_service),
    job_query_service: JobQueryService = Depends(get_job_query_service),
) -> RuntimeJobResponse:
    job_id = service.enqueue_run_evaluation_job(session, run_id)
    return job_query_service.get_runtime_job(session, job_id)


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


@router.post(
    "/findings/{finding_id}/queue-proposal",
    response_model=RuntimeJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def queue_proposal_from_finding(
    finding_id: str,
    session: Session = Depends(get_db_session),
    governance_service: GovernanceService = Depends(get_governance_service),
    job_query_service: JobQueryService = Depends(get_job_query_service),
) -> RuntimeJobResponse:
    job_id = governance_service.enqueue_proposal_from_finding_job(session, finding_id)
    return job_query_service.get_runtime_job(session, job_id)


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


@evaluation_activity_ws_router.websocket("/evaluations/{evaluation_id}")
async def evaluation_activity_websocket(websocket: WebSocket, evaluation_id: str) -> None:
    await websocket.accept()
    await websocket.send_json(
        {
            "event_type": "ws.connected",
            "evaluation_id": evaluation_id,
            "occurred_at": None,
            "payload": {"message": "connected"},
        }
    )
    service = EvaluationActivityService()
    last_payload = ""
    try:
        while True:
            with websocket.app.state.db_manager.session() as session:
                snapshot = service.build_snapshot(session, evaluation_id)
            encoded = json.dumps(snapshot, ensure_ascii=False, sort_keys=True)
            if encoded != last_payload:
                await websocket.send_json(
                    {
                        "event_type": "evaluation.activity.snapshot",
                        "evaluation_id": evaluation_id,
                        "occurred_at": snapshot["evaluation"]["created_at"],
                        "payload": snapshot,
                    }
                )
                last_payload = encoded
            await asyncio.sleep(1)
    except SkillsError as exc:
        await websocket.send_json(
            {
                "event_type": "evaluation.activity.error",
                "evaluation_id": evaluation_id,
                "occurred_at": None,
                "payload": {
                    "code": exc.error_code,
                    "message": exc.message,
                    "details": exc.details,
                },
            }
        )
        await websocket.close(code=1008)
    except (RuntimeError, WebSocketDisconnect):
        return
