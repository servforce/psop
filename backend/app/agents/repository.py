from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agents.models import (
    AgentBinding,
    AgentDefinition,
    AgentEvent,
    AgentModelCall,
    AgentRun,
    AgentSession,
    AgentToolAuthorization,
    AgentToolCall,
    AgentVersion,
)


class AgentRepository:
    def list_definitions(self, session: Session, *, status: str | None = None) -> list[AgentDefinition]:
        query = select(AgentDefinition).order_by(AgentDefinition.key.asc())
        if status:
            query = query.where(AgentDefinition.status == status)
        else:
            query = query.where(AgentDefinition.status != "archived")
        return list(session.scalars(query).all())

    def get_definition(self, session: Session, definition_id: str) -> AgentDefinition | None:
        return session.get(AgentDefinition, definition_id)

    def get_definition_by_key(self, session: Session, key: str) -> AgentDefinition | None:
        return session.scalar(select(AgentDefinition).where(AgentDefinition.key == key))

    def list_versions(self, session: Session, definition_id: str) -> list[AgentVersion]:
        return list(
            session.scalars(
                select(AgentVersion)
                .where(AgentVersion.definition_id == definition_id)
                .order_by(AgentVersion.version_no.desc())
            ).all()
        )

    def get_version(self, session: Session, version_id: str | None) -> AgentVersion | None:
        if not version_id:
            return None
        return session.get(AgentVersion, version_id)

    def get_version_by_hash(self, session: Session, *, definition_id: str, content_hash: str) -> AgentVersion | None:
        return session.scalar(
            select(AgentVersion).where(
                AgentVersion.definition_id == definition_id,
                AgentVersion.content_hash == content_hash,
            )
        )

    def next_version_no(self, session: Session, definition_id: str) -> int:
        current = session.scalar(
            select(func.max(AgentVersion.version_no)).where(AgentVersion.definition_id == definition_id)
        )
        return int(current or 0) + 1

    def get_binding(self, session: Session, usage_key: str) -> AgentBinding | None:
        return session.scalar(select(AgentBinding).where(AgentBinding.usage_key == usage_key))

    def list_bindings_for_definition(self, session: Session, definition_id: str) -> list[AgentBinding]:
        return list(
            session.scalars(
                select(AgentBinding)
                .where(AgentBinding.definition_id == definition_id)
                .order_by(AgentBinding.usage_key.asc())
            ).all()
        )

    def get_session_by_owner(
        self,
        session: Session,
        *,
        agent_key: str,
        owner_type: str,
        owner_id: str,
    ) -> AgentSession | None:
        return session.scalar(
            select(AgentSession).where(
                AgentSession.agent_key == agent_key,
                AgentSession.owner_type == owner_type,
                AgentSession.owner_id == owner_id,
                AgentSession.status == "active",
            )
        )

    def get_session(self, session: Session, agent_session_id: str | None) -> AgentSession | None:
        if not agent_session_id:
            return None
        return session.get(AgentSession, agent_session_id)

    def list_runs(
        self,
        session: Session,
        *,
        agent_key: str | None = None,
        status: str | None = None,
        owner_type: str | None = None,
        owner_id: str | None = None,
    ) -> list[AgentRun]:
        query = select(AgentRun).order_by(AgentRun.created_at.desc())
        if agent_key:
            query = query.where(AgentRun.agent_key == agent_key)
        if status:
            query = query.where(AgentRun.status == status)
        if owner_type:
            query = query.where(AgentRun.owner_type == owner_type)
        if owner_id:
            query = query.where(AgentRun.owner_id == owner_id)
        return list(session.scalars(query).all())

    def get_run(self, session: Session, agent_run_id: str) -> AgentRun | None:
        return session.get(AgentRun, agent_run_id)

    def next_event_seq(self, session: Session, agent_run_id: str) -> int:
        current = session.scalar(
            select(func.max(AgentEvent.seq_no)).where(AgentEvent.agent_run_id == agent_run_id)
        )
        return int(current or 0) + 1

    def list_events(self, session: Session, agent_run_id: str) -> list[AgentEvent]:
        return list(
            session.scalars(
                select(AgentEvent)
                .where(AgentEvent.agent_run_id == agent_run_id)
                .order_by(AgentEvent.seq_no.asc())
            ).all()
        )

    def list_model_calls(self, session: Session, agent_run_id: str) -> list[AgentModelCall]:
        return list(
            session.scalars(
                select(AgentModelCall)
                .where(AgentModelCall.agent_run_id == agent_run_id)
                .order_by(AgentModelCall.created_at.asc())
            ).all()
        )

    def list_tool_calls(self, session: Session, agent_run_id: str) -> list[AgentToolCall]:
        return list(
            session.scalars(
                select(AgentToolCall)
                .where(AgentToolCall.agent_run_id == agent_run_id)
                .order_by(AgentToolCall.created_at.asc())
            ).all()
        )

    def get_tool_call(self, session: Session, tool_call_id: str | None) -> AgentToolCall | None:
        if not tool_call_id:
            return None
        return session.get(AgentToolCall, tool_call_id)

    def list_tool_authorizations(
        self,
        session: Session,
        *,
        agent_run_id: str | None = None,
        run_id: str | None = None,
        status: str | None = None,
    ) -> list[AgentToolAuthorization]:
        query = select(AgentToolAuthorization).order_by(AgentToolAuthorization.created_at.desc())
        if agent_run_id:
            query = query.where(AgentToolAuthorization.agent_run_id == agent_run_id)
        if run_id:
            query = query.where(AgentToolAuthorization.run_id == run_id)
        if status:
            query = query.where(AgentToolAuthorization.status == status)
        return list(session.scalars(query).all())

    def get_tool_authorization(self, session: Session, authorization_id: str) -> AgentToolAuthorization | None:
        return session.get(AgentToolAuthorization, authorization_id)
