from __future__ import annotations

import json
import logging
import time

import pytest
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langgraph.prebuilt.tool_node import ToolCallRequest

from app.agent_harness.errors import AgentBudgetExceededError, AgentDeadlineExceededError
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
    assert writer.events[-1].payload["result_status"] == "success"
    assert writer.events[-1].payload["result_summary"] == "ok"

    failed = middleware.wrap_tool_call(
        request,
        lambda _: (_ for _ in ()).throw(ValueError("bad")),
    )

    assert isinstance(failed, ToolMessage)
    assert failed.status == "error"
    assert writer.events[-1].event_type == "agent.tool.failed"


def test_tool_call_middleware_records_structured_error_and_stops_after_limit(tmp_path) -> None:
    writer = AgentEventWriter(tmp_path / "events.jsonl")
    middleware = ToolCallMiddleware(writer, max_error_counts={"psop.builder.submit_candidate": 2})
    request = ToolCallRequest(
        tool_call={"name": "psop.builder.submit_candidate", "id": "call-1", "args": {"files": {}}},
        tool=None,
        state={},
        runtime=None,
    )
    error_content = json.dumps({"status": "error", "type": "invalid_arguments", "message": "files 缺少必需文件"}, ensure_ascii=False)

    first = middleware.wrap_tool_call(
        request,
        lambda _: ToolMessage(content=error_content, tool_call_id="call-1", name="psop.builder.submit_candidate", status="success"),
    )

    assert isinstance(first, ToolMessage)
    assert writer.events[-1].event_type == "agent.tool.completed"
    assert writer.events[-1].payload["result_status"] == "error"
    assert writer.events[-1].payload["result_type"] == "invalid_arguments"
    assert "必需文件" in writer.events[-1].payload["result_message"]

    with pytest.raises(AgentBudgetExceededError):
        middleware.wrap_tool_call(
            request,
            lambda _: ToolMessage(content=error_content, tool_call_id="call-1", name="psop.builder.submit_candidate", status="success"),
        )

    assert writer.events[-1].event_type == "agent.budget.exceeded"
    assert writer.events[-1].payload["tool_name"] == "psop.builder.submit_candidate"


def test_tool_call_middleware_preserves_all_validation_diagnostics(tmp_path) -> None:
    writer = AgentEventWriter(tmp_path / "events.jsonl")
    middleware = ToolCallMiddleware(writer)
    request = ToolCallRequest(
        tool_call={"name": "psop.builder.submit_candidate", "id": "call-1", "args": {}},
        tool=None,
        state={},
        runtime=None,
    )
    diagnostics = [
        {"path": f"workflow_step_candidates.{index}", "code": "missing_evidence_coverage"}
        for index in range(12)
    ]
    error_content = json.dumps(
        {"status": "error", "type": "invalid_arguments", "diagnostics": diagnostics},
        ensure_ascii=False,
    )

    middleware.wrap_tool_call(
        request,
        lambda _: ToolMessage(
            content=error_content,
            tool_call_id="call-1",
            name="psop.builder.submit_candidate",
            status="success",
        ),
    )

    payload = writer.events[-1].payload
    assert payload["validation_diagnostic_count"] == 12
    assert payload["validation_diagnostics"] == diagnostics


def test_tool_call_middleware_counts_failed_tool_errors_for_limit(tmp_path) -> None:
    writer = AgentEventWriter(tmp_path / "events.jsonl")
    middleware = ToolCallMiddleware(writer, max_error_counts={"psop.builder.submit_candidate": 2})
    request = ToolCallRequest(
        tool_call={"name": "psop.builder.submit_candidate", "id": "call-1", "args": {"review_notes": "bad"}},
        tool=None,
        state={},
        runtime=None,
    )

    first = middleware.wrap_tool_call(request, lambda _: (_ for _ in ()).throw(ValueError("数组字段类型错误")))

    assert isinstance(first, ToolMessage)
    assert first.status == "error"
    assert writer.events[-1].event_type == "agent.tool.failed"

    with pytest.raises(AgentBudgetExceededError):
        middleware.wrap_tool_call(request, lambda _: (_ for _ in ()).throw(ValueError("数组字段类型错误")))

    assert writer.events[-1].event_type == "agent.budget.exceeded"
    assert writer.events[-1].payload["last_error_type"] == "ValueError"


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


def test_model_call_middleware_logs_safe_llm_input_and_output_without_new_events(
    tmp_path,
    caplog,
) -> None:
    writer = AgentEventWriter(tmp_path / "events.jsonl")
    middleware = ModelCallEventMiddleware(writer)

    def inspect_photo(area: str) -> str:
        return area

    tool = StructuredTool.from_function(
        inspect_photo,
        name="inspect_photo",
        description="Inspect a requested photo area.",
    )
    request = ModelRequest(
        model=FakeListChatModel(responses=["ok"]),
        system_message=SystemMessage(content="检查 I/O 对齐和四颗主板螺丝。"),
        messages=[
            HumanMessage(
                content=[
                    {"type": "text", "text": "请分析这张现场照片。"},
                    {
                        "type": "image_url",
                        "image_url": {"url": "data:image/jpeg;base64,QUJDREVGRw=="},
                    },
                ]
            )
        ],
        tools=[tool],
        tool_choice=None,
    )
    response = ModelResponse(
        result=[
            AIMessage(
                content="四颗螺丝可见。",
                additional_kwargs={"reasoning_content": "private chain of thought"},
                tool_calls=[
                    {"name": "inspect_photo", "id": "call-1", "args": {"area": "corners"}}
                ],
                usage_metadata={"input_tokens": 11, "output_tokens": 7, "total_tokens": 18},
            )
        ]
    )

    with caplog.at_level(logging.INFO, logger="app.agent_harness.middlewares.model_events"):
        middleware.wrap_model_call(request, lambda _: response)

    input_record = next(
        record for record in caplog.records if record.getMessage() == "Agent LLM API input"
    )
    output_record = next(
        record for record in caplog.records if record.getMessage() == "Agent LLM API output"
    )

    assert input_record.llm_input["system_message"]["content"] == "检查 I/O 对齐和四颗主板螺丝。"
    assert input_record.llm_input["messages"][0]["content"][0]["text"] == "请分析这张现场照片。"
    logged_url = input_record.llm_input["messages"][0]["content"][1]["image_url"]["url"]
    assert logged_url == {
        "binary_omitted": True,
        "media_type": "image/jpeg",
        "encoding": "base64",
        "base64_chars": 12,
    }
    assert input_record.llm_input["tools"][0]["function"]["name"] == "inspect_photo"
    assert "QUJDREVGRw==" not in str(input_record.llm_input)
    assert output_record.llm_output["messages"][0]["content"] == "四颗螺丝可见。"
    assert (
        output_record.llm_output["messages"][0]["additional_kwargs"]["reasoning_content"]
        == "[OMITTED_HIDDEN_REASONING]"
    )
    assert output_record.llm_output["messages"][0]["tool_calls"][0]["name"] == "inspect_photo"
    assert output_record.llm_output["messages"][0]["usage_metadata"]["total_tokens"] == 18
    assert [event.event_type for event in writer.events] == ["agent.model.started", "agent.model.completed"]


def test_model_call_event_middleware_stops_after_call_limit(tmp_path) -> None:
    writer = AgentEventWriter(tmp_path / "events.jsonl")
    middleware = ModelCallEventMiddleware(writer, max_model_calls=1)
    request = ModelRequest(
        model=FakeListChatModel(responses=["ok"]),
        messages=[HumanMessage(content="hello")],
        tools=[],
    )

    middleware.wrap_model_call(request, lambda _: AIMessage(content="ok"))
    with pytest.raises(AgentBudgetExceededError):
        middleware.wrap_model_call(request, lambda _: AIMessage(content="again"))

    assert writer.events[-1].event_type == "agent.budget.exceeded"
    assert writer.events[-1].payload["budget_type"] == "model_calls"


def test_model_and_tool_middleware_fence_expired_runtime_deadline(tmp_path) -> None:
    writer = AgentEventWriter(tmp_path / "events.jsonl")
    expired = time.monotonic() - 1
    model_middleware = ModelCallEventMiddleware(writer, deadline_monotonic=expired)
    model_request = ModelRequest(
        model=FakeListChatModel(responses=["ok"]),
        messages=[HumanMessage(content="hello")],
        tools=[],
    )
    tool_middleware = ToolCallMiddleware(writer, deadline_monotonic=expired)
    tool_request = ToolCallRequest(
        tool_call={"name": "demo_tool", "id": "call-deadline", "args": {}},
        tool=None,
        state={},
        runtime=None,
    )

    with pytest.raises(AgentDeadlineExceededError):
        model_middleware.wrap_model_call(model_request, lambda _: AIMessage(content="late"))
    with pytest.raises(AgentDeadlineExceededError):
        tool_middleware.wrap_tool_call(
            tool_request,
            lambda _: ToolMessage(content="late", tool_call_id="call-deadline", name="demo_tool"),
        )

    assert writer.events == []


def test_model_middleware_refreshes_provider_timeout_for_each_call(tmp_path) -> None:
    writer = AgentEventWriter(tmp_path / "events.jsonl")
    refresh_calls = []
    middleware = ModelCallEventMiddleware(
        writer,
        deadline_monotonic=time.monotonic() + 60,
        before_model_call=lambda: refresh_calls.append("refresh"),
    )
    request = ModelRequest(
        model=FakeListChatModel(responses=["ok"]),
        messages=[HumanMessage(content="hello")],
        tools=[],
    )

    middleware.wrap_model_call(request, lambda _: AIMessage(content="one"))
    middleware.wrap_model_call(request, lambda _: AIMessage(content="two"))

    assert refresh_calls == ["refresh", "refresh"]


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
