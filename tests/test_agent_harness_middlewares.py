from __future__ import annotations

from langchain.agents.middleware.types import ModelRequest
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest

from app.agent_harness.events import AgentEventWriter
from app.agent_harness.middlewares.dangling_tool_call import DanglingToolCallMiddleware
from app.agent_harness.middlewares.model_events import ModelCallEventMiddleware
from app.agent_harness.middlewares.token_usage import TokenUsageMiddleware
from app.agent_harness.middlewares.tool_calls import ToolCallMiddleware


def test_tool_call_middleware_records_success_and_failure(tmp_path) -> None:
    writer = AgentEventWriter(tmp_path / "events.jsonl")
    middleware = ToolCallMiddleware(writer)
    request = ToolCallRequest(
        tool_call={"name": "demo_tool", "id": "call-1", "args": {"x": 1}},
        tool=None,
        state={},
        runtime=None,
    )

    result = middleware.wrap_tool_call(
        request,
        lambda _: ToolMessage(content="ok", tool_call_id="call-1", name="demo_tool"),
    )

    assert isinstance(result, ToolMessage)
    assert [event.event_type for event in writer.events] == ["agent.tool.started", "agent.tool.completed"]

    failed = middleware.wrap_tool_call(
        request,
        lambda _: (_ for _ in ()).throw(ValueError("bad")),
    )

    assert isinstance(failed, ToolMessage)
    assert failed.status == "error"
    assert writer.events[-1].event_type == "agent.tool.failed"


def test_model_call_event_middleware_records_model_lifecycle(tmp_path) -> None:
    writer = AgentEventWriter(tmp_path / "events.jsonl")
    middleware = ModelCallEventMiddleware(writer)
    request = ModelRequest(
        model=FakeListChatModel(responses=["ok"]),
        messages=[HumanMessage(content="hello")],
        tools=[],
    )

    middleware.wrap_model_call(request, lambda _: AIMessage(content="ok"))

    assert [event.event_type for event in writer.events] == ["agent.model.started", "agent.model.completed"]
    assert "duration_ms" in writer.events[-1].payload


def test_token_usage_middleware_records_usage(tmp_path) -> None:
    writer = AgentEventWriter(tmp_path / "events.jsonl")
    middleware = TokenUsageMiddleware(writer)

    middleware.after_model(
        {"messages": [AIMessage(content="ok", usage_metadata={"input_tokens": 1, "output_tokens": 2, "total_tokens": 3})]},
        runtime=None,
    )

    assert writer.events[-1].event_type == "agent.token.usage"
    assert writer.events[-1].payload["total"]["total_tokens"] == 3


def test_dangling_tool_call_middleware_inserts_missing_tool_message() -> None:
    middleware = DanglingToolCallMiddleware()
    ai_message = AIMessage(content="", tool_calls=[{"name": "demo_tool", "id": "call-1", "args": {}}])
    request = ModelRequest(
        model=FakeListChatModel(responses=["ok"]),
        messages=[HumanMessage(content="hello"), ai_message],
        tools=[],
    )
    seen_messages = []

    middleware.wrap_model_call(request, lambda patched: seen_messages.extend(patched.messages) or AIMessage(content="ok"))

    assert isinstance(seen_messages[-1], ToolMessage)
    assert seen_messages[-1].tool_call_id == "call-1"
