from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.api.dependencies import get_app_settings, get_db_session, get_observability_service
from app.core.config import Settings
from app.observability.schemas import DashboardMetricsResponse, ObservabilityMetricsResponse
from app.observability.service import ObservabilityService


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
