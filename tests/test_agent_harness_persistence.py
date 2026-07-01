from __future__ import annotations

from datetime import datetime, timezone

from app.agent_harness.persistence.models import AgentArtifactRecord, AgentEventRecord, AgentRunRecord
from app.agent_harness.persistence.service import AgentHarnessPersistenceService
from app.agent_harness.schemas import AgentArtifact, AgentEvent, AgentResult
from app.infra.database import DatabaseManager


def test_agent_harness_persistence_records_result() -> None:
    manager = DatabaseManager("sqlite+pysqlite:///:memory:")
    manager.create_schema()

    with manager.session() as session:
        service = AgentHarnessPersistenceService()
        result = AgentResult(
            agent_run_id="run-1",
            agent_key="psop.builder",
            status="succeeded",
            final_output="ok",
            events=[
                AgentEvent(
                    seq_no=1,
                    event_type="agent.run.started",
                    payload={"agent_key": "psop.builder"},
                    occurred_at=datetime.now(timezone.utc),
                )
            ],
            artifacts=[AgentArtifact(artifact_type="skill_draft_candidate", path="sandbox://outputs/builder-result.json")],
            sandbox_path="/tmp/run-1",
        )

        service.persist_result(session, result)
        session.commit()

        assert session.get(AgentRunRecord, "run-1").status == "succeeded"
        assert session.query(AgentEventRecord).count() == 1
        assert session.query(AgentArtifactRecord).count() == 1


def test_agent_harness_persistence_replaces_existing_events_and_artifacts() -> None:
    manager = DatabaseManager("sqlite+pysqlite:///:memory:")
    manager.create_schema()

    with manager.session() as session:
        service = AgentHarnessPersistenceService()
        result = AgentResult(
            agent_run_id="run-1",
            agent_key="psop.builder",
            status="failed",
            final_output="",
            error_message="first failure",
            events=[
                AgentEvent(
                    seq_no=1,
                    event_type="agent.run.started",
                    payload={"agent_key": "psop.builder"},
                    occurred_at=datetime.now(timezone.utc),
                ),
                AgentEvent(
                    seq_no=2,
                    event_type="agent.run.failed",
                    payload={"error": "first failure"},
                    occurred_at=datetime.now(timezone.utc),
                ),
            ],
            artifacts=[AgentArtifact(artifact_type="debug", path="sandbox://debug-1")],
            sandbox_path="/tmp/run-1",
        )

        service.persist_result(session, result)
        session.commit()
        result.error_message = "second failure"
        result.events = result.events[:1]
        result.artifacts = [AgentArtifact(artifact_type="debug", path="sandbox://debug-2")]
        service.persist_result(session, result)
        session.commit()

        assert session.get(AgentRunRecord, "run-1").error_message == "second failure"
        assert session.query(AgentEventRecord).count() == 1
        assert session.query(AgentArtifactRecord).count() == 1
        assert session.query(AgentArtifactRecord).one().path == "sandbox://debug-2"


def test_agent_harness_persistence_can_preserve_live_events_when_persisting_result() -> None:
    manager = DatabaseManager("sqlite+pysqlite:///:memory:")
    manager.create_schema()

    with manager.session() as session:
        service = AgentHarnessPersistenceService()
        service.start_run(
            session,
            agent_run_id="run-1",
            agent_key="psop.builder",
            related_generation_id="generation-1",
        )
        session.add(
            AgentEventRecord(
                agent_run_id="run-1",
                seq_no=1,
                event_type="agent.run.started",
                payload={"agent_key": "psop.builder"},
                occurred_at=datetime.now(timezone.utc),
            )
        )
        session.commit()

        result = AgentResult(
            agent_run_id="run-1",
            agent_key="psop.builder",
            status="succeeded",
            final_output="ok",
            events=[
                AgentEvent(
                    seq_no=1,
                    event_type="agent.run.started",
                    payload={"agent_key": "psop.builder"},
                    occurred_at=datetime.now(timezone.utc),
                )
            ],
            artifacts=[AgentArtifact(artifact_type="skill_draft_candidate", path="sandbox://outputs/builder-result.json")],
            sandbox_path="/tmp/run-1",
        )

        service.persist_result(session, result, replace_events=False)
        session.commit()

        assert session.get(AgentRunRecord, "run-1").status == "succeeded"
        assert session.query(AgentEventRecord).count() == 1
        assert session.query(AgentArtifactRecord).count() == 1
