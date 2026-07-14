from __future__ import annotations


class McpToolProvider:
    """MVP placeholder for future MCP server-backed tool loading."""

    def __init__(self, enabled: bool = False) -> None:
        self.enabled = enabled

    def list_tools(self) -> list[object]:
        return []

    def to_langchain_tools(self) -> list[object]:
        return []
