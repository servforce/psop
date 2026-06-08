from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.api.dependencies import get_app_settings, get_db_session, get_observability_service
from app.agents.schemas import (
    AgentEventResponse,
    AgentModelCallResponse,
    AgentToolAuthorizationResponse,
    AgentToolCallResponse,
)
from app.core.config import Settings
from app.observability.schemas import DashboardMetricsResponse, ObservabilityMetricsResponse
from app.observability.service import ObservabilityService
from app.runtime.schemas import RunEventResponse, RunTraceResponse
from app.skills.schemas import SkillActivationResponse


router = APIRouter(prefix="/observability", tags=["observability"])


@router.get("/dashboard", response_model=DashboardMetricsResponse)
def get_dashboard_metrics(
    window_hours: int = Query(default=24, ge=1, le=24 * 30),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_app_settings),
    service: ObservabilityService = Depends(get_observability_service),
) -> DashboardMetricsResponse:
    return service.get_dashboard_metrics(session, settings=settings, window_hours=window_hours)


@router.get("/metrics", response_model=ObservabilityMetricsResponse)
def get_observability_metrics(
    request: Request,
    window_hours: int = Query(default=24, ge=1, le=24 * 30),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_app_settings),
    service: ObservabilityService = Depends(get_observability_service),
) -> ObservabilityMetricsResponse:
    observability = getattr(request.app.state, "observability", None)
    otel_configured = bool(getattr(observability, "enabled", False))
    return service.get_global_metrics(
        session,
        settings=settings,
        window_hours=window_hours,
        otel_configured=otel_configured,
    )


@router.get("/run-traces", response_model=list[RunTraceResponse])
def list_observability_run_traces(
    window_hours: int = Query(default=24, ge=1, le=24 * 30),
    run_id: str | None = Query(default=None),
    run_trace_event_type: str | None = Query(default=None),
    event_type: str | None = Query(default=None, deprecated=True),
    phase: str | None = Query(default=None),
    agent_run_id: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    session: Session = Depends(get_db_session),
    service: ObservabilityService = Depends(get_observability_service),
) -> list[RunTraceResponse]:
    return service.list_run_traces(
        session,
        window_hours=window_hours,
        run_id=run_id,
        event_type=run_trace_event_type or event_type,
        phase=phase,
        agent_run_id=agent_run_id,
        limit=limit,
    )


@router.get("/run-events", response_model=list[RunEventResponse])
def list_observability_run_events(
    window_hours: int = Query(default=24, ge=1, le=24 * 30),
    run_id: str | None = Query(default=None),
    event_kind: str | None = Query(default=None),
    direction: str | None = Query(default=None),
    agent_run_id: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    session: Session = Depends(get_db_session),
    service: ObservabilityService = Depends(get_observability_service),
) -> list[RunEventResponse]:
    return service.list_run_events(
        session,
        window_hours=window_hours,
        run_id=run_id,
        event_kind=event_kind,
        direction=direction,
        agent_run_id=agent_run_id,
        limit=limit,
    )


@router.get("/agent-events", response_model=list[AgentEventResponse])
def list_observability_agent_events(
    window_hours: int = Query(default=24, ge=1, le=24 * 30),
    agent_run_id: str | None = Query(default=None),
    agent_key: str | None = Query(default=None),
    run_id: str | None = Query(default=None),
    event_type: str | None = Query(default=None),
    phase: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    session: Session = Depends(get_db_session),
    service: ObservabilityService = Depends(get_observability_service),
) -> list[AgentEventResponse]:
    return service.list_agent_events(
        session,
        window_hours=window_hours,
        agent_run_id=agent_run_id,
        agent_key=agent_key,
        run_id=run_id,
        event_type=event_type,
        phase=phase,
        limit=limit,
    )


@router.get("/tool-calls", response_model=list[AgentToolCallResponse])
def list_observability_tool_calls(
    window_hours: int = Query(default=24, ge=1, le=24 * 30),
    agent_run_id: str | None = Query(default=None),
    agent_key: str | None = Query(default=None),
    run_id: str | None = Query(default=None),
    tool_name: str | None = Query(default=None),
    status: str | None = Query(default=None),
    side_effect_level: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    session: Session = Depends(get_db_session),
    service: ObservabilityService = Depends(get_observability_service),
) -> list[AgentToolCallResponse]:
    return service.list_tool_calls(
        session,
        window_hours=window_hours,
        agent_run_id=agent_run_id,
        agent_key=agent_key,
        run_id=run_id,
        tool_name=tool_name,
        status=status,
        side_effect_level=side_effect_level,
        limit=limit,
    )


@router.get("/model-calls", response_model=list[AgentModelCallResponse])
def list_observability_model_calls(
    window_hours: int = Query(default=24, ge=1, le=24 * 30),
    agent_run_id: str | None = Query(default=None),
    agent_key: str | None = Query(default=None),
    run_id: str | None = Query(default=None),
    provider: str | None = Query(default=None),
    status: str | None = Query(default=None),
    route_key: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    session: Session = Depends(get_db_session),
    service: ObservabilityService = Depends(get_observability_service),
) -> list[AgentModelCallResponse]:
    return service.list_model_calls(
        session,
        window_hours=window_hours,
        agent_run_id=agent_run_id,
        agent_key=agent_key,
        run_id=run_id,
        provider=provider,
        status=status,
        route_key=route_key,
        limit=limit,
    )


@router.get("/skill-activations", response_model=list[SkillActivationResponse])
def list_observability_skill_activations(
    window_hours: int = Query(default=24, ge=1, le=24 * 30),
    agent_run_id: str | None = Query(default=None),
    agent_key: str | None = Query(default=None),
    run_id: str | None = Query(default=None),
    package_id: str | None = Query(default=None),
    version_id: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    session: Session = Depends(get_db_session),
    service: ObservabilityService = Depends(get_observability_service),
) -> list[SkillActivationResponse]:
    return service.list_skill_activations(
        session,
        window_hours=window_hours,
        agent_run_id=agent_run_id,
        agent_key=agent_key,
        run_id=run_id,
        package_id=package_id,
        version_id=version_id,
        limit=limit,
    )


@router.get("/tool-authorizations", response_model=list[AgentToolAuthorizationResponse])
def list_observability_tool_authorizations(
    window_hours: int = Query(default=24, ge=1, le=24 * 30),
    agent_run_id: str | None = Query(default=None),
    agent_key: str | None = Query(default=None),
    run_id: str | None = Query(default=None),
    tool_name: str | None = Query(default=None),
    status: str | None = Query(default=None),
    risk_level: str | None = Query(default=None),
    side_effect_level: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    session: Session = Depends(get_db_session),
    service: ObservabilityService = Depends(get_observability_service),
) -> list[AgentToolAuthorizationResponse]:
    return service.list_tool_authorizations(
        session,
        window_hours=window_hours,
        agent_run_id=agent_run_id,
        agent_key=agent_key,
        run_id=run_id,
        tool_name=tool_name,
        status=status,
        risk_level=risk_level,
        side_effect_level=side_effect_level,
        limit=limit,
    )
