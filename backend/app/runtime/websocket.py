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
TOOL_AUTHORIZATION_WS_CHANNEL = "global"


def run_event_ws_message(run_id: str, event: Any) -> dict[str, Any]:
    return {
        "event_type": "terminal.event.appended",
        "run_id": run_id,
        "invocation_id": None,
        "seq_no": event.seq_no,
        "occurred_at": event.occurred_at.isoformat(),
        "payload": event.model_dump(mode="json"),
    }


def run_trace_ws_message(run_id: str, event: Any) -> dict[str, Any]:
    return {
        "event_type": "trace.event.appended",
        "run_id": run_id,
        "invocation_id": None,
        "seq_no": event.seq_no,
        "occurred_at": event.occurred_at.isoformat(),
        "payload": event.model_dump(mode="json"),
    }


def session_token_snapshot_ws_message(run_id: str, snapshot: Any) -> dict[str, Any]:
    return {
        "event_type": "session_token.snapshot.appended",
        "run_id": run_id,
        "invocation_id": None,
        "seq_no": snapshot.seq_no,
        "occurred_at": snapshot.created_at.isoformat(),
        "payload": snapshot.model_dump(mode="json"),
    }


def run_updated_ws_message(run_id: str, run: Any) -> dict[str, Any]:
    return {
        "event_type": "run.updated",
        "run_id": run_id,
        "invocation_id": getattr(run, "invocation_id", None),
        "seq_no": max(
            int(getattr(run, "latest_run_event_seq", 0) or 0),
            int(getattr(run, "latest_trace_seq", 0) or 0),
            int(getattr(run, "latest_snapshot_seq", 0) or 0),
        ),
        "occurred_at": run.updated_at.isoformat(),
        "payload": run.model_dump(mode="json"),
    }


def run_bindings_ws_message(run_id: str, bindings: list[Any], *, action: str = "updated") -> dict[str, Any]:
    event_type = "binding.resolved" if action == "resolved" else "binding.updated"
    return {
        "event_type": event_type,
        "run_id": run_id,
        "invocation_id": None,
        "seq_no": 0,
        "occurred_at": None,
        "payload": {
            "bindings": [binding.model_dump(mode="json") for binding in bindings],
        },
    }


def tool_authorization_ws_message(authorization: Any, *, action: str) -> dict[str, Any]:
    event_type = {
        "requested": "tool.authorization_requested",
        "approved": "tool.authorization_approved",
        "rejected": "tool.authorization_rejected",
        "expired": "tool.authorization_expired",
        "cancelled": "tool.authorization_cancelled",
        "executed": "tool.authorization_executed",
    }.get(action, "tool.authorization_updated")
    return {
        "event_type": event_type,
        "authorization_id": authorization.id,
        "run_id": authorization.run_id,
        "agent_run_id": authorization.agent_run_id,
        "occurred_at": _tool_authorization_occurred_at(authorization, action).isoformat(),
        "payload": authorization.model_dump(mode="json"),
    }


def _tool_authorization_occurred_at(authorization: Any, action: str) -> Any:
    if action == "executed" and authorization.executed_at:
        return authorization.executed_at
    if authorization.responded_at:
        return authorization.responded_at
    return authorization.created_at
