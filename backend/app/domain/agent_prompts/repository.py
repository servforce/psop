from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.agent_prompts.models import AgentPromptBinding, AgentPromptDefinition, AgentPromptVersion


class AgentPromptRepository:
    def list_definitions(self, session: Session, *, status: str | None = None) -> list[AgentPromptDefinition]:
        query = select(AgentPromptDefinition).order_by(AgentPromptDefinition.updated_at.desc())
        if status:
            query = query.where(AgentPromptDefinition.status == status)
        else:
            query = query.where(AgentPromptDefinition.status != "archived")
        return list(session.scalars(query).all())

    def get_definition(self, session: Session, definition_id: str) -> AgentPromptDefinition | None:
        return session.get(AgentPromptDefinition, definition_id)

    def get_definition_by_key(self, session: Session, key: str) -> AgentPromptDefinition | None:
        return session.scalar(select(AgentPromptDefinition).where(AgentPromptDefinition.key == key))

    def list_versions(self, session: Session, definition_id: str) -> list[AgentPromptVersion]:
        return list(
            session.scalars(
                select(AgentPromptVersion)
                .where(AgentPromptVersion.definition_id == definition_id)
                .order_by(AgentPromptVersion.version_no.desc())
            ).all()
        )

    def get_version(self, session: Session, version_id: str) -> AgentPromptVersion | None:
        return session.get(AgentPromptVersion, version_id)

    def get_version_by_hash(
        self,
        session: Session,
        *,
        definition_id: str,
        content_hash: str,
    ) -> AgentPromptVersion | None:
        return session.scalar(
            select(AgentPromptVersion).where(
                AgentPromptVersion.definition_id == definition_id,
                AgentPromptVersion.content_hash == content_hash,
            )
        )

    def latest_version(self, session: Session, definition_id: str) -> AgentPromptVersion | None:
        return session.scalar(
            select(AgentPromptVersion)
            .where(AgentPromptVersion.definition_id == definition_id)
            .order_by(AgentPromptVersion.version_no.desc())
        )

    def next_version_no(self, session: Session, definition_id: str) -> int:
        current = session.scalar(
            select(func.max(AgentPromptVersion.version_no)).where(AgentPromptVersion.definition_id == definition_id)
        )
        return int(current or 0) + 1

    def list_bindings(self, session: Session) -> list[AgentPromptBinding]:
        return list(session.scalars(select(AgentPromptBinding).order_by(AgentPromptBinding.usage_key.asc())).all())

    def list_bindings_for_definition(self, session: Session, definition_id: str) -> list[AgentPromptBinding]:
        return list(
            session.scalars(
                select(AgentPromptBinding)
                .where(AgentPromptBinding.definition_id == definition_id)
                .order_by(AgentPromptBinding.usage_key.asc())
            ).all()
        )

    def get_binding(self, session: Session, usage_key: str) -> AgentPromptBinding | None:
        return session.scalar(select(AgentPromptBinding).where(AgentPromptBinding.usage_key == usage_key))

