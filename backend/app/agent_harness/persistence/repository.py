from __future__ import annotations

from sqlalchemy.orm import Session

from app.agent_harness.persistence.models import AgentArtifactRecord, AgentEventRecord, AgentRunRecord
from app.agent_harness.schemas import AgentArtifact, AgentEvent


class AgentHarnessRepository:
    def create_run(
        self,
        session: Session,
        *,
        agent_run_id: str,
        agent_key: str,
        agent_version: str,
        related_skill_definition_id: str = "",
        related_generation_id: str = "",
        related_job_id: str = "",
        input_summary: dict | None = None,
        sandbox_path: str = "",
        model_info: dict | None = None,
    ) -> AgentRunRecord:
        record = AgentRunRecord(
            id=agent_run_id,
            agent_key=agent_key,
            agent_version=agent_version,
            status="running",
            related_skill_definition_id=related_skill_definition_id,
            related_generation_id=related_generation_id,
            related_job_id=related_job_id,
            input_summary=input_summary or {},
            sandbox_path=sandbox_path,
            model_info=model_info or {},
        )
        session.add(record)
        return record

    def finish_run(self, session: Session, *, agent_run_id: str, status: str, error_message: str = "") -> None:
        record = session.get(AgentRunRecord, agent_run_id)
        if not record:
            return
        record.status = status
        record.error_message = error_message

    def add_events(self, session: Session, *, agent_run_id: str, events: list[AgentEvent]) -> None:
        for event in events:
            session.add(
                AgentEventRecord(
                    agent_run_id=agent_run_id,
                    seq_no=event.seq_no,
                    event_type=event.event_type,
                    payload=event.payload,
                    occurred_at=event.occurred_at,
                )
            )

    def add_artifacts(self, session: Session, *, agent_run_id: str, artifacts: list[AgentArtifact]) -> None:
        for artifact in artifacts:
            session.add(
                AgentArtifactRecord(
                    agent_run_id=agent_run_id,
                    artifact_type=artifact.artifact_type,
                    path=artifact.path or "",
                    content_hash=str(artifact.provenance.get("content_hash") or ""),
                    provenance=artifact.provenance or {},
                    status="draft",
                )
            )
