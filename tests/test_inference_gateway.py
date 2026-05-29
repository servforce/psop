from __future__ import annotations

from app.gateway.inference import LlmAttachment, LlmCompletion, OpenAICompatibleInferenceGateway


def test_multimodal_vision_route_uses_configured_model(monkeypatch) -> None:
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
        default_model="glm-5.1",
        route_models={"vision": "qwen-vl-test"},
    )

    completion = gateway.complete_multimodal(
        system_prompt="识别图片",
        user_prompt="请描述画面",
        attachments=[
            LlmAttachment(filename="frame.jpg", media_type="image/jpeg", content_base64="aW1hZ2U="),
            LlmAttachment(filename="clip.mp4", media_type="video/mp4", content_base64="dmlkZW8="),
            LlmAttachment(filename="note.wav", media_type="audio/wav", content_base64="YXVkaW8="),
        ],
        route_key="vision",
    )

    assert completion.model == "qwen-vl-test"
    assert captured["model"] == "qwen-vl-test"
    assert captured["route_key"] == "vision"
    assert captured["payload"]["model"] == "qwen-vl-test"
    user_content = captured["payload"]["messages"][1]["content"]
    assert [part["type"] for part in user_content] == ["text", "image_url", "video_url", "input_audio"]
    assert user_content[1]["image_url"]["url"] == "data:image/jpeg;base64,aW1hZ2U="
    assert user_content[2]["video_url"]["url"] == "data:video/mp4;base64,dmlkZW8="
    assert user_content[3]["input_audio"] == {"data": "YXVkaW8=", "format": "wav"}


def test_named_route_without_mapping_still_uses_route_key_as_model() -> None:
    gateway = OpenAICompatibleInferenceGateway(
        provider="test",
        api_base_url="https://example.test/v1",
        api_key="test-key",
        default_model="glm-5.1",
        route_models={"skill-creation": "qwen3.6-plus", "vision": "qwen-vl-test"},
    )

    assert gateway._resolve_model("skill-test-judge") == "skill-test-judge"
    assert gateway._resolve_model("skill-creation") == "qwen3.6-plus"
    assert gateway._resolve_model("default") == "glm-5.1"


def test_skill_creation_route_can_attach_qwen_thinking_options(monkeypatch) -> None:
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
        default_model="glm-5.1",
        route_models={"skill-creation": "qwen3.6-plus"},
        route_payload_options={"skill-creation": {"enable_thinking": True, "thinking_budget": 8192}},
    )

    gateway.complete_multimodal(
        system_prompt="system",
        user_prompt="user",
        attachments=[],
        route_key="skill-creation",
    )

    assert captured["enable_thinking"] is True
    assert captured["thinking_budget"] == 8192
