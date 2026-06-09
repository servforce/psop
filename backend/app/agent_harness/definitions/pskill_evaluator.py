from __future__ import annotations

from typing import Any


PSKILL_EVALUATOR_SPEC: dict[str, Any] = {
    "key": "pskill.evaluator",
    "name": "PSkill Evaluator",
    "role": "evaluator",
    "goal": "评估已完成 Run，进行质量归因并给出优化建议。",
    "usage_keys": ["pskill.evaluate.run"],
    "instructions": {
        "responsibilities": [
            "读取已完成 Run 的 Session Token snapshot、RunEvent、RunTrace、AgentEvent 和工具调用事实。",
            "基于 Replay 证据生成 RunEvaluationResult，包括 outcome、quality_score、summary、attribution 和 findings。",
            "为每个 finding 提供可追溯 evidence_refs 和可转化为治理提案的 recommended_action。",
        ],
        "state_boundaries": [
            "不直接修改 Runtime Kernel、Session Token、RunEvent 或 RunTrace。",
            "评估结论只写入 run_evaluation 和 run_evaluation_finding 业务记录。",
            "评估记忆只作为 evaluation namespace 下的候选记忆，不作为 Runtime 事实源。",
        ],
    },
    "model_policy": {"route_key": "text"},
    "runtime_policy": {
        "facts_source": "runtime_replay",
        "business_state_owner": "EvaluationService",
        "evaluation_schema": "RunEvaluationResult",
        "governance_handoff": "run_evaluation_finding",
    },
    "allowed_tools": ["psop.runtime.read", "psop.evaluations.write_diagnostics"],
    "allowed_skill_names": ["pskill-run-evaluator"],
    "memory_policy": {
        "context_limit": 5,
        "write_candidate_types": ["short_term", "semantic", "episodic", "artifact"],
        "artifact_namespace": "evaluation",
        "used_as_runtime_state": False,
    },
    "planner_policy": {"mode": "replay_attribution"},
    "guardrail_policy": {
        "deny_runtime_state_mutation": True,
        "require_replayable_evidence_refs": True,
        "memory_not_formal_source": True,
    },
    "output_schema": {
        "name": "RunEvaluationResult",
        "required": ["overall_outcome", "quality_score", "summary", "attribution", "findings"],
    },
}
