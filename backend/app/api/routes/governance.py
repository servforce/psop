from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect, status
from sqlalchemy.orm import Session

from app.api.dependencies import get_db_session, get_governance_service
from app.governance.activity import GovernanceProposalActivityService
from app.governance.schemas import (
    GovernanceExperimentResponse,
    GovernanceProposalCreateRequest,
    GovernanceProposalResponse,
    GovernanceReviewRequest,
)
from app.governance.service import GovernanceService
from app.pskills.exceptions import SkillsError


router = APIRouter(prefix="/governance", tags=["governance"])
governance_proposal_activity_ws_router = APIRouter(prefix="/ws", tags=["ws"])


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


@router.get("/proposals/{proposal_id}/experiments", response_model=list[GovernanceExperimentResponse])
def list_proposal_experiments(
    proposal_id: str,
    status: str | None = Query(default=None),
    experiment_type: str | None = Query(default=None),
    session: Session = Depends(get_db_session),
    service: GovernanceService = Depends(get_governance_service),
) -> list[GovernanceExperimentResponse]:
    return service.list_experiments(
        session,
        proposal_id=proposal_id,
        status=status,
        experiment_type=experiment_type,
    )


@router.get("/experiments", response_model=list[GovernanceExperimentResponse])
def list_experiments(
    proposal_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
    experiment_type: str | None = Query(default=None),
    session: Session = Depends(get_db_session),
    service: GovernanceService = Depends(get_governance_service),
) -> list[GovernanceExperimentResponse]:
    return service.list_experiments(
        session,
        proposal_id=proposal_id,
        status=status,
        experiment_type=experiment_type,
    )


@router.get("/experiments/{experiment_id}", response_model=GovernanceExperimentResponse)
def get_experiment(
    experiment_id: str,
    session: Session = Depends(get_db_session),
    service: GovernanceService = Depends(get_governance_service),
) -> GovernanceExperimentResponse:
    return service.get_experiment(session, experiment_id)


@governance_proposal_activity_ws_router.websocket("/governance/proposals/{proposal_id}")
async def governance_proposal_activity_websocket(websocket: WebSocket, proposal_id: str) -> None:
    await websocket.accept()
    await websocket.send_json(
        {
            "event_type": "ws.connected",
            "proposal_id": proposal_id,
            "occurred_at": None,
            "payload": {"message": "connected"},
        }
    )
    service = GovernanceProposalActivityService()
    last_payload = ""
    try:
        while True:
            with websocket.app.state.db_manager.session() as session:
                snapshot = service.build_snapshot(session, proposal_id)
            encoded = json.dumps(snapshot, ensure_ascii=False, sort_keys=True)
            if encoded != last_payload:
                await websocket.send_json(
                    {
                        "event_type": "governance_proposal.activity.snapshot",
                        "proposal_id": proposal_id,
                        "occurred_at": snapshot["proposal"]["updated_at"],
                        "payload": snapshot,
                    }
                )
                last_payload = encoded
            await asyncio.sleep(1)
    except SkillsError as exc:
        await websocket.send_json(
            {
                "event_type": "governance_proposal.activity.error",
                "proposal_id": proposal_id,
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
