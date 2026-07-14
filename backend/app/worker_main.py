from __future__ import annotations

import asyncio
import logging
import signal
from contextlib import suppress
from typing import Any

from app.agent_harness.service import AgentHarnessService
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.core.observability import ObservabilityHandle, configure_observability
from app.domain.jobs.worker import RuntimeJobWorkerSupervisor
from app.domain.runtime.events import NoopRuntimeEventSink, PostgresRuntimeEventSink, RuntimeEventSink
from app.gateway.asr import HttpAsrGateway
from app.gateway.gitlab import HttpGitLabSkillSourceGateway
from app.gateway.inference import OpenAICompatibleInferenceGateway
from app.infra.database import DatabaseManager
from app.infra.object_store import ObjectStoreService


LOGGER = logging.getLogger(__name__)


class _WorkerInstrumentationTarget:
    """Minimal target that lets the shared OTel setup initialize worker spans."""

    def add_middleware(self, *_args: Any, **_kwargs: Any) -> None:
        return None


def _runtime_event_sink(settings: Settings) -> RuntimeEventSink:
    transport = str(getattr(settings, "runtime_event_transport", "auto") or "auto").strip().lower()
    is_postgres = settings.sqlalchemy_database_url.startswith(("postgresql://", "postgresql+psycopg://"))
    if transport == "inprocess":
        return NoopRuntimeEventSink()
    if transport == "postgres_notify" or is_postgres:
        return PostgresRuntimeEventSink(
            database_url=settings.sqlalchemy_database_url,
            channel=str(getattr(settings, "runtime_event_channel", "psop_runtime_events")),
        )
    return NoopRuntimeEventSink()


async def run_worker(settings: Settings) -> None:
    database_manager = DatabaseManager(settings.sqlalchemy_database_url)
    event_sink = _runtime_event_sink(settings)
    observability: ObservabilityHandle | None = None
    if settings.database_auto_create_schema:
        database_manager.create_schema()
    if settings.database_check_on_startup:
        database_manager.check_connection()
    worker_observability_settings = settings.model_copy(
        update={"otel_service_name": f"{settings.otel_service_name}-worker"}
    )
    observability = configure_observability(
        app=_WorkerInstrumentationTarget(),
        settings=worker_observability_settings,
        engine=database_manager.engine,
    )
    object_store = ObjectStoreService.from_settings(settings)

    supervisor = RuntimeJobWorkerSupervisor(
        settings=settings,
        database_manager=database_manager,
        gitlab_gateway=HttpGitLabSkillSourceGateway.from_settings(settings),
        inference_gateway=OpenAICompatibleInferenceGateway.from_settings(settings),
        asr_gateway=HttpAsrGateway.from_settings(settings),
        object_store=object_store,
        agent_harness_service=AgentHarnessService(settings=settings),
        runtime_event_sink=event_sink,
    )
    stop_requested = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop_requested.set)

    supervisor_task = asyncio.create_task(supervisor.run_forever())
    stop_task = asyncio.create_task(stop_requested.wait())
    LOGGER.info("PSOP runtime worker process started")
    try:
        done, _ = await asyncio.wait({supervisor_task, stop_task}, return_when=asyncio.FIRST_COMPLETED)
        if supervisor_task in done:
            await supervisor_task
            return

        supervisor.request_stop()
        grace_seconds = max(0.1, float(getattr(settings, "runtime_worker_shutdown_grace_seconds", 30)))
        try:
            await asyncio.wait_for(asyncio.shield(supervisor_task), timeout=grace_seconds)
        except asyncio.TimeoutError:
            LOGGER.warning(
                "worker shutdown grace elapsed; waiting for active handlers to preserve job consistency",
                extra={"grace_seconds": grace_seconds},
            )
            await supervisor_task
    finally:
        supervisor.request_stop()
        stop_task.cancel()
        with suppress(asyncio.CancelledError):
            await stop_task
        close = getattr(event_sink, "close", None)
        if callable(close):
            close()
        object_store.close()
        database_manager.dispose()
        if observability is not None:
            observability.shutdown()
        LOGGER.info("PSOP runtime worker process stopped")


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, log_format=settings.log_format)
    if not settings.runtime_worker_enabled:
        LOGGER.warning("runtime worker is disabled by PSOP_RUNTIME_WORKER_ENABLED")
        return
    asyncio.run(run_worker(settings))


if __name__ == "__main__":
    main()
