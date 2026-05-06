from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.dependencies import get_db_session, get_runtime_service
from app.domain.jobs.schemas import RuntimeJobResponse
from app.domain.runtime.schemas import (
    CreateInvocationRequest,
    InvocationResponse,
    ReplayDetailResponse,
    RunResponse,
    SessionTokenSnapshotResponse,
    TraceEventResponse,
)
from app.domain.runtime.service import RuntimeService


gateway_router = APIRouter(prefix="/gateway/invocations", tags=["gateway"])
runs_router = APIRouter(prefix="/runs", tags=["runs"])
replay_router = APIRouter(prefix="/replay", tags=["replay"])
runtime_router = APIRouter(prefix="/runtime", tags=["runtime"])


@gateway_router.post("", response_model=InvocationResponse, status_code=201)
def create_invocation(
    payload: CreateInvocationRequest,
    session: Session = Depends(get_db_session),
    service: RuntimeService = Depends(get_runtime_service),
) -> InvocationResponse:
    return service.create_invocation(session, payload)


@gateway_router.get("", response_model=list[InvocationResponse])
def list_invocations(
    skill_key: str | None = Query(default=None),
    status: str | None = Query(default=None),
    session: Session = Depends(get_db_session),
    service: RuntimeService = Depends(get_runtime_service),
) -> list[InvocationResponse]:
    return service.list_invocations(session, skill_key=skill_key, status=status)


@gateway_router.get("/{invocation_id}", response_model=InvocationResponse)
def get_invocation(
    invocation_id: str,
    session: Session = Depends(get_db_session),
    service: RuntimeService = Depends(get_runtime_service),
) -> InvocationResponse:
    return service.get_invocation(session, invocation_id)


@runs_router.get("", response_model=list[RunResponse])
def list_runs(
    status: str | None = Query(default=None),
    skill_id: str | None = Query(default=None),
    session: Session = Depends(get_db_session),
    service: RuntimeService = Depends(get_runtime_service),
) -> list[RunResponse]:
    return service.list_runs(session, status=status, skill_id=skill_id)


@runs_router.get("/{run_id}", response_model=RunResponse)
def get_run(
    run_id: str,
    session: Session = Depends(get_db_session),
    service: RuntimeService = Depends(get_runtime_service),
) -> RunResponse:
    return service.get_run(session, run_id)


@runs_router.get("/{run_id}/snapshots", response_model=list[SessionTokenSnapshotResponse])
def list_snapshots(
    run_id: str,
    session: Session = Depends(get_db_session),
    service: RuntimeService = Depends(get_runtime_service),
) -> list[SessionTokenSnapshotResponse]:
    return service.list_snapshots(session, run_id)


@runs_router.get("/{run_id}/trace-events", response_model=list[TraceEventResponse])
def list_trace_events(
    run_id: str,
    event_type: str | None = Query(default=None),
    session: Session = Depends(get_db_session),
    service: RuntimeService = Depends(get_runtime_service),
) -> list[TraceEventResponse]:
    return service.list_trace_events(session, run_id, event_type=event_type)


@replay_router.get("/runs", response_model=list[RunResponse])
def list_replay_runs(
    skill_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
    session: Session = Depends(get_db_session),
    service: RuntimeService = Depends(get_runtime_service),
) -> list[RunResponse]:
    return service.list_runs(session, skill_id=skill_id, status=status)


@replay_router.get("/runs/{run_id}", response_model=ReplayDetailResponse)
def get_replay(
    run_id: str,
    session: Session = Depends(get_db_session),
    service: RuntimeService = Depends(get_runtime_service),
) -> ReplayDetailResponse:
    return service.build_replay(session, run_id)


@runtime_router.get("/jobs", response_model=list[RuntimeJobResponse])
def list_runtime_jobs(
    status: str | None = Query(default=None),
    job_type: str | None = Query(default=None),
    session: Session = Depends(get_db_session),
    service: RuntimeService = Depends(get_runtime_service),
) -> list[RuntimeJobResponse]:
    return service.list_runtime_jobs(session, status=status, job_type=job_type)
