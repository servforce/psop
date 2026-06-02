from __future__ import annotations

import pytest

from app.domain.skills.exceptions import SkillsConfigurationError
from app.gateway.inference import (
    LlmAttachment,
    LlmCompletion,
    MULTIMODAL_ROUTE_KEY,
    OpenAICompatibleInferenceGateway,
    TEXT_ROUTE_KEY,
)


def test_multimodal_route_uses_configured_model(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_post_chat_completion(
        self: OpenAICompatibleInferenceGateway,
        *,
        payload: dict[str, object],
        model: str,
        route_key: str,
        system_prompt: str,
        user_prompt: str,
    ) -> LlmCompletion:
        captured.update(
            {
                "payload": payload,
                "model": model,
                "route_key": route_key,
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
            }
        )
        return LlmCompletion(
            content='{"caption":"ok","observations":[]}',
            provider="test",
            model=model,
            raw_response={"id": "response-1"},
        )

    monkeypatch.setattr(OpenAICompatibleInferenceGateway, "_post_chat_completion", fake_post_chat_completion)
    gateway = OpenAICompatibleInferenceGateway(
        provider="test",
        api_base_url="https://example.test/v1",
        api_key="test-key",
        text_model="qwen3.7-plus",
        multimodal_model="qwen3.6-plus",
    )

    completion = gateway.complete_multimodal(
        system_prompt="识别图片",
        user_prompt="请描述画面",
        attachments=[
            LlmAttachment(filename="frame.jpg", media_type="image/jpeg", content_base64="aW1hZ2U="),
            LlmAttachment(filename="clip.mp4", media_type="video/mp4", content_base64="dmlkZW8="),
            LlmAttachment(filename="note.wav", media_type="audio/wav", content_base64="YXVkaW8="),
        ],
        route_key=MULTIMODAL_ROUTE_KEY,
    )

    assert completion.model == "qwen3.6-plus"
    assert captured["model"] == "qwen3.6-plus"
    assert captured["route_key"] == MULTIMODAL_ROUTE_KEY
    assert captured["payload"]["model"] == "qwen3.6-plus"
    user_content = captured["payload"]["messages"][1]["content"]
    assert [part["type"] for part in user_content] == ["text", "image_url", "video_url", "input_audio"]
    assert user_content[1]["image_url"]["url"] == "data:image/jpeg;base64,aW1hZ2U="
    assert user_content[2]["video_url"]["url"] == "data:video/mp4;base64,dmlkZW8="
    assert user_content[3]["input_audio"] == {"data": "YXVkaW8=", "format": "wav"}
    assert completion.request["headers"]["Authorization"] == "Bearer [redacted]"
    request_body = completion.request["body"]
    redacted_user_content = request_body["messages"][1]["content"]
    assert redacted_user_content[1]["image_url"] == {"url": "data:image/jpeg;base64,[redacted]"}
    assert redacted_user_content[2]["video_url"] == {"url": "data:video/mp4;base64,[redacted]"}
    assert redacted_user_content[3]["input_audio"] == {"data": "[redacted]", "format": "wav"}
    assert completion.request["attachments"] == [
        {"filename": "frame.jpg", "media_type": "image/jpeg", "content_base64_chars": 8},
        {"filename": "clip.mp4", "media_type": "video/mp4", "content_base64_chars": 8},
        {"filename": "note.wav", "media_type": "audio/wav", "content_base64_chars": 8},
    ]
    assert "aW1hZ2U=" not in str(completion.request)
    assert "dmlkZW8=" not in str(completion.request)
    assert "YXVkaW8=" not in str(completion.request)


def test_gateway_only_accepts_text_and_multimodal_routes() -> None:
    gateway = OpenAICompatibleInferenceGateway(
        provider="test",
        api_base_url="https://example.test/v1",
        api_key="test-key",
        text_model="qwen3.7-plus",
        multimodal_model="qwen3.6-plus",
    )

    assert gateway._resolve_model(TEXT_ROUTE_KEY) == "qwen3.7-plus"
    assert gateway._resolve_model(MULTIMODAL_ROUTE_KEY) == "qwen3.6-plus"
    with pytest.raises(SkillsConfigurationError) as exc_info:
        gateway._resolve_model("skill-test-judge")
    assert exc_info.value.details["supported_route_keys"] == ["multimodal", "text"]


def test_model_capabilities_expose_two_routes_without_credentials() -> None:
    gateway = OpenAICompatibleInferenceGateway(
        provider="test-provider",
        api_base_url="https://example.test/v1",
        api_key="test-key",
        text_model="qwen3.7-plus",
        multimodal_model="qwen3.6-plus",
        text_payload_options={"enable_thinking": True, "thinking_budget": 8192},
        multimodal_payload_options={"enable_thinking": True, "thinking_budget": 4096},
    )

    capabilities = gateway.list_model_capabilities()

    assert [item.route_key for item in capabilities] == [TEXT_ROUTE_KEY, MULTIMODAL_ROUTE_KEY]
    text_capability, multimodal_capability = capabilities
    assert text_capability.model == "qwen3.7-plus"
    assert text_capability.supports_text is True
    assert text_capability.supports_attachments is False
    assert text_capability.thinking_enabled is True
    assert text_capability.thinking_budget == 8192
    assert multimodal_capability.model == "qwen3.6-plus"
    assert multimodal_capability.supports_text is True
    assert multimodal_capability.supports_attachments is True
    assert multimodal_capability.thinking_enabled is True
    assert multimodal_capability.thinking_budget == 4096
    assert not hasattr(text_capability, "api_key")


def test_multimodal_route_can_attach_thinking_options(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_post_chat_completion(
        self: OpenAICompatibleInferenceGateway,
        *,
        payload: dict[str, object],
        model: str,
        route_key: str,
        system_prompt: str,
        user_prompt: str,
    ) -> LlmCompletion:
        captured.update(payload)
        return LlmCompletion(content="{}", provider="test", model=model, raw_response={"id": "response-1"})

    monkeypatch.setattr(OpenAICompatibleInferenceGateway, "_post_chat_completion", fake_post_chat_completion)
    gateway = OpenAICompatibleInferenceGateway(
        provider="test",
        api_base_url="https://example.test/v1",
        api_key="test-key",
        text_model="qwen3.7-plus",
        multimodal_model="qwen3.6-plus",
        multimodal_payload_options={"enable_thinking": True, "thinking_budget": 8192},
    )

    completion = gateway.complete_multimodal(
        system_prompt="system",
        user_prompt="user",
        attachments=[],
    )

    assert captured["enable_thinking"] is True
    assert captured["thinking_budget"] == 8192
    assert completion.request["body"]["enable_thinking"] is True
    assert completion.request["body"]["thinking_budget"] == 8192


def test_multimodal_completion_rejects_text_route() -> None:
    gateway = OpenAICompatibleInferenceGateway(
        provider="test",
        api_base_url="https://example.test/v1",
        api_key="test-key",
        text_model="qwen3.7-plus",
        multimodal_model="qwen3.6-plus",
    )

    with pytest.raises(SkillsConfigurationError):
        gateway.complete_multimodal(
            system_prompt="system",
            user_prompt="user",
            attachments=[],
            route_key=TEXT_ROUTE_KEY,
        )
