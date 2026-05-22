from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.skills.models import (
    SkillDefinition,
    SkillPublishRecord,
    SkillRawMaterial,
    SkillRawMaterialAnalysis,
    SkillRawMaterialDerivedAsset,
    SkillRawMaterialGeneration,
    SkillVersion,
)


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
        else:
            query = query.where(SkillDefinition.status != "archived")
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

    def list_raw_materials(self, session: Session, skill_definition_id: str) -> list[SkillRawMaterial]:
        query = (
            select(SkillRawMaterial)
            .where(
                SkillRawMaterial.skill_definition_id == skill_definition_id,
                SkillRawMaterial.status != "archived",
            )
            .order_by(SkillRawMaterial.created_at.desc())
        )
        return list(session.scalars(query).all())

    def get_raw_material(self, session: Session, material_id: str) -> SkillRawMaterial | None:
        return session.get(SkillRawMaterial, material_id)

    def list_raw_materials_by_ids(
        self,
        session: Session,
        *,
        skill_definition_id: str,
        material_ids: list[str],
    ) -> list[SkillRawMaterial]:
        if not material_ids:
            return []
        query = select(SkillRawMaterial).where(
            SkillRawMaterial.skill_definition_id == skill_definition_id,
            SkillRawMaterial.id.in_(material_ids),
            SkillRawMaterial.status != "archived",
        )
        return list(session.scalars(query).all())

    def add_raw_material_generation(
        self,
        session: Session,
        generation: SkillRawMaterialGeneration,
    ) -> SkillRawMaterialGeneration:
        session.add(generation)
        session.flush()
        return generation

    def get_latest_raw_material_analysis(
        self,
        session: Session,
        raw_material_id: str,
    ) -> SkillRawMaterialAnalysis | None:
        query = (
            select(SkillRawMaterialAnalysis)
            .where(SkillRawMaterialAnalysis.raw_material_id == raw_material_id)
            .order_by(SkillRawMaterialAnalysis.created_at.desc())
            .limit(1)
        )
        return session.scalar(query)

    def get_raw_material_analysis(self, session: Session, analysis_id: str) -> SkillRawMaterialAnalysis | None:
        return session.get(SkillRawMaterialAnalysis, analysis_id)

    def get_derived_asset(self, session: Session, asset_id: str) -> SkillRawMaterialDerivedAsset | None:
        return session.get(SkillRawMaterialDerivedAsset, asset_id)

    def list_derived_assets(
        self,
        session: Session,
        *,
        raw_material_id: str,
        analysis_id: str | None = None,
    ) -> list[SkillRawMaterialDerivedAsset]:
        query = select(SkillRawMaterialDerivedAsset).where(SkillRawMaterialDerivedAsset.raw_material_id == raw_material_id)
        if analysis_id:
            query = query.where(SkillRawMaterialDerivedAsset.analysis_id == analysis_id)
        query = query.order_by(SkillRawMaterialDerivedAsset.timestamp_ms.asc(), SkillRawMaterialDerivedAsset.created_at.asc())
        return list(session.scalars(query).all())
