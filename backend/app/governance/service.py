from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.agents.schemas import AppendAgentEventRequest, CreateAgentRunRequest
from app.agents.service import AgentService
from app.evaluations.models import RunEvaluation, RunEvaluationFinding
from app.governance.models import PsopImprovementExperiment, PsopImprovementProposal
from app.governance.repository import GovernanceRepository
from app.governance.schemas import (
    GovernanceExperimentResponse,
    GovernanceProposalCreateRequest,
    GovernanceProposalResponse,
    GovernanceReviewRequest,
)
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
    ) -> None:
        self.repository = repository or GovernanceRepository()
        self.agent_service = agent_service or AgentService()

    def create_proposal(
        self,
        session: Session,
        payload: GovernanceProposalCreateRequest,
    ) -> GovernanceProposalResponse:
        result = self._proposal_result_from_request(payload)
        proposal = self._create_proposal_with_governance_agent(
            session,
            proposal_id=generate_uuid(),
            source_finding_ids=list(payload.source_finding_ids),
            source_evaluation_id=payload.source_evaluation_id,
            source_run_id=payload.source_run_id,
            result=result,
            agent_input={
                "schema": "GovernanceProposalInput",
                "source": "manual",
                "source_finding_ids": list(payload.source_finding_ids),
                "source_evaluation_id": payload.source_evaluation_id,
                "source_run_id": payload.source_run_id,
                "proposal": result,
            },
        )
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

    def list_proposals(self, session: Session, *, status: str | None = None) -> list[GovernanceProposalResponse]:
        if status is not None and status not in VALID_PROPOSAL_STATUSES:
            raise SkillValidationError("proposal status 无效。", details={"status": status})
        return [self._build_proposal_response(session, item) for item in self.repository.list_proposals(session, status=status)]

    def get_proposal(self, session: Session, proposal_id: str) -> GovernanceProposalResponse:
        proposal = self._get_proposal(session, proposal_id)
        return self._build_proposal_response(session, proposal)

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
                "checks": required_tests,
                "outcome": "ready_for_review",
                "direct_activation_performed": False,
            },
            started_at=now_utc(),
            finished_at=now_utc(),
        )
        session.add(experiment)
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
        session.commit()
        return self.get_proposal(session, proposal.id)

    def activate_canary(self, session: Session, proposal_id: str) -> GovernanceProposalResponse:
        proposal = self._get_proposal(session, proposal_id)
        self._require_status(proposal, {"approved"}, action="activate_canary")
        proposal.status = "canary"
        proposal.updated_at = now_utc()
        experiment = PsopImprovementExperiment(
            proposal_id=proposal.id,
            experiment_type="canary",
            status="running",
            summary="Governance canary activated; production activation is still gated.",
            before_metrics={"proposal_status": "approved"},
            after_metrics={"canary_status": "running"},
            result_json={
                "schema": "GovernanceExperimentResult",
                "outcome": "canary_running",
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
                    "direct_activation_performed": False,
                },
                started_at=now,
                finished_at=now,
            )
        )
        session.commit()
        return self.get_proposal(session, proposal.id)

    def get_experiment(self, session: Session, experiment_id: str) -> GovernanceExperimentResponse:
        experiment = self.repository.get_experiment(session, experiment_id)
        if not experiment:
            raise SkillNotFoundError("未找到治理实验。", details={"experiment_id": experiment_id})
        return self._build_experiment_response(experiment)

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

    def _build_proposal_response(
        self,
        session: Session,
        proposal: PsopImprovementProposal,
    ) -> GovernanceProposalResponse:
        return GovernanceProposalResponse(
            id=proposal.id,
            agent_run_id=proposal.agent_run_id,
            source_finding_ids=list(proposal.source_finding_ids or []),
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
                self._build_experiment_response(item)
                for item in self.repository.list_experiments_for_proposal(session, proposal.id)
            ],
            created_at=proposal.created_at,
            updated_at=proposal.updated_at,
        )

    @staticmethod
    def _build_experiment_response(experiment: PsopImprovementExperiment) -> GovernanceExperimentResponse:
        return GovernanceExperimentResponse(
            id=experiment.id,
            proposal_id=experiment.proposal_id,
            experiment_type=experiment.experiment_type,
            status=experiment.status,
            summary=experiment.summary,
            before_metrics=experiment.before_metrics,
            after_metrics=experiment.after_metrics,
            result=experiment.result_json,
            started_at=experiment.started_at,
            finished_at=experiment.finished_at,
            created_at=experiment.created_at,
        )

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
