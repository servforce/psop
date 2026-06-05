from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import httpx

from app.core.config import Settings
from app.pskills.exceptions import SkillsConfigurationError, SkillsGatewayError


@dataclass(slots=True)
class AsrTranscription:
    text: str
    language: str | None = None
    raw_response: dict | None = None


class AsrGateway(Protocol):
    def transcribe(
        self,
        *,
        filename: str,
        content: bytes,
        media_type: str = "audio/wav",
        language: str | None = None,
        prompt: str | None = None,
    ) -> AsrTranscription:
        ...


class HttpAsrGateway:
    """OpenAI-compatible ASR gateway wrapper used for video raw material analysis."""

    def __init__(
        self,
        *,
        api_base_url: str,
        default_language: str | None = "zh",
        temperature: float | None = 0.0,
        timeout_seconds: float = 600.0,
    ) -> None:
        self.api_base_url = api_base_url.rstrip("/")
        self.default_language = default_language
        self.temperature = temperature
        self.timeout_seconds = timeout_seconds

    @classmethod
    def from_settings(cls, settings: Settings) -> "HttpAsrGateway":
        return cls(
            api_base_url=settings.asr_api_base_url,
            default_language=settings.asr_language,
            temperature=settings.asr_temperature,
            timeout_seconds=settings.asr_timeout_seconds,
        )

    def transcribe(
        self,
        *,
        filename: str,
        content: bytes,
        media_type: str = "audio/wav",
        language: str | None = None,
        prompt: str | None = None,
    ) -> AsrTranscription:
        if not self.api_base_url:
            raise SkillsConfigurationError("未配置 ASR 服务地址，无法分析视频素材。")
        if not content:
            raise SkillsGatewayError("ASR 输入音频为空。")

        data: dict[str, str] = {}
        resolved_language = language if language is not None else self.default_language
        if resolved_language:
            data["language"] = resolved_language
        if prompt:
            data["prompt"] = prompt
        if self.temperature is not None:
            data["temperature"] = str(self.temperature)

        timeout = httpx.Timeout(self.timeout_seconds, connect=min(15.0, self.timeout_seconds))
        files = {"file": (filename, content, media_type)}
        try:
            with httpx.Client(timeout=timeout) as client:
                response = client.post(f"{self.api_base_url}/v1/audio/transcriptions", data=data, files=files)
        except httpx.HTTPError as exc:
            raise SkillsGatewayError(
                "调用 ASR Gateway 失败。",
                details={"error_type": exc.__class__.__name__, "error": str(exc), "api_base_url": self.api_base_url},
            ) from exc

        if response.status_code >= 400:
            raise SkillsGatewayError(
                "ASR Gateway 返回错误响应。",
                details={"status_code": response.status_code, "body": response.text, "api_base_url": self.api_base_url},
            )

        payload = response.json()
        text = str(payload.get("text") or "").strip() if isinstance(payload, dict) else ""
        if not text:
            raise SkillsGatewayError("ASR Gateway 未返回可用文本。", details={"raw_response": payload})
        return AsrTranscription(
            text=text,
            language=str(payload.get("language")) if isinstance(payload, dict) and payload.get("language") else None,
            raw_response=payload if isinstance(payload, dict) else {"value": payload},
        )
