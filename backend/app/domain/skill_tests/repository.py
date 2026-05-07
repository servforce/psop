from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.compiler.models import ArtifactObject, EgCompileArtifact
from app.domain.runtime.models import Run
from app.domain.skill_tests.models import SkillTestCase, SkillTestDataObject, SkillTestRun
from app.domain.skills.models import SkillDefinition, SkillVersion


class SkillTestRepository:
    def get_skill(self, session: Session, skill_id: str) -> SkillDefinition | None:
        return session.get(SkillDefinition, skill_id)

    def get_artifact(self, session: Session, artifact_id: str | None) -> EgCompileArtifact | None:
        if not artifact_id:
            return None
        return session.get(EgCompileArtifact, artifact_id)

    def get_skill_version(self, session: Session, version_id: str | None) -> SkillVersion | None:
        if not version_id:
            return None
        return session.get(SkillVersion, version_id)

    def get_run(self, session: Session, run_id: str | None) -> Run | None:
        if not run_id:
            return None
        return session.get(Run, run_id)

    def list_cases(self, session: Session, skill_id: str) -> list[SkillTestCase]:
        return list(
            session.scalars(
                select(SkillTestCase)
                .where(SkillTestCase.skill_definition_id == skill_id, SkillTestCase.status != "archived")
                .order_by(SkillTestCase.updated_at.desc(), SkillTestCase.created_at.desc())
            ).all()
        )

    def get_case(self, session: Session, case_id: str) -> SkillTestCase | None:
        return session.get(SkillTestCase, case_id)

    def list_data_objects(self, session: Session, case_id: str) -> list[SkillTestDataObject]:
        return list(
            session.scalars(
                select(SkillTestDataObject)
                .where(SkillTestDataObject.test_case_id == case_id)
                .order_by(SkillTestDataObject.created_at.desc())
            ).all()
        )

    def get_data_object(self, session: Session, data_id: str) -> SkillTestDataObject | None:
        return session.get(SkillTestDataObject, data_id)

    def get_artifact_object(self, session: Session, artifact_object_id: str) -> ArtifactObject | None:
        return session.get(ArtifactObject, artifact_object_id)

    def list_runs(self, session: Session, case_id: str) -> list[SkillTestRun]:
        return list(
            session.scalars(
                select(SkillTestRun)
                .where(SkillTestRun.test_case_id == case_id)
                .order_by(SkillTestRun.created_at.desc())
            ).all()
        )

    def list_open_runs(self, session: Session, case_id: str) -> list[SkillTestRun]:
        return list(
            session.scalars(
                select(SkillTestRun)
                .where(
                    SkillTestRun.test_case_id == case_id,
                    SkillTestRun.status.in_(("pending", "queued", "running", "waiting_input")),
                )
                .order_by(SkillTestRun.created_at.desc())
            ).all()
        )

    def get_latest_run(self, session: Session, case_id: str) -> SkillTestRun | None:
        return session.scalar(
            select(SkillTestRun)
            .where(SkillTestRun.test_case_id == case_id)
            .order_by(SkillTestRun.created_at.desc())
        )

    def get_test_run(self, session: Session, test_run_id: str) -> SkillTestRun | None:
        return session.get(SkillTestRun, test_run_id)
