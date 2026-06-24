from __future__ import annotations

import pytest

from app.agent_harness.models.factory import HarnessModelConfig, create_chat_model, default_harness_model_config
from app.core.config import Settings


def test_default_harness_model_config_uses_existing_llm_settings() -> None:
    settings = Settings(
        database_url="sqlite+pysqlite:///:memory:",
        llm_api_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        llm_api_key="test-key",
        llm_text_model="qwen-plus",
        llm_text_enable_thinking=True,
        llm_text_thinking_budget=2048,
    )

    config = default_harness_model_config(settings)

    assert config.use == "langchain_openai:ChatOpenAI"
    assert config.base_url == "https://dashscope.aliyuncs.com/compatible-mode/v1"
    assert config.api_key == "test-key"
    assert config.model == "qwen-plus"
    assert config.when_thinking_enabled == {"extra_body": {"enable_thinking": True, "thinking_budget": 2048}}


def test_create_chat_model_instantiates_langchain_provider_from_config() -> None:
    settings = Settings(database_url="sqlite+pysqlite:///:memory:")
    config = HarnessModelConfig(
        name="fake",
        use="langchain_core.language_models.fake_chat_models:FakeListChatModel",
        model="fake",
        responses=["ok"],
        stream_usage=False,
    )

    model = create_chat_model(settings=settings, name="fake", config=config)

    assert model.invoke("hello").content == "ok"


def test_create_chat_model_reports_missing_provider() -> None:
    settings = Settings(database_url="sqlite+pysqlite:///:memory:")
    config = HarnessModelConfig(name="bad", use="missing_provider:Missing", model="x")

    with pytest.raises(ImportError):
        create_chat_model(settings=settings, name="bad", config=config)
