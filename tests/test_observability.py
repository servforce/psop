from __future__ import annotations

import builtins
import io
import json
import logging

import pytest
from fastapi.testclient import TestClient

from app.app import create_app
from app.core.config import Settings
from app.core.logging import JsonLogFormatter, configure_logging, log_context
from app.core.observability import configure_observability, start_span


def test_configure_logging_plain_and_json_does_not_duplicate_handlers() -> None:
    root_logger = logging.getLogger()
    original_handlers = list(root_logger.handlers)
    original_level = root_logger.level
    try:
        for handler in original_handlers:
            root_logger.removeHandler(handler)

        configure_logging("INFO", log_format="plain")
        assert len(root_logger.handlers) == 1
        assert root_logger.level == logging.INFO

        configure_logging("DEBUG", log_format="json")
        assert len(root_logger.handlers) == 1
        assert root_logger.level == logging.DEBUG
        assert isinstance(root_logger.handlers[0].formatter, JsonLogFormatter)
    finally:
        for handler in list(root_logger.handlers):
            root_logger.removeHandler(handler)
        for handler in original_handlers:
            root_logger.addHandler(handler)
        root_logger.setLevel(original_level)


def test_json_log_formatter_includes_context_and_redacts_sensitive_values() -> None:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonLogFormatter())
    logger = logging.getLogger("tests.observability")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)

    with log_context(skill_id="skill-1", compile_request_id="compile-1"):
        logger.info("observability smoke", extra={"api_key": "secret", "job_id": "job-1"})

    payload = json.loads(stream.getvalue())
    assert payload["message"] == "observability smoke"
    assert payload["skill_id"] == "skill-1"
    assert payload["compile_request_id"] == "compile-1"
    assert payload["job_id"] == "job-1"
    assert payload["api_key"] == "[REDACTED]"


def test_json_log_formatter_preserves_llm_token_usage() -> None:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonLogFormatter())
    logger = logging.getLogger("tests.observability.llm")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)

    logger.info(
        "llm usage",
        extra={
            "llm_usage": {
                "input_tokens": 11,
                "output_tokens": 7,
                "total_tokens": 18,
                "raw": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
            },
            "session_token": "secret",
        },
    )

    payload = json.loads(stream.getvalue())
    assert payload["llm_usage"]["input_tokens"] == 11
    assert payload["llm_usage"]["raw"]["prompt_tokens"] == 11
    assert payload["session_token"] == "[REDACTED]"


def test_observability_disabled_is_noop() -> None:
    settings = Settings(otel_enabled=False)
    handle = configure_observability(app=object(), settings=settings, engine=None)

    assert handle.enabled is False
    handle.shutdown()


def test_observability_missing_sdk_fails_open(monkeypatch) -> None:
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name.startswith("opentelemetry"):
            raise ImportError("otel intentionally unavailable")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    settings = Settings(otel_enabled=True)

    handle = configure_observability(app=object(), settings=settings, engine=None)

    assert handle.enabled is False


def test_observability_does_not_import_sqlalchemy_instrumentation(monkeypatch) -> None:
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "opentelemetry.instrumentation.sqlalchemy":
            raise AssertionError("SQLAlchemy OTel instrumentation should stay disabled")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    settings = Settings(otel_enabled=True, otel_traces_enabled=False, otel_logs_enabled=False)

    handle = configure_observability(app=object(), settings=settings, engine=object())

    assert handle.enabled is True


def test_start_span_preserves_business_exception() -> None:
    with pytest.raises(RuntimeError, match="business failed"):
        with start_span("test.business_exception"):
            raise RuntimeError("business failed")


def test_create_app_request_spans_share_one_trace(monkeypatch) -> None:
    import app.core.observability as observability

    monkeypatch.setattr(observability, "_build_otlp_span_exporter", lambda _settings: None)
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonLogFormatter())
    logger = logging.getLogger("tests.observability.request")
    original_handlers = list(logger.handlers)
    original_propagate = logger.propagate
    original_level = logger.level
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)

    settings = Settings(
        database_url="sqlite+pysqlite:///:memory:",
        database_check_on_startup=False,
        database_auto_create_schema=False,
        runtime_worker_enabled=False,
        otel_enabled=True,
        otel_traces_enabled=True,
        otel_logs_enabled=False,
    )
    app = create_app(settings)

    @app.get("/probe-trace")
    def probe_trace() -> dict[str, bool]:
        logger.info("request start")
        with start_span("probe.child1"):
            logger.info("child 1")
        with start_span("probe.child2"):
            logger.info("child 2")
        return {"ok": True}

    try:
        with TestClient(app) as client:
            response = client.get("/probe-trace")
    finally:
        logger.handlers = original_handlers
        logger.propagate = original_propagate
        logger.setLevel(original_level)

    assert response.status_code == 200
    payloads = [json.loads(line) for line in stream.getvalue().splitlines()]
    by_message = {payload["message"]: payload for payload in payloads}

    request_trace_id = by_message["request start"]["trace_id"]
    assert by_message["child 1"]["trace_id"] == request_trace_id
    assert by_message["child 2"]["trace_id"] == request_trace_id
    assert by_message["child 1"]["span_id"] != by_message["child 2"]["span_id"]


def test_cors_preflight_succeeds_with_observability_enabled() -> None:
    settings = Settings(
        database_url="sqlite+pysqlite:///:memory:",
        database_check_on_startup=False,
        database_auto_create_schema=False,
        runtime_worker_enabled=False,
        otel_enabled=True,
        otel_traces_enabled=False,
        otel_logs_enabled=False,
    )
    app = create_app(settings)

    with TestClient(app) as client:
        response = client.options(
            "/api/v1/skills",
            headers={
                "Origin": "http://10.0.0.20:4173",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "content-type",
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "*"
