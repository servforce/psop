from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.compiler.models import ArtifactObject, EgCompileArtifact
from app.domain.runtime.models import Run
from app.domain.skill_tests.models import (
    SkillTestAsset,
    SkillTestExpectationEvaluation,
    SkillTestScenario,
    SkillTestScenarioRun,
)
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

    def get_latest_ready_artifact(self, session: Session, skill_version_id: str | None) -> EgCompileArtifact | None:
        if not skill_version_id:
            return None
        return session.scalar(
            select(EgCompileArtifact)
            .where(
                EgCompileArtifact.skill_version_id == skill_version_id,
                EgCompileArtifact.status == "ready",
            )
            .order_by(EgCompileArtifact.created_at.desc())
        )

    def get_latest_ready_artifact_for_skill(self, session: Session, skill_id: str) -> EgCompileArtifact | None:
        return session.scalar(
            select(EgCompileArtifact)
            .join(SkillVersion, SkillVersion.id == EgCompileArtifact.skill_version_id)
            .where(
                SkillVersion.skill_definition_id == skill_id,
                EgCompileArtifact.status == "ready",
            )
            .order_by(EgCompileArtifact.created_at.desc())
        )

    def get_run(self, session: Session, run_id: str | None) -> Run | None:
        if not run_id:
            return None
        return session.get(Run, run_id)

    def list_scenarios(self, session: Session, skill_id: str) -> list[SkillTestScenario]:
        return list(
            session.scalars(
                select(SkillTestScenario)
                .where(
                    SkillTestScenario.skill_definition_id == skill_id,
                    SkillTestScenario.status != "archived",
                )
                .order_by(SkillTestScenario.updated_at.desc(), SkillTestScenario.created_at.desc())
            ).all()
        )

    def get_scenario(self, session: Session, scenario_id: str) -> SkillTestScenario | None:
        return session.get(SkillTestScenario, scenario_id)

    def list_assets(self, session: Session, scenario_id: str) -> list[SkillTestAsset]:
        return list(
            session.scalars(
                select(SkillTestAsset)
                .where(SkillTestAsset.scenario_id == scenario_id)
                .order_by(SkillTestAsset.created_at.desc())
            ).all()
        )

    def get_asset(self, session: Session, asset_id: str) -> SkillTestAsset | None:
        return session.get(SkillTestAsset, asset_id)

    def get_artifact_object(self, session: Session, artifact_object_id: str) -> ArtifactObject | None:
        return session.get(ArtifactObject, artifact_object_id)

    def list_runs(self, session: Session, scenario_id: str) -> list[SkillTestScenarioRun]:
        return list(
            session.scalars(
                select(SkillTestScenarioRun)
                .where(SkillTestScenarioRun.scenario_id == scenario_id)
                .order_by(SkillTestScenarioRun.created_at.desc())
            ).all()
        )

    def list_open_runs(self, session: Session, scenario_id: str) -> list[SkillTestScenarioRun]:
        return list(
            session.scalars(
                select(SkillTestScenarioRun)
                .where(
                    SkillTestScenarioRun.scenario_id == scenario_id,
                    SkillTestScenarioRun.status.in_(("pending", "queued", "running", "waiting_input")),
                )
                .order_by(SkillTestScenarioRun.created_at.desc())
            ).all()
        )

    def get_latest_run(self, session: Session, scenario_id: str) -> SkillTestScenarioRun | None:
        return session.scalar(
            select(SkillTestScenarioRun)
            .where(SkillTestScenarioRun.scenario_id == scenario_id)
            .order_by(SkillTestScenarioRun.created_at.desc())
        )

    def get_scenario_run(self, session: Session, scenario_run_id: str) -> SkillTestScenarioRun | None:
        return session.get(SkillTestScenarioRun, scenario_run_id)

    def list_expectation_evaluations(
        self,
        session: Session,
        scenario_run_id: str,
    ) -> list[SkillTestExpectationEvaluation]:
        return list(
            session.scalars(
                select(SkillTestExpectationEvaluation)
                .where(SkillTestExpectationEvaluation.scenario_run_id == scenario_run_id)
                .order_by(SkillTestExpectationEvaluation.created_at.asc())
            ).all()
        )

    def delete_expectation_evaluations(self, session: Session, scenario_run_id: str) -> None:
        for item in self.list_expectation_evaluations(session, scenario_run_id):
            session.delete(item)
