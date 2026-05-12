from __future__ import annotations

import builtins
import io
import json
import logging

import pytest

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
