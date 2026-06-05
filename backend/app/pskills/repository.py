from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.pskills.models import (
    PSkillDefinition,
    PSkillPublishRecord,
    PSkillMaterial,
    PSkillMaterialAnalysis,
    PSkillMaterialDerivedAsset,
    PSkillMaterialGeneration,
    PSkillVersion,
)


class SkillsRepository:
    """Encapsulates database reads and writes for skill-related objects."""

    def list_pskill_definitions(
        self,
        session: Session,
        *,
        search: str | None = None,
        status: str | None = None,
        is_published: bool | None = None,
    ) -> list[PSkillDefinition]:
        query = select(PSkillDefinition).order_by(PSkillDefinition.updated_at.desc())

        if status:
            query = query.where(PSkillDefinition.status == status)
        else:
            query = query.where(PSkillDefinition.status != "archived")
        if is_published is True:
            query = query.where(PSkillDefinition.latest_published_version_id.is_not(None))
        elif is_published is False:
            query = query.where(PSkillDefinition.latest_published_version_id.is_(None))
        if search:
            pattern = f"%{search.strip()}%"
            query = query.where(
                PSkillDefinition.key.ilike(pattern) | PSkillDefinition.name.ilike(pattern)
            )

        return list(session.scalars(query).all())

    def get_pskill_definition(self, session: Session, skill_id: str) -> PSkillDefinition | None:
        return session.get(PSkillDefinition, skill_id)

    def get_pskill_definition_by_key(self, session: Session, key: str) -> PSkillDefinition | None:
        return session.scalar(select(PSkillDefinition).where(PSkillDefinition.key == key))

    def get_pskill_version(self, session: Session, version_id: str | None) -> PSkillVersion | None:
        if not version_id:
            return None
        return session.get(PSkillVersion, version_id)

    def get_publish_records(self, session: Session, pskill_definition_id: str) -> list[PSkillPublishRecord]:
        query = (
            select(PSkillPublishRecord)
            .where(PSkillPublishRecord.pskill_definition_id == pskill_definition_id)
            .order_by(PSkillPublishRecord.published_at.desc(), PSkillPublishRecord.created_at.desc())
        )
        return list(session.scalars(query).all())

    def get_draft_version(self, session: Session, pskill_definition: PSkillDefinition) -> PSkillVersion | None:
        if pskill_definition.latest_draft_version_id:
            return self.get_pskill_version(session, pskill_definition.latest_draft_version_id)

        query = (
            select(PSkillVersion)
            .where(
                PSkillVersion.pskill_definition_id == pskill_definition.id,
                PSkillVersion.status == "draft",
            )
            .order_by(PSkillVersion.updated_at.desc())
        )
        return session.scalar(query)

    def next_published_version_no(self, session: Session, pskill_definition_id: str) -> int:
        query = select(func.max(PSkillVersion.version_no)).where(
            PSkillVersion.pskill_definition_id == pskill_definition_id,
            PSkillVersion.status == "published",
        )
        current = session.scalar(query)
        if current is None:
            return 1
        return int(current) + 1

    def list_materials(self, session: Session, pskill_definition_id: str) -> list[PSkillMaterial]:
        query = (
            select(PSkillMaterial)
            .where(
                PSkillMaterial.pskill_definition_id == pskill_definition_id,
                PSkillMaterial.status != "archived",
            )
            .order_by(PSkillMaterial.created_at.desc())
        )
        return list(session.scalars(query).all())

    def get_material(self, session: Session, material_id: str) -> PSkillMaterial | None:
        return session.get(PSkillMaterial, material_id)

    def list_materials_by_ids(
        self,
        session: Session,
        *,
        pskill_definition_id: str,
        material_ids: list[str],
    ) -> list[PSkillMaterial]:
        if not material_ids:
            return []
        query = select(PSkillMaterial).where(
            PSkillMaterial.pskill_definition_id == pskill_definition_id,
            PSkillMaterial.id.in_(material_ids),
            PSkillMaterial.status != "archived",
        )
        return list(session.scalars(query).all())

    def add_material_generation(
        self,
        session: Session,
        generation: PSkillMaterialGeneration,
    ) -> PSkillMaterialGeneration:
        session.add(generation)
        session.flush()
        return generation

    def get_material_generation(self, session: Session, generation_id: str) -> PSkillMaterialGeneration | None:
        return session.get(PSkillMaterialGeneration, generation_id)

    def get_latest_material_analysis(
        self,
        session: Session,
        material_id: str,
    ) -> PSkillMaterialAnalysis | None:
        query = (
            select(PSkillMaterialAnalysis)
            .where(PSkillMaterialAnalysis.material_id == material_id)
            .order_by(PSkillMaterialAnalysis.created_at.desc())
            .limit(1)
        )
        return session.scalar(query)

    def get_material_analysis(self, session: Session, analysis_id: str) -> PSkillMaterialAnalysis | None:
        return session.get(PSkillMaterialAnalysis, analysis_id)

    def get_derived_asset(self, session: Session, asset_id: str) -> PSkillMaterialDerivedAsset | None:
        return session.get(PSkillMaterialDerivedAsset, asset_id)

    def list_derived_assets(
        self,
        session: Session,
        *,
        material_id: str,
        analysis_id: str | None = None,
    ) -> list[PSkillMaterialDerivedAsset]:
        query = select(PSkillMaterialDerivedAsset).where(PSkillMaterialDerivedAsset.material_id == material_id)
        if analysis_id:
            query = query.where(PSkillMaterialDerivedAsset.analysis_id == analysis_id)
        query = query.order_by(PSkillMaterialDerivedAsset.timestamp_ms.asc(), PSkillMaterialDerivedAsset.created_at.asc())
        return list(session.scalars(query).all())
