from __future__ import annotations

import importlib
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.core.config import Settings
from app.agent_harness.schemas import AgentDefinition


ChatModelFactory = Callable[[AgentDefinition], Any]


class HarnessModelConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str
    use: str = "langchain_openai:ChatOpenAI"
    model: str
    api_key: str | None = None
    base_url: str | None = None
    timeout: float | None = None
    temperature: float = 0.2
    stream_usage: bool = True
    supports_thinking: bool = False
    when_thinking_enabled: dict[str, Any] | None = None
    when_thinking_disabled: dict[str, Any] | None = None
    supports_vision: bool = False


def default_harness_model_config(settings: Settings) -> HarnessModelConfig:
    return HarnessModelConfig(
        name="default",
        model=settings.llm_text_model,
        api_key=settings.llm_api_key,
        base_url=settings.llm_api_base_url,
        timeout=settings.llm_timeout_seconds,
        stream_usage=True,
        supports_thinking=settings.llm_text_enable_thinking,
        when_thinking_enabled=_thinking_options(
            enabled=settings.llm_text_enable_thinking,
            budget=settings.llm_text_thinking_budget,
        ),
        when_thinking_disabled=_thinking_options(enabled=False, budget=None),
    )


def multimodal_harness_model_config(settings: Settings) -> HarnessModelConfig:
    return HarnessModelConfig(
        name="default",
        model=settings.llm_multimodal_model,
        api_key=settings.llm_api_key,
        base_url=settings.llm_api_base_url,
        timeout=settings.llm_timeout_seconds,
        stream_usage=True,
        supports_thinking=settings.llm_multimodal_enable_thinking,
        when_thinking_enabled=_thinking_options(
            enabled=settings.llm_multimodal_enable_thinking,
            budget=settings.llm_multimodal_thinking_budget,
        ),
        when_thinking_disabled=_thinking_options(enabled=False, budget=None),
        supports_vision=True,
    )


def create_chat_model(
    *,
    settings: Settings,
    name: str | None = None,
    thinking_enabled: bool = False,
    attach_tracing: bool = False,
    multimodal: bool = False,
    config: HarnessModelConfig | None = None,
    **kwargs: Any,
) -> Any:
    model_config = config or (multimodal_harness_model_config(settings) if multimodal else default_harness_model_config(settings))
    if name not in {None, model_config.name}:
        raise ValueError(f"未找到 Agent Harness model 配置：{name}")

    model_class = _resolve_class(model_config.use)
    init_kwargs = model_config.model_dump(
        exclude_none=True,
        exclude={
            "name",
            "use",
            "supports_thinking",
            "when_thinking_enabled",
            "when_thinking_disabled",
            "supports_vision",
        },
    )
    if thinking_enabled:
        if not model_config.supports_thinking:
            raise ValueError(f"Model {model_config.name} 不支持 thinking。")
        if model_config.when_thinking_enabled:
            init_kwargs.update(model_config.when_thinking_enabled)
    elif model_config.when_thinking_disabled:
        init_kwargs.update(model_config.when_thinking_disabled)
    if "stream_usage" not in init_kwargs:
        init_kwargs["stream_usage"] = True
    init_kwargs.update(kwargs)
    model = model_class(**init_kwargs)
    if attach_tracing:
        _attach_tracing_callbacks(model)
    return model


def _resolve_class(path: str) -> type:
    module_name, sep, class_name = path.partition(":")
    if not sep:
        module_name, _, class_name = path.rpartition(".")
    if not module_name or not class_name:
        raise ValueError(f"无效模型类路径：{path}")
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise ImportError(f"无法导入模型 provider：{module_name}") from exc
    try:
        cls = getattr(module, class_name)
    except AttributeError as exc:
        raise ImportError(f"模型 provider 中不存在类：{path}") from exc
    if not isinstance(cls, type):
        raise TypeError(f"模型 provider 不是类：{path}")
    return cls


def _thinking_options(*, enabled: bool, budget: int | None) -> dict[str, Any] | None:
    if not enabled:
        return {"extra_body": {"enable_thinking": False}}
    options: dict[str, Any] = {"extra_body": {"enable_thinking": True}}
    if budget is not None:
        options["extra_body"]["thinking_budget"] = budget
    return options


def _attach_tracing_callbacks(model: Any) -> None:
    # PSOP 当前使用 OpenTelemetry；LangChain/LangSmith callback 后续再接入。
    # 保留 attach_tracing 参数是为了与 deer-flow 风格的 model factory API 对齐。
    return None
