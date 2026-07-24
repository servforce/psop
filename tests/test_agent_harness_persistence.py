from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from sqlalchemy import inspect, text

from app.agent_harness.events import AgentEventWriter
from app.agent_harness.persistence.models import AgentArtifactRecord, AgentEventRecord, AgentRunRecord
from app.agent_harness.persistence.query_service import AgentRunQueryService
from app.agent_harness.persistence.service import AgentHarnessPersistenceService
from app.agent_harness.schemas import AgentArtifact, AgentEvent, AgentResult
from app.agent_harness.service import AgentHarnessService
from app.infra.database import DatabaseManager


def test_create_schema_reconciles_legacy_agent_run_runtime_run_column() -> None:
    manager = DatabaseManager("sqlite+pysqlite:///:memory:")
    with manager.engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE agent_run (
                    id VARCHAR(36) NOT NULL,
                    agent_key VARCHAR(160) NOT NULL,
                    agent_version VARCHAR(64) NOT NULL,
                    status VARCHAR(32) NOT NULL,
                    related_skill_definition_id VARCHAR(36) NOT NULL,
                    related_generation_id VARCHAR(36) NOT NULL,
                    related_job_id VARCHAR(36) NOT NULL,
                    input_summary JSON NOT NULL,
                    sandbox_path TEXT NOT NULL,
                    model_info JSON NOT NULL,
                    error_message TEXT NOT NULL,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL,
                    PRIMARY KEY (id)
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO agent_run (
                    id,
                    agent_key,
                    agent_version,
                    status,
                    related_skill_definition_id,
                    related_generation_id,
                    related_job_id,
                    input_summary,
                    sandbox_path,
                    model_info,
                    error_message,
                    created_at,
                    updated_at
                )
                VALUES (
                    'legacy-run',
                    'psop.compiler',
                    '',
                    'running',
                    '',
                    '',
                    'job-1',
                    '{}',
                    '/tmp/legacy-run',
                    '{}',
                    '',
                    '2026-01-01 00:00:00',
                    '2026-01-01 00:00:00'
                )
                """
            )
        )

    manager.create_schema()

    inspector = inspect(manager.engine)
    columns = {column["name"] for column in inspector.get_columns("agent_run")}
    indexes = {index["name"] for index in inspector.get_indexes("agent_run")}
    assert "related_runtime_run_id" in columns
    assert "idx_agent_run_related_runtime_run" in indexes

    with manager.session() as session:
        record = session.get(AgentRunRecord, "legacy-run")
        assert record is not None
        assert record.related_runtime_run_id == ""


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

        service.persist_result(session, result, related_runtime_run_id="runtime-run-1")
        session.commit()

        record = session.get(AgentRunRecord, "run-1")
        assert record.status == "succeeded"
        assert record.related_runtime_run_id == "runtime-run-1"
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
            related_runtime_run_id="runtime-run-1",
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

        service.persist_result(session, result, related_runtime_run_id="runtime-run-1", replace_events=False)
        session.commit()

        record = session.get(AgentRunRecord, "run-1")
        assert record.status == "succeeded"
        assert record.related_runtime_run_id == "runtime-run-1"
        assert session.query(AgentEventRecord).count() == 1
        assert session.query(AgentArtifactRecord).count() == 1


def test_agent_event_writer_serializes_parallel_live_event_persistence(tmp_path) -> None:
    manager = DatabaseManager("sqlite+pysqlite:///:memory:")
    manager.create_schema()

    with manager.session() as session:
        AgentHarnessPersistenceService().start_run(
            session,
            agent_run_id="parallel-run",
            agent_key="psop.builder",
        )
        session.commit()
        writer = AgentEventWriter(
            tmp_path / "events.jsonl",
            on_event=AgentHarnessService._live_event_sink(session, "parallel-run"),
        )

        with ThreadPoolExecutor(max_workers=3) as executor:
            list(executor.map(lambda index: writer.record("agent.tool.completed", {"index": index}), range(30)))

        records = (
            session.query(AgentEventRecord)
            .filter(AgentEventRecord.agent_run_id == "parallel-run")
            .order_by(AgentEventRecord.seq_no.asc())
            .all()
        )

    file_events = [json.loads(line) for line in (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [event.seq_no for event in writer.events] == list(range(1, 31))
    assert [record.seq_no for record in records] == list(range(1, 31))
    assert [event["seq_no"] for event in file_events] == list(range(1, 31))


def test_agent_run_timeline_preserves_all_validation_diagnostics() -> None:
    manager = DatabaseManager("sqlite+pysqlite:///:memory:")
    manager.create_schema()
    diagnostics = [
        {"path": f"safety_constraints.{index}", "code": "missing_evidence_coverage"}
        for index in range(12)
    ]

    with manager.session() as session:
        AgentHarnessPersistenceService().start_run(
            session,
            agent_run_id="run-diagnostics",
            agent_key="psop.builder",
        )
        session.flush()
        session.add(
            AgentEventRecord(
                agent_run_id="run-diagnostics",
                seq_no=1,
                event_type="agent.validation.failed",
                payload={"attempt": 1, "validation_stage": "schema_validation", "diagnostics": diagnostics},
                occurred_at=datetime.now(timezone.utc),
            )
        )
        session.commit()

        timeline = AgentRunQueryService().get_run_timeline(session, "run-diagnostics")

    assert timeline.validation_diagnostic_count == 12
    assert timeline.validation_diagnostics == diagnostics
    validation_step = next(step for step in timeline.steps if step.event_type == "agent.validation.failed")
    assert validation_step.metadata["diagnostic_count"] == 12
    assert validation_step.metadata["diagnostics"] == diagnostics
