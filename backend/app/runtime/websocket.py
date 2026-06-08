from __future__ import annotations

from typing import Any

from fastapi import WebSocket


class WebSocketHub:
    def __init__(self) -> None:
        self._connections: dict[str, set[WebSocket]] = {}

    async def connect(self, channel: str, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections.setdefault(channel, set()).add(websocket)

    def disconnect(self, channel: str, websocket: WebSocket) -> None:
        connections = self._connections.get(channel)
        if not connections:
            return
        connections.discard(websocket)
        if not connections:
            self._connections.pop(channel, None)

    async def broadcast(self, channel: str, event: dict[str, Any]) -> None:
        connections = list(self._connections.get(channel, set()))
        for websocket in connections:
            try:
                await websocket.send_json(event)
            except RuntimeError:
                self.disconnect(channel, websocket)


run_ws_hub = WebSocketHub()
tool_authorization_ws_hub = WebSocketHub()


def run_event_ws_message(run_id: str, event: Any) -> dict[str, Any]:
    return {
        "event_type": "terminal.event.appended",
        "run_id": run_id,
        "invocation_id": None,
        "seq_no": event.seq_no,
        "occurred_at": event.occurred_at.isoformat(),
        "payload": event.model_dump(mode="json"),
    }


def tool_authorization_ws_message(authorization: Any, *, action: str) -> dict[str, Any]:
    event_type = {
        "requested": "tool.authorization_requested",
        "approved": "tool.authorization_approved",
        "rejected": "tool.authorization_rejected",
    }.get(action, "tool.authorization_updated")
    return {
        "event_type": event_type,
        "authorization_id": authorization.id,
        "run_id": authorization.run_id,
        "agent_run_id": authorization.agent_run_id,
        "occurred_at": authorization.responded_at.isoformat()
        if authorization.responded_at
        else authorization.created_at.isoformat(),
        "payload": authorization.model_dump(mode="json"),
    }
