from __future__ import annotations

import asyncio
import json
import logging
import queue
import re
import threading
import uuid
from typing import Any, Protocol
from collections.abc import Callable


LOGGER = logging.getLogger(__name__)
_POSTGRES_EVENT_TYPES = {"terminal.event.appended", "trace.event.appended"}
_CLOSE_SENTINEL = object()


class RuntimeEventSink(Protocol):
    def publish(self, event: dict[str, Any]) -> None:
        """Publish a post-commit runtime event."""


class NoopRuntimeEventSink:
    def publish(self, event: dict[str, Any]) -> None:
        return None


class CompositeRuntimeEventSink:
    def __init__(self, *sinks: RuntimeEventSink) -> None:
        self.sinks = tuple(sinks)

    def publish(self, event: dict[str, Any]) -> None:
        for sink in self.sinks:
            try:
                sink.publish(event)
            except Exception:  # Event delivery is best effort; REST remains authoritative.
                LOGGER.exception("runtime event sink publish failed", extra={"event_type": event.get("event_type")})


class PostgresRuntimeEventSink:
    """Non-blocking PostgreSQL NOTIFY publisher for cross-process event hints."""

    def __init__(
        self,
        *,
        database_url: str,
        channel: str = "psop_runtime_events",
        source_id: str | None = None,
        queue_size: int = 10_000,
    ) -> None:
        self.database_url = _psycopg_dsn(database_url)
        self.channel = _validate_channel(channel)
        self.source_id = source_id or f"runtime-events:{uuid.uuid4()}"
        self._queue: queue.Queue[dict[str, Any] | object] = queue.Queue(maxsize=queue_size)
        self._closed = False
        self._thread = threading.Thread(target=self._run, name="psop-runtime-event-publisher", daemon=True)
        self._thread.start()

    def publish(self, event: dict[str, Any]) -> None:
        if self._closed or event.get("event_type") not in _POSTGRES_EVENT_TYPES:
            return
        hint = {
            "event_type": str(event.get("event_type") or ""),
            "run_id": str(event.get("run_id") or ""),
            "seq_no": int(event.get("seq_no") or 0),
            "source_id": self.source_id,
        }
        if not hint["run_id"] or hint["seq_no"] <= 0:
            return
        try:
            self._queue.put_nowait(hint)
        except queue.Full:
            LOGGER.error(
                "runtime event notification queue full",
                extra={"event_type": hint["event_type"], "run_id": hint["run_id"], "seq_no": hint["seq_no"]},
            )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._queue.put(_CLOSE_SENTINEL, timeout=1)
        except queue.Full:
            LOGGER.warning("runtime event notification queue did not drain during shutdown")
            try:
                self._queue.get_nowait()
                self._queue.put_nowait(_CLOSE_SENTINEL)
            except (queue.Empty, queue.Full):
                pass
        self._thread.join(timeout=5)

    def _run(self) -> None:
        connection = None
        while True:
            item = self._queue.get()
            if item is _CLOSE_SENTINEL:
                break
            assert isinstance(item, dict)
            payload = json.dumps(item, separators=(",", ":"), ensure_ascii=True)
            for attempt in range(2):
                try:
                    if connection is None or connection.closed:
                        import psycopg

                        connection = psycopg.connect(self.database_url, autocommit=True, connect_timeout=3)
                    connection.execute("SELECT pg_notify(%s, %s)", (self.channel, payload))
                    break
                except Exception:
                    if connection is not None:
                        try:
                            connection.close()
                        except Exception:
                            pass
                    connection = None
                    if attempt:
                        LOGGER.exception("runtime event PostgreSQL notification failed", extra=item)
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass


class PostgresRuntimeEventListener:
    """LISTEN loop that forwards external event hints into the API event bus."""

    def __init__(
        self,
        *,
        database_url: str,
        callback: Callable[[dict[str, Any]], None],
        channel: str = "psop_runtime_events",
        source_id: str = "",
    ) -> None:
        self.database_url = _psycopg_dsn(database_url)
        self.channel = _validate_channel(channel)
        self.source_id = source_id
        self.callback = callback
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="psop-runtime-event-listener", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                import psycopg
                from psycopg import sql

                with psycopg.connect(self.database_url, autocommit=True, connect_timeout=3) as connection:
                    connection.execute(sql.SQL("LISTEN {}").format(sql.Identifier(self.channel)))
                    while not self._stop.is_set():
                        for notification in connection.notifies(timeout=1, stop_after=1):
                            hint = json.loads(notification.payload)
                            if not isinstance(hint, dict) or hint.get("source_id") == self.source_id:
                                continue
                            if hint.get("event_type") not in _POSTGRES_EVENT_TYPES:
                                continue
                            self.callback(hint)
            except Exception:
                if not self._stop.is_set():
                    LOGGER.exception("runtime event PostgreSQL listener failed; retrying")
                    self._stop.wait(1)


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


def _psycopg_dsn(database_url: str) -> str:
    return database_url.replace("postgresql+psycopg://", "postgresql://", 1)


def _validate_channel(channel: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", channel):
        raise ValueError("PostgreSQL notification channel is invalid.")
    return channel
