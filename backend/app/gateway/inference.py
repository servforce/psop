from __future__ import annotations

from dataclasses import dataclass
import logging
import time
from typing import Protocol

import httpx

from app.core.config import Settings
from app.core.observability import record_span_exception, set_span_attributes, start_span
from app.domain.skills.exceptions import SkillsConfigurationError, SkillsGatewayError

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class LlmCompletion:
    content: str
    provider: str
    model: str
    raw_response: dict


class LlmInferenceGateway(Protocol):
    def complete(self, *, system_prompt: str, user_prompt: str, route_key: str = "default") -> LlmCompletion:
        ...


class OpenAICompatibleInferenceGateway:
    """Minimal OpenAI-compatible chat completion gateway for RuntimeKernel."""

    def __init__(
        self,
        *,
        provider: str,
        api_base_url: str,
        api_key: str | None,
        default_model: str,
        timeout_seconds: float = 60.0,
    ) -> None:
        self.provider = provider
        self.api_base_url = api_base_url.rstrip("/")
        self.api_key = api_key
        self.default_model = default_model
        self.timeout_seconds = timeout_seconds

    @classmethod
    def from_settings(cls, settings: Settings) -> "OpenAICompatibleInferenceGateway":
        return cls(
            provider=settings.llm_provider,
            api_base_url=settings.llm_api_base_url,
            api_key=settings.llm_api_key,
            default_model=settings.llm_default_model,
            timeout_seconds=settings.llm_timeout_seconds,
        )

    def complete(self, *, system_prompt: str, user_prompt: str, route_key: str = "default") -> LlmCompletion:
        if not self.api_key:
            raise SkillsConfigurationError("未配置 LLM API Key，无法执行真实运行链路。")

        model = route_key if route_key and route_key != "default" else self.default_model
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.2,
        }
        headers = {"Authorization": f"Bearer {self.api_key}"}

        timeout = httpx.Timeout(self.timeout_seconds, connect=min(15.0, self.timeout_seconds))
        started_at = time.perf_counter()
        try:
            with start_span(
                "gateway.inference",
                provider=self.provider,
                model=model,
                route_key=route_key,
                api_base_url=self.api_base_url,
            ) as span:
                with httpx.Client(timeout=timeout, headers=headers) as client:
                    response = client.post(f"{self.api_base_url}/chat/completions", json=payload)
                elapsed_ms = int((time.perf_counter() - started_at) * 1000)
                set_span_attributes(span, {"http.status_code": response.status_code, "duration_ms": elapsed_ms})
                LOGGER.info(
                    "LLM inference completed",
                    extra={
                        "provider": self.provider,
                        "model": model,
                        "route_key": route_key,
                        "status_code": response.status_code,
                        "duration_ms": elapsed_ms,
                    },
                )
        except httpx.HTTPError as exc:
            error_type = exc.__class__.__name__
            elapsed_ms = int((time.perf_counter() - started_at) * 1000)
            with start_span(
                "gateway.inference.error",
                provider=self.provider,
                model=model,
                route_key=route_key,
                api_base_url=self.api_base_url,
                duration_ms=elapsed_ms,
            ) as span:
                record_span_exception(span, exc)
            LOGGER.warning(
                "LLM inference failed",
                extra={
                    "provider": self.provider,
                    "model": model,
                    "route_key": route_key,
                    "error_type": error_type,
                    "duration_ms": elapsed_ms,
                },
            )
            raise SkillsGatewayError(
                f"调用 LLM Inference Gateway 失败：{error_type}。",
                details={
                    "error_type": error_type,
                    "error": str(exc),
                    "provider": self.provider,
                    "api_base_url": self.api_base_url,
                    "model": model,
                    "timeout_seconds": self.timeout_seconds,
                },
            ) from exc

        if response.status_code >= 400:
            LOGGER.warning(
                "LLM inference returned error response",
                extra={
                    "provider": self.provider,
                    "model": model,
                    "route_key": route_key,
                    "status_code": response.status_code,
                },
            )
            raise SkillsGatewayError(
                "LLM Inference Gateway 返回错误响应。",
                details={
                    "status_code": response.status_code,
                    "body": response.text,
                    "provider": self.provider,
                    "api_base_url": self.api_base_url,
                    "model": model,
                },
            )

        data = response.json()
        try:
            content = str(data["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError) as exc:
            raise SkillsGatewayError("LLM Inference Gateway 响应缺少 message content。") from exc

        return LlmCompletion(content=content, provider=self.provider, model=model, raw_response=data)
