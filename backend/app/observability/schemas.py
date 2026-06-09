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
    activated_proposal_count: int = 0
    rollback_proposal_count: int = 0
    experiment_count: int = 0
    status_counts: dict[str, int] = Field(default_factory=dict)


class AgentDashboardMetrics(BaseModel):
    agent_key: str
    recent_run_count: int = 0
    succeeded_count: int = 0
    failed_count: int = 0
    waiting_tool_authorization_count: int = 0
    success_rate: float = 0.0
    average_duration_ms: int = 0
    model_call_count: int = 0
    failed_model_call_count: int = 0
    model_failure_rate: float = 0.0
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


class RuntimeObservabilityMetrics(BaseModel):
    run_count: int = 0
    run_status_counts: dict[str, int] = Field(default_factory=dict)
    run_event_count: int = 0
    run_event_kind_counts: dict[str, int] = Field(default_factory=dict)
    run_trace_count: int = 0
    run_trace_event_type_counts: dict[str, int] = Field(default_factory=dict)
    run_trace_phase_counts: dict[str, int] = Field(default_factory=dict)


class AgentObservabilityMetrics(BaseModel):
    agent_run_count: int = 0
    agent_run_status_counts: dict[str, int] = Field(default_factory=dict)
    agent_run_key_counts: dict[str, int] = Field(default_factory=dict)
    agent_event_count: int = 0
    agent_event_type_counts: dict[str, int] = Field(default_factory=dict)
    agent_event_phase_counts: dict[str, int] = Field(default_factory=dict)
    model_call_count: int = 0
    model_call_status_counts: dict[str, int] = Field(default_factory=dict)
    model_call_provider_counts: dict[str, int] = Field(default_factory=dict)
    tool_call_count: int = 0
    tool_call_status_counts: dict[str, int] = Field(default_factory=dict)
    tool_call_side_effect_counts: dict[str, int] = Field(default_factory=dict)
    skill_activation_count: int = 0
    skill_activation_package_counts: dict[str, int] = Field(default_factory=dict)
    tool_authorization_count: int = 0
    tool_authorization_status_counts: dict[str, int] = Field(default_factory=dict)
    tool_authorization_risk_counts: dict[str, int] = Field(default_factory=dict)


class EvaluationObservabilityMetrics(BaseModel):
    evaluation_count: int = 0
    average_quality_score: float = 0.0
    outcome_counts: dict[str, int] = Field(default_factory=dict)
    finding_count: int = 0
    high_severity_finding_count: int = 0
    unresolved_finding_count: int = 0
    finding_status_counts: dict[str, int] = Field(default_factory=dict)
    finding_category_counts: dict[str, int] = Field(default_factory=dict)
    finding_severity_counts: dict[str, int] = Field(default_factory=dict)


class GovernanceObservabilityMetrics(BaseModel):
    proposal_count: int = 0
    open_proposal_count: int = 0
    testing_proposal_count: int = 0
    canary_proposal_count: int = 0
    activated_proposal_count: int = 0
    rollback_proposal_count: int = 0
    status_counts: dict[str, int] = Field(default_factory=dict)
    proposal_type_counts: dict[str, int] = Field(default_factory=dict)
    source_run_linked_count: int = 0
    source_evaluation_linked_count: int = 0
    source_finding_linked_count: int = 0
    experiment_count: int = 0
    experiment_status_counts: dict[str, int] = Field(default_factory=dict)
    experiment_type_counts: dict[str, int] = Field(default_factory=dict)


class OpenTelemetryStatus(BaseModel):
    enabled: bool = False
    configured: bool = False
    traces_enabled: bool = False
    logs_enabled: bool = False
    console_exporter: bool = False
    exporter_otlp_endpoint: str = ""
    exporter_otlp_protocol: str = ""
    service_name: str = ""


class ObservabilityMetricsResponse(BaseModel):
    generated_at: datetime
    since: datetime
    window_hours: int
    runtime: RuntimeObservabilityMetrics
    agents: AgentObservabilityMetrics
    evaluations: EvaluationObservabilityMetrics
    governance: GovernanceObservabilityMetrics
    open_telemetry: OpenTelemetryStatus


class DashboardMetricsResponse(BaseModel):
    generated_at: datetime
    window_hours: int
    pskills: PSkillDashboardMetrics
    runtime: RuntimeDashboardMetrics
    evaluations: EvaluationDashboardMetrics
    governance: GovernanceDashboardMetrics
    agents: list[AgentDashboardMetrics] = Field(default_factory=list)
    observability: GlobalObservabilityMetrics
