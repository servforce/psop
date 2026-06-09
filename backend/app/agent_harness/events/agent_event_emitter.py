from __future__ import annotations

from typing import Any, TYPE_CHECKING

from sqlalchemy.orm import Session

from app.agent_harness.events.event_redaction import redact_event_payload
from app.agents.schemas import AgentEventResponse, AppendAgentEventRequest

if TYPE_CHECKING:
    from app.agents.service import AgentService


class AgentEventEmitter:
    def __init__(self, agent_service: "AgentService") -> None:
        self.agent_service = agent_service

    def emit(
        self,
        session: Session,
        agent_run_id: str,
        *,
        event_type: str,
        phase: str,
        payload: dict[str, Any] | None = None,
        commit: bool = False,
    ) -> AgentEventResponse:
        return self.agent_service.append_event(
            session,
            agent_run_id,
            AppendAgentEventRequest(
                event_type=event_type,
                phase=phase,
                payload=redact_event_payload(payload or {}),
            ),
            commit=commit,
        )
