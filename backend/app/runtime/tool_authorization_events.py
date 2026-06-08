from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.pskills.models import now_utc
from app.runtime.models import RunEvent
from app.runtime.repository import RuntimeRepository


class RunToolAuthorizationEventWriter:
    """Writes AgentRun tool authorization gates into the Runtime event stream."""

    def __init__(self, repository: RuntimeRepository | None = None) -> None:
        self.repository = repository or RuntimeRepository()

    def append_request_event(self, session: Session, authorization: Any) -> str | None:
        event = self._append_event(
            session,
            authorization=authorization,
            event_kind="tool_authorization_request",
            external_event_id=f"tool-authorization:{authorization.id}:request",
            payload={
                "authorization_id": authorization.id,
                "agent_run_id": authorization.agent_run_id,
                "agent_tool_call_id": authorization.agent_tool_call_id,
                "tool_name": authorization.tool_name,
                "tool_provider": authorization.tool_provider,
                "mcp_server_name": authorization.mcp_server_name,
                "side_effect_level": authorization.side_effect_level,
                "risk_level": authorization.risk_level,
                "authorization_reason": authorization.authorization_reason,
                "tool_arguments_summary": authorization.tool_arguments_summary,
                "expected_effect_summary": authorization.expected_effect_summary,
                "reversible": authorization.reversible,
                "idempotency_key": authorization.idempotency_key,
                "status": authorization.status,
                "request_payload": authorization.request_payload,
            },
        )
        return event.id if event else None

    def append_decision_event(self, session: Session, authorization: Any, *, decision: str) -> str | None:
        event = self._append_event(
            session,
            authorization=authorization,
            event_kind="tool_authorization_response",
            external_event_id=f"tool-authorization:{authorization.id}:{decision}",
            payload={
                "authorization_id": authorization.id,
                "agent_run_id": authorization.agent_run_id,
                "agent_tool_call_id": authorization.agent_tool_call_id,
                "tool_name": authorization.tool_name,
                "tool_provider": authorization.tool_provider,
                "side_effect_level": authorization.side_effect_level,
                "risk_level": authorization.risk_level,
                "decision": decision,
                "status": authorization.status,
                "response_payload": authorization.response_payload,
                "responded_at": authorization.responded_at.isoformat() if authorization.responded_at else None,
                "request_run_event_id": authorization.run_event_id,
            },
        )
        return event.id if event else None

    def _append_event(
        self,
        session: Session,
        *,
        authorization: Any,
        event_kind: str,
        external_event_id: str,
        payload: dict[str, Any],
    ) -> RunEvent | None:
        if not authorization.run_id:
            return None
        run = self.repository.get_run(session, authorization.run_id)
        if not run:
            return None
        terminal_session = self.repository.get_terminal_session_for_run(session, run.id)
        if not terminal_session:
            return None

        existing = self.repository.get_run_event_by_external_id(
            session,
            run_id=run.id,
            external_event_id=external_event_id,
        )
        if existing:
            return existing

        binding_id = self._default_output_binding_id(session, run.id)
        next_seq = run.latest_run_event_seq + 1
        run.latest_run_event_seq = next_seq
        event = RunEvent(
            terminal_session_id=terminal_session.id,
            run_id=run.id,
            agent_run_id=authorization.agent_run_id,
            run_capability_binding_id=binding_id,
            direction="output",
            event_kind=event_kind,
            mime_type="application/json",
            payload_inline=payload,
            seq_no=next_seq,
            external_event_id=external_event_id,
            source_ref={
                "kind": "agent_tool_authorization",
                "agent_run_id": authorization.agent_run_id,
                "authorization_id": authorization.id,
            },
            occurred_at=now_utc(),
        )
        session.add(event)
        session.flush()
        return event

    def _default_output_binding_id(self, session: Session, run_id: str) -> str | None:
        for binding in self.repository.list_run_bindings(session, run_id):
            if binding.status == "active" and binding.requirement_key.endswith("output"):
                return binding.id
        return None
