from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.dependencies import get_db_session, get_skills_service
from app.domain.skills.schemas import (
    CreateSkillRequest,
    PublishSkillRequest,
    PublishSkillResponse,
    SaveSkillSourceRequest,
    SkillDetailResponse,
    SkillPublishRecordResponse,
    SkillSourceResponse,
    SkillSummaryResponse,
    UpdateSkillRequest,
)
from app.domain.skills.service import SkillsService


router = APIRouter(prefix="/skills", tags=["skills"])


@router.get("", response_model=list[SkillSummaryResponse])
def list_skills(
    search: str | None = Query(default=None),
    status: str | None = Query(default=None),
    session: Session = Depends(get_db_session),
    service: SkillsService = Depends(get_skills_service),
) -> list[SkillSummaryResponse]:
    return service.list_skills(session, search=search, status=status)


@router.post("", response_model=SkillDetailResponse, status_code=201)
def create_skill(
    payload: CreateSkillRequest,
    session: Session = Depends(get_db_session),
    service: SkillsService = Depends(get_skills_service),
) -> SkillDetailResponse:
    return service.create_skill(session, payload)


@router.get("/{skill_id}", response_model=SkillDetailResponse)
def get_skill_detail(
    skill_id: str,
    session: Session = Depends(get_db_session),
    service: SkillsService = Depends(get_skills_service),
) -> SkillDetailResponse:
    return service.get_skill_detail(session, skill_id)


@router.patch("/{skill_id}", response_model=SkillDetailResponse)
def update_skill_metadata(
    skill_id: str,
    payload: UpdateSkillRequest,
    session: Session = Depends(get_db_session),
    service: SkillsService = Depends(get_skills_service),
) -> SkillDetailResponse:
    return service.update_skill_metadata(session, skill_id=skill_id, payload=payload)


@router.get("/{skill_id}/source", response_model=SkillSourceResponse)
def get_skill_source(
    skill_id: str,
    session: Session = Depends(get_db_session),
    service: SkillsService = Depends(get_skills_service),
) -> SkillSourceResponse:
    return service.get_skill_source(session, skill_id)


@router.put("/{skill_id}/source", response_model=SkillSourceResponse)
def save_skill_source(
    skill_id: str,
    payload: SaveSkillSourceRequest,
    session: Session = Depends(get_db_session),
    service: SkillsService = Depends(get_skills_service),
) -> SkillSourceResponse:
    return service.save_skill_source(session, skill_id=skill_id, payload=payload)


@router.post("/{skill_id}/publish", response_model=PublishSkillResponse)
def publish_skill(
    skill_id: str,
    payload: PublishSkillRequest,
    session: Session = Depends(get_db_session),
    service: SkillsService = Depends(get_skills_service),
) -> PublishSkillResponse:
    return service.publish_skill(session, skill_id=skill_id, payload=payload)


@router.get("/{skill_id}/publishes", response_model=list[SkillPublishRecordResponse])
def list_publish_records(
    skill_id: str,
    session: Session = Depends(get_db_session),
    service: SkillsService = Depends(get_skills_service),
) -> list[SkillPublishRecordResponse]:
    return service.list_publish_records(session, skill_id=skill_id)
