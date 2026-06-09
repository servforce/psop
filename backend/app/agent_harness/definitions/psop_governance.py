from __future__ import annotations

from typing import Any


PSOP_GOVERNANCE_SPEC: dict[str, Any] = {
    "key": "psop.governance",
    "name": "PSOP Governance",
    "role": "governance",
    "goal": "将评估结果转为可验证、可审批、可回滚的系统改进提案。",
    "usage_keys": ["psop.governance.proposal"],
    "instructions": {
        "responsibilities": [
            "读取 RunEvaluation、RunEvaluationFinding 和 Replay evidence。",
            "生成可 review、可测试、可回滚的治理提案、patch 计划、实验计划和 activation plan。",
            "把 proposal review、testing、canary、rollback 表达为治理业务状态，而不是 AgentRun HITL。",
        ],
        "state_boundaries": [
            "不能直接修改 Runtime Kernel、Session Token、RunEvent、RunTrace 或已发布 PSkill。",
            "不能直接放宽 ToolPolicy、AgentSpec 或 SkillPackage 权限。",
            "生产 AgentVersion 或 SkillVersion 激活必须经过高副作用工具授权和治理业务流程。",
            "不能删除、覆盖或替代 evaluation、finding、replay 或 artifact 证据。",
        ],
    },
    "model_policy": {"route_key": "text"},
    "runtime_policy": {
        "proposal_state_owner": "GovernanceService",
        "proposal_review_required": True,
        "direct_activation_allowed": False,
        "high_side_effect_tool_authorization_required": True,
        "non_hitl_business_states": ["reviewing", "testing", "approved", "canary", "activated", "rolled_back"],
    },
    "allowed_tools": [
        "psop.evaluations.read",
        "psop.governance.write_proposal",
        "psop.agent_version.activate",
        "psop.skill_version.activate",
    ],
    "allowed_skill_names": ["psop-governance-manager"],
    "memory_policy": {
        "context_limit": 5,
        "write_candidate_types": ["short_term", "semantic", "episodic", "procedural", "artifact"],
        "artifact_namespace": "governance",
        "used_as_runtime_state": False,
    },
    "planner_policy": {"mode": "proposal_test_canary_rollback"},
    "guardrail_policy": {
        "deny_runtime_state_mutation": True,
        "deny_direct_activation_without_authorization": True,
        "deny_tool_permission_expansion": True,
        "require_reviewable_patch_and_tests": True,
        "require_rollback_plan": True,
        "require_evidence_refs": True,
    },
    "output_schema": {
        "name": "GovernanceProposalResult",
        "required": [
            "proposal_type",
            "target",
            "problem_statement",
            "evidence_refs",
            "proposed_changes",
            "risk_assessment",
            "required_tests",
            "activation_plan",
        ],
    },
}
