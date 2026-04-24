from __future__ import annotations

from collections.abc import Generator

from fastapi import Request
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.domain.skills.service import SkillsService
from app.gateway.gitlab import GitLabSkillSourceGateway
from app.infra.database import DatabaseManager


def get_app_settings(request: Request) -> Settings:
    return request.app.state.settings  # type: ignore[return-value]


def get_database_manager(request: Request) -> DatabaseManager:
    return request.app.state.db_manager  # type: ignore[return-value]


def get_gitlab_gateway(request: Request) -> GitLabSkillSourceGateway:
    return request.app.state.gitlab_gateway  # type: ignore[return-value]


def get_db_session(request: Request) -> Generator[Session, None, None]:
    database_manager: DatabaseManager = get_database_manager(request)
    with database_manager.session() as session:
        yield session


def get_skills_service(request: Request) -> SkillsService:
    return SkillsService(
        settings=get_app_settings(request),
        gitlab_gateway=get_gitlab_gateway(request),
    )
