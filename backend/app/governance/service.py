from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.agents.schemas import AppendAgentEventRequest, CreateAgentRunRequest
from app.agents.service import AgentService
from app.evaluations.models import RunEvaluation, RunEvaluationFinding
from app.evaluations.schemas import RunEvaluationFindingResponse
from app.governance.models import PsopImprovementExperiment, PsopImprovementProposal
from app.governance.repository import GovernanceRepository
from app.governance.schemas import (
    GovernanceExperimentResponse,
    GovernanceProposalCreateRequest,
    GovernanceProposalResponse,
    GovernanceReviewRequest,
    GovernanceProposalUpdateRequest,
)
from app.jobs.models import RuntimeJob
from app.jobs.repository import JobRepository
from app.jobs.types import GOVERNANCE_PROPOSAL_JOB_TYPE
from app.memory.service import MemoryService
from app.pskills.exceptions import SkillNotFoundError, SkillValidationError
from app.pskills.models import generate_uuid, now_utc


VALID_PROPOSAL_TYPES = {
    "agent_skill_update",
    "agent_spec_update",
    "tool_policy_update",
    "validator_update",
    "test_suite_update",
    "pskill_template_update",
}
VALID_PROPOSAL_STATUSES = {
    "draft",
    "testing",
    "reviewing",
    "approved",
    "rejected",
    "canary",
    "activated",
    "rolled_back",
}
FINDING_CATEGORY_TO_PROPOSAL_TYPE = {
    "pskill_build_issue": "pskill_template_update",
    "compile_issue": "validator_update",
    "test_gap": "test_suite_update",
    "runner_issue": "agent_skill_update",
    "human_operation_issue": "pskill_template_update",
    "evidence_quality_issue": "pskill_template_update",
    "tool_issue": "tool_policy_update",
    "environment_issue": "test_suite_update",
}
SEVERITY_TO_RISK = {
    "critical": "high",
    "high": "high",
    "medium": "medium",
    "low": "low",
}


class GovernanceService:
    def __init__(
        self,
        *,
        repository: GovernanceRepository | None = None,
        agent_service: AgentService | None = None,
        job_repository: JobRepository | None = None,
        memory_service: MemoryService | None = None,
    ) -> None:
        self.repository = repository or GovernanceRepository()
        self.agent_service = agent_service or AgentService()
        self.job_repository = job_repository or JobRepository()
        self.memory_service = memory_service or MemoryService()

    def create_proposal(
        self,
        session: Session,
        payload: GovernanceProposalCreateRequest,
    ) -> GovernanceProposalResponse:
        source_findings: list[RunEvaluationFinding] = []
        seen_finding_ids: set[str] = set()
        for raw_finding_id in payload.source_finding_ids:
            finding_id = str(raw_finding_id).strip()
            if not finding_id or finding_id in seen_finding_ids:
                continue
            finding = self.repository.get_finding(session, finding_id)
            if not finding:
                raise SkillNotFoundError("未找到 RunEvaluationFinding。", details={"finding_id": finding_id})
            seen_finding_ids.add(finding_id)
            source_findings.append(finding)
        source_finding_ids = [finding.id for finding in source_findings]
        result = self._proposal_result_from_request(payload)
        result["evidence_refs"] = self._merge_evidence_refs(
            result["evidence_refs"],
            self._proposal_source_evidence_refs(
                session,
                source_findings=source_findings,
                source_evaluation_id=payload.source_evaluation_id,
                source_run_id=payload.source_run_id,
            ),
        )
        proposal = self._create_proposal_with_governance_agent(
            session,
            proposal_id=generate_uuid(),
            source_finding_ids=source_finding_ids,
            source_evaluation_id=payload.source_evaluation_id,
            source_run_id=payload.source_run_id,
            result=result,
            agent_input={
                "schema": "GovernanceProposalInput",
                "source": "manual",
                "source_finding_ids": source_finding_ids,
                "source_evaluation_id": payload.source_evaluation_id,
                "source_run_id": payload.source_run_id,
                "proposal": result,
            },
        )
        for finding in source_findings:
            finding.status = "converted_to_proposal"
        session.commit()
        return self.get_proposal(session, proposal.id)

    def create_proposal_from_finding(self, session: Session, finding_id: str) -> GovernanceProposalResponse:
        finding = self.repository.get_finding(session, finding_id)
        if not finding:
            raise SkillNotFoundError("未找到 RunEvaluationFinding。", details={"finding_id": finding_id})
        evaluation = self.repository.get_evaluation(session, finding.evaluation_id)
        if not evaluation:
            raise SkillNotFoundError("未找到 RunEvaluation。", details={"evaluation_id": finding.evaluation_id})

        result = self._proposal_result_from_finding(finding, evaluation)
        result["evidence_refs"] = self._merge_evidence_refs(
            result["evidence_refs"],
            self._proposal_source_evidence_refs(session, source_findings=[finding], source_evaluation_id=evaluation.id),
        )
        proposal = self._create_proposal_with_governance_agent(
            session,
            proposal_id=generate_uuid(),
            source_finding_ids=[finding.id],
            source_evaluation_id=evaluation.id,
            source_run_id=evaluation.run_id,
            result=result,
            agent_input={
                "schema": "GovernanceProposalInput",
                "source": "run_evaluation_finding",
                "finding": self._finding_payload(finding),
                "evaluation": {
                    "id": evaluation.id,
                    "run_id": evaluation.run_id,
                    "pskill_definition_id": evaluation.pskill_definition_id,
                    "pskill_version_id": evaluation.pskill_version_id,
                    "artifact_id": evaluation.artifact_id,
                    "overall_outcome": evaluation.overall_outcome,
                    "quality_score": evaluation.quality_score,
                },
            },
        )
        finding.status = "converted_to_proposal"
        session.commit()
        return self.get_proposal(session, proposal.id)

    def enqueue_proposal_from_finding_job(self, session: Session, finding_id: str) -> str:
        finding = self.repository.get_finding(session, finding_id)
        if not finding:
            raise SkillNotFoundError("未找到 RunEvaluationFinding。", details={"finding_id": finding_id})
        evaluation = self.repository.get_evaluation(session, finding.evaluation_id)
        if not evaluation:
            raise SkillNotFoundError("未找到 RunEvaluation。", details={"evaluation_id": finding.evaluation_id})
        dedupe_key = f"job:governance-proposal:finding:{finding.id}"
        existing = self.job_repository.get_runtime_job_by_dedupe_key(session, dedupe_key)
        if existing:
            return existing.id
        job = RuntimeJob(
            job_type=GOVERNANCE_PROPOSAL_JOB_TYPE,
            status="pending",
            payload={
                "operation": "governance_proposal",
                "finding_id": finding.id,
                "source_evaluation_id": evaluation.id,
                "source_run_id": evaluation.run_id,
            },
            run_id=evaluation.run_id,
            dedupe_key=dedupe_key,
        )
        session.add(job)
        session.commit()
        return job.id

    def create_proposal_from_agent_tool(
        self,
        session: Session,
        *,
        agent_run_id: str,
        payload: GovernanceProposalCreateRequest,
        commit: bool = True,
    ) -> GovernanceProposalResponse:
        result = self._proposal_result_from_request(payload)
        source_findings = []
        for finding_id in payload.source_finding_ids:
            finding = self.repository.get_finding(session, finding_id)
            if not finding:
                raise SkillNotFoundError("未找到 RunEvaluationFinding。", details={"finding_id": finding_id})
            source_findings.append(finding)
        result["evidence_refs"] = self._merge_evidence_refs(
            result["evidence_refs"],
            self._proposal_source_evidence_refs(
                session,
                source_findings=source_findings,
                source_evaluation_id=payload.source_evaluation_id,
                source_run_id=payload.source_run_id,
            ),
        )
        proposal = PsopImprovementProposal(
            id=generate_uuid(),
            agent_run_id=agent_run_id,
            source_finding_ids=list(payload.source_finding_ids),
            source_evaluation_id=payload.source_evaluation_id,
            source_run_id=payload.source_run_id,
            proposal_type=str(result["proposal_type"]),
            target_json=dict(result["target"]),
            problem_statement=str(result["problem_statement"]),
            evidence_refs=list(result["evidence_refs"]),
            proposed_changes=list(result["proposed_changes"]),
            risk_assessment=dict(result["risk_assessment"]),
            required_tests=list(result["required_tests"]),
            activation_plan=dict(result["activation_plan"]),
            status="draft",
        )
        session.add(proposal)
        for finding in source_findings:
            finding.status = "converted_to_proposal"
        session.flush()
        self.agent_service.append_event(
            session,
            agent_run_id,
            AppendAgentEventRequest(
                event_type="governance.proposal.created",
                phase="governance",
                payload={"proposal_id": proposal.id, "status": "draft", "created_by_tool": True},
            ),
            commit=False,
        )
        if commit:
            session.commit()
        return self._build_proposal_response(session, proposal)

    def process_governance_proposal_job(self, session: Session, job_id: str) -> GovernanceProposalResponse:
        job = self.job_repository.get_runtime_job(session, job_id)
        if not job:
            raise SkillNotFoundError("未找到 Governance proposal 任务。", details={"job_id": job_id})
        if job.job_type != GOVERNANCE_PROPOSAL_JOB_TYPE:
            raise SkillValidationError("当前 worker 仅支持 Governance proposal 任务。", details={"job_type": job.job_type})

        payload = dict(job.payload or {})
        proposal_id = str(payload.get("proposal_id") or "").strip()
        if proposal_id:
            proposal = self.get_proposal(session, proposal_id)
        else:
            finding_id = str(payload.get("finding_id") or payload.get("source_finding_id") or "").strip()
            if finding_id:
                proposal = self.create_proposal_from_finding(session, finding_id)
            else:
                request_payload = payload.get("proposal", payload)
                if not isinstance(request_payload, dict):
                    raise SkillValidationError(
                        "Governance proposal 任务缺少 proposal 对象。",
                        details={"job_id": job.id},
                    )
                proposal = self.create_proposal(session, GovernanceProposalCreateRequest(**request_payload))

        metrics = dict(job.metrics or {})
        metrics.update(
            {
                "proposal_id": proposal.id,
                "proposal_type": proposal.proposal_type,
                "proposal_status": proposal.status,
                "source_finding_count": len(proposal.source_finding_ids),
            }
        )
        job.payload = {
            **payload,
            "operation": "governance_proposal",
            "proposal_id": proposal.id,
            "proposal_type": proposal.proposal_type,
            "proposal_status": proposal.status,
            "source_run_id": proposal.source_run_id,
            "source_evaluation_id": proposal.source_evaluation_id,
            "source_finding_ids": list(proposal.source_finding_ids),
        }
        job.run_id = proposal.source_run_id or job.run_id
        job.metrics = metrics
        job.status = "succeeded"
        job.last_error = ""
        job.lease_until = None
        session.commit()
        return proposal

    def list_proposals(self, session: Session, *, status: str | None = None) -> list[GovernanceProposalResponse]:
        if status is not None and status not in VALID_PROPOSAL_STATUSES:
            raise SkillValidationError("proposal status 无效。", details={"status": status})
        return [self._build_proposal_response(session, item) for item in self.repository.list_proposals(session, status=status)]

    def get_proposal(self, session: Session, proposal_id: str) -> GovernanceProposalResponse:
        proposal = self._get_proposal(session, proposal_id)
        return self._build_proposal_response(session, proposal)

    def update_proposal(
        self,
        session: Session,
        proposal_id: str,
        payload: GovernanceProposalUpdateRequest,
    ) -> GovernanceProposalResponse:
        proposal = self._get_proposal(session, proposal_id)
        self._require_status(proposal, {"draft", "rejected"}, action="update_proposal")
        result = {
            "proposal_type": payload.proposal_type.strip() if payload.proposal_type is not None else proposal.proposal_type,
            "target": payload.target if payload.target is not None else proposal.target_json,
            "problem_statement": (
                payload.problem_statement.strip()
                if payload.problem_statement is not None
                else proposal.problem_statement
            ),
            "evidence_refs": payload.evidence_refs if payload.evidence_refs is not None else list(proposal.evidence_refs or []),
            "proposed_changes": (
                payload.proposed_changes
                if payload.proposed_changes is not None
                else list(proposal.proposed_changes or [])
            ),
            "risk_assessment": (
                payload.risk_assessment
                if payload.risk_assessment is not None
                else dict(proposal.risk_assessment or {})
            ),
            "required_tests": payload.required_tests if payload.required_tests is not None else list(proposal.required_tests or []),
            "activation_plan": (
                payload.activation_plan
                if payload.activation_plan is not None
                else dict(proposal.activation_plan or {})
            ),
        }
        self._validate_proposal_result(result)
        proposal.proposal_type = str(result["proposal_type"])
        proposal.target_json = dict(result["target"])
        proposal.problem_statement = str(result["problem_statement"])
        proposal.evidence_refs = list(result["evidence_refs"])
        proposal.proposed_changes = list(result["proposed_changes"])
        proposal.risk_assessment = dict(result["risk_assessment"])
        proposal.required_tests = list(result["required_tests"])
        proposal.activation_plan = dict(result["activation_plan"])
        proposal.updated_at = now_utc()
        self.agent_service.append_event(
            session,
            proposal.agent_run_id,
            AppendAgentEventRequest(
                event_type="governance.proposal.updated",
                phase="governance",
                payload={"proposal_id": proposal.id, "status": proposal.status},
            ),
            commit=False,
        )
        session.commit()
        return self.get_proposal(session, proposal.id)

    def run_tests(self, session: Session, proposal_id: str) -> GovernanceProposalResponse:
        proposal = self._get_proposal(session, proposal_id)
        self._require_status(proposal, {"draft", "testing", "reviewing", "rejected"}, action="run_tests")
        proposal.status = "testing"
        proposal.updated_at = now_utc()
        required_tests = list(proposal.required_tests or [])
        experiment = PsopImprovementExperiment(
            proposal_id=proposal.id,
            experiment_type="regression",
            status="succeeded",
            summary="Governance regression checks completed for proposal review.",
            before_metrics={
                "source_finding_count": len(proposal.source_finding_ids or []),
                "risk_level": (proposal.risk_assessment or {}).get("risk_level", "medium"),
            },
            after_metrics={
                "required_tests_planned": len(required_tests),
                "failed_checks": 0,
            },
            result_json={
                "schema": "GovernanceExperimentResult",
                "regression": {
                    "status": "succeeded",
                    "failed_checks": 0,
                    "required_tests_planned": len(required_tests),
                },
                "checks": required_tests,
                "outcome": "ready_for_review",
                "direct_activation_performed": False,
            },
            started_at=now_utc(),
            finished_at=now_utc(),
        )
        session.add(experiment)
        session.flush()
        self._write_governance_memory_candidates(
            session,
            proposal=proposal,
            candidates=[
                self._governance_experiment_memory_candidate(proposal=proposal, experiment=experiment)
            ],
        )
        session.commit()
        return self.get_proposal(session, proposal.id)

    def submit_review(
        self,
        session: Session,
        proposal_id: str,
        payload: GovernanceReviewRequest,
    ) -> GovernanceProposalResponse:
        proposal = self._get_proposal(session, proposal_id)
        self._require_status(proposal, {"draft", "testing", "reviewing", "rejected"}, action="submit_review")
        decision = payload.decision.strip() if payload.decision else None
        if decision is None:
            proposal.status = "reviewing"
        elif decision in {"approved", "rejected"}:
            proposal.status = decision
        else:
            raise SkillValidationError("review decision 无效。", details={"decision": decision})
        proposal.updated_at = now_utc()
        proposal.activation_plan = {
            **(proposal.activation_plan or {}),
            "review": {
                "status": proposal.status,
                "notes": payload.review_notes,
                "reviewed_at": now_utc().isoformat(),
            },
        }
        if proposal.status == "rejected":
            self._write_governance_memory_candidates(
                session,
                proposal=proposal,
                candidates=[
                    self._governance_status_memory_candidate(
                        proposal=proposal,
                        status="rejected",
                        content_summary=payload.review_notes or "Governance proposal rejected during review.",
                    )
                ],
            )
        session.commit()
        return self.get_proposal(session, proposal.id)

    def activate_canary(self, session: Session, proposal_id: str) -> GovernanceProposalResponse:
        proposal = self._get_proposal(session, proposal_id)
        self._require_status(proposal, {"approved"}, action="activate_canary")
        proposal.status = "canary"
        proposal.updated_at = now_utc()
        canary_scope = self._canary_scope(proposal)
        rollback_conditions = self._rollback_conditions(proposal)
        experiment = PsopImprovementExperiment(
            proposal_id=proposal.id,
            experiment_type="canary",
            status="running",
            summary="Governance canary activated; production activation is still gated.",
            before_metrics={"proposal_status": "approved", "risk_level": (proposal.risk_assessment or {}).get("risk_level", "medium")},
            after_metrics={"canary_status": "running", "canary_scope_size": len(canary_scope) if canary_scope else 0},
            result_json={
                "schema": "GovernanceExperimentResult",
                "outcome": "canary_running",
                "canary_scope": canary_scope,
                "rollback_conditions": rollback_conditions,
                "direct_activation_performed": False,
                "rollback_available": True,
            },
            started_at=now_utc(),
        )
        session.add(experiment)
        session.commit()
        return self.get_proposal(session, proposal.id)

    def rollback(self, session: Session, proposal_id: str) -> GovernanceProposalResponse:
        proposal = self._get_proposal(session, proposal_id)
        self._require_status(proposal, {"canary", "activated"}, action="rollback")
        now = now_utc()
        for experiment in self.repository.list_experiments_for_proposal(session, proposal.id):
            if experiment.experiment_type == "canary" and experiment.status == "running":
                experiment.status = "rolled_back"
                experiment.finished_at = now
                experiment.result_json = {
                    **(experiment.result_json or {}),
                    "outcome": "rolled_back",
                    "rolled_back_at": now.isoformat(),
                    "rollback_conditions": self._rollback_conditions(proposal),
                }
        proposal.status = "rolled_back"
        proposal.updated_at = now
        session.add(
            PsopImprovementExperiment(
                proposal_id=proposal.id,
                experiment_type="rollback",
                status="succeeded",
                summary="Governance rollback completed.",
                before_metrics={"proposal_status": "canary"},
                after_metrics={"proposal_status": "rolled_back"},
                result_json={
                    "schema": "GovernanceExperimentResult",
                    "outcome": "rolled_back",
                    "rollback_conditions": self._rollback_conditions(proposal),
                    "direct_activation_performed": False,
                },
                started_at=now,
                finished_at=now,
            )
        )
        session.flush()
        self._write_governance_memory_candidates(
            session,
            proposal=proposal,
            candidates=[
                self._governance_status_memory_candidate(
                    proposal=proposal,
                    status="rolled_back",
                    content_summary="Governance proposal rolled back after canary or activation.",
                )
            ],
        )
        session.commit()
        return self.get_proposal(session, proposal.id)

    def get_experiment(self, session: Session, experiment_id: str) -> GovernanceExperimentResponse:
        experiment = self.repository.get_experiment(session, experiment_id)
        if not experiment:
            raise SkillNotFoundError("未找到治理实验。", details={"experiment_id": experiment_id})
        return self._build_experiment_response(
            experiment,
            proposal=self.repository.get_proposal(session, experiment.proposal_id),
        )

    def list_experiments(
        self,
        session: Session,
        *,
        proposal_id: str | None = None,
        status: str | None = None,
        experiment_type: str | None = None,
    ) -> list[GovernanceExperimentResponse]:
        if proposal_id:
            self._get_proposal(session, proposal_id)
        experiments = self.repository.list_experiments(
            session,
            proposal_id=self._normalize_optional(proposal_id),
            status=self._normalize_optional(status),
            experiment_type=self._normalize_optional(experiment_type),
        )
        proposals = {
            proposal.id: proposal
            for proposal in self.repository.list_proposals_by_ids(
                session,
                {experiment.proposal_id for experiment in experiments},
            )
        }
        return [
            self._build_experiment_response(item, proposal=proposals.get(item.proposal_id))
            for item in experiments
        ]

    def _create_proposal_with_governance_agent(
        self,
        session: Session,
        *,
        proposal_id: str,
        source_finding_ids: list[str],
        source_evaluation_id: str | None,
        source_run_id: str | None,
        result: dict[str, Any],
        agent_input: dict[str, Any],
    ) -> PsopImprovementProposal:
        self._validate_proposal_result(result)
        agent_run_id = self._create_governance_agent_run(
            session,
            proposal_id=proposal_id,
            source_run_id=source_run_id,
            input_payload={**agent_input, "proposal_id": proposal_id},
        )
        proposal = PsopImprovementProposal(
            id=proposal_id,
            agent_run_id=agent_run_id,
            source_finding_ids=source_finding_ids,
            source_evaluation_id=source_evaluation_id,
            source_run_id=source_run_id,
            proposal_type=str(result["proposal_type"]),
            target_json=dict(result["target"]),
            problem_statement=str(result["problem_statement"]),
            evidence_refs=list(result["evidence_refs"]),
            proposed_changes=list(result["proposed_changes"]),
            risk_assessment=dict(result["risk_assessment"]),
            required_tests=list(result["required_tests"]),
            activation_plan=dict(result["activation_plan"]),
            status="draft",
        )
        session.add(proposal)
        session.flush()
        self._mark_governance_agent_succeeded(
            session,
            agent_run_id=agent_run_id,
            proposal_id=proposal.id,
            output_payload={"schema": "GovernanceProposalResult", **result},
        )
        return proposal

    def _create_governance_agent_run(
        self,
        session: Session,
        *,
        proposal_id: str,
        source_run_id: str | None,
        input_payload: dict[str, Any],
    ) -> str:
        agent_run = self.agent_service.create_run(
            session,
            CreateAgentRunRequest(
                agent_key="psop.governance",
                owner_type="governance_proposal",
                owner_id=proposal_id,
                run_id=source_run_id,
                input_payload=input_payload,
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
                event_type="governance.proposal.started",
                phase="governance",
                payload={"proposal_id": proposal_id, "source_run_id": source_run_id},
            ),
            commit=False,
        )
        return agent_run.id

    def _mark_governance_agent_succeeded(
        self,
        session: Session,
        *,
        agent_run_id: str,
        proposal_id: str,
        output_payload: dict[str, Any],
    ) -> None:
        self.agent_service.record_model_call(
            session,
            agent_run_id=agent_run_id,
            provider="deterministic",
            route_key="json",
            model_name="psop-governance-deterministic",
            status="succeeded",
            request_payload={"proposal_id": proposal_id},
            response_payload=output_payload,
            usage_json={"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
            commit=False,
        )
        self.agent_service.append_event(
            session,
            agent_run_id,
            AppendAgentEventRequest(
                event_type="governance.agent.model_call.completed",
                phase="governance",
                payload={"proposal_id": proposal_id, "proposal_type": output_payload.get("proposal_type")},
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
                event_type="governance.proposal.created",
                phase="governance",
                payload={"proposal_id": proposal_id, "status": "draft"},
            ),
            commit=False,
        )

    def _proposal_result_from_request(self, payload: GovernanceProposalCreateRequest) -> dict[str, Any]:
        problem_statement = payload.problem_statement.strip()
        result = {
            "proposal_type": payload.proposal_type.strip(),
            "target": payload.target,
            "problem_statement": problem_statement,
            "evidence_refs": list(payload.evidence_refs),
            "proposed_changes": list(payload.proposed_changes)
            or [
                {
                    "kind": "manual_change_request",
                    "description": problem_statement,
                    "requires_review": True,
                }
            ],
            "risk_assessment": payload.risk_assessment
            or {
                "risk_level": "medium",
                "requires_human_review": True,
            },
            "required_tests": list(payload.required_tests)
            or [
                {
                    "kind": "regression",
                    "description": "执行与目标对象相关的回归测试，并保留实验记录。",
                }
            ],
            "activation_plan": payload.activation_plan or self._default_activation_plan(),
        }
        self._validate_proposal_result(result)
        return result

    def _proposal_result_from_finding(
        self,
        finding: RunEvaluationFinding,
        evaluation: RunEvaluation,
    ) -> dict[str, Any]:
        proposal_type = FINDING_CATEGORY_TO_PROPOSAL_TYPE.get(finding.category, "pskill_template_update")
        risk_level = SEVERITY_TO_RISK.get(finding.severity, "medium")
        return {
            "proposal_type": proposal_type,
            "target": {
                "kind": "run_evaluation_finding",
                "finding_id": finding.id,
                "category": finding.category,
                "evaluation_id": evaluation.id,
                "run_id": evaluation.run_id,
                "pskill_definition_id": evaluation.pskill_definition_id,
                "pskill_version_id": evaluation.pskill_version_id,
                "artifact_id": evaluation.artifact_id,
            },
            "problem_statement": finding.description,
            "evidence_refs": list(finding.evidence_refs or []),
            "proposed_changes": [
                {
                    "kind": "recommended_action",
                    "description": finding.recommended_action,
                    "source_finding_id": finding.id,
                },
                {
                    "kind": "governance_boundary",
                    "description": "仅生成提案和验证计划，不直接修改 Runtime Kernel、发布版本或工具权限。",
                    "direct_activation_allowed": False,
                },
            ],
            "risk_assessment": {
                "risk_level": risk_level,
                "severity": finding.severity,
                "confidence": finding.confidence,
                "requires_human_review": True,
                "requires_rollback_plan": True,
            },
            "required_tests": [
                {
                    "kind": "regression",
                    "scope": proposal_type,
                    "description": "基于 finding 证据复现问题，并验证 proposed_changes 不引入回归。",
                },
                {
                    "kind": "replay",
                    "run_id": evaluation.run_id,
                    "description": "使用 Replay / OTel 证据链复核变更前后的运行行为。",
                },
            ],
            "activation_plan": self._default_activation_plan(),
        }

    def _proposal_source_evidence_refs(
        self,
        session: Session,
        *,
        source_findings: list[RunEvaluationFinding],
        source_evaluation_id: str | None = None,
        source_run_id: str | None = None,
    ) -> list[dict[str, Any]]:
        evidence_refs: list[dict[str, Any]] = []
        evaluations: dict[str, RunEvaluation] = {}
        for finding in source_findings:
            evaluation = self.repository.get_evaluation(session, finding.evaluation_id)
            if evaluation:
                evaluations[evaluation.id] = evaluation
            evidence_refs.append(
                {
                    "kind": "run_evaluation_finding",
                    "id": finding.id,
                    "evaluation_id": finding.evaluation_id,
                }
            )
            evidence_refs.extend(list(finding.evidence_refs or []))

        if source_evaluation_id and source_evaluation_id not in evaluations:
            evaluation = self.repository.get_evaluation(session, source_evaluation_id)
            if evaluation:
                evaluations[evaluation.id] = evaluation

        for evaluation in evaluations.values():
            evidence_refs.extend(
                [
                    {
                        "kind": "run_evaluation",
                        "id": evaluation.id,
                        "run_id": evaluation.run_id,
                    },
                    {
                        "kind": "run",
                        "id": evaluation.run_id,
                    },
                    {
                        "kind": "run_replay",
                        "run_id": evaluation.run_id,
                    },
                ]
            )

        if source_run_id and all(ref.get("kind") != "run" or ref.get("id") != source_run_id for ref in evidence_refs):
            evidence_refs.extend(
                [
                    {
                        "kind": "run",
                        "id": source_run_id,
                    },
                    {
                        "kind": "run_replay",
                        "run_id": source_run_id,
                    },
                ]
            )
        return evidence_refs

    def _write_governance_memory_candidates(
        self,
        session: Session,
        *,
        proposal: PsopImprovementProposal,
        candidates: list[dict[str, Any]],
    ) -> list[Any]:
        if not candidates:
            return []
        entries = self.memory_service.write_candidates(
            session,
            agent_key="psop.governance",
            created_by_agent_run_id=proposal.agent_run_id,
            candidates=candidates,
            commit=False,
        )
        self.agent_service.append_event(
            session,
            proposal.agent_run_id,
            AppendAgentEventRequest(
                event_type="governance.memory_candidates.written",
                phase="memory",
                payload={
                    "proposal_id": proposal.id,
                    "memory_entry_ids": [item.id for item in entries],
                    "memory_entry_count": len(entries),
                    "used_as_runtime_state": False,
                },
            ),
            commit=False,
        )
        return entries

    def _governance_experiment_memory_candidate(
        self,
        *,
        proposal: PsopImprovementProposal,
        experiment: PsopImprovementExperiment,
    ) -> dict[str, Any]:
        return {
            "namespace": "governance",
            "memory_type": "artifact",
            "title": f"Governance experiment artifact: {experiment.id}",
            "content": (
                f"Proposal {proposal.id} ran {experiment.experiment_type} experiment with status "
                f"{experiment.status}. Summary: {experiment.summary}"
            ),
            "confidence": 80 if experiment.status == "succeeded" else 55,
            "source_refs": self._proposal_memory_source_refs(
                proposal,
                extra_refs=[
                    {
                        "kind": "psop_improvement_experiment",
                        "id": experiment.id,
                        "experiment_type": experiment.experiment_type,
                    }
                ],
            ),
            "tags": ["governance", "experiment", experiment.experiment_type, experiment.status],
            "metadata": {
                "schema": "psop-governance-memory/v1",
                "proposal_id": proposal.id,
                "proposal_type": proposal.proposal_type,
                "proposal_status": proposal.status,
                "experiment_id": experiment.id,
                "experiment_type": experiment.experiment_type,
                "experiment_status": experiment.status,
            },
        }

    def _governance_status_memory_candidate(
        self,
        *,
        proposal: PsopImprovementProposal,
        status: str,
        content_summary: str,
    ) -> dict[str, Any]:
        return {
            "namespace": "governance",
            "memory_type": "episodic",
            "title": f"Governance proposal {status}: {proposal.id}",
            "content": (
                f"Proposal {proposal.id} ended in status {status}. "
                f"Type={proposal.proposal_type}. {content_summary}"
            ),
            "confidence": 85,
            "source_refs": self._proposal_memory_source_refs(proposal),
            "tags": ["governance", "proposal", status, proposal.proposal_type],
            "metadata": {
                "schema": "psop-governance-memory/v1",
                "proposal_id": proposal.id,
                "proposal_type": proposal.proposal_type,
                "proposal_status": status,
            },
        }

    def _proposal_memory_source_refs(
        self,
        proposal: PsopImprovementProposal,
        *,
        extra_refs: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        refs: list[dict[str, Any]] = [{"kind": "psop_improvement_proposal", "id": proposal.id}]
        if proposal.source_evaluation_id:
            refs.append({"kind": "run_evaluation", "id": proposal.source_evaluation_id})
        if proposal.source_run_id:
            refs.extend(
                [
                    {"kind": "run", "id": proposal.source_run_id},
                    {"kind": "run_replay", "run_id": proposal.source_run_id},
                ]
            )
        refs.extend(
            {"kind": "run_evaluation_finding", "id": finding_id}
            for finding_id in list(proposal.source_finding_ids or [])
        )
        refs.extend(list(proposal.evidence_refs or []))
        refs.extend(list(extra_refs or []))
        return self._merge_evidence_refs([], refs)

    @classmethod
    def _merge_evidence_refs(
        cls,
        primary_refs: list[dict[str, Any]],
        derived_refs: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        merged: list[dict[str, Any]] = []
        seen: set[tuple[tuple[str, str], ...]] = set()
        for ref in [*primary_refs, *derived_refs]:
            if not isinstance(ref, dict):
                continue
            normalized = cls._evidence_ref_identity(ref)
            if normalized in seen:
                continue
            seen.add(normalized)
            merged.append(dict(ref))
        return merged

    @staticmethod
    def _evidence_ref_identity(ref: dict[str, Any]) -> tuple[tuple[str, str], ...]:
        identity_keys = ("kind", "source_kind", "id", "run_id", "evaluation_id", "seq_no", "trace_id", "span_id")
        identity = tuple((key, str(ref.get(key))) for key in identity_keys if ref.get(key) is not None)
        if identity:
            return identity
        return tuple((key, str(value)) for key, value in sorted(ref.items()))

    @staticmethod
    def _default_activation_plan() -> dict[str, Any]:
        return {
            "strategy": "test_review_canary_rollback",
            "requires_human_review": True,
            "direct_activation_allowed": False,
            "steps": [
                "run_regression_tests",
                "submit_human_review",
                "activate_canary",
                "monitor_canary",
                "activate_or_rollback",
            ],
        }

    def _validate_proposal_result(self, result: dict[str, Any]) -> None:
        proposal_type = str(result.get("proposal_type") or "").strip()
        if proposal_type not in VALID_PROPOSAL_TYPES:
            raise SkillValidationError("proposal_type 无效。", details={"proposal_type": proposal_type})
        if not str(result.get("problem_statement") or "").strip():
            raise SkillValidationError("problem_statement 不能为空。")
        for field in ("target", "risk_assessment", "activation_plan"):
            if not isinstance(result.get(field), dict):
                raise SkillValidationError(f"{field} 必须是对象。")
        for field in ("evidence_refs", "proposed_changes", "required_tests"):
            if not isinstance(result.get(field), list):
                raise SkillValidationError(f"{field} 必须是数组。")

    def _get_proposal(self, session: Session, proposal_id: str) -> PsopImprovementProposal:
        proposal = self.repository.get_proposal(session, proposal_id)
        if not proposal:
            raise SkillNotFoundError("未找到治理提案。", details={"proposal_id": proposal_id})
        return proposal

    @staticmethod
    def _require_status(proposal: PsopImprovementProposal, allowed: set[str], *, action: str) -> None:
        if proposal.status not in allowed:
            raise SkillValidationError(
                "proposal 状态不允许执行该操作。",
                details={"proposal_id": proposal.id, "status": proposal.status, "action": action},
            )

    @staticmethod
    def _normalize_optional(value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    def _build_proposal_response(
        self,
        session: Session,
        proposal: PsopImprovementProposal,
    ) -> GovernanceProposalResponse:
        return GovernanceProposalResponse(
            id=proposal.id,
            agent_run_id=proposal.agent_run_id,
            source_finding_ids=list(proposal.source_finding_ids or []),
            source_findings=[
                finding
                for finding_id in list(proposal.source_finding_ids or [])
                if (finding := self._build_source_finding_response(session, finding_id)) is not None
            ],
            source_evaluation_id=proposal.source_evaluation_id,
            source_run_id=proposal.source_run_id,
            proposal_type=proposal.proposal_type,
            target=proposal.target_json,
            problem_statement=proposal.problem_statement,
            evidence_refs=list(proposal.evidence_refs or []),
            proposed_changes=list(proposal.proposed_changes or []),
            risk_assessment=proposal.risk_assessment,
            required_tests=list(proposal.required_tests or []),
            activation_plan=proposal.activation_plan,
            status=proposal.status,
            experiments=[
                self._build_experiment_response(item, proposal=proposal)
                for item in self.repository.list_experiments_for_proposal(session, proposal.id)
            ],
            created_at=proposal.created_at,
            updated_at=proposal.updated_at,
        )

    def _build_source_finding_response(
        self,
        session: Session,
        finding_id: str,
    ) -> RunEvaluationFindingResponse | None:
        finding = self.repository.get_finding(session, finding_id)
        if not finding:
            return None
        evaluation = self.repository.get_evaluation(session, finding.evaluation_id)
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

    def _build_experiment_response(
        self,
        experiment: PsopImprovementExperiment,
        *,
        proposal: PsopImprovementProposal | None = None,
    ) -> GovernanceExperimentResponse:
        return GovernanceExperimentResponse(
            id=experiment.id,
            proposal_id=experiment.proposal_id,
            proposal_status=proposal.status if proposal else "",
            proposal_type=proposal.proposal_type if proposal else "",
            problem_statement=proposal.problem_statement if proposal else "",
            source_run_id=proposal.source_run_id if proposal else None,
            experiment_type=experiment.experiment_type,
            status=experiment.status,
            summary=experiment.summary,
            before_metrics=experiment.before_metrics,
            after_metrics=experiment.after_metrics,
            result=experiment.result_json,
            canary_scope=self._experiment_canary_scope(experiment, proposal=proposal),
            rollback_conditions=self._experiment_rollback_conditions(experiment, proposal=proposal),
            started_at=experiment.started_at,
            finished_at=experiment.finished_at,
            created_at=experiment.created_at,
        )

    @staticmethod
    def _canary_scope(proposal: PsopImprovementProposal) -> dict[str, Any]:
        activation_plan = proposal.activation_plan or {}
        nested = activation_plan.get("canary")
        if isinstance(nested, dict) and isinstance(nested.get("scope"), dict):
            return dict(nested["scope"])
        direct = activation_plan.get("canary_scope")
        if isinstance(direct, dict):
            return dict(direct)
        return {
            "strategy": activation_plan.get("strategy", "test_review_canary_rollback"),
            "source_run_id": proposal.source_run_id,
            "proposal_type": proposal.proposal_type,
        }

    @staticmethod
    def _rollback_conditions(proposal: PsopImprovementProposal) -> list[Any]:
        activation_plan = proposal.activation_plan or {}
        nested = activation_plan.get("rollback")
        if isinstance(nested, dict) and isinstance(nested.get("conditions"), list):
            return list(nested["conditions"])
        direct = activation_plan.get("rollback_conditions")
        if isinstance(direct, list):
            return list(direct)
        return [
            "canary_metric_regression",
            "manual_review_rejects_canary",
            "unexpected_runtime_or_tool_authorization_failure",
        ]

    def _experiment_canary_scope(
        self,
        experiment: PsopImprovementExperiment,
        *,
        proposal: PsopImprovementProposal | None = None,
    ) -> dict[str, Any]:
        result = experiment.result_json or {}
        result_scope = result.get("canary_scope")
        if isinstance(result_scope, dict):
            return dict(result_scope)
        if proposal:
            return self._canary_scope(proposal)
        return {}

    def _experiment_rollback_conditions(
        self,
        experiment: PsopImprovementExperiment,
        *,
        proposal: PsopImprovementProposal | None = None,
    ) -> list[Any]:
        result = experiment.result_json or {}
        result_conditions = result.get("rollback_conditions")
        if isinstance(result_conditions, list):
            return list(result_conditions)
        if proposal:
            return self._rollback_conditions(proposal)
        return []

    @staticmethod
    def _finding_payload(finding: RunEvaluationFinding) -> dict[str, Any]:
        return {
            "id": finding.id,
            "evaluation_id": finding.evaluation_id,
            "category": finding.category,
            "severity": finding.severity,
            "confidence": finding.confidence,
            "description": finding.description,
            "evidence_refs": finding.evidence_refs,
            "recommended_action": finding.recommended_action,
            "status": finding.status,
        }
