from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from contextlib import suppress
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.api.routes.agents import agent_runs_ws_router, tool_authorizations_ws_router
from app.api.routes.evaluations import evaluation_activity_ws_router
from app.api.routes.governance import governance_proposal_activity_ws_router
from app.api.routes.runtime import ws_router
from app.api.routes.skills import pskill_activity_ws_router
from app.api.routes.skill_tests import test_run_activity_ws_router
from app.api.routes.system import root_router
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.core.observability import configure_observability
from app.pskills.exceptions import SkillsError
from app.jobs.worker import RuntimeJobWorker
from app.gateway.asr import AsrGateway, HttpAsrGateway
from app.gateway.inference import LlmInferenceGateway, OpenAICompatibleInferenceGateway
from app.gateway.gitlab import GitLabSkillSourceGateway, HttpGitLabSkillSourceGateway
from app.infra.database import DatabaseManager
from app.infra.object_store import ObjectStoreService


LOGGER = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    db_manager: DatabaseManager = app.state.db_manager
    configure_logging(settings.log_level, log_format=settings.log_format)
    observability = getattr(app.state, "observability", None)
    if observability is None:
        observability = configure_observability(app=app, settings=settings, engine=db_manager.engine)
        app.state.observability = observability
    LOGGER.info(
        "starting %s in %s mode",
        settings.app_name,
        settings.environment,
        extra={"service_name": settings.otel_service_name, "otel_enabled": settings.otel_enabled},
    )
    if settings.database_auto_create_schema:
        db_manager.create_schema()
    if settings.database_check_on_startup:
        db_manager.check_connection()
    worker_task: asyncio.Task[None] | None = None
    if settings.runtime_worker_enabled:
        worker = RuntimeJobWorker(
            settings=settings,
            database_manager=db_manager,
            gitlab_gateway=app.state.gitlab_gateway,
            inference_gateway=app.state.inference_gateway,
            asr_gateway=app.state.asr_gateway,
            object_store=app.state.object_store,
        )
        worker_task = asyncio.create_task(worker.run_forever())
    try:
        yield
    finally:
        if worker_task:
            worker_task.cancel()
            with suppress(asyncio.CancelledError):
                await worker_task
        db_manager.dispose()
        observability.shutdown()
        LOGGER.info("stopping %s", settings.app_name)


def create_app(
    settings: Settings | None = None,
    *,
    gitlab_gateway: GitLabSkillSourceGateway | None = None,
    inference_gateway: LlmInferenceGateway | None = None,
    asr_gateway: AsrGateway | None = None,
    object_store: ObjectStoreService | None = None,
) -> FastAPI:
    resolved_settings = settings or get_settings()
    configure_logging(resolved_settings.log_level, log_format=resolved_settings.log_format)

    app = FastAPI(
        title=resolved_settings.app_name,
        version=resolved_settings.app_version,
        debug=resolved_settings.debug,
        lifespan=lifespan,
    )

    app.state.settings = resolved_settings
    app.state.db_manager = DatabaseManager(resolved_settings.sqlalchemy_database_url)
    app.state.gitlab_gateway = gitlab_gateway or HttpGitLabSkillSourceGateway.from_settings(resolved_settings)
    app.state.inference_gateway = inference_gateway or OpenAICompatibleInferenceGateway.from_settings(resolved_settings)
    app.state.asr_gateway = asr_gateway or HttpAsrGateway.from_settings(resolved_settings)
    app.state.object_store = object_store or ObjectStoreService.from_settings(resolved_settings)
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
    app.include_router(ws_router)
    app.include_router(agent_runs_ws_router)
    app.include_router(tool_authorizations_ws_router)
    app.include_router(pskill_activity_ws_router)
    app.include_router(test_run_activity_ws_router)
    app.include_router(evaluation_activity_ws_router)
    app.include_router(governance_proposal_activity_ws_router)
    app.include_router(api_router, prefix=resolved_settings.api_prefix)
    # FastAPI OTel instrumentation must run before the ASGI middleware stack is built.
    app.state.observability = configure_observability(
        app=app,
        settings=resolved_settings,
        engine=app.state.db_manager.engine,
    )
    return app
