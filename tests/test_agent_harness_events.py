from __future__ import annotations

from app.agent_harness.events import AgentEventEmitter


class FakeAgentService:
    def __init__(self) -> None:
        self.request = None
        self.commit = None

    def append_event(self, session, agent_run_id, payload, *, commit=False):
        self.request = payload
        self.commit = commit
        return payload


def test_agent_event_emitter_redacts_sensitive_payload_fields() -> None:
    service = FakeAgentService()
    emitter = AgentEventEmitter(service)

    event = emitter.emit(
        object(),
        "agent-run-1",
        event_type="agent.test",
        phase="test",
        payload={
            "safe": "visible",
            "api_key": "secret-api-key",
            "nested": {"session-token": "secret-token", "values": [{"password": "secret-password"}]},
        },
        commit=False,
    )

    assert event.payload["safe"] == "visible"
    assert event.payload["api_key"] == "[redacted]"
    assert event.payload["nested"]["session-token"] == "[redacted]"
    assert event.payload["nested"]["values"][0]["password"] == "[redacted]"
    assert service.commit is False
