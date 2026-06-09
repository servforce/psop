from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agents.models import AgentEvent, AgentModelCall, AgentRun, AgentToolAuthorization, AgentToolCall
from app.agents.schemas import (
    AgentEventResponse,
    AgentModelCallResponse,
    AgentToolAuthorizationResponse,
    AgentToolCallResponse,
)
from app.agents.tool_authorization_context import tool_authorization_business_context
from app.core.config import Settings
from app.evaluations.models import RunEvaluation, RunEvaluationFinding
from app.governance.models import PsopImprovementExperiment, PsopImprovementProposal
from app.pskills.models import PSkillDefinition, PSkillVersion, now_utc
from app.runtime.models import Run, RunEvent, RunEventPart, RunTrace
from app.runtime.schemas import RunEventPartResponse, RunEventResponse, RunTraceResponse
from app.skills.models import SkillActivation
from app.skills.schemas import SkillActivationResponse
from app.testing.models import PSkillPublishGate
from app.observability.schemas import (
    AgentDashboardMetrics,
    AgentObservabilityMetrics,
    DashboardMetricsResponse,
    EvaluationDashboardMetrics,
    EvaluationObservabilityMetrics,
    GlobalObservabilityMetrics,
    GovernanceObservabilityMetrics,
    GovernanceDashboardMetrics,
    ObservabilityMetricsResponse,
    OpenTelemetryStatus,
    PSkillDashboardMetrics,
    RuntimeObservabilityMetrics,
    RuntimeDashboardMetrics,
)


AGENT_KEYS = [
    "pskill.builder",
    "pskill.compiler",
    "pskill.tester",
    "pskill.runner",
    "pskill.evaluator",
    "psop.governance",
]

OPEN_PROPOSAL_STATUSES = {"draft", "reviewing", "testing", "approved", "canary"}
HIGH_SEVERITY_VALUES = {"high", "critical", "blocker"}
UNRESOLVED_FINDING_STATUSES = {"open", "accepted", "converted_to_proposal"}
TOOL_FAILURE_STATUSES = {"failed", "denied"}


class ObservabilityService:
    """Read-only global metrics for PSOP health dashboards."""

    def get_dashboard_metrics(
        self,
        session: Session,
        *,
        settings: Settings,
        window_hours: int = 24,
    ) -> DashboardMetricsResponse:
        resolved_window_hours = max(1, min(24 * 30, int(window_hours or 24)))
        generated_at = now_utc()
        since = generated_at - timedelta(hours=resolved_window_hours)
        return DashboardMetricsResponse(
            generated_at=generated_at,
            window_hours=resolved_window_hours,
            pskills=self._pskill_metrics(session, since=since),
            runtime=self._runtime_metrics(session, since=since),
            evaluations=self._evaluation_metrics(session, since=since),
            governance=self._governance_metrics(session),
            agents=self._agent_metrics(session, since=since),
            observability=self._global_metrics(session, settings=settings, since=since),
        )

    def get_global_metrics(
        self,
        session: Session,
        *,
        settings: Settings,
        window_hours: int = 24,
        otel_configured: bool = False,
    ) -> ObservabilityMetricsResponse:
        resolved_window_hours = max(1, min(24 * 30, int(window_hours or 24)))
        generated_at = now_utc()
        since = generated_at - timedelta(hours=resolved_window_hours)
        return ObservabilityMetricsResponse(
            generated_at=generated_at,
            since=since,
            window_hours=resolved_window_hours,
            runtime=self._runtime_observability_metrics(session, since=since),
            agents=self._agent_observability_metrics(session, since=since),
            evaluations=self._evaluation_observability_metrics(session, since=since),
            governance=self._governance_observability_metrics(session, since=since),
            open_telemetry=self._open_telemetry_status(settings=settings, configured=otel_configured),
        )

    def list_run_traces(
        self,
        session: Session,
        *,
        window_hours: int = 24,
        run_id: str | None = None,
        event_type: str | None = None,
        phase: str | None = None,
        agent_run_id: str | None = None,
        limit: int = 50,
    ) -> list[RunTraceResponse]:
        resolved_window_hours = max(1, min(24 * 30, int(window_hours or 24)))
        resolved_limit = max(1, min(200, int(limit or 50)))
        since = now_utc() - timedelta(hours=resolved_window_hours)
        conditions = [RunTrace.occurred_at >= since]
        if run_id:
            conditions.append(RunTrace.run_id == run_id)
        if event_type:
            conditions.append(RunTrace.event_type == event_type)
        if phase:
            conditions.append(RunTrace.phase == phase)
        if agent_run_id:
            conditions.append(RunTrace.agent_run_id == agent_run_id)
        traces = list(
            session.scalars(
                select(RunTrace)
                .where(*conditions)
                .order_by(RunTrace.occurred_at.desc(), RunTrace.seq_no.desc())
                .limit(resolved_limit)
            ).all()
        )
        return [self._build_run_trace_response(trace) for trace in traces]

    def list_run_events(
        self,
        session: Session,
        *,
        window_hours: int = 24,
        run_id: str | None = None,
        event_kind: str | None = None,
        direction: str | None = None,
        agent_run_id: str | None = None,
        limit: int = 50,
    ) -> list[RunEventResponse]:
        resolved_window_hours = max(1, min(24 * 30, int(window_hours or 24)))
        resolved_limit = max(1, min(200, int(limit or 50)))
        since = now_utc() - timedelta(hours=resolved_window_hours)
        conditions = [RunEvent.occurred_at >= since]
        if run_id:
            conditions.append(RunEvent.run_id == run_id)
        if event_kind:
            conditions.append(RunEvent.event_kind == event_kind)
        if direction:
            conditions.append(RunEvent.direction == direction)
        if agent_run_id:
            conditions.append(RunEvent.agent_run_id == agent_run_id)
        events = list(
            session.scalars(
                select(RunEvent)
                .where(*conditions)
                .order_by(RunEvent.occurred_at.desc(), RunEvent.seq_no.desc())
                .limit(resolved_limit)
            ).all()
        )
        return [self._build_run_event_response(session, event) for event in events]

    def list_agent_events(
        self,
        session: Session,
        *,
        window_hours: int = 24,
        agent_run_id: str | None = None,
        agent_key: str | None = None,
        run_id: str | None = None,
        event_type: str | None = None,
        phase: str | None = None,
        limit: int = 50,
    ) -> list[AgentEventResponse]:
        resolved_window_hours = max(1, min(24 * 30, int(window_hours or 24)))
        resolved_limit = max(1, min(200, int(limit or 50)))
        since = now_utc() - timedelta(hours=resolved_window_hours)
        conditions = [AgentEvent.occurred_at >= since]
        if agent_run_id:
            conditions.append(AgentEvent.agent_run_id == agent_run_id)
        if event_type:
            conditions.append(AgentEvent.event_type == event_type)
        if phase:
            conditions.append(AgentEvent.phase == phase)
        if agent_key:
            conditions.append(AgentRun.agent_key == agent_key)
        if run_id:
            conditions.append(AgentRun.run_id == run_id)
        events = list(
            session.scalars(
                select(AgentEvent)
                .join(AgentRun, AgentRun.id == AgentEvent.agent_run_id)
                .where(*conditions)
                .order_by(AgentEvent.occurred_at.desc(), AgentEvent.seq_no.desc())
                .limit(resolved_limit)
            ).all()
        )
        return [self._build_agent_event_response(event) for event in events]

    def list_tool_calls(
        self,
        session: Session,
        *,
        window_hours: int = 24,
        agent_run_id: str | None = None,
        agent_key: str | None = None,
        run_id: str | None = None,
        tool_name: str | None = None,
        status: str | None = None,
        side_effect_level: str | None = None,
        limit: int = 50,
    ) -> list[AgentToolCallResponse]:
        resolved_window_hours = max(1, min(24 * 30, int(window_hours or 24)))
        resolved_limit = max(1, min(200, int(limit or 50)))
        since = now_utc() - timedelta(hours=resolved_window_hours)
        conditions = [AgentToolCall.created_at >= since]
        if agent_run_id:
            conditions.append(AgentToolCall.agent_run_id == agent_run_id)
        if tool_name:
            conditions.append(AgentToolCall.tool_name == tool_name)
        if status:
            conditions.append(AgentToolCall.status == status)
        if side_effect_level:
            conditions.append(AgentToolCall.side_effect_level == side_effect_level)
        if agent_key:
            conditions.append(AgentRun.agent_key == agent_key)
        if run_id:
            conditions.append(AgentRun.run_id == run_id)
        calls = list(
            session.scalars(
                select(AgentToolCall)
                .join(AgentRun, AgentRun.id == AgentToolCall.agent_run_id)
                .where(*conditions)
                .order_by(AgentToolCall.updated_at.desc(), AgentToolCall.created_at.desc())
                .limit(resolved_limit)
            ).all()
        )
        return [self._build_tool_call_response(call) for call in calls]

    def list_model_calls(
        self,
        session: Session,
        *,
        window_hours: int = 24,
        agent_run_id: str | None = None,
        agent_key: str | None = None,
        run_id: str | None = None,
        provider: str | None = None,
        status: str | None = None,
        route_key: str | None = None,
        limit: int = 50,
    ) -> list[AgentModelCallResponse]:
        resolved_window_hours = max(1, min(24 * 30, int(window_hours or 24)))
        resolved_limit = max(1, min(200, int(limit or 50)))
        since = now_utc() - timedelta(hours=resolved_window_hours)
        conditions = [AgentModelCall.created_at >= since]
        if agent_run_id:
            conditions.append(AgentModelCall.agent_run_id == agent_run_id)
        if provider:
            conditions.append(AgentModelCall.provider == provider)
        if status:
            conditions.append(AgentModelCall.status == status)
        if route_key:
            conditions.append(AgentModelCall.route_key == route_key)
        if agent_key:
            conditions.append(AgentRun.agent_key == agent_key)
        if run_id:
            conditions.append(AgentRun.run_id == run_id)
        calls = list(
            session.scalars(
                select(AgentModelCall)
                .join(AgentRun, AgentRun.id == AgentModelCall.agent_run_id)
                .where(*conditions)
                .order_by(AgentModelCall.created_at.desc())
                .limit(resolved_limit)
            ).all()
        )
        return [self._build_model_call_response(call) for call in calls]

    def list_skill_activations(
        self,
        session: Session,
        *,
        window_hours: int = 24,
        agent_run_id: str | None = None,
        agent_key: str | None = None,
        run_id: str | None = None,
        package_id: str | None = None,
        version_id: str | None = None,
        limit: int = 50,
    ) -> list[SkillActivationResponse]:
        resolved_window_hours = max(1, min(24 * 30, int(window_hours or 24)))
        resolved_limit = max(1, min(200, int(limit or 50)))
        since = now_utc() - timedelta(hours=resolved_window_hours)
        conditions = [SkillActivation.created_at >= since]
        if agent_run_id:
            conditions.append(SkillActivation.agent_run_id == agent_run_id)
        if package_id:
            conditions.append(SkillActivation.package_id == package_id)
        if version_id:
            conditions.append(SkillActivation.version_id == version_id)
        if agent_key:
            conditions.append(AgentRun.agent_key == agent_key)
        if run_id:
            conditions.append(AgentRun.run_id == run_id)
        activations = list(
            session.scalars(
                select(SkillActivation)
                .join(AgentRun, AgentRun.id == SkillActivation.agent_run_id)
                .where(*conditions)
                .order_by(SkillActivation.created_at.desc())
                .limit(resolved_limit)
            ).all()
        )
        return [self._build_skill_activation_response(activation) for activation in activations]

    def list_tool_authorizations(
        self,
        session: Session,
        *,
        window_hours: int = 24,
        agent_run_id: str | None = None,
        agent_key: str | None = None,
        run_id: str | None = None,
        tool_name: str | None = None,
        status: str | None = None,
        risk_level: str | None = None,
        side_effect_level: str | None = None,
        proposal_id: str | None = None,
        source_run_id: str | None = None,
        source_evaluation_id: str | None = None,
        source_finding_id: str | None = None,
        limit: int = 50,
    ) -> list[AgentToolAuthorizationResponse]:
        resolved_window_hours = max(1, min(24 * 30, int(window_hours or 24)))
        resolved_limit = max(1, min(200, int(limit or 50)))
        since = now_utc() - timedelta(hours=resolved_window_hours)
        has_context_filters = any(
            self._normalize_filter_value(value)
            for value in [proposal_id, source_run_id, source_evaluation_id, source_finding_id]
        )
        conditions = [AgentToolAuthorization.created_at >= since]
        if agent_run_id:
            conditions.append(AgentToolAuthorization.agent_run_id == agent_run_id)
        if run_id:
            conditions.append(AgentToolAuthorization.run_id == run_id)
        if tool_name:
            conditions.append(AgentToolAuthorization.tool_name == tool_name)
        if status:
            conditions.append(AgentToolAuthorization.status == status)
        if risk_level:
            conditions.append(AgentToolAuthorization.risk_level == risk_level)
        if side_effect_level:
            conditions.append(AgentToolAuthorization.side_effect_level == side_effect_level)
        if agent_key:
            conditions.append(AgentRun.agent_key == agent_key)
        query = (
            select(AgentToolAuthorization)
            .join(AgentRun, AgentRun.id == AgentToolAuthorization.agent_run_id)
            .where(*conditions)
            .order_by(AgentToolAuthorization.created_at.desc())
        )
        if not has_context_filters:
            query = query.limit(resolved_limit)
        authorizations = list(session.scalars(query).all())
        if has_context_filters:
            authorizations = [
                authorization
                for authorization in authorizations
                if self._tool_authorization_matches_context_filters(
                    authorization,
                    proposal_id=proposal_id,
                    source_run_id=source_run_id,
                    source_evaluation_id=source_evaluation_id,
                    source_finding_id=source_finding_id,
                )
            ][:resolved_limit]
        return [self._build_tool_authorization_response(authorization) for authorization in authorizations]

    def _pskill_metrics(self, session: Session, *, since: datetime) -> PSkillDashboardMetrics:
        status_counts = self._count_by(session, PSkillDefinition.status)
        total_count = self._count(session, PSkillDefinition)
        draft_count = self._count(session, PSkillVersion, PSkillVersion.status == "draft")
        testing_count = self._count(session, PSkillPublishGate, PSkillPublishGate.status.in_(["pending", "running"]))
        published_count = self._count(session, PSkillDefinition, PSkillDefinition.latest_published_version_id.is_not(None))
        gate_total = self._count(session, PSkillPublishGate, PSkillPublishGate.created_at >= since)
        gate_passed = self._count(
            session,
            PSkillPublishGate,
            PSkillPublishGate.created_at >= since,
            PSkillPublishGate.status.in_(["passed", "succeeded", "approved"]),
        )
        return PSkillDashboardMetrics(
            total_count=total_count,
            draft_count=draft_count,
            testing_count=testing_count,
            published_count=published_count,
            publish_gate_total=gate_total,
            publish_gate_passed=gate_passed,
            publish_gate_pass_rate=self._rate(gate_passed, gate_total),
            status_counts=status_counts,
        )

    def _runtime_metrics(self, session: Session, *, since: datetime) -> RuntimeDashboardMetrics:
        runs = list(session.scalars(select(Run).where(Run.created_at >= since)).all())
        status_counts = self._status_counts_from_items(runs)
        run_count = len(runs)
        succeeded_count = status_counts.get("succeeded", 0)
        failed_count = status_counts.get("failed", 0)
        aborted_count = status_counts.get("aborted", 0)
        cancelled_count = status_counts.get("cancelled", 0) + status_counts.get("canceled", 0)
        return RuntimeDashboardMetrics(
            recent_run_count=run_count,
            succeeded_count=succeeded_count,
            failed_count=failed_count,
            aborted_count=aborted_count,
            cancelled_count=cancelled_count,
            success_rate=self._rate(succeeded_count, run_count),
            average_duration_ms=self._average_duration_ms(runs),
            status_counts=status_counts,
        )

    def _evaluation_metrics(self, session: Session, *, since: datetime) -> EvaluationDashboardMetrics:
        evaluations = list(session.scalars(select(RunEvaluation).where(RunEvaluation.created_at >= since)).all())
        findings = list(session.scalars(select(RunEvaluationFinding).where(RunEvaluationFinding.created_at >= since)).all())
        scores = [evaluation.quality_score for evaluation in evaluations]
        outcome_counts: dict[str, int] = {}
        for evaluation in evaluations:
            outcome_counts[evaluation.overall_outcome] = outcome_counts.get(evaluation.overall_outcome, 0) + 1
        finding_status_counts = self._status_counts_from_items(findings)
        return EvaluationDashboardMetrics(
            recent_evaluation_count=len(evaluations),
            average_quality_score=round(sum(scores) / len(scores), 2) if scores else 0.0,
            high_severity_finding_count=sum(1 for finding in findings if finding.severity in HIGH_SEVERITY_VALUES),
            unresolved_finding_count=sum(1 for finding in findings if finding.status in UNRESOLVED_FINDING_STATUSES),
            outcome_counts=outcome_counts,
            finding_status_counts=finding_status_counts,
        )

    def _governance_metrics(self, session: Session) -> GovernanceDashboardMetrics:
        status_counts = self._count_by(session, PsopImprovementProposal.status)
        return GovernanceDashboardMetrics(
            open_proposal_count=sum(status_counts.get(status, 0) for status in OPEN_PROPOSAL_STATUSES),
            testing_proposal_count=status_counts.get("testing", 0),
            canary_proposal_count=status_counts.get("canary", 0),
            activated_proposal_count=status_counts.get("activated", 0),
            rollback_proposal_count=status_counts.get("rolled_back", 0),
            experiment_count=self._count(session, PsopImprovementExperiment),
            status_counts=status_counts,
        )

    def _agent_metrics(self, session: Session, *, since: datetime) -> list[AgentDashboardMetrics]:
        result: list[AgentDashboardMetrics] = []
        for agent_key in AGENT_KEYS:
            runs = list(
                session.scalars(
                    select(AgentRun).where(
                        AgentRun.agent_key == agent_key,
                        AgentRun.created_at >= since,
                    )
                ).all()
            )
            status_counts = self._status_counts_from_items(runs)
            run_ids = [run.id for run in runs]
            model_calls = []
            tool_calls = []
            if run_ids:
                model_calls = list(
                    session.scalars(select(AgentModelCall).where(AgentModelCall.agent_run_id.in_(run_ids))).all()
                )
                tool_calls = list(
                    session.scalars(select(AgentToolCall).where(AgentToolCall.agent_run_id.in_(run_ids))).all()
                )
            failed_model_call_count = sum(1 for call in model_calls if call.status == "failed")
            failed_tool_call_count = sum(1 for call in tool_calls if call.status in TOOL_FAILURE_STATUSES)
            result.append(
                AgentDashboardMetrics(
                    agent_key=agent_key,
                    recent_run_count=len(runs),
                    succeeded_count=status_counts.get("succeeded", 0),
                    failed_count=status_counts.get("failed", 0),
                    waiting_tool_authorization_count=status_counts.get("waiting_tool_authorization", 0),
                    success_rate=self._rate(status_counts.get("succeeded", 0), len(runs)),
                    average_duration_ms=self._average_duration_ms(runs),
                    model_call_count=len(model_calls),
                    failed_model_call_count=failed_model_call_count,
                    model_failure_rate=self._rate(failed_model_call_count, len(model_calls)),
                    tool_call_count=len(tool_calls),
                    failed_tool_call_count=failed_tool_call_count,
                    tool_failure_rate=self._rate(failed_tool_call_count, len(tool_calls)),
                )
            )
        return result

    def _global_metrics(
        self,
        session: Session,
        *,
        settings: Settings,
        since: datetime,
    ) -> GlobalObservabilityMetrics:
        return GlobalObservabilityMetrics(
            run_trace_count=self._count(session, RunTrace, RunTrace.occurred_at >= since),
            agent_event_count=self._count(session, AgentEvent, AgentEvent.occurred_at >= since),
            model_call_count=self._count(session, AgentModelCall, AgentModelCall.created_at >= since),
            tool_call_count=self._count(session, AgentToolCall, AgentToolCall.created_at >= since),
            pending_tool_authorization_count=self._count(
                session,
                AgentToolAuthorization,
                AgentToolAuthorization.status == "pending",
            ),
            otel_enabled=settings.otel_enabled,
            otel_service_name=settings.otel_service_name,
        )

    def _runtime_observability_metrics(self, session: Session, *, since: datetime) -> RuntimeObservabilityMetrics:
        return RuntimeObservabilityMetrics(
            run_count=self._count(session, Run, Run.created_at >= since),
            run_status_counts=self._count_by(session, Run.status, Run.created_at >= since),
            run_event_count=self._count(session, RunEvent, RunEvent.occurred_at >= since),
            run_event_kind_counts=self._count_by(session, RunEvent.event_kind, RunEvent.occurred_at >= since),
            run_trace_count=self._count(session, RunTrace, RunTrace.occurred_at >= since),
            run_trace_event_type_counts=self._count_by(session, RunTrace.event_type, RunTrace.occurred_at >= since),
            run_trace_phase_counts=self._count_by(session, RunTrace.phase, RunTrace.occurred_at >= since),
        )

    def _agent_observability_metrics(self, session: Session, *, since: datetime) -> AgentObservabilityMetrics:
        return AgentObservabilityMetrics(
            agent_run_count=self._count(session, AgentRun, AgentRun.created_at >= since),
            agent_run_status_counts=self._count_by(session, AgentRun.status, AgentRun.created_at >= since),
            agent_run_key_counts=self._count_by(session, AgentRun.agent_key, AgentRun.created_at >= since),
            agent_event_count=self._count(session, AgentEvent, AgentEvent.occurred_at >= since),
            agent_event_type_counts=self._count_by(session, AgentEvent.event_type, AgentEvent.occurred_at >= since),
            agent_event_phase_counts=self._count_by(session, AgentEvent.phase, AgentEvent.occurred_at >= since),
            model_call_count=self._count(session, AgentModelCall, AgentModelCall.created_at >= since),
            model_call_status_counts=self._count_by(session, AgentModelCall.status, AgentModelCall.created_at >= since),
            model_call_provider_counts=self._count_by(session, AgentModelCall.provider, AgentModelCall.created_at >= since),
            tool_call_count=self._count(session, AgentToolCall, AgentToolCall.created_at >= since),
            tool_call_status_counts=self._count_by(session, AgentToolCall.status, AgentToolCall.created_at >= since),
            tool_call_side_effect_counts=self._count_by(
                session,
                AgentToolCall.side_effect_level,
                AgentToolCall.created_at >= since,
            ),
            skill_activation_count=self._count(session, SkillActivation, SkillActivation.created_at >= since),
            skill_activation_package_counts=self._count_by(
                session,
                SkillActivation.package_id,
                SkillActivation.created_at >= since,
            ),
            tool_authorization_count=self._count(
                session,
                AgentToolAuthorization,
                AgentToolAuthorization.created_at >= since,
            ),
            tool_authorization_status_counts=self._count_by(
                session,
                AgentToolAuthorization.status,
                AgentToolAuthorization.created_at >= since,
            ),
            tool_authorization_risk_counts=self._count_by(
                session,
                AgentToolAuthorization.risk_level,
                AgentToolAuthorization.created_at >= since,
            ),
        )

    def _evaluation_observability_metrics(
        self,
        session: Session,
        *,
        since: datetime,
    ) -> EvaluationObservabilityMetrics:
        evaluations = list(session.scalars(select(RunEvaluation).where(RunEvaluation.created_at >= since)).all())
        findings = list(
            session.scalars(select(RunEvaluationFinding).where(RunEvaluationFinding.created_at >= since)).all()
        )
        scores = [evaluation.quality_score for evaluation in evaluations]
        return EvaluationObservabilityMetrics(
            evaluation_count=len(evaluations),
            average_quality_score=round(sum(scores) / len(scores), 2) if scores else 0.0,
            outcome_counts=self._count_by(
                session,
                RunEvaluation.overall_outcome,
                RunEvaluation.created_at >= since,
            ),
            finding_count=len(findings),
            high_severity_finding_count=sum(1 for finding in findings if finding.severity in HIGH_SEVERITY_VALUES),
            unresolved_finding_count=sum(1 for finding in findings if finding.status in UNRESOLVED_FINDING_STATUSES),
            finding_status_counts=self._count_by(
                session,
                RunEvaluationFinding.status,
                RunEvaluationFinding.created_at >= since,
            ),
            finding_category_counts=self._count_by(
                session,
                RunEvaluationFinding.category,
                RunEvaluationFinding.created_at >= since,
            ),
            finding_severity_counts=self._count_by(
                session,
                RunEvaluationFinding.severity,
                RunEvaluationFinding.created_at >= since,
            ),
        )

    def _governance_observability_metrics(
        self,
        session: Session,
        *,
        since: datetime,
    ) -> GovernanceObservabilityMetrics:
        proposals = list(
            session.scalars(select(PsopImprovementProposal).where(PsopImprovementProposal.updated_at >= since)).all()
        )
        status_counts = self._status_counts_from_items(proposals)
        source_finding_linked_count = sum(
            len(proposal.source_finding_ids or [])
            for proposal in proposals
            if isinstance(proposal.source_finding_ids, list)
        )
        return GovernanceObservabilityMetrics(
            proposal_count=len(proposals),
            open_proposal_count=sum(status_counts.get(status, 0) for status in OPEN_PROPOSAL_STATUSES),
            testing_proposal_count=status_counts.get("testing", 0),
            canary_proposal_count=status_counts.get("canary", 0),
            activated_proposal_count=status_counts.get("activated", 0),
            rollback_proposal_count=status_counts.get("rolled_back", 0),
            status_counts=status_counts,
            proposal_type_counts=self._count_by(
                session,
                PsopImprovementProposal.proposal_type,
                PsopImprovementProposal.updated_at >= since,
            ),
            source_run_linked_count=sum(1 for proposal in proposals if proposal.source_run_id),
            source_evaluation_linked_count=sum(1 for proposal in proposals if proposal.source_evaluation_id),
            source_finding_linked_count=source_finding_linked_count,
            experiment_count=self._count(
                session,
                PsopImprovementExperiment,
                PsopImprovementExperiment.created_at >= since,
            ),
            experiment_status_counts=self._count_by(
                session,
                PsopImprovementExperiment.status,
                PsopImprovementExperiment.created_at >= since,
            ),
            experiment_type_counts=self._count_by(
                session,
                PsopImprovementExperiment.experiment_type,
                PsopImprovementExperiment.created_at >= since,
            ),
        )

    @staticmethod
    def _build_run_trace_response(trace: RunTrace) -> RunTraceResponse:
        return RunTraceResponse(
            id=trace.id,
            run_id=trace.run_id,
            agent_run_id=trace.agent_run_id,
            seq_no=trace.seq_no,
            phase=trace.phase,
            event_type=trace.event_type,
            trace_id=trace.trace_id,
            span_id=trace.span_id,
            parent_span_id=trace.parent_span_id,
            payload=trace.payload,
            occurred_at=trace.occurred_at,
        )

    def _build_run_event_response(self, session: Session, event: RunEvent) -> RunEventResponse:
        return RunEventResponse(
            id=event.id,
            terminal_session_id=event.terminal_session_id,
            run_id=event.run_id,
            run_trace_id=event.run_trace_id,
            agent_run_id=event.agent_run_id,
            artifact_object_id=event.artifact_object_id,
            run_capability_binding_id=event.run_capability_binding_id,
            direction=event.direction,
            event_kind=event.event_kind,
            mime_type=event.mime_type,
            payload_inline=event.payload_inline,
            seq_no=event.seq_no,
            external_event_id=event.external_event_id,
            source_ref=event.source_ref,
            parts=[
                self._build_run_event_part_response(part)
                for part in session.scalars(
                    select(RunEventPart)
                    .where(RunEventPart.run_event_id == event.id)
                    .order_by(RunEventPart.order_index.asc())
                ).all()
            ],
            occurred_at=event.occurred_at,
            created_at=event.created_at,
        )

    @staticmethod
    def _build_run_event_part_response(part: RunEventPart) -> RunEventPartResponse:
        return RunEventPartResponse(
            id=part.id,
            run_event_id=part.run_event_id,
            run_id=part.run_id,
            artifact_object_id=part.artifact_object_id,
            part_id=part.part_id,
            order_index=part.order_index,
            kind=part.kind,
            mime_type=part.mime_type,
            text=part.text_inline,
            size_bytes=part.size_bytes,
            checksum=part.checksum,
            metadata=part.part_metadata,
            created_at=part.created_at,
        )

    @staticmethod
    def _build_agent_event_response(event: AgentEvent) -> AgentEventResponse:
        return AgentEventResponse(
            id=event.id,
            agent_run_id=event.agent_run_id,
            seq_no=event.seq_no,
            event_type=event.event_type,
            phase=event.phase,
            payload=event.payload,
            occurred_at=event.occurred_at,
        )

    @staticmethod
    def _build_tool_call_response(call: AgentToolCall) -> AgentToolCallResponse:
        return AgentToolCallResponse(
            id=call.id,
            agent_run_id=call.agent_run_id,
            tool_name=call.tool_name,
            tool_provider=call.tool_provider,
            status=call.status,
            arguments_summary=call.arguments_summary,
            result_summary=call.result_summary,
            side_effect_level=call.side_effect_level,
            idempotency_key=call.idempotency_key,
            created_at=call.created_at,
            updated_at=call.updated_at,
        )

    @staticmethod
    def _build_model_call_response(call: AgentModelCall) -> AgentModelCallResponse:
        return AgentModelCallResponse(
            id=call.id,
            agent_run_id=call.agent_run_id,
            provider=call.provider,
            route_key=call.route_key,
            model_name=call.model_name,
            status=call.status,
            request_payload=call.request_payload,
            response_payload=call.response_payload,
            usage_json=call.usage_json,
            error_message=call.error_message,
            started_at=call.started_at,
            ended_at=call.ended_at,
            created_at=call.created_at,
        )

    @staticmethod
    def _build_skill_activation_response(activation: SkillActivation) -> SkillActivationResponse:
        return SkillActivationResponse(
            id=activation.id,
            agent_run_id=activation.agent_run_id,
            package_id=activation.package_id,
            version_id=activation.version_id,
            activation_context=activation.activation_context,
            created_at=activation.created_at,
        )

    @staticmethod
    def _build_tool_authorization_response(authorization: AgentToolAuthorization) -> AgentToolAuthorizationResponse:
        return AgentToolAuthorizationResponse(
            id=authorization.id,
            agent_run_id=authorization.agent_run_id,
            agent_tool_call_id=authorization.agent_tool_call_id,
            run_id=authorization.run_id,
            run_event_id=authorization.run_event_id,
            tool_name=authorization.tool_name,
            tool_provider=authorization.tool_provider,
            mcp_server_name=authorization.mcp_server_name,
            side_effect_level=authorization.side_effect_level,
            risk_level=authorization.risk_level,
            authorization_reason=authorization.authorization_reason,
            tool_arguments_summary=authorization.tool_arguments_summary,
            expected_effect_summary=authorization.expected_effect_summary,
            reversible=authorization.reversible,
            idempotency_key=authorization.idempotency_key,
            status=authorization.status,
            business_context=tool_authorization_business_context(authorization),
            request_payload=authorization.request_payload,
            response_payload=authorization.response_payload,
            created_at=authorization.created_at,
            responded_at=authorization.responded_at,
            executed_at=authorization.executed_at,
        )

    def _tool_authorization_matches_context_filters(
        self,
        authorization: AgentToolAuthorization,
        *,
        proposal_id: str | None = None,
        source_run_id: str | None = None,
        source_evaluation_id: str | None = None,
        source_finding_id: str | None = None,
    ) -> bool:
        context = tool_authorization_business_context(authorization)
        expected = {
            "proposal_id": self._normalize_filter_value(proposal_id),
            "source_run_id": self._normalize_filter_value(source_run_id),
            "source_evaluation_id": self._normalize_filter_value(source_evaluation_id),
        }
        for key, value in expected.items():
            if value and value not in self._business_context_values(context, key):
                return False
        normalized_source_finding_id = self._normalize_filter_value(source_finding_id)
        if normalized_source_finding_id and normalized_source_finding_id not in self._business_context_values(
            context,
            "source_finding_ids",
            "source_finding_id",
        ):
            return False
        return True

    @staticmethod
    def _normalize_filter_value(value: object) -> str:
        return str(value or "").strip()

    @classmethod
    def _business_context_values(cls, context: dict[str, object], *keys: str) -> set[str]:
        values: set[str] = set()
        for key in keys:
            value = context.get(key)
            if isinstance(value, list):
                for item in value:
                    normalized = cls._normalize_filter_value(item)
                    if normalized:
                        values.add(normalized)
            else:
                normalized = cls._normalize_filter_value(value)
                if normalized:
                    values.add(normalized)
        return values

    @staticmethod
    def _open_telemetry_status(*, settings: Settings, configured: bool) -> OpenTelemetryStatus:
        return OpenTelemetryStatus(
            enabled=settings.otel_enabled,
            configured=configured,
            traces_enabled=settings.otel_traces_enabled,
            logs_enabled=settings.otel_logs_enabled,
            console_exporter=settings.otel_console_exporter,
            exporter_otlp_endpoint=settings.otel_exporter_otlp_endpoint,
            exporter_otlp_protocol=settings.otel_exporter_otlp_protocol,
            service_name=settings.otel_service_name,
        )

    @staticmethod
    def _count(session: Session, model: type, *conditions: object) -> int:
        query = select(func.count()).select_from(model)
        for condition in conditions:
            query = query.where(condition)
        return int(session.scalar(query) or 0)

    @staticmethod
    def _count_by(session: Session, column: object, *conditions: object) -> dict[str, int]:
        query = select(column, func.count()).group_by(column)
        for condition in conditions:
            query = query.where(condition)
        rows = session.execute(query).all()
        return {str(value): int(count) for value, count in rows}

    @staticmethod
    def _status_counts_from_items(items: list[object]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in items:
            status = str(getattr(item, "status", "") or "")
            counts[status] = counts.get(status, 0) + 1
        return counts

    @staticmethod
    def _average_duration_ms(items: list[object]) -> int:
        durations: list[float] = []
        for item in items:
            started_at = getattr(item, "started_at", None)
            ended_at = getattr(item, "ended_at", None)
            if not started_at or not ended_at:
                continue
            duration_ms = (ended_at - started_at).total_seconds() * 1000
            if duration_ms >= 0:
                durations.append(duration_ms)
        if not durations:
            return 0
        return int(round(sum(durations) / len(durations)))

    @staticmethod
    def _rate(numerator: int, denominator: int) -> float:
        if denominator <= 0:
            return 0.0
        return round(numerator / denominator, 4)
