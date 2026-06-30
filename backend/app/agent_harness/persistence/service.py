from __future__ import annotations

from sqlalchemy.orm import Session

from app.agent_harness.persistence.models import AgentRunRecord
from app.agent_harness.persistence.repository import AgentHarnessRepository
from app.agent_harness.schemas import AgentResult


class AgentHarnessPersistenceService:
    def __init__(self, repository: AgentHarnessRepository | None = None) -> None:
        self.repository = repository or AgentHarnessRepository()

    def persist_result(
        self,
        session: Session,
        result: AgentResult,
        *,
        agent_version: str = "",
        related_skill_definition_id: str = "",
        related_generation_id: str = "",
        related_job_id: str = "",
        input_summary: dict | None = None,
        model_info: dict | None = None,
    ) -> None:
        record = session.get(AgentRunRecord, result.agent_run_id)
        if record is None:
            record = self.repository.create_run(
                session,
                agent_run_id=result.agent_run_id,
                agent_key=result.agent_key,
                agent_version=agent_version,
                related_skill_definition_id=related_skill_definition_id,
                related_generation_id=related_generation_id,
                related_job_id=related_job_id,
                input_summary=input_summary or {},
                sandbox_path=result.sandbox_path or "",
                model_info=model_info or {},
            )
        record.status = result.status
        record.agent_version = agent_version
        record.related_skill_definition_id = related_skill_definition_id
        record.related_generation_id = related_generation_id
        record.related_job_id = related_job_id
        record.input_summary = input_summary or {}
        record.sandbox_path = result.sandbox_path or ""
        record.model_info = model_info or {}
        record.error_message = result.error_message
        self.repository.add_events(session, agent_run_id=result.agent_run_id, events=result.events)
        self.repository.add_artifacts(session, agent_run_id=result.agent_run_id, artifacts=result.artifacts)
