from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.compiler.models import ArtifactObject, EgCompileArtifact
from app.domain.runtime.models import (
    RunCapabilityBinding,
    Run,
    SessionTokenSnapshot,
    SkillInvocation,
    TerminalEvent,
    TerminalEventPart,
    TerminalSession,
    TraceEvent,
)
from app.domain.skills.models import SkillDefinition, SkillVersion


class RuntimeRepository:
    """Database access for invocation, Runtime Kernel and replay objects."""

    def get_skill_definition_by_key(self, session: Session, skill_key: str) -> SkillDefinition | None:
        return session.scalar(select(SkillDefinition).where(SkillDefinition.key == skill_key))

    def get_skill_version(self, session: Session, version_id: str | None) -> SkillVersion | None:
        if not version_id:
            return None
        return session.get(SkillVersion, version_id)

    def get_artifact(self, session: Session, artifact_id: str) -> EgCompileArtifact | None:
        return session.get(EgCompileArtifact, artifact_id)

    def get_latest_ready_artifact(self, session: Session, skill_version_id: str) -> EgCompileArtifact | None:
        return session.scalar(
            select(EgCompileArtifact)
            .where(
                EgCompileArtifact.skill_version_id == skill_version_id,
                EgCompileArtifact.status == "ready",
            )
            .order_by(EgCompileArtifact.created_at.desc())
        )

    def get_artifact_object(self, session: Session, object_id: str) -> ArtifactObject | None:
        return session.get(ArtifactObject, object_id)

    def get_invocation(self, session: Session, invocation_id: str) -> SkillInvocation | None:
        return session.get(SkillInvocation, invocation_id)

    def list_invocations(
        self,
        session: Session,
        *,
        skill_key: str | None = None,
        status: str | None = None,
    ) -> list[SkillInvocation]:
        query = select(SkillInvocation).order_by(SkillInvocation.created_at.desc())
        if status:
            query = query.where(SkillInvocation.status == status)
        if skill_key:
            query = query.join(SkillDefinition, SkillDefinition.id == SkillInvocation.skill_definition_id).where(
                SkillDefinition.key == skill_key
            )
        return list(session.scalars(query).all())

    def get_run(self, session: Session, run_id: str) -> Run | None:
        return session.get(Run, run_id)

    def get_run_for_update(self, session: Session, run_id: str) -> Run | None:
        return session.scalar(
            select(Run)
            .where(Run.id == run_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    def get_run_for_invocation(self, session: Session, invocation_id: str) -> Run | None:
        return session.scalar(select(Run).where(Run.invocation_id == invocation_id))

    def get_terminal_session_for_run(self, session: Session, run_id: str) -> TerminalSession | None:
        return session.scalar(select(TerminalSession).where(TerminalSession.run_id == run_id))

    def list_terminal_events(
        self,
        session: Session,
        run_id: str,
        *,
        from_seq: int | None = None,
        to_seq: int | None = None,
    ) -> list[TerminalEvent]:
        query = select(TerminalEvent).where(TerminalEvent.run_id == run_id)
        if from_seq is not None:
            query = query.where(TerminalEvent.seq_no >= from_seq)
        if to_seq is not None:
            query = query.where(TerminalEvent.seq_no <= to_seq)
        query = query.order_by(TerminalEvent.seq_no.asc())
        return list(session.scalars(query).all())

    def get_terminal_event(self, session: Session, event_id: str) -> TerminalEvent | None:
        return session.get(TerminalEvent, event_id)

    def list_terminal_event_parts(self, session: Session, event_id: str) -> list[TerminalEventPart]:
        return list(
            session.scalars(
                select(TerminalEventPart)
                .where(TerminalEventPart.terminal_event_id == event_id)
                .order_by(TerminalEventPart.order_index.asc())
            ).all()
        )

    def get_terminal_event_part(self, session: Session, part_id: str) -> TerminalEventPart | None:
        return session.get(TerminalEventPart, part_id)

    def get_terminal_event_part_by_public_id(
        self,
        session: Session,
        *,
        terminal_event_id: str,
        part_id: str,
    ) -> TerminalEventPart | None:
        return session.scalar(
            select(TerminalEventPart).where(
                TerminalEventPart.terminal_event_id == terminal_event_id,
                TerminalEventPart.part_id == part_id,
            )
        )

    def get_terminal_event_by_external_id(
        self,
        session: Session,
        *,
        run_id: str,
        external_event_id: str,
    ) -> TerminalEvent | None:
        return session.scalar(
            select(TerminalEvent).where(
                TerminalEvent.run_id == run_id,
                TerminalEvent.external_event_id == external_event_id,
            )
        )

    def get_run_capability_binding(self, session: Session, binding_id: str) -> RunCapabilityBinding | None:
        return session.get(RunCapabilityBinding, binding_id)

    def get_run_binding_by_requirement(
        self,
        session: Session,
        *,
        run_id: str,
        requirement_key: str,
    ) -> RunCapabilityBinding | None:
        return session.scalar(
            select(RunCapabilityBinding).where(
                RunCapabilityBinding.run_id == run_id,
                RunCapabilityBinding.requirement_key == requirement_key,
            )
        )

    def list_run_bindings(self, session: Session, run_id: str) -> list[RunCapabilityBinding]:
        return list(
            session.scalars(
                select(RunCapabilityBinding)
                .where(RunCapabilityBinding.run_id == run_id)
                .order_by(RunCapabilityBinding.created_at.asc(), RunCapabilityBinding.requirement_key.asc())
            ).all()
        )

    def list_runs(
        self,
        session: Session,
        *,
        status: str | None = None,
        skill_id: str | None = None,
    ) -> list[Run]:
        query = select(Run).order_by(Run.created_at.desc())
        if status:
            query = query.where(Run.status == status)
        if skill_id:
            query = query.where(Run.skill_definition_id == skill_id)
        return list(session.scalars(query).all())

    def list_snapshots(self, session: Session, run_id: str) -> list[SessionTokenSnapshot]:
        return list(
            session.scalars(
                select(SessionTokenSnapshot)
                .where(SessionTokenSnapshot.run_id == run_id)
                .order_by(SessionTokenSnapshot.seq_no.asc())
            ).all()
        )

    def list_trace_events(self, session: Session, run_id: str, event_type: str | None = None) -> list[TraceEvent]:
        query = select(TraceEvent).where(TraceEvent.run_id == run_id)
        if event_type:
            query = query.where(TraceEvent.event_type == event_type)
        query = query.order_by(TraceEvent.seq_no.asc())
        return list(session.scalars(query).all())

    def get_trace_event_by_seq(self, session: Session, run_id: str, seq_no: int) -> TraceEvent | None:
        return session.scalar(
            select(TraceEvent).where(TraceEvent.run_id == run_id, TraceEvent.seq_no == seq_no).limit(1)
        )

    def get_terminal_event_by_seq(self, session: Session, run_id: str, seq_no: int) -> TerminalEvent | None:
        return session.scalar(
            select(TerminalEvent).where(TerminalEvent.run_id == run_id, TerminalEvent.seq_no == seq_no).limit(1)
        )
