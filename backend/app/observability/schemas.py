from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class PSkillDashboardMetrics(BaseModel):
    total_count: int = 0
    draft_count: int = 0
    testing_count: int = 0
    published_count: int = 0
    publish_gate_total: int = 0
    publish_gate_passed: int = 0
    publish_gate_pass_rate: float = 0.0
    status_counts: dict[str, int] = Field(default_factory=dict)


class RuntimeDashboardMetrics(BaseModel):
    recent_run_count: int = 0
    succeeded_count: int = 0
    failed_count: int = 0
    aborted_count: int = 0
    cancelled_count: int = 0
    success_rate: float = 0.0
    average_duration_ms: int = 0
    status_counts: dict[str, int] = Field(default_factory=dict)


class EvaluationDashboardMetrics(BaseModel):
    recent_evaluation_count: int = 0
    average_quality_score: float = 0.0
    high_severity_finding_count: int = 0
    unresolved_finding_count: int = 0
    outcome_counts: dict[str, int] = Field(default_factory=dict)
    finding_status_counts: dict[str, int] = Field(default_factory=dict)


class GovernanceDashboardMetrics(BaseModel):
    open_proposal_count: int = 0
    testing_proposal_count: int = 0
    canary_proposal_count: int = 0
    rollback_proposal_count: int = 0
    status_counts: dict[str, int] = Field(default_factory=dict)


class AgentDashboardMetrics(BaseModel):
    agent_key: str
    recent_run_count: int = 0
    succeeded_count: int = 0
    failed_count: int = 0
    waiting_tool_authorization_count: int = 0
    success_rate: float = 0.0
    average_duration_ms: int = 0
    tool_call_count: int = 0
    failed_tool_call_count: int = 0
    tool_failure_rate: float = 0.0


class GlobalObservabilityMetrics(BaseModel):
    run_trace_count: int = 0
    agent_event_count: int = 0
    model_call_count: int = 0
    tool_call_count: int = 0
    pending_tool_authorization_count: int = 0
    otel_enabled: bool = False
    otel_service_name: str = ""


class DashboardMetricsResponse(BaseModel):
    generated_at: datetime
    window_hours: int
    pskills: PSkillDashboardMetrics
    runtime: RuntimeDashboardMetrics
    evaluations: EvaluationDashboardMetrics
    governance: GovernanceDashboardMetrics
    agents: list[AgentDashboardMetrics] = Field(default_factory=list)
    observability: GlobalObservabilityMetrics
