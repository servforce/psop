from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.agents.schemas import AppendAgentEventRequest, CreateAgentRunRequest
from app.agents.service import AgentService
from app.evaluations.models import RunEvaluation, RunEvaluationFinding
from app.evaluations.repository import EvaluationRepository
from app.evaluations.schemas import (
    RunEvaluationFindingResponse,
    RunEvaluationResponse,
    UpdateRunEvaluationFindingRequest,
)
from app.pskills.exceptions import SkillNotFoundError, SkillValidationError
from app.pskills.models import generate_uuid, now_utc
from app.runtime.models import Run, RunEvent, RunTrace


TERMINAL_RUN_STATUSES = {"succeeded", "failed", "aborted", "cancelled"}
VALID_FINDING_STATUSES = {"open", "accepted", "dismissed", "converted_to_proposal", "resolved"}
VALID_FINDING_CATEGORIES = {
    "pskill_build_issue",
    "compile_issue",
    "test_gap",
    "runner_issue",
    "human_operation_issue",
    "evidence_quality_issue",
    "tool_issue",
    "environment_issue",
}
VALID_FINDING_SEVERITIES = {"low", "medium", "high", "critical"}
SEVERITY_PENALTIES = {"low": 5, "medium": 12, "high": 24, "critical": 40}


class EvaluationService:
    def __init__(
        self,
        *,
        repository: EvaluationRepository | None = None,
        agent_service: AgentService | None = None,
    ) -> None:
        self.repository = repository or EvaluationRepository()
        self.agent_service = agent_service or AgentService()

    def create_run_evaluation(self, session: Session, run_id: str) -> RunEvaluationResponse:
        run = self._get_terminal_run(session, run_id)
        evaluation_id = generate_uuid()
        facts = self._collect_run_facts(session, run)
        result = self._score_run(run, facts)
        agent_run_id = self._create_evaluator_agent_run(session, evaluation_id=evaluation_id, run=run, facts=facts)

        evaluation = RunEvaluation(
            id=evaluation_id,
            run_id=run.id,
            pskill_definition_id=run.pskill_definition_id,
            pskill_version_id=run.pskill_version_id,
            artifact_id=run.compile_artifact_id,
            agent_run_id=agent_run_id,
            overall_outcome=str(result["overall_outcome"]),
            quality_score=int(result["quality_score"]),
            summary=str(result["summary"]),
            attribution_json=dict(result["attribution"]),
        )
        session.add(evaluation)
        session.flush()

        finding_models: list[RunEvaluationFinding] = []
        for finding_payload in result["findings"]:
            finding = RunEvaluationFinding(
                evaluation_id=evaluation.id,
                category=str(finding_payload["category"]),
                severity=str(finding_payload["severity"]),
                confidence=int(finding_payload["confidence"]),
                description=str(finding_payload["description"]),
                evidence_refs=list(finding_payload["evidence_refs"]),
                recommended_action=str(finding_payload["recommended_action"]),
                status="open",
            )
            session.add(finding)
            finding_models.append(finding)
        session.flush()

        output_payload = {
            "schema": "RunEvaluationResult",
            "overall_outcome": evaluation.overall_outcome,
            "quality_score": evaluation.quality_score,
            "summary": evaluation.summary,
            "attribution": evaluation.attribution_json,
            "findings": [self._finding_result_payload(item) for item in finding_models],
        }
        self._mark_evaluator_agent_succeeded(
            session,
            agent_run_id=agent_run_id,
            evaluation=evaluation,
            output_payload=output_payload,
        )
        session.commit()
        return self.get_evaluation(session, evaluation.id)

    def get_evaluation(self, session: Session, evaluation_id: str) -> RunEvaluationResponse:
        evaluation = self.repository.get_evaluation(session, evaluation_id)
        if not evaluation:
            raise SkillNotFoundError("未找到 RunEvaluation。", details={"evaluation_id": evaluation_id})
        return self._build_evaluation_response(session, evaluation)

    def list_evaluation_findings(self, session: Session, evaluation_id: str) -> list[RunEvaluationFindingResponse]:
        evaluation = self.repository.get_evaluation(session, evaluation_id)
        if not evaluation:
            raise SkillNotFoundError("未找到 RunEvaluation。", details={"evaluation_id": evaluation_id})
        return [self._build_finding_response(item) for item in self.repository.list_evaluation_findings(session, evaluation_id)]

    def list_findings(
        self,
        session: Session,
        *,
        status: str | None = None,
        category: str | None = None,
        severity: str | None = None,
        run_id: str | None = None,
        pskill_definition_id: str | None = None,
    ) -> list[RunEvaluationFindingResponse]:
        self._validate_optional_filter("status", status, VALID_FINDING_STATUSES)
        self._validate_optional_filter("category", category, VALID_FINDING_CATEGORIES)
        self._validate_optional_filter("severity", severity, VALID_FINDING_SEVERITIES)
        return [
            self._build_finding_response(item)
            for item in self.repository.list_findings(
                session,
                status=status,
                category=category,
                severity=severity,
                run_id=run_id,
                pskill_definition_id=pskill_definition_id,
            )
        ]

    def update_finding_status(
        self,
        session: Session,
        finding_id: str,
        payload: UpdateRunEvaluationFindingRequest,
    ) -> RunEvaluationFindingResponse:
        finding = self.repository.get_finding(session, finding_id)
        if not finding:
            raise SkillNotFoundError("未找到 RunEvaluationFinding。", details={"finding_id": finding_id})
        status = payload.status.strip()
        if status not in VALID_FINDING_STATUSES:
            raise SkillValidationError("finding status 无效。", details={"status": status})
        finding.status = status
        session.commit()
        return self._build_finding_response(finding)

    def _get_terminal_run(self, session: Session, run_id: str) -> Run:
        run = self.repository.get_run(session, run_id)
        if not run:
            raise SkillNotFoundError("未找到 Run。", details={"run_id": run_id})
        if run.status not in TERMINAL_RUN_STATUSES:
            raise SkillValidationError(
                "只能评估已结束的 Run。",
                details={"run_id": run.id, "status": run.status},
            )
        return run

    def _collect_run_facts(self, session: Session, run: Run) -> dict[str, Any]:
        snapshots = self.repository.list_snapshots(session, run.id)
        run_events = self.repository.list_run_events(session, run.id)
        run_traces = self.repository.list_run_traces(session, run.id)
        latest_token = snapshots[-1].token_payload if snapshots else {}
        latest_evaluation = {}
        control = latest_token.get("control") if isinstance(latest_token, dict) else {}
        if isinstance(control, dict) and isinstance(control.get("latest_evaluation"), dict):
            latest_evaluation = control["latest_evaluation"]
        return {
            "run": {
                "id": run.id,
                "status": run.status,
                "runtime_phase": run.runtime_phase,
                "exit_reason": run.exit_reason,
                "final_output_chars": len(run.final_output or ""),
                "latest_snapshot_seq": run.latest_snapshot_seq,
                "latest_run_event_seq": run.latest_run_event_seq,
                "latest_trace_seq": run.latest_trace_seq,
            },
            "latest_evaluation": latest_evaluation,
            "counts": {
                "snapshots": len(snapshots),
                "run_events": len(run_events),
                "run_traces": len(run_traces),
                "input_events": len([item for item in run_events if item.direction == "input"]),
                "output_events": len([item for item in run_events if item.direction == "output"]),
                "recoverable_failures": len(
                    [item for item in run_traces if item.event_type == "runtime.message_processing.failed"]
                ),
            },
            "trace_event_types": [item.event_type for item in run_traces],
            "evidence": {
                "last_trace": self._trace_ref(run_traces[-1]) if run_traces else None,
                "last_run_event": self._run_event_ref(run_events[-1]) if run_events else None,
                "runtime_failed": [self._trace_ref(item) for item in run_traces if item.event_type == "runtime.failed"],
                "recoverable_failures": [
                    self._trace_ref(item) for item in run_traces if item.event_type == "runtime.message_processing.failed"
                ],
            },
        }

    def _score_run(self, run: Run, facts: dict[str, Any]) -> dict[str, Any]:
        findings = self._derive_findings(run, facts)
        score = self._base_score_for_status(run.status)
        for finding in findings:
            score -= SEVERITY_PENALTIES[str(finding["severity"])]
        score = max(0, min(100, score))
        overall_outcome = self._overall_outcome(run.status, findings)
        attribution = self._attribution(findings, facts)
        return {
            "overall_outcome": overall_outcome,
            "quality_score": score,
            "summary": self._summary(run, overall_outcome=overall_outcome, quality_score=score, finding_count=len(findings)),
            "attribution": attribution,
            "findings": findings,
        }

    def _derive_findings(self, run: Run, facts: dict[str, Any]) -> list[dict[str, Any]]:
        findings: list[dict[str, Any]] = []
        evidence = facts.get("evidence") if isinstance(facts.get("evidence"), dict) else {}
        counts = facts.get("counts") if isinstance(facts.get("counts"), dict) else {}
        if run.status == "failed":
            findings.append(
                self._finding_payload(
                    category="runner_issue",
                    severity="high",
                    confidence=90,
                    description=f"Run 以 failed 结束：{run.exit_reason or '未记录失败原因'}",
                    evidence_refs=evidence.get("runtime_failed") or [evidence.get("last_trace")],
                    recommended_action="查看 runtime.failed trace 和关联 AgentRun，补充失败场景测试后再修复 Runtime 或 PSkill。",
                )
            )
        if run.status == "aborted":
            findings.append(
                self._finding_payload(
                    category="environment_issue",
                    severity="high",
                    confidence=82,
                    description=f"Run 被语义中止：{run.exit_reason or '未记录中止原因'}",
                    evidence_refs=[evidence.get("last_trace")],
                    recommended_action="复核现场约束和安全停止条件，必要时补充 PSkill 的安全分支与测试场景。",
                )
            )
        if run.status == "cancelled":
            findings.append(
                self._finding_payload(
                    category="human_operation_issue",
                    severity="medium",
                    confidence=75,
                    description="Run 被取消，无法证明任务完成质量。",
                    evidence_refs=[evidence.get("last_trace")],
                    recommended_action="确认取消原因，并判断是否需要补充恢复流程或用户提示。",
                )
            )
        recoverable_count = int(counts.get("recoverable_failures") or 0)
        if recoverable_count:
            findings.append(
                self._finding_payload(
                    category="runner_issue",
                    severity="medium",
                    confidence=80,
                    description=f"运行过程中出现 {recoverable_count} 次可恢复消息处理失败。",
                    evidence_refs=evidence.get("recoverable_failures") or [],
                    recommended_action="复盘失败 trace，补充回归测试并优化 provider 错误恢复策略。",
                )
            )
        if int(counts.get("input_events") or 0) == 0:
            findings.append(
                self._finding_payload(
                    category="evidence_quality_issue",
                    severity="low",
                    confidence=70,
                    description="Run 没有记录用户输入 run_event，评估证据链不完整。",
                    evidence_refs=[evidence.get("last_trace")],
                    recommended_action="确保 Gateway 在调用入口写入原始输入或要求用户补充现场证据。",
                )
            )
        return findings

    @staticmethod
    def _finding_payload(
        *,
        category: str,
        severity: str,
        confidence: int,
        description: str,
        evidence_refs: list[dict[str, Any] | None],
        recommended_action: str,
    ) -> dict[str, Any]:
        return {
            "category": category,
            "severity": severity,
            "confidence": max(0, min(100, confidence)),
            "description": description,
            "evidence_refs": [item for item in evidence_refs if isinstance(item, dict)],
            "recommended_action": recommended_action,
        }

    @staticmethod
    def _base_score_for_status(status: str) -> int:
        return {
            "succeeded": 94,
            "aborted": 58,
            "failed": 42,
            "cancelled": 50,
        }.get(status, 50)

    @staticmethod
    def _overall_outcome(status: str, findings: list[dict[str, Any]]) -> str:
        if status == "succeeded":
            return "completed_with_issues" if findings else "success"
        if status == "aborted":
            return "aborted"
        if status == "failed":
            return "failed"
        return status

    @staticmethod
    def _attribution(findings: list[dict[str, Any]], facts: dict[str, Any]) -> dict[str, Any]:
        categories: dict[str, dict[str, int]] = {}
        for finding in findings:
            category = str(finding["category"])
            severity = str(finding["severity"])
            bucket = categories.setdefault(category, {"count": 0, "penalty": 0})
            bucket["count"] += 1
            bucket["penalty"] += SEVERITY_PENALTIES[severity]
        total_penalty = sum(item["penalty"] for item in categories.values())
        return {
            "categories": categories,
            "total_penalty": total_penalty,
            "finding_count": len(findings),
            "fact_counts": facts.get("counts") if isinstance(facts.get("counts"), dict) else {},
        }

    @staticmethod
    def _summary(run: Run, *, overall_outcome: str, quality_score: int, finding_count: int) -> str:
        if finding_count:
            return f"Run 评估完成：状态 {run.status}，结论 {overall_outcome}，质量分 {quality_score}，发现 {finding_count} 个问题。"
        return f"Run 评估完成：状态 {run.status}，结论 {overall_outcome}，质量分 {quality_score}，未发现阻塞性问题。"

    def _create_evaluator_agent_run(
        self,
        session: Session,
        *,
        evaluation_id: str,
        run: Run,
        facts: dict[str, Any],
    ) -> str:
        agent_run = self.agent_service.create_run(
            session,
            CreateAgentRunRequest(
                agent_key="pskill.evaluator",
                owner_type="run_evaluation",
                owner_id=evaluation_id,
                run_id=run.id,
                input_payload={
                    "schema": "RunEvaluationInput",
                    "evaluation_id": evaluation_id,
                    "run_id": run.id,
                    "pskill_definition_id": run.pskill_definition_id,
                    "pskill_version_id": run.pskill_version_id,
                    "artifact_id": run.compile_artifact_id,
                    "facts": facts,
                },
            ),
            commit=False,
        )
        agent_run_model = self.agent_service.get_run_model(session, agent_run.id)
        agent_run_model.status = "running"
        agent_run_model.started_at = agent_run_model.started_at or now_utc()
        self.agent_service.append_event(
            session,
            agent_run.id,
            AppendAgentEventRequest(
                event_type="evaluation.run.started",
                phase="evaluation",
                payload={"evaluation_id": evaluation_id, "run_id": run.id},
            ),
            commit=False,
        )
        return agent_run.id

    def _mark_evaluator_agent_succeeded(
        self,
        session: Session,
        *,
        agent_run_id: str,
        evaluation: RunEvaluation,
        output_payload: dict[str, Any],
    ) -> None:
        self.agent_service.record_model_call(
            session,
            agent_run_id=agent_run_id,
            provider="deterministic",
            route_key="json",
            model_name="pskill-evaluator-deterministic",
            status="succeeded",
            request_payload={"evaluation_id": evaluation.id, "run_id": evaluation.run_id},
            response_payload=output_payload,
            usage_json={"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
            commit=False,
        )
        self.agent_service.append_event(
            session,
            agent_run_id,
            AppendAgentEventRequest(
                event_type="evaluation.agent.model_call.completed",
                phase="evaluation",
                payload={"evaluation_id": evaluation.id, "quality_score": evaluation.quality_score},
            ),
            commit=False,
        )
        agent_run = self.agent_service.get_run_model(session, agent_run_id)
        agent_run.status = "succeeded"
        agent_run.output_payload = output_payload
        agent_run.error_message = ""
        agent_run.ended_at = now_utc()
        self.agent_service.append_event(
            session,
            agent_run_id,
            AppendAgentEventRequest(
                event_type="evaluation.run.completed",
                phase="evaluation",
                payload={
                    "evaluation_id": evaluation.id,
                    "overall_outcome": evaluation.overall_outcome,
                    "quality_score": evaluation.quality_score,
                    "finding_count": len(output_payload.get("findings") or []),
                },
            ),
            commit=False,
        )

    @staticmethod
    def _trace_ref(trace: RunTrace) -> dict[str, Any]:
        return {
            "kind": "run_trace",
            "id": trace.id,
            "seq_no": trace.seq_no,
            "event_type": trace.event_type,
            "agent_run_id": trace.agent_run_id,
        }

    @staticmethod
    def _run_event_ref(event: RunEvent) -> dict[str, Any]:
        return {
            "kind": "run_event",
            "id": event.id,
            "seq_no": event.seq_no,
            "event_kind": event.event_kind,
            "direction": event.direction,
            "agent_run_id": event.agent_run_id,
        }

    @staticmethod
    def _finding_result_payload(finding: RunEvaluationFinding) -> dict[str, Any]:
        return {
            "id": finding.id,
            "category": finding.category,
            "severity": finding.severity,
            "confidence": finding.confidence,
            "description": finding.description,
            "evidence_refs": finding.evidence_refs,
            "recommended_action": finding.recommended_action,
            "status": finding.status,
        }

    def _build_evaluation_response(self, session: Session, evaluation: RunEvaluation) -> RunEvaluationResponse:
        return RunEvaluationResponse(
            id=evaluation.id,
            run_id=evaluation.run_id,
            pskill_definition_id=evaluation.pskill_definition_id,
            pskill_version_id=evaluation.pskill_version_id,
            artifact_id=evaluation.artifact_id,
            agent_run_id=evaluation.agent_run_id,
            overall_outcome=evaluation.overall_outcome,
            quality_score=evaluation.quality_score,
            summary=evaluation.summary,
            attribution=evaluation.attribution_json,
            findings=[
                self._build_finding_response(item)
                for item in self.repository.list_evaluation_findings(session, evaluation.id)
            ],
            created_at=evaluation.created_at,
        )

    @staticmethod
    def _build_finding_response(finding: RunEvaluationFinding) -> RunEvaluationFindingResponse:
        return RunEvaluationFindingResponse(
            id=finding.id,
            evaluation_id=finding.evaluation_id,
            category=finding.category,
            severity=finding.severity,
            confidence=finding.confidence,
            description=finding.description,
            evidence_refs=finding.evidence_refs,
            recommended_action=finding.recommended_action,
            status=finding.status,
            created_at=finding.created_at,
        )

    @staticmethod
    def _validate_optional_filter(name: str, value: str | None, allowed: set[str]) -> None:
        if value is not None and value not in allowed:
            raise SkillValidationError(f"{name} filter 无效。", details={name: value})
