from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.api.routes.system import root_router
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.domain.skills.exceptions import SkillsError
from app.gateway.gitlab import GitLabSkillSourceGateway, HttpGitLabSkillSourceGateway
from app.infra.database import DatabaseManager


LOGGER = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    db_manager: DatabaseManager = app.state.db_manager
    configure_logging(settings.log_level)
    LOGGER.info("starting %s in %s mode", settings.app_name, settings.environment)
    if settings.database_auto_create_schema:
        db_manager.create_schema()
    if settings.database_check_on_startup:
        db_manager.check_connection()
    yield
    db_manager.dispose()
    LOGGER.info("stopping %s", settings.app_name)


def create_app(
    settings: Settings | None = None,
    *,
    gitlab_gateway: GitLabSkillSourceGateway | None = None,
) -> FastAPI:
    resolved_settings = settings or get_settings()

    app = FastAPI(
        title=resolved_settings.app_name,
        version=resolved_settings.app_version,
        debug=resolved_settings.debug,
        lifespan=lifespan,
    )

    app.state.settings = resolved_settings
    app.state.db_manager = DatabaseManager(resolved_settings.sqlalchemy_database_url)
    app.state.gitlab_gateway = gitlab_gateway or HttpGitLabSkillSourceGateway.from_settings(resolved_settings)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=resolved_settings.cors_allow_origins,
        allow_credentials="*" not in resolved_settings.cors_allow_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(SkillsError)
    async def handle_skills_error(_, exc: SkillsError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "code": exc.error_code,
                "message": exc.message,
                "details": exc.details,
            },
        )

    app.include_router(root_router)
    app.include_router(api_router, prefix=resolved_settings.api_prefix)
    return app
