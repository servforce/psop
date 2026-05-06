from __future__ import annotations

import logging
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from app.core.config import Settings

LOGGER = logging.getLogger(__name__)
_PROVIDERS_INITIALIZED = False
_FASTAPI_INSTRUMENTED_IDS: set[int] = set()
_SQLALCHEMY_INSTRUMENTED_URLS: set[str] = set()
_HTTPX_INSTRUMENTED = False


@dataclass(slots=True)
class ObservabilityHandle:
    """Holds SDK providers so lifespan shutdown can flush them."""

    enabled: bool = False
    tracer_provider: Any | None = None
    logger_provider: Any | None = None

    def shutdown(self) -> None:
        for provider in (self.logger_provider, self.tracer_provider):
            if provider is None:
                continue
            try:
                provider.shutdown()
            except Exception as exc:  # pragma: no cover - defensive shutdown guard
                LOGGER.warning("OpenTelemetry provider shutdown failed: %s", exc)


class _NoopSpan:
    def set_attribute(self, _key: str, _value: Any) -> None:
        return None

    def add_event(self, _name: str, attributes: dict[str, Any] | None = None) -> None:
        return None

    def record_exception(self, _exception: Exception) -> None:
        return None

    def set_status(self, _status: Any) -> None:
        return None


def configure_observability(*, app: Any, settings: Settings, engine: Any | None = None) -> ObservabilityHandle:
    """Configure OTel traces/logs and best-effort framework instrumentation.

    The function intentionally fails open. A collector outage or missing optional
    package should reduce observability, not prevent PSOP from starting.
    """

    if not settings.otel_enabled:
        LOGGER.info("OpenTelemetry disabled by configuration")
        return ObservabilityHandle(enabled=False)

    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
    except Exception as exc:
        LOGGER.warning("OpenTelemetry SDK is unavailable; continuing without OTel: %s", exc)
        return ObservabilityHandle(enabled=False)

    resource = Resource.create(
        {
            "service.name": settings.otel_service_name,
            "service.version": settings.app_version,
            "deployment.environment": settings.environment,
        }
    )

    handle = ObservabilityHandle(enabled=True)
    global _PROVIDERS_INITIALIZED
    if not _PROVIDERS_INITIALIZED:
        if settings.otel_traces_enabled:
            handle.tracer_provider = _configure_traces(settings=settings, resource=resource, trace_module=trace)
        if settings.otel_logs_enabled:
            handle.logger_provider = _configure_logs(settings=settings, resource=resource)
        _PROVIDERS_INITIALIZED = True

    _instrument_fastapi(app)
    _instrument_httpx()
    if engine is not None:
        _instrument_sqlalchemy(engine)
    return handle


@contextmanager
def start_span(name: str, **attributes: Any) -> Iterator[Any]:
    """Start a current span if OTel is available, otherwise yield a no-op span."""

    try:
        from opentelemetry import trace

        tracer = trace.get_tracer("psop.backend")
        span_manager = tracer.start_as_current_span(name)
        span = span_manager.__enter__()
    except Exception:
        yield _NoopSpan()
        return

    try:
        set_span_attributes(span, attributes)
        yield span
    except BaseException:
        exc_info = sys.exc_info()
        try:
            span_manager.__exit__(*exc_info)
        except Exception as exc:  # pragma: no cover - defensive OTel guard
            LOGGER.warning("OpenTelemetry span exit failed after exception: %s", exc)
        raise
    else:
        try:
            span_manager.__exit__(None, None, None)
        except Exception as exc:  # pragma: no cover - defensive OTel guard
            LOGGER.warning("OpenTelemetry span exit failed: %s", exc)


def set_span_attributes(span: Any, attributes: dict[str, Any]) -> None:
    for key, value in attributes.items():
        if value is None:
            continue
        try:
            span.set_attribute(key, value)
        except Exception:
            continue


def add_span_event(name: str, **attributes: Any) -> None:
    try:
        from opentelemetry import trace

        span = trace.get_current_span()
        span.add_event(name, attributes={key: value for key, value in attributes.items() if value is not None})
    except Exception:
        return


def record_span_exception(span: Any, exc: Exception) -> None:
    try:
        span.record_exception(exc)
        from opentelemetry.trace.status import Status, StatusCode

        span.set_status(Status(StatusCode.ERROR, str(exc)))
    except Exception:
        return


def _configure_traces(*, settings: Settings, resource: Any, trace_module: Any) -> Any | None:
    try:
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
    except Exception as exc:
        LOGGER.warning("OpenTelemetry trace SDK is unavailable; traces disabled: %s", exc)
        return None

    provider = TracerProvider(resource=resource)
    exporter = _build_otlp_span_exporter(settings)
    if exporter is not None:
        provider.add_span_processor(BatchSpanProcessor(exporter))
    if settings.otel_console_exporter:
        provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))

    try:
        trace_module.set_tracer_provider(provider)
    except Exception as exc:
        LOGGER.warning("OpenTelemetry tracer provider was not installed: %s", exc)
        return None
    return provider


def _configure_logs(*, settings: Settings, resource: Any) -> Any | None:
    import warnings

    try:
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=DeprecationWarning)
            from opentelemetry._logs import set_logger_provider
            from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
            from opentelemetry.sdk._logs.export import BatchLogRecordProcessor, ConsoleLogExporter
    except Exception as exc:
        LOGGER.warning("OpenTelemetry log SDK is unavailable; OTLP logs disabled: %s", exc)
        return None

    provider = LoggerProvider(resource=resource)
    exporter = _build_otlp_log_exporter(settings)
    if exporter is not None:
        provider.add_log_record_processor(BatchLogRecordProcessor(exporter))
    if settings.otel_console_exporter:
        provider.add_log_record_processor(BatchLogRecordProcessor(ConsoleLogExporter()))

    try:
        set_logger_provider(provider)
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=DeprecationWarning)
            logging.getLogger().addHandler(LoggingHandler(level=logging.NOTSET, logger_provider=provider))
    except Exception as exc:
        LOGGER.warning("OpenTelemetry log provider was not installed: %s", exc)
        return None
    return provider


def _build_otlp_span_exporter(settings: Settings) -> Any | None:
    if settings.otel_exporter_otlp_protocol != "http/protobuf":
        LOGGER.warning(
            "OpenTelemetry protocol %s is not enabled in this build; skipping OTLP trace exporter",
            settings.otel_exporter_otlp_protocol,
        )
        return None
    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        return OTLPSpanExporter(endpoint=f"{settings.otel_exporter_otlp_endpoint.rstrip('/')}/v1/traces")
    except Exception as exc:
        LOGGER.warning("OpenTelemetry OTLP trace exporter initialization failed: %s", exc)
        return None


def _build_otlp_log_exporter(settings: Settings) -> Any | None:
    if settings.otel_exporter_otlp_protocol != "http/protobuf":
        LOGGER.warning(
            "OpenTelemetry protocol %s is not enabled in this build; skipping OTLP log exporter",
            settings.otel_exporter_otlp_protocol,
        )
        return None
    try:
        from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter

        return OTLPLogExporter(endpoint=f"{settings.otel_exporter_otlp_endpoint.rstrip('/')}/v1/logs")
    except Exception as exc:
        LOGGER.warning("OpenTelemetry OTLP log exporter initialization failed: %s", exc)
        return None


def _instrument_fastapi(app: Any) -> None:
    app_id = id(app)
    if app_id in _FASTAPI_INSTRUMENTED_IDS:
        return
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app)
        _FASTAPI_INSTRUMENTED_IDS.add(app_id)
    except Exception as exc:
        LOGGER.warning("FastAPI OpenTelemetry instrumentation skipped: %s", exc)


def _instrument_httpx() -> None:
    global _HTTPX_INSTRUMENTED
    if _HTTPX_INSTRUMENTED:
        return
    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

        HTTPXClientInstrumentor().instrument()
        _HTTPX_INSTRUMENTED = True
    except Exception as exc:
        LOGGER.warning("httpx OpenTelemetry instrumentation skipped: %s", exc)


def _instrument_sqlalchemy(engine: Any) -> None:
    url = str(getattr(engine, "url", ""))
    if url in _SQLALCHEMY_INSTRUMENTED_URLS:
        return
    try:
        from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

        SQLAlchemyInstrumentor().instrument(engine=engine)
        _SQLALCHEMY_INSTRUMENTED_URLS.add(url)
    except Exception as exc:
        LOGGER.warning("SQLAlchemy OpenTelemetry instrumentation skipped: %s", exc)
