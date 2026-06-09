from __future__ import annotations

from typing import Any


PSKILL_RUNNER_SPEC: dict[str, Any] = {
    "key": "pskill.runner",
    "name": "PSkill Runner",
    "role": "runner",
    "goal": "在 RuntimeService 主权边界内为运行节点生成 observation。",
    "usage_keys": ["pskill.run.node"],
    "instructions": {
        "responsibilities": [
            "读取 RuntimeService 提供的 Session Token、RunEvent、RunTrace 和当前 EG node 上下文。",
            "生成 RuntimeAgentObservation，协助 RuntimeService 判断 proceed、retry、need_more_evidence、abort 或 complete。",
            "给出 terminal_message、facts、evidence_refs 与 safety_flags，但不直接修改正式运行状态。",
        ],
        "state_boundaries": [
            "不能直接写 run.status、session_token_snapshot、run_event、run_event_part 或 run_trace。",
            "只能通过 RuntimeAgentObservation 返回建议，由 RuntimeService merge 并写正式状态。",
            "Memory context 只能作为非正式参考，不可替代 Session Token、RunEvent 或 RunTrace。",
        ],
    },
    "model_policy": {"route_key": "text"},
    "runtime_policy": {
        "state_sovereign": "RuntimeService",
        "observation_schema": "RuntimeAgentObservation",
        "allowed_decisions": ["proceed", "retry", "need_more_evidence", "abort", "complete"],
    },
    "allowed_tools": ["psop.runtime.read"],
    "allowed_skill_names": [
        "pskill-runner-field-assistant",
        "pskill-runner-evidence-evaluator",
        "ffmpeg-video-processing",
    ],
    "memory_policy": {
        "context_limit": 5,
        "write_candidate_types": ["short_term", "semantic", "episodic", "artifact"],
        "artifact_namespace": "run",
        "used_as_runtime_state": False,
    },
    "planner_policy": {"mode": "runtime_node_observation"},
    "guardrail_policy": {
        "deny_runtime_state_mutation": True,
        "require_replayable_evidence_refs": True,
    },
    "output_schema": {
        "name": "RuntimeAgentObservation",
        "required": [
            "decision",
            "reason",
            "next_phase",
            "terminal_message",
            "facts",
            "evidence_refs",
            "safety_flags",
        ],
    },
}
