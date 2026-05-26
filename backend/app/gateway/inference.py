from __future__ import annotations

from dataclasses import dataclass, field
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
    usage: dict[str, int | dict[str, object]] = field(default_factory=dict)


@dataclass(slots=True)
class LlmAttachment:
    filename: str
    media_type: str
    content_base64: str


class LlmInferenceGateway(Protocol):
    def complete(self, *, system_prompt: str, user_prompt: str, route_key: str = "default") -> LlmCompletion:
        ...

    def complete_multimodal(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        attachments: list[LlmAttachment],
        route_key: str = "default",
    ) -> LlmCompletion:
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
        route_models: dict[str, str] | None = None,
        route_payload_options: dict[str, dict[str, object]] | None = None,
        timeout_seconds: float = 60.0,
    ) -> None:
        self.provider = provider
        self.api_base_url = api_base_url.rstrip("/")
        self.api_key = api_key
        self.default_model = default_model
        self.route_models = {
            str(route_key): str(model)
            for route_key, model in (route_models or {}).items()
            if str(route_key).strip() and str(model).strip()
        }
        self.route_payload_options = {
            str(route_key): dict(options)
            for route_key, options in (route_payload_options or {}).items()
            if str(route_key).strip() and isinstance(options, dict)
        }
        self.timeout_seconds = timeout_seconds

    @classmethod
    def from_settings(cls, settings: Settings) -> "OpenAICompatibleInferenceGateway":
        skill_creation_options: dict[str, object] = {}
        if settings.llm_skill_creation_enable_thinking:
            skill_creation_options["enable_thinking"] = True
            if settings.llm_skill_creation_thinking_budget:
                skill_creation_options["thinking_budget"] = settings.llm_skill_creation_thinking_budget
        return cls(
            provider=settings.llm_provider,
            api_base_url=settings.llm_api_base_url,
            api_key=settings.llm_api_key,
            default_model=settings.llm_default_model,
            route_models={
                "skill-creation": settings.llm_skill_creation_model or "",
                "vision": settings.llm_vision_model or "",
            },
            route_payload_options={"skill-creation": skill_creation_options},
            timeout_seconds=settings.llm_timeout_seconds,
        )

    def _resolve_model(self, route_key: str) -> str:
        if not route_key or route_key == "default":
            return self.default_model
        return self.route_models.get(route_key, route_key)

    def complete(self, *, system_prompt: str, user_prompt: str, route_key: str = "default") -> LlmCompletion:
        if not self.api_key:
            raise SkillsConfigurationError("未配置 LLM API Key，无法执行真实运行链路。")

        model = self._resolve_model(route_key)
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.2,
        }
        self._apply_route_payload_options(payload, route_key)
        headers = {"Authorization": f"Bearer {self.api_key}"}

        timeout = httpx.Timeout(self.timeout_seconds, connect=min(15.0, self.timeout_seconds))
        started_at = time.perf_counter()
        with start_span(
            "gateway.inference",
            provider=self.provider,
            model=model,
            route_key=route_key,
            api_base_url=self.api_base_url,
            llm_input_system_prompt_length=len(system_prompt),
            llm_input_user_prompt_length=len(user_prompt),
        ) as span:
            try:
                span.add_event(
                    "llm.input",
                    attributes={
                        "llm.system_prompt": system_prompt,
                        "llm.user_prompt": user_prompt,
                    },
                )
                with httpx.Client(timeout=timeout, headers=headers) as client:
                    response = client.post(f"{self.api_base_url}/chat/completions", json=payload)
                elapsed_ms = int((time.perf_counter() - started_at) * 1000)
                set_span_attributes(span, {"http.status_code": response.status_code, "duration_ms": elapsed_ms})
            except httpx.HTTPError as exc:
                error_type = exc.__class__.__name__
                elapsed_ms = int((time.perf_counter() - started_at) * 1000)
                set_span_attributes(span, {"duration_ms": elapsed_ms, "error.type": error_type})
                record_span_exception(span, exc)
                LOGGER.warning(
                    "LLM inference failed",
                    extra={
                        "provider": self.provider,
                        "model": model,
                        "route_key": route_key,
                        "error_type": error_type,
                        "duration_ms": elapsed_ms,
                        "llm_input": {
                            "system_prompt": system_prompt,
                            "user_prompt": user_prompt,
                        },
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
                        "route_key": route_key,
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
                        "llm_input": {
                            "system_prompt": system_prompt,
                            "user_prompt": user_prompt,
                        },
                        "llm_output": {"error_body": response.text},
                    },
                )
                span.add_event("llm.output", attributes={"llm.error_body": response.text})
                raise SkillsGatewayError(
                    "LLM Inference Gateway 返回错误响应。",
                    details={
                        "status_code": response.status_code,
                        "body": response.text,
                        "provider": self.provider,
                        "api_base_url": self.api_base_url,
                        "model": model,
                        "route_key": route_key,
                    },
                )

            data = response.json()
            usage = _normalize_usage(data.get("usage"))
            try:
                content = str(data["choices"][0]["message"]["content"])
            except (KeyError, IndexError, TypeError) as exc:
                raise SkillsGatewayError(
                    "LLM Inference Gateway 响应缺少 message content。",
                    details={
                        "provider": self.provider,
                        "api_base_url": self.api_base_url,
                        "model": model,
                        "route_key": route_key,
                    },
                ) from exc

            set_span_attributes(
                span,
                {
                    "llm.output.content_length": len(content),
                    "llm.usage.input_tokens": usage.get("input_tokens"),
                    "llm.usage.output_tokens": usage.get("output_tokens"),
                    "llm.usage.total_tokens": usage.get("total_tokens"),
                },
            )
            span.add_event("llm.output", attributes={"llm.content": content})
            log_extra = {
                "provider": self.provider,
                "model": model,
                "route_key": route_key,
                "status_code": response.status_code,
                "duration_ms": elapsed_ms,
                "llm_input": {
                    "system_prompt": system_prompt,
                    "user_prompt": user_prompt,
                },
                "llm_output": {"content": content},
                "llm_usage": usage,
            }
            LOGGER.info(
                "LLM inference completed",
                extra=log_extra,
            )
            return LlmCompletion(
                content=content,
                provider=self.provider,
                model=model,
                raw_response=data,
                usage=usage,
            )

    def complete_multimodal(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        attachments: list[LlmAttachment],
        route_key: str = "default",
    ) -> LlmCompletion:
        if not self.api_key:
            raise SkillsConfigurationError("未配置 LLM API Key，无法执行真实运行链路。")

        model = self._resolve_model(route_key)
        content_parts: list[dict[str, object]] = [{"type": "text", "text": user_prompt}]
        for attachment in attachments:
            if attachment.media_type.startswith("image/"):
                content_parts.append(
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{attachment.media_type};base64,{attachment.content_base64}",
                        },
                    }
                )
            elif attachment.media_type.startswith("audio/"):
                content_parts.append(
                    {
                        "type": "input_audio",
                        "input_audio": {
                            "data": attachment.content_base64,
                            "format": _audio_format(attachment.filename, attachment.media_type),
                        },
                    }
                )
            else:
                content_parts.append(
                    {
                        "type": "text",
                        "text": (
                            f"附件 `{attachment.filename}` 的 MIME 为 `{attachment.media_type}`，"
                            "当前 OpenAI-compatible 网关只以文本方式传递其元数据。"
                        ),
                    }
                )

        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content_parts},
            ],
            "temperature": 0.2,
        }
        self._apply_route_payload_options(payload, route_key)
        return self._post_chat_completion(
            payload=payload,
            model=model,
            route_key=route_key,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )

    def _apply_route_payload_options(self, payload: dict[str, object], route_key: str) -> None:
        for key, value in self.route_payload_options.get(route_key, {}).items():
            if value is not None:
                payload[key] = value

    def _post_chat_completion(
        self,
        *,
        payload: dict[str, object],
        model: str,
        route_key: str,
        system_prompt: str,
        user_prompt: str,
    ) -> LlmCompletion:
        headers = {"Authorization": f"Bearer {self.api_key}"}
        timeout = httpx.Timeout(self.timeout_seconds, connect=min(15.0, self.timeout_seconds))
        started_at = time.perf_counter()
        with start_span(
            "gateway.inference",
            provider=self.provider,
            model=model,
            route_key=route_key,
            api_base_url=self.api_base_url,
            llm_input_system_prompt_length=len(system_prompt),
            llm_input_user_prompt_length=len(user_prompt),
        ) as span:
            try:
                with httpx.Client(timeout=timeout, headers=headers) as client:
                    response = client.post(f"{self.api_base_url}/chat/completions", json=payload)
                elapsed_ms = int((time.perf_counter() - started_at) * 1000)
                set_span_attributes(span, {"http.status_code": response.status_code, "duration_ms": elapsed_ms})
            except httpx.HTTPError as exc:
                error_type = exc.__class__.__name__
                elapsed_ms = int((time.perf_counter() - started_at) * 1000)
                set_span_attributes(span, {"duration_ms": elapsed_ms, "error.type": error_type})
                record_span_exception(span, exc)
                raise SkillsGatewayError(
                    f"调用 LLM Inference Gateway 失败：{error_type}。",
                    details={
                        "error_type": error_type,
                        "error": str(exc),
                        "provider": self.provider,
                        "api_base_url": self.api_base_url,
                        "model": model,
                        "route_key": route_key,
                        "timeout_seconds": self.timeout_seconds,
                    },
                ) from exc

            if response.status_code >= 400:
                span.add_event("llm.output", attributes={"llm.error_body": response.text})
                raise SkillsGatewayError(
                    "LLM Inference Gateway 返回错误响应。",
                    details={
                        "status_code": response.status_code,
                        "body": response.text,
                        "provider": self.provider,
                        "api_base_url": self.api_base_url,
                        "model": model,
                        "route_key": route_key,
                    },
                )

            data = response.json()
            usage = _normalize_usage(data.get("usage"))
            try:
                content = str(data["choices"][0]["message"]["content"])
            except (KeyError, IndexError, TypeError) as exc:
                raise SkillsGatewayError(
                    "LLM Inference Gateway 响应缺少 message content。",
                    details={
                        "provider": self.provider,
                        "api_base_url": self.api_base_url,
                        "model": model,
                        "route_key": route_key,
                    },
                ) from exc
            set_span_attributes(
                span,
                {
                    "llm.output.content_length": len(content),
                    "llm.usage.input_tokens": usage.get("input_tokens"),
                    "llm.usage.output_tokens": usage.get("output_tokens"),
                    "llm.usage.total_tokens": usage.get("total_tokens"),
                },
            )
            span.add_event("llm.output", attributes={"llm.content": content})
            return LlmCompletion(
                content=content,
                provider=self.provider,
                model=model,
                raw_response=data,
                usage=usage,
            )


def _normalize_usage(raw_usage: object) -> dict[str, int | dict[str, object]]:
    if not isinstance(raw_usage, dict):
        return {}

    input_tokens = _coerce_int(_first_present(raw_usage, "prompt_tokens", "input_tokens"))
    output_tokens = _coerce_int(_first_present(raw_usage, "completion_tokens", "output_tokens"))
    total_tokens = _coerce_int(raw_usage.get("total_tokens"))
    if total_tokens is None and input_tokens is not None and output_tokens is not None:
        total_tokens = input_tokens + output_tokens

    usage: dict[str, int | dict[str, object]] = {"raw": raw_usage}
    if input_tokens is not None:
        usage["input_tokens"] = input_tokens
    if output_tokens is not None:
        usage["output_tokens"] = output_tokens
    if total_tokens is not None:
        usage["total_tokens"] = total_tokens
    return usage


def _coerce_int(value: object) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _first_present(payload: dict[str, object], *keys: str) -> object:
    for key in keys:
        if key in payload:
            return payload[key]
    return None


def _audio_format(filename: str, media_type: str) -> str:
    lower_name = filename.lower()
    if lower_name.endswith(".wav") or media_type == "audio/wav":
        return "wav"
    if lower_name.endswith(".mp3") or media_type == "audio/mpeg":
        return "mp3"
    return lower_name.rsplit(".", 1)[-1] if "." in lower_name else "mp3"
