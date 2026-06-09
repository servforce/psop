from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.skills.models import SkillActivation, SkillBinding, SkillPackage, SkillResource, SkillVersion


class SkillPackageRepository:
    def list_packages(
        self,
        session: Session,
        *,
        scope: str | None = None,
        status: str | None = None,
    ) -> list[SkillPackage]:
        query = select(SkillPackage).order_by(SkillPackage.scope.asc(), SkillPackage.name.asc())
        if scope:
            query = query.where(SkillPackage.scope == scope)
        if status:
            query = query.where(SkillPackage.status == status)
        else:
            query = query.where(SkillPackage.status != "archived")
        return list(session.scalars(query).all())

    def get_package_by_name(self, session: Session, name: str) -> SkillPackage | None:
        return session.scalar(select(SkillPackage).where(SkillPackage.name == name))

    def get_package(self, session: Session, package_id: str) -> SkillPackage | None:
        return session.get(SkillPackage, package_id)

    def list_bindings(
        self,
        session: Session,
        *,
        agent_key: str | None = None,
        package_id: str | None = None,
    ) -> list[SkillBinding]:
        query = select(SkillBinding).order_by(SkillBinding.agent_key.asc(), SkillBinding.usage_key.asc())
        if agent_key:
            query = query.where(SkillBinding.agent_key == agent_key)
        if package_id:
            query = query.where(SkillBinding.package_id == package_id)
        return list(session.scalars(query).all())

    def get_binding(
        self,
        session: Session,
        *,
        agent_key: str,
        package_id: str,
    ) -> SkillBinding | None:
        return session.scalar(
            select(SkillBinding).where(
                SkillBinding.agent_key == agent_key,
                SkillBinding.package_id == package_id,
            )
        )

    def get_version_by_hash(
        self,
        session: Session,
        *,
        package_id: str,
        content_hash: str,
    ) -> SkillVersion | None:
        return session.scalar(
            select(SkillVersion).where(
                SkillVersion.package_id == package_id,
                SkillVersion.content_hash == content_hash,
            )
        )

    def get_version(self, session: Session, version_id: str | None) -> SkillVersion | None:
        if not version_id:
            return None
        return session.get(SkillVersion, version_id)

    def list_versions(self, session: Session, package_id: str) -> list[SkillVersion]:
        return list(
            session.scalars(
                select(SkillVersion)
                .where(SkillVersion.package_id == package_id)
                .order_by(SkillVersion.created_at.desc())
            ).all()
        )

    def list_resources(self, session: Session, version_id: str) -> list[SkillResource]:
        return list(
            session.scalars(
                select(SkillResource)
                .where(SkillResource.version_id == version_id)
                .order_by(SkillResource.resource_path.asc())
            ).all()
        )

    def get_activation(
        self,
        session: Session,
        *,
        agent_run_id: str,
        version_id: str,
    ) -> SkillActivation | None:
        return session.scalar(
            select(SkillActivation).where(
                SkillActivation.agent_run_id == agent_run_id,
                SkillActivation.version_id == version_id,
            )
        )

    def list_activations(self, session: Session, agent_run_id: str) -> list[SkillActivation]:
        return list(
            session.scalars(
                select(SkillActivation)
                .where(SkillActivation.agent_run_id == agent_run_id)
                .order_by(SkillActivation.created_at.asc())
            ).all()
        )
