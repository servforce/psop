from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.compiler.models import ArtifactObject, CompileDiagnostic, EgCompileArtifact, SkillCompileRequest
from app.pskills.models import PSkillDefinition, PSkillVersion


class CompilerRepository:
    """Database access for compile requests, diagnostics and EG artifacts."""

    def get_pskill_definition(self, session: Session, skill_id: str) -> PSkillDefinition | None:
        return session.get(PSkillDefinition, skill_id)

    def get_pskill_version(self, session: Session, version_id: str | None) -> PSkillVersion | None:
        if not version_id:
            return None
        return session.get(PSkillVersion, version_id)

    def get_compile_request(self, session: Session, request_id: str) -> SkillCompileRequest | None:
        return session.get(SkillCompileRequest, request_id)

    def get_compile_request_by_dedupe_key(self, session: Session, dedupe_key: str) -> SkillCompileRequest | None:
        return session.scalar(select(SkillCompileRequest).where(SkillCompileRequest.dedupe_key == dedupe_key))

    def list_compile_requests(
        self,
        session: Session,
        *,
        skill_id: str | None = None,
        status: str | None = None,
    ) -> list[SkillCompileRequest]:
        query = select(SkillCompileRequest).order_by(SkillCompileRequest.requested_at.desc())
        if skill_id:
            query = query.where(SkillCompileRequest.pskill_definition_id == skill_id)
        if status:
            query = query.where(SkillCompileRequest.status == status)
        return list(session.scalars(query).all())

    def get_artifact_for_request(self, session: Session, request_id: str) -> EgCompileArtifact | None:
        return session.scalar(
            select(EgCompileArtifact).where(EgCompileArtifact.skill_compile_request_id == request_id)
        )

    def get_artifact(self, session: Session, artifact_id: str) -> EgCompileArtifact | None:
        return session.get(EgCompileArtifact, artifact_id)

    def get_artifact_object(self, session: Session, object_id: str) -> ArtifactObject | None:
        return session.get(ArtifactObject, object_id)

    def list_compile_diagnostics(self, session: Session, request_id: str) -> list[CompileDiagnostic]:
        return list(
            session.scalars(
                select(CompileDiagnostic)
                .where(CompileDiagnostic.skill_compile_request_id == request_id)
                .order_by(CompileDiagnostic.created_at.asc())
            ).all()
        )

