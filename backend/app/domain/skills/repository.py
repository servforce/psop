from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.skills.models import SkillDefinition, SkillPublishRecord, SkillVersion


class SkillsRepository:
    """Encapsulates database reads and writes for skill-related objects."""

    def list_skill_definitions(
        self,
        session: Session,
        *,
        search: str | None = None,
        status: str | None = None,
    ) -> list[SkillDefinition]:
        query = select(SkillDefinition).order_by(SkillDefinition.updated_at.desc())

        if status:
            query = query.where(SkillDefinition.status == status)
        if search:
            pattern = f"%{search.strip()}%"
            query = query.where(
                SkillDefinition.key.ilike(pattern) | SkillDefinition.name.ilike(pattern)
            )

        return list(session.scalars(query).all())

    def get_skill_definition(self, session: Session, skill_id: str) -> SkillDefinition | None:
        return session.get(SkillDefinition, skill_id)

    def get_skill_definition_by_key(self, session: Session, key: str) -> SkillDefinition | None:
        return session.scalar(select(SkillDefinition).where(SkillDefinition.key == key))

    def get_skill_version(self, session: Session, version_id: str | None) -> SkillVersion | None:
        if not version_id:
            return None
        return session.get(SkillVersion, version_id)

    def get_publish_records(self, session: Session, skill_definition_id: str) -> list[SkillPublishRecord]:
        query = (
            select(SkillPublishRecord)
            .where(SkillPublishRecord.skill_definition_id == skill_definition_id)
            .order_by(SkillPublishRecord.published_at.desc(), SkillPublishRecord.created_at.desc())
        )
        return list(session.scalars(query).all())

    def get_draft_version(self, session: Session, skill_definition: SkillDefinition) -> SkillVersion | None:
        if skill_definition.latest_draft_version_id:
            return self.get_skill_version(session, skill_definition.latest_draft_version_id)

        query = (
            select(SkillVersion)
            .where(
                SkillVersion.skill_definition_id == skill_definition.id,
                SkillVersion.status == "draft",
            )
            .order_by(SkillVersion.updated_at.desc())
        )
        return session.scalar(query)

    def next_published_version_no(self, session: Session, skill_definition_id: str) -> int:
        query = select(func.max(SkillVersion.version_no)).where(
            SkillVersion.skill_definition_id == skill_definition_id,
            SkillVersion.status == "published",
        )
        current = session.scalar(query)
        if current is None:
            return 1
        return int(current) + 1
