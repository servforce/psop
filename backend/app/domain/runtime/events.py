from __future__ import annotations

import asyncio
from typing import Any, Protocol


class RuntimeEventSink(Protocol):
    def publish(self, event: dict[str, Any]) -> None:
        """Publish a post-commit runtime event."""


class NoopRuntimeEventSink:
    def publish(self, event: dict[str, Any]) -> None:
        return None


class AsyncioRuntimeEventBus:
    _CLOSED_EVENT_TYPE = "runtime.event_bus.closed"

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._closed = False

    def publish(self, event: dict[str, Any]) -> None:
        if self._closed:
            return
        self._loop.call_soon_threadsafe(self._queue.put_nowait, event)

    async def next_event(self) -> dict[str, Any]:
        return await self._queue.get()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._loop.call_soon_threadsafe(
            self._queue.put_nowait,
            {"event_type": self._CLOSED_EVENT_TYPE},
        )

    @classmethod
    def is_closed_event(cls, event: dict[str, Any]) -> bool:
        return event.get("event_type") == cls._CLOSED_EVENT_TYPE
