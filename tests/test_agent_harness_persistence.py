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
