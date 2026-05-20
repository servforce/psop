from fastapi import APIRouter, Depends

from app.api.dependencies import get_app_settings
from app.api.schemas import HealthResponse, ServiceInfoResponse
from app.core.config import Settings


root_router = APIRouter(tags=["system"])
router = APIRouter(prefix="/system", tags=["system"])


def build_service_info(settings: Settings) -> ServiceInfoResponse:
    return ServiceInfoResponse(
        name=settings.app_name,
        version=settings.app_version,
        environment=settings.environment,
        api_prefix=settings.api_prefix,
        source_root=str(settings.repo_root),
        mode="scaffold",
        modules=[
            "backend",
            "docs",
            "scripts",
            "skills",
            "static",
            "tests",
        ],
    )


def build_health(settings: Settings) -> HealthResponse:
    return HealthResponse(
        status="ok",
        service=settings.app_name,
        version=settings.app_version,
        mode="scaffold",
    )


@root_router.get("/", response_model=ServiceInfoResponse)
async def service_info(settings: Settings = Depends(get_app_settings)) -> ServiceInfoResponse:
    return build_service_info(settings)


@root_router.get("/healthz", response_model=HealthResponse)
async def healthz(settings: Settings = Depends(get_app_settings)) -> HealthResponse:
    return build_health(settings)


@router.get("", response_model=ServiceInfoResponse)
async def api_service_info(settings: Settings = Depends(get_app_settings)) -> ServiceInfoResponse:
    return build_service_info(settings)


@router.get("/health", response_model=HealthResponse)
async def api_health(settings: Settings = Depends(get_app_settings)) -> HealthResponse:
    return build_health(settings)
