from __future__ import annotations

from types import SimpleNamespace

from app.agent_harness.models.psop_gateway_chat_model import (
    langchain_ai_message_from_psop,
    psop_messages_from_langchain,
)
from app.gateway.inference import LlmChatMessage, LlmToolCall


def test_model_adapter_maps_generic_langchain_like_messages_without_dependency() -> None:
    messages = psop_messages_from_langchain(
        [
            SimpleNamespace(type="system", content="system"),
            SimpleNamespace(type="human", content="hello"),
            SimpleNamespace(type="ai", content="answer"),
        ]
    )

    assert [message.role for message in messages] == ["system", "user", "assistant"]
    assert messages[1].content == "hello"


def test_model_adapter_maps_psop_tool_calls_to_ai_message_or_fallback() -> None:
    message = LlmChatMessage(
        role="assistant",
        content=None,
        tool_calls=[LlmToolCall(id="call-1", name="demo_tool", arguments={"x": 1})],
    )

    mapped = langchain_ai_message_from_psop(message)

    if isinstance(mapped, LlmChatMessage):
        assert mapped.tool_calls[0].name == "demo_tool"
    else:
        assert mapped.tool_calls[0]["name"] == "demo_tool"
