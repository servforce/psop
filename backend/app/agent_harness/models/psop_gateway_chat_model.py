from __future__ import annotations

from typing import Any

from app.gateway.inference import LlmChatMessage, LlmToolCall, TEXT_ROUTE_KEY

try:  # pragma: no cover - exercised when LangChain is installed.
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
    from langchain_core.outputs import ChatGeneration, ChatResult
    from pydantic import ConfigDict
except ImportError:  # pragma: no cover - local minimal test environment.
    BaseChatModel = object  # type: ignore[assignment,misc]
    AIMessage = None  # type: ignore[assignment]
    BaseMessage = object  # type: ignore[assignment,misc]
    HumanMessage = None  # type: ignore[assignment]
    SystemMessage = None  # type: ignore[assignment]
    ToolMessage = None  # type: ignore[assignment]
    ChatGeneration = None  # type: ignore[assignment]
    ChatResult = None  # type: ignore[assignment]
    ConfigDict = None  # type: ignore[assignment]


class PsopGatewayChatModel(BaseChatModel):  # type: ignore[misc]
    if ConfigDict is not None:
        model_config = ConfigDict(arbitrary_types_allowed=True)

    inference_gateway: Any
    route_key: str = TEXT_ROUTE_KEY
    bound_tools: list[dict[str, Any]] = []

    def __init__(self, **data: Any) -> None:
        if BaseChatModel is object:
            self.inference_gateway = data["inference_gateway"]
            self.route_key = data.get("route_key", TEXT_ROUTE_KEY)
            self.bound_tools = list(data.get("bound_tools") or [])
            return
        super().__init__(**data)

    @property
    def _llm_type(self) -> str:
        return "psop-gateway-chat-model"

    def bind_tools(self, tools: list[Any], **kwargs: Any) -> "PsopGatewayChatModel":
        formatted = [_format_tool(tool) for tool in tools]
        if hasattr(self, "model_copy"):
            return self.model_copy(update={"bound_tools": formatted})  # type: ignore[attr-defined,no-any-return]
        return PsopGatewayChatModel(
            inference_gateway=self.inference_gateway,
            route_key=self.route_key,
            bound_tools=formatted,
        )

    def _generate(self, messages: list[Any], stop: list[str] | None = None, run_manager: Any = None, **kwargs: Any) -> Any:
        if AIMessage is None or ChatGeneration is None or ChatResult is None:
            raise RuntimeError("LangChain 未安装，无法使用 PsopGatewayChatModel。")
        completion = self.inference_gateway.complete_chat(
            messages=psop_messages_from_langchain(messages),
            tools=self.bound_tools or kwargs.get("tools"),
            route_key=self.route_key,
        )
        return ChatResult(generations=[ChatGeneration(message=langchain_ai_message_from_psop(completion.message))])


def psop_messages_from_langchain(messages: list[Any]) -> list[LlmChatMessage]:
    return [_psop_message_from_langchain(message) for message in messages]


def langchain_ai_message_from_psop(message: LlmChatMessage) -> Any:
    tool_calls = [
        {"id": tool_call.id, "name": tool_call.name, "args": tool_call.arguments}
        for tool_call in message.tool_calls
    ]
    if AIMessage is None:
        return message
    return AIMessage(content=message.content or "", tool_calls=tool_calls)


def _psop_message_from_langchain(message: Any) -> LlmChatMessage:
    content = _string_content(getattr(message, "content", ""))
    if SystemMessage is not None and isinstance(message, SystemMessage):
        return LlmChatMessage(role="system", content=content)
    if HumanMessage is not None and isinstance(message, HumanMessage):
        return LlmChatMessage(role="user", content=content)
    if ToolMessage is not None and isinstance(message, ToolMessage):
        return LlmChatMessage(
            role="tool",
            content=content,
            name=str(getattr(message, "name", "") or ""),
            tool_call_id=str(getattr(message, "tool_call_id", "") or ""),
        )
    if AIMessage is not None and isinstance(message, AIMessage):
        return LlmChatMessage(
            role="assistant",
            content=content,
            tool_calls=[
                LlmToolCall(
                    id=str(item.get("id") or ""),
                    name=str(item.get("name") or ""),
                    arguments=dict(item.get("args") or {}),
                )
                for item in getattr(message, "tool_calls", []) or []
                if isinstance(item, dict)
            ],
        )
    role = str(getattr(message, "role", "") or getattr(message, "type", "") or "user")
    if role == "human":
        role = "user"
    if role == "ai":
        role = "assistant"
    return LlmChatMessage(role=role, content=content)


def _format_tool(tool: Any) -> dict[str, Any]:
    if isinstance(tool, dict):
        if tool.get("type") == "function":
            return tool
        if tool.get("name"):
            return {
                "type": "function",
                "function": {
                    "name": str(tool["name"]),
                    "description": str(tool.get("description") or ""),
                    "parameters": tool.get("parameters") or tool.get("input_schema") or {"type": "object", "properties": {}},
                },
            }
    name = str(getattr(tool, "name", "") or getattr(tool, "__name__", "tool"))
    description = str(getattr(tool, "description", "") or getattr(tool, "__doc__", "") or "")
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": {}},
        },
    }


def _string_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text") or ""))
            else:
                parts.append(str(item))
        return "\n".join(part for part in parts if part)
    return "" if content is None else str(content)
