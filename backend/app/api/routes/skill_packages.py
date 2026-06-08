from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.api.dependencies import get_db_session, get_job_query_service, get_skill_package_service
from app.jobs.schemas import RuntimeJobResponse
from app.jobs.service import JobQueryService
from app.skills.schemas import (
    CreateSkillVersionRequest,
    QueueSkillSyncRequest,
    SkillPackageDetailResponse,
    SkillPackageSummaryResponse,
    SkillPackageSyncResponse,
    SkillVersionResponse,
)
from app.skills.service import SkillPackageService


router = APIRouter(prefix="/skills", tags=["skill-packages"])


@router.get("", response_model=list[SkillPackageSummaryResponse])
def list_skill_packages(
    scope: str | None = Query(default=None),
    status: str | None = Query(default=None),
    session: Session = Depends(get_db_session),
    service: SkillPackageService = Depends(get_skill_package_service),
) -> list[SkillPackageSummaryResponse]:
    service.sync_packages(session)
    return service.list_packages(session, scope=scope, status=status)


@router.post("/sync", response_model=SkillPackageSyncResponse)
def sync_skill_packages(
    session: Session = Depends(get_db_session),
    service: SkillPackageService = Depends(get_skill_package_service),
) -> SkillPackageSyncResponse:
    return service.sync_packages(session)


@router.post("/sync/queue", response_model=RuntimeJobResponse, status_code=status.HTTP_202_ACCEPTED)
def queue_skill_package_sync(
    payload: QueueSkillSyncRequest | None = None,
    session: Session = Depends(get_db_session),
    service: SkillPackageService = Depends(get_skill_package_service),
    job_query_service: JobQueryService = Depends(get_job_query_service),
) -> RuntimeJobResponse:
    job_id = service.enqueue_skill_sync_job(session, payload)
    return job_query_service.get_runtime_job(session, job_id)


@router.get("/{package_name}", response_model=SkillPackageDetailResponse)
def get_skill_package(
    package_name: str,
    session: Session = Depends(get_db_session),
    service: SkillPackageService = Depends(get_skill_package_service),
) -> SkillPackageDetailResponse:
    service.sync_packages(session)
    return service.get_package(session, package_name)


@router.get("/{package_name}/versions", response_model=list[SkillVersionResponse])
def list_skill_package_versions(
    package_name: str,
    session: Session = Depends(get_db_session),
    service: SkillPackageService = Depends(get_skill_package_service),
) -> list[SkillVersionResponse]:
    service.sync_packages(session)
    return service.list_versions(session, package_name)


@router.post("/{package_name}/versions", response_model=SkillPackageDetailResponse, status_code=status.HTTP_201_CREATED)
def create_skill_package_version(
    package_name: str,
    payload: CreateSkillVersionRequest,
    session: Session = Depends(get_db_session),
    service: SkillPackageService = Depends(get_skill_package_service),
) -> SkillPackageDetailResponse:
    service.sync_packages(session)
    return service.create_version(session, package_name, payload)


@router.post("/{package_name}/versions/{version_id}/validate", response_model=SkillVersionResponse)
def validate_skill_package_version(
    package_name: str,
    version_id: str,
    session: Session = Depends(get_db_session),
    service: SkillPackageService = Depends(get_skill_package_service),
) -> SkillVersionResponse:
    service.sync_packages(session)
    return service.validate_version(session, package_name, version_id)


@router.post("/{package_name}/versions/{version_id}/activate", response_model=SkillPackageDetailResponse)
def activate_skill_package_version(
    package_name: str,
    version_id: str,
    session: Session = Depends(get_db_session),
    service: SkillPackageService = Depends(get_skill_package_service),
) -> SkillPackageDetailResponse:
    service.sync_packages(session)
    return service.activate_version(session, package_name, version_id)
