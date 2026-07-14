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
from app.api.routes.runtime import ws_router
from app.api.routes.system import root_router
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.core.observability import configure_observability, record_metric_histogram
from app.agent_harness.service import AgentHarnessService
from app.domain.skills.exceptions import SkillsError
from app.domain.jobs.worker import RuntimeJobWorker
from app.domain.runtime.events import (
    AsyncioRuntimeEventBus,
    CompositeRuntimeEventSink,
    NoopRuntimeEventSink,
    PostgresRuntimeEventListener,
    PostgresRuntimeEventSink,
)
from app.domain.runtime.service import RuntimeService
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
    app.state.terminal_upload_admission = asyncio.Semaphore(settings.terminal_object_store_io_workers)
    event_loop_lag_task: asyncio.Task[None] | None = None
    if observability.enabled:
        event_loop_lag_task = asyncio.create_task(_monitor_event_loop_lag())
    runtime_event_bus = AsyncioRuntimeEventBus(asyncio.get_running_loop())
    postgres_event_sink: PostgresRuntimeEventSink | None = None
    postgres_event_listener: PostgresRuntimeEventListener | None = None
    if _use_postgres_runtime_events(settings):
        postgres_event_sink = PostgresRuntimeEventSink(
            database_url=settings.sqlalchemy_database_url,
            channel=settings.runtime_event_channel,
        )
        postgres_event_listener = PostgresRuntimeEventListener(
            database_url=settings.sqlalchemy_database_url,
            channel=settings.runtime_event_channel,
            source_id=postgres_event_sink.source_id,
            callback=runtime_event_bus.publish,
        )
        postgres_event_listener.start()
        app.state.runtime_event_sink = CompositeRuntimeEventSink(runtime_event_bus, postgres_event_sink)
    else:
        app.state.runtime_event_sink = runtime_event_bus
    broadcaster_task = asyncio.create_task(_broadcast_runtime_events(app, runtime_event_bus))
    worker_task: asyncio.Task[None] | None = None
    if settings.runtime_worker_enabled and settings.runtime_worker_embedded_enabled:
        worker = RuntimeJobWorker(
            settings=settings,
            database_manager=db_manager,
            gitlab_gateway=app.state.gitlab_gateway,
            inference_gateway=app.state.inference_gateway,
            asr_gateway=app.state.asr_gateway,
            object_store=app.state.object_store,
            agent_harness_service=app.state.agent_harness_service,
            runtime_event_sink=runtime_event_bus,
        )
        worker_task = asyncio.create_task(worker.run_forever())
    try:
        yield
    finally:
        if event_loop_lag_task:
            event_loop_lag_task.cancel()
            with suppress(asyncio.CancelledError):
                await event_loop_lag_task
        if worker_task:
            worker_task.cancel()
            with suppress(asyncio.CancelledError):
                await worker_task
        if postgres_event_listener:
            postgres_event_listener.close()
        if postgres_event_sink:
            postgres_event_sink.close()
        runtime_event_bus.close()
        with suppress(asyncio.CancelledError):
            await broadcaster_task
        close_object_store = getattr(app.state.object_store, "close", None)
        if callable(close_object_store):
            close_object_store()
        db_manager.dispose()
        observability.shutdown()
        LOGGER.info("stopping %s", settings.app_name)


async def _monitor_event_loop_lag(*, sample_interval_seconds: float = 0.1) -> None:
    loop = asyncio.get_running_loop()
    while True:
        started_at = loop.time()
        await asyncio.sleep(sample_interval_seconds)
        lag_seconds = max(0.0, loop.time() - started_at - sample_interval_seconds)
        record_metric_histogram(
            "psop.event_loop.lag",
            lag_seconds,
            unit="s",
            description="Delay beyond the scheduled event-loop sampling interval",
        )


async def _broadcast_runtime_events(app: FastAPI, runtime_event_bus: AsyncioRuntimeEventBus) -> None:
    from app.api.routes.runtime import run_ws_hub

    while True:
        event = await runtime_event_bus.next_event()
        if AsyncioRuntimeEventBus.is_closed_event(event):
            return
        try:
            if "payload" not in event and event.get("event_type") in {
                "terminal.event.appended",
                "trace.event.appended",
                "run.task_status.updated",
            }:
                event = await asyncio.to_thread(_hydrate_runtime_event, app, event)
                if event is None:
                    continue
            run_id = str(event.get("run_id") or "")
            if run_id:
                await run_ws_hub.broadcast(run_id, event)
        except Exception:
            LOGGER.exception("runtime event broadcast failed; continuing", extra={"event_type": event.get("event_type")})


def _hydrate_runtime_event(app: FastAPI, hint: dict) -> dict | None:
    with app.state.db_manager.session() as session:
        service = RuntimeService(
            settings=app.state.settings,
            inference_gateway=app.state.inference_gateway,
            object_store=app.state.object_store,
            agent_harness_service=app.state.agent_harness_service,
            runtime_event_sink=NoopRuntimeEventSink(),
        )
        return service.runtime_event_envelope(
            session,
            event_type=str(hint.get("event_type") or ""),
            run_id=str(hint.get("run_id") or ""),
            seq_no=int(hint.get("seq_no") or hint.get("snapshot_seq") or 0),
        )


def _use_postgres_runtime_events(settings: Settings) -> bool:
    transport = str(settings.runtime_event_transport or "auto").strip().lower()
    if transport == "inprocess":
        return False
    if transport == "postgres_notify":
        return True
    return settings.sqlalchemy_database_url.startswith("postgresql")


def create_app(
    settings: Settings | None = None,
    *,
    gitlab_gateway: GitLabSkillSourceGateway | None = None,
    inference_gateway: LlmInferenceGateway | None = None,
    asr_gateway: AsrGateway | None = None,
    object_store: ObjectStoreService | None = None,
    agent_harness_service: AgentHarnessService | None = None,
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
    app.state.agent_harness_service = agent_harness_service or AgentHarnessService(settings=resolved_settings)
    app.state.runtime_event_sink = NoopRuntimeEventSink()

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
    app.include_router(api_router, prefix=resolved_settings.api_prefix)
    # FastAPI OTel instrumentation must run before the ASGI middleware stack is built.
    app.state.observability = configure_observability(
        app=app,
        settings=resolved_settings,
        engine=app.state.db_manager.engine,
    )
    # Keep CORS outside framework instrumentation so browser preflight requests
    # are answered by Starlette before observability middleware inspects routing.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=resolved_settings.cors_allow_origins,
        allow_credentials="*" not in resolved_settings.cors_allow_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    return app
