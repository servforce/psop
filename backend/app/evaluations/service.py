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
from app.jobs.models import RuntimeJob
from app.jobs.repository import JobRepository
from app.jobs.types import RUN_EVALUATION_JOB_TYPE
from app.memory.service import MemoryService
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
VALID_EVALUATION_OUTCOMES = {"success", "completed_with_issues", "failed", "aborted", "cancelled"}
SEVERITY_PENALTIES = {"low": 5, "medium": 12, "high": 24, "critical": 40}


class EvaluationService:
    def __init__(
        self,
        *,
        repository: EvaluationRepository | None = None,
        agent_service: AgentService | None = None,
        job_repository: JobRepository | None = None,
        memory_service: MemoryService | None = None,
    ) -> None:
        self.repository = repository or EvaluationRepository()
        self.agent_service = agent_service or AgentService()
        self.job_repository = job_repository or JobRepository()
        self.memory_service = memory_service or MemoryService()

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
        memory_entries = self._write_evaluation_memory_candidates(
            session,
            agent_run_id=agent_run_id,
            evaluation=evaluation,
            findings=finding_models,
            facts=facts,
        )
        output_payload["memory_candidates"] = {
            "written_count": len(memory_entries),
            "memory_entry_ids": [item.id for item in memory_entries],
        }
        self._mark_evaluator_agent_succeeded(
            session,
            agent_run_id=agent_run_id,
            evaluation=evaluation,
            output_payload=output_payload,
        )
        session.commit()
        return self.get_evaluation(session, evaluation.id)

    def enqueue_run_evaluation_job(self, session: Session, run_id: str) -> str:
        run = self._get_terminal_run(session, run_id)
        dedupe_key = f"job:run-evaluation:{run.id}"
        existing = self.job_repository.get_runtime_job_by_dedupe_key(session, dedupe_key)
        if existing:
            return existing.id
        job = RuntimeJob(
            job_type=RUN_EVALUATION_JOB_TYPE,
            status="pending",
            payload={"operation": "run_evaluation", "run_id": run.id},
            run_id=run.id,
            dedupe_key=dedupe_key,
        )
        session.add(job)
        session.commit()
        return job.id

    def process_run_evaluation_job(self, session: Session, job_id: str) -> RunEvaluationResponse:
        job = self.job_repository.get_runtime_job(session, job_id)
        if not job:
            raise SkillNotFoundError("未找到 RunEvaluation 任务。", details={"job_id": job_id})
        if job.job_type != RUN_EVALUATION_JOB_TYPE:
            raise SkillValidationError("当前 worker 仅支持 RunEvaluation 任务。", details={"job_type": job.job_type})

        payload = dict(job.payload or {})
        evaluation_id = str(payload.get("evaluation_id") or "").strip()
        if evaluation_id:
            evaluation = self.get_evaluation(session, evaluation_id)
        else:
            run_id = str(job.run_id or payload.get("run_id") or "").strip()
            if not run_id:
                raise SkillValidationError("RunEvaluation 任务缺少 run_id。", details={"job_id": job.id})
            evaluation = self.create_run_evaluation(session, run_id)

        finding_count = len(evaluation.findings)
        metrics = dict(job.metrics or {})
        metrics.update(
            {
                "evaluation_id": evaluation.id,
                "finding_count": finding_count,
                "quality_score": evaluation.quality_score,
                "overall_outcome": evaluation.overall_outcome,
            }
        )
        job.payload = {
            **payload,
            "operation": "run_evaluation",
            "run_id": evaluation.run_id,
            "evaluation_id": evaluation.id,
            "overall_outcome": evaluation.overall_outcome,
            "quality_score": evaluation.quality_score,
            "finding_count": finding_count,
        }
        job.run_id = evaluation.run_id
        job.metrics = metrics
        job.status = "succeeded"
        job.last_error = ""
        job.lease_until = None
        session.commit()
        return evaluation

    def get_evaluation(self, session: Session, evaluation_id: str) -> RunEvaluationResponse:
        evaluation = self.repository.get_evaluation(session, evaluation_id)
        if not evaluation:
            raise SkillNotFoundError("未找到 RunEvaluation。", details={"evaluation_id": evaluation_id})
        return self._build_evaluation_response(session, evaluation)

    def list_evaluations(
        self,
        session: Session,
        *,
        run_id: str | None = None,
        pskill_definition_id: str | None = None,
        overall_outcome: str | None = None,
        limit: int = 50,
    ) -> list[RunEvaluationResponse]:
        self._validate_optional_filter("overall_outcome", overall_outcome, VALID_EVALUATION_OUTCOMES)
        return [
            self._build_evaluation_response(session, item)
            for item in self.repository.list_evaluations(
                session,
                run_id=run_id,
                pskill_definition_id=pskill_definition_id,
                overall_outcome=overall_outcome,
                limit=limit,
            )
        ]

    def list_evaluation_findings(self, session: Session, evaluation_id: str) -> list[RunEvaluationFindingResponse]:
        evaluation = self.repository.get_evaluation(session, evaluation_id)
        if not evaluation:
            raise SkillNotFoundError("未找到 RunEvaluation。", details={"evaluation_id": evaluation_id})
        return [
            self._build_finding_response(item, evaluation=evaluation)
            for item in self.repository.list_evaluation_findings(session, evaluation_id)
        ]

    def get_finding(self, session: Session, finding_id: str) -> RunEvaluationFindingResponse:
        finding = self.repository.get_finding(session, finding_id)
        if not finding:
            raise SkillNotFoundError("未找到 RunEvaluationFinding。", details={"finding_id": finding_id})
        evaluation = self.repository.get_evaluation(session, finding.evaluation_id)
        return self._build_finding_response(finding, evaluation=evaluation)

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
        findings = self.repository.list_findings(
            session,
            status=status,
            category=category,
            severity=severity,
            run_id=run_id,
            pskill_definition_id=pskill_definition_id,
        )
        evaluations = {
            item.id: item
            for item in self.repository.list_evaluations_by_ids(
                session,
                {finding.evaluation_id for finding in findings},
            )
        }
        return [
            self._build_finding_response(item, evaluation=evaluations.get(item.evaluation_id))
            for item in findings
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
        evaluation = self.repository.get_evaluation(session, finding.evaluation_id)
        session.commit()
        return self._build_finding_response(finding, evaluation=evaluation)

    def write_diagnostics_from_agent_tool(
        self,
        session: Session,
        *,
        agent_run_id: str,
        evaluation_id: str,
        payload: dict[str, Any],
        commit: bool = True,
    ) -> dict[str, Any]:
        evaluation = self.repository.get_evaluation(session, evaluation_id)
        if not evaluation:
            raise SkillNotFoundError("未找到 RunEvaluation。", details={"evaluation_id": evaluation_id})
        raw_findings = payload.get("findings", payload.get("diagnostics"))
        if isinstance(raw_findings, dict):
            raw_findings = [raw_findings]
        if not isinstance(raw_findings, list) or not raw_findings:
            raise SkillValidationError(
                "psop.evaluations.write_diagnostics 缺少 findings。",
                details={"evaluation_id": evaluation_id},
            )

        created_findings: list[RunEvaluationFinding] = []
        for raw_finding in raw_findings:
            if not isinstance(raw_finding, dict):
                raise SkillValidationError(
                    "evaluation finding 必须是对象。",
                    details={"evaluation_id": evaluation_id, "finding": raw_finding},
                )
            finding = RunEvaluationFinding(
                evaluation_id=evaluation.id,
                category=self._validated_finding_field(
                    raw_finding,
                    "category",
                    VALID_FINDING_CATEGORIES,
                    default="runner_issue",
                ),
                severity=self._validated_finding_field(
                    raw_finding,
                    "severity",
                    VALID_FINDING_SEVERITIES,
                    default="medium",
                ),
                confidence=self._bounded_int(raw_finding.get("confidence"), default=70, minimum=0, maximum=100),
                description=self._required_finding_text(raw_finding, "description", fallback_key="message"),
                evidence_refs=list(raw_finding.get("evidence_refs") or []),
                recommended_action=str(raw_finding.get("recommended_action") or raw_finding.get("action") or "").strip(),
                status=self._validated_finding_field(
                    raw_finding,
                    "status",
                    VALID_FINDING_STATUSES,
                    default="open",
                ),
            )
            session.add(finding)
            created_findings.append(finding)
        session.flush()
        finding_responses = [self._build_finding_response(item, evaluation=evaluation) for item in created_findings]
        self.agent_service.append_event(
            session,
            agent_run_id,
            AppendAgentEventRequest(
                event_type="evaluation.diagnostics.written",
                phase="evaluation",
                payload={
                    "evaluation_id": evaluation.id,
                    "finding_ids": [item.id for item in created_findings],
                    "finding_count": len(created_findings),
                },
            ),
            commit=False,
        )
        if commit:
            session.commit()
        return {
            "evaluation_id": evaluation.id,
            "finding_count": len(created_findings),
            "findings": [item.model_dump(mode="json") for item in finding_responses],
        }

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
            "run_trace_event_types": [item.event_type for item in run_traces],
            "evidence": {
                "last_run_trace": self._trace_ref(run_traces[-1]) if run_traces else None,
                "last_run_event": self._run_event_ref(run_events[-1]) if run_events else None,
                "runtime_failed_run_traces": [
                    self._trace_ref(item) for item in run_traces if item.event_type == "runtime.failed"
                ],
                "recoverable_failure_run_traces": [
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
                    evidence_refs=evidence.get("runtime_failed_run_traces") or [evidence.get("last_run_trace")],
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
                    evidence_refs=[evidence.get("last_run_trace")],
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
                    evidence_refs=[evidence.get("last_run_trace")],
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
                    evidence_refs=evidence.get("recoverable_failure_run_traces") or [],
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
                    evidence_refs=[evidence.get("last_run_trace")],
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

    def _write_evaluation_memory_candidates(
        self,
        session: Session,
        *,
        agent_run_id: str,
        evaluation: RunEvaluation,
        findings: list[RunEvaluationFinding],
        facts: dict[str, Any],
    ) -> list[Any]:
        candidates = self._evaluation_memory_candidates(evaluation=evaluation, findings=findings, facts=facts)
        if not candidates:
            return []
        entries = self.memory_service.write_candidates(
            session,
            agent_key="pskill.evaluator",
            created_by_agent_run_id=agent_run_id,
            candidates=candidates,
            commit=False,
        )
        self.agent_service.append_event(
            session,
            agent_run_id,
            AppendAgentEventRequest(
                event_type="evaluation.memory_candidates.written",
                phase="memory",
                payload={
                    "evaluation_id": evaluation.id,
                    "run_id": evaluation.run_id,
                    "memory_entry_ids": [item.id for item in entries],
                    "memory_entry_count": len(entries),
                    "used_as_runtime_state": False,
                },
            ),
            commit=False,
        )
        return entries

    def _evaluation_memory_candidates(
        self,
        *,
        evaluation: RunEvaluation,
        findings: list[RunEvaluationFinding],
        facts: dict[str, Any],
    ) -> list[dict[str, Any]]:
        fact_counts = facts.get("counts") if isinstance(facts.get("counts"), dict) else {}
        candidates: list[dict[str, Any]] = [
            {
                "namespace": "evaluation",
                "memory_type": "artifact",
                "title": f"Run replay evaluation artifact: {evaluation.run_id}",
                "content": (
                    f"Run {evaluation.run_id} evaluated as {evaluation.overall_outcome} "
                    f"with quality_score={evaluation.quality_score}; "
                    f"snapshots={fact_counts.get('snapshots', 0)}, "
                    f"run_events={fact_counts.get('run_events', 0)}, "
                    f"run_traces={fact_counts.get('run_traces', 0)}, "
                    f"findings={len(findings)}."
                ),
                "confidence": max(0, min(100, evaluation.quality_score)),
                "source_refs": [
                    {"kind": "run_evaluation", "id": evaluation.id},
                    {"kind": "run", "id": evaluation.run_id},
                    {"kind": "eg_compile_artifact", "id": evaluation.artifact_id},
                ],
                "tags": ["evaluation", "replay", evaluation.overall_outcome],
                "metadata": {
                    "schema": "psop-run-evaluation-memory/v1",
                    "run_id": evaluation.run_id,
                    "evaluation_id": evaluation.id,
                    "quality_score": evaluation.quality_score,
                    "overall_outcome": evaluation.overall_outcome,
                    "fact_counts": fact_counts,
                },
            }
        ]
        for finding in findings:
            candidates.append(
                {
                    "namespace": "evaluation",
                    "memory_type": "episodic",
                    "title": f"{finding.category}: {finding.severity} finding for {evaluation.run_id}",
                    "content": (
                        f"{finding.description} Recommended action: {finding.recommended_action} "
                        f"Confidence={finding.confidence}."
                    ),
                    "confidence": finding.confidence,
                    "source_refs": [
                        {"kind": "run_evaluation", "id": evaluation.id},
                        {"kind": "run_evaluation_finding", "id": finding.id},
                        *list(finding.evidence_refs or []),
                    ],
                    "tags": ["evaluation", "finding", finding.category, finding.severity],
                    "metadata": {
                        "schema": "psop-run-evaluation-memory/v1",
                        "run_id": evaluation.run_id,
                        "evaluation_id": evaluation.id,
                        "finding_id": finding.id,
                        "category": finding.category,
                        "severity": finding.severity,
                    },
                }
            )
        return candidates

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
                self._build_finding_response(item, evaluation=evaluation)
                for item in self.repository.list_evaluation_findings(session, evaluation.id)
            ],
            created_at=evaluation.created_at,
        )

    @staticmethod
    def _build_finding_response(
        finding: RunEvaluationFinding,
        *,
        evaluation: RunEvaluation | None = None,
    ) -> RunEvaluationFindingResponse:
        return RunEvaluationFindingResponse(
            id=finding.id,
            evaluation_id=finding.evaluation_id,
            run_id=evaluation.run_id if evaluation else "",
            pskill_definition_id=evaluation.pskill_definition_id if evaluation else "",
            pskill_version_id=evaluation.pskill_version_id if evaluation else "",
            overall_outcome=evaluation.overall_outcome if evaluation else "",
            quality_score=evaluation.quality_score if evaluation else None,
            category=finding.category,
            severity=finding.severity,
            confidence=finding.confidence,
            description=finding.description,
            evidence_refs=finding.evidence_refs,
            recommended_action=finding.recommended_action,
            status=finding.status,
            evaluation_created_at=evaluation.created_at if evaluation else None,
            created_at=finding.created_at,
        )

    @staticmethod
    def _validate_optional_filter(name: str, value: str | None, allowed: set[str]) -> None:
        if value is not None and value not in allowed:
            raise SkillValidationError(f"{name} filter 无效。", details={name: value})

    @staticmethod
    def _validated_finding_field(
        payload: dict[str, Any],
        field_name: str,
        allowed: set[str],
        *,
        default: str,
    ) -> str:
        value = str(payload.get(field_name) or default).strip()
        if value not in allowed:
            raise SkillValidationError(f"{field_name} 无效。", details={field_name: value})
        return value

    @staticmethod
    def _required_finding_text(payload: dict[str, Any], field_name: str, *, fallback_key: str) -> str:
        value = str(payload.get(field_name) or payload.get(fallback_key) or "").strip()
        if not value:
            raise SkillValidationError(f"{field_name} 不能为空。")
        return value

    @staticmethod
    def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
        if value is None or value == "":
            parsed = default
        else:
            try:
                parsed = int(value)
            except (TypeError, ValueError) as error:
                raise SkillValidationError("整数参数无效。", details={"value": value}) from error
        return max(minimum, min(parsed, maximum))
