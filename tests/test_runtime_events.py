from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app import app as app_module
from app.api.routes.runtime import RunWebSocketHub, run_ws_hub


def test_websocket_hub_disconnects_one_broken_client_without_blocking_others() -> None:
    hub = RunWebSocketHub()
    delivered = []

    class BrokenSocket:
        async def send_json(self, _event) -> None:
            raise ValueError("socket closed")

    class HealthySocket:
        async def send_json(self, event) -> None:
            delivered.append(event)

    broken = BrokenSocket()
    healthy = HealthySocket()
    hub._connections["run-1"] = {broken, healthy}  # type: ignore[assignment]

    asyncio.run(hub.broadcast("run-1", {"event_type": "terminal.event.appended"}))

    assert delivered == [{"event_type": "terminal.event.appended"}]
    assert broken not in hub._connections["run-1"]
    assert healthy in hub._connections["run-1"]


def test_broadcaster_continues_after_one_bad_notification(monkeypatch) -> None:
    class FakeBus:
        def __init__(self) -> None:
            self.events = [
                {"event_type": "terminal.event.appended", "run_id": "run-1", "seq_no": "bad"},
                {
                    "event_type": "terminal.event.appended",
                    "run_id": "run-1",
                    "seq_no": 2,
                    "payload": {"seq_no": 2},
                },
                {"event_type": "runtime.event_bus.closed"},
            ]

        async def next_event(self):
            return self.events.pop(0)

    monkeypatch.setattr(
        app_module,
        "_hydrate_runtime_event",
        lambda _app, _event: (_ for _ in ()).throw(ValueError("invalid hint")),
    )
    broadcast = AsyncMock()
    monkeypatch.setattr(run_ws_hub, "broadcast", broadcast)

    asyncio.run(app_module._broadcast_runtime_events(SimpleNamespace(), FakeBus()))

    broadcast.assert_awaited_once()
    assert broadcast.await_args.args[0] == "run-1"
    assert broadcast.await_args.args[1]["seq_no"] == 2
