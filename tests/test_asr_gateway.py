from __future__ import annotations

import httpx
import pytest

from app.gateway.asr import HttpAsrGateway
from app.domain.skills.exceptions import SkillsGatewayError


class FakeResponse:
    def __init__(self, *, status_code: int = 200, payload: dict | None = None, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload if payload is not None else {"text": "识别文本", "language": "Chinese"}
        self.text = text or str(self._payload)

    def json(self):
        return self._payload


class FakeClient:
    response = FakeResponse()
    error: Exception | None = None
    calls: list[dict[str, object]] = []

    def __init__(self, *_, **__) -> None:
        pass

    def __enter__(self) -> "FakeClient":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def post(self, url: str, *, data: dict, files: dict) -> FakeResponse:
        if self.error:
            raise self.error
        self.calls.append({"url": url, "data": data, "files": files})
        return self.response


def test_http_asr_gateway_transcribes_audio(monkeypatch) -> None:
    FakeClient.response = FakeResponse(payload={"text": "关闭电源。", "language": "Chinese"})
    FakeClient.error = None
    FakeClient.calls = []
    monkeypatch.setattr("app.gateway.asr.httpx.Client", FakeClient)

    gateway = HttpAsrGateway(api_base_url="http://asr.local", default_language="zh", temperature=0)
    result = gateway.transcribe(filename="audio.wav", content=b"wav-bytes")

    assert result.text == "关闭电源。"
    assert result.language == "Chinese"
    assert FakeClient.calls[0]["url"] == "http://asr.local/v1/audio/transcriptions"
    assert FakeClient.calls[0]["data"] == {"language": "zh", "temperature": "0"}
    assert FakeClient.calls[0]["files"]["file"][2] == "audio/wav"


def test_http_asr_gateway_allows_custom_media_type(monkeypatch) -> None:
    FakeClient.response = FakeResponse(payload={"text": "关闭电源。", "language": "Chinese"})
    FakeClient.error = None
    FakeClient.calls = []
    monkeypatch.setattr("app.gateway.asr.httpx.Client", FakeClient)

    gateway = HttpAsrGateway(api_base_url="http://asr.local")
    gateway.transcribe(filename="audio.mp3", content=b"mp3-bytes", media_type="audio/mpeg")

    assert FakeClient.calls[0]["files"]["file"] == ("audio.mp3", b"mp3-bytes", "audio/mpeg")


def test_http_asr_gateway_rejects_empty_text(monkeypatch) -> None:
    FakeClient.response = FakeResponse(payload={"text": ""})
    FakeClient.error = None
    FakeClient.calls = []
    monkeypatch.setattr("app.gateway.asr.httpx.Client", FakeClient)

    gateway = HttpAsrGateway(api_base_url="http://asr.local")

    with pytest.raises(SkillsGatewayError, match="未返回可用文本"):
        gateway.transcribe(filename="audio.wav", content=b"wav-bytes")


def test_http_asr_gateway_wraps_http_error(monkeypatch) -> None:
    FakeClient.response = FakeResponse()
    FakeClient.error = httpx.ConnectError("offline")
    FakeClient.calls = []
    monkeypatch.setattr("app.gateway.asr.httpx.Client", FakeClient)

    gateway = HttpAsrGateway(api_base_url="http://asr.local")

    with pytest.raises(SkillsGatewayError, match="调用 ASR Gateway 失败"):
        gateway.transcribe(filename="audio.wav", content=b"wav-bytes")
