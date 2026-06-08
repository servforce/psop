from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.compiler.models import ArtifactObject, EgCompileArtifact
from app.runtime.models import Run
from app.testing.models import (
    PSkillTestSuite,
    PSkillPublishGate,
    SkillTestAsset,
    SkillTestExpectationEvaluation,
    SkillTestScenario,
    SkillTestScenarioRun,
)
from app.pskills.models import PSkillDefinition, PSkillVersion


class SkillTestRepository:
    def get_skill(self, session: Session, skill_id: str) -> PSkillDefinition | None:
        return session.get(PSkillDefinition, skill_id)

    def get_artifact(self, session: Session, artifact_id: str | None) -> EgCompileArtifact | None:
        if not artifact_id:
            return None
        return session.get(EgCompileArtifact, artifact_id)

    def get_pskill_version(self, session: Session, version_id: str | None) -> PSkillVersion | None:
        if not version_id:
            return None
        return session.get(PSkillVersion, version_id)

    def get_latest_ready_artifact(self, session: Session, pskill_version_id: str | None) -> EgCompileArtifact | None:
        if not pskill_version_id:
            return None
        return session.scalar(
            select(EgCompileArtifact)
            .where(
                EgCompileArtifact.pskill_version_id == pskill_version_id,
                EgCompileArtifact.status == "ready",
            )
            .order_by(EgCompileArtifact.created_at.desc())
        )

    def get_default_suite(
        self,
        session: Session,
        *,
        pskill_definition_id: str,
        pskill_version_id: str | None,
        suite_type: str = "runtime_simulation",
    ) -> PSkillTestSuite | None:
        query = select(PSkillTestSuite).where(
            PSkillTestSuite.pskill_definition_id == pskill_definition_id,
            PSkillTestSuite.suite_type == suite_type,
            PSkillTestSuite.status == "active",
        )
        if pskill_version_id:
            query = query.where(PSkillTestSuite.pskill_version_id == pskill_version_id)
        else:
            query = query.where(PSkillTestSuite.pskill_version_id.is_(None))
        return session.scalar(query.order_by(PSkillTestSuite.created_at.desc()))

    def list_suites(
        self,
        session: Session,
        *,
        pskill_definition_id: str | None = None,
        status: str | None = None,
    ) -> list[PSkillTestSuite]:
        query = select(PSkillTestSuite)
        if pskill_definition_id:
            query = query.where(PSkillTestSuite.pskill_definition_id == pskill_definition_id)
        if status:
            query = query.where(PSkillTestSuite.status == status)
        else:
            query = query.where(PSkillTestSuite.status != "archived")
        return list(session.scalars(query.order_by(PSkillTestSuite.created_at.desc())).all())

    def get_suite(self, session: Session, suite_id: str | None) -> PSkillTestSuite | None:
        if not suite_id:
            return None
        return session.get(PSkillTestSuite, suite_id)

    def list_scenarios_for_suite(self, session: Session, suite_id: str) -> list[SkillTestScenario]:
        return list(
            session.scalars(
                select(SkillTestScenario)
                .where(
                    SkillTestScenario.suite_id == suite_id,
                    SkillTestScenario.status != "archived",
                )
                .order_by(SkillTestScenario.updated_at.desc(), SkillTestScenario.created_at.desc())
            ).all()
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
                    SkillTestScenario.pskill_definition_id == skill_id,
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

    def create_publish_gate(
        self,
        session: Session,
        *,
        pskill_definition_id: str,
        pskill_version_id: str | None,
        test_run_id: str | None,
        status: str,
        score: int,
        result_json: dict,
    ) -> PSkillPublishGate:
        gate = PSkillPublishGate(
            pskill_definition_id=pskill_definition_id,
            pskill_version_id=pskill_version_id,
            test_run_id=test_run_id,
            status=status,
            score=score,
            result_json=result_json,
        )
        session.add(gate)
        session.flush()
        return gate

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
