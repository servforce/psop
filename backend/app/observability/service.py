from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agents.models import AgentEvent, AgentModelCall, AgentRun, AgentToolAuthorization, AgentToolCall
from app.core.config import Settings
from app.evaluations.models import RunEvaluation, RunEvaluationFinding
from app.governance.models import PsopImprovementProposal
from app.pskills.models import PSkillDefinition, PSkillVersion, now_utc
from app.runtime.models import Run, RunTrace
from app.testing.models import PSkillPublishGate
from app.observability.schemas import (
    AgentDashboardMetrics,
    DashboardMetricsResponse,
    EvaluationDashboardMetrics,
    GlobalObservabilityMetrics,
    GovernanceDashboardMetrics,
    PSkillDashboardMetrics,
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
            rollback_proposal_count=status_counts.get("rolled_back", 0),
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
            tool_calls = []
            if run_ids:
                tool_calls = list(
                    session.scalars(select(AgentToolCall).where(AgentToolCall.agent_run_id.in_(run_ids))).all()
                )
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

    @staticmethod
    def _count(session: Session, model: type, *conditions: object) -> int:
        query = select(func.count()).select_from(model)
        for condition in conditions:
            query = query.where(condition)
        return int(session.scalar(query) or 0)

    @staticmethod
    def _count_by(session: Session, column: object) -> dict[str, int]:
        rows = session.execute(select(column, func.count()).group_by(column)).all()
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
