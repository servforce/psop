from __future__ import annotations

from collections.abc import Sequence
from typing import Any


def create_psop_agent(
    *,
    model: Any,
    tools: Sequence[Any],
    system_prompt: str,
    middleware: Sequence[Any],
    name: str,
) -> Any:
    try:
        from langchain.agents import create_agent
    except ImportError as exc:
        raise RuntimeError("未安装 LangChain agents，无法执行 Agent Harness。") from exc

    return create_agent(
        model=model,
        tools=list(tools),
        system_prompt=system_prompt,
        middleware=list(middleware),
        name=name,
    )
