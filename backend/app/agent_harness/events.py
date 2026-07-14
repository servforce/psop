from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from app.agent_harness.schemas import AgentEvent


class AgentEventWriter:
    def __init__(self, events_path: Path, on_event: Callable[[AgentEvent], None] | None = None) -> None:
        self.events_path = events_path
        self.on_event = on_event
        self.events_path.parent.mkdir(parents=True, exist_ok=True)
        self._events: list[AgentEvent] = []
        self._seq_no = 0

    @property
    def events(self) -> list[AgentEvent]:
        return list(self._events)

    def record(self, event_type: str, payload: dict[str, Any] | None = None) -> AgentEvent:
        self._seq_no += 1
        event = AgentEvent(
            seq_no=self._seq_no,
            event_type=event_type,
            payload=payload or {},
            occurred_at=datetime.now(timezone.utc),
        )
        self._events.append(event)
        with self.events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event.model_dump(mode="json"), ensure_ascii=False) + "\n")
        if self.on_event is not None:
            self.on_event(event)
        return event
