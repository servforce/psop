from __future__ import annotations

from typing import Any


PSKILL_TESTER_SPEC: dict[str, Any] = {
    "key": "pskill.tester",
    "name": "PSkill Tester",
    "role": "tester",
    "goal": "发布前测试 PSkill、执行图、交互、安全和回归。",
    "usage_keys": ["pskill.test.pre_publish"],
    "instructions": {
        "responsibilities": [
            "基于 PSkill source、EG artifact 和测试目标生成发布前测试场景。",
            "执行运行时模拟、校验交互输出、生成发布门禁结果。",
            "输出 decision、score、coverage、blocking_findings、warnings 与 publish_gate_summary。",
        ],
        "state_boundaries": [
            "不直接修改 Runtime Kernel、Session Token、RunEvent 或 RunTrace。",
            "测试运行证据必须通过 pskill_test_run、AgentRun 与 Runtime Replay 引用。",
            "测试记忆只作为 artifact/procedural/episodic 候选，不作为发布门禁事实源。",
        ],
    },
    "model_policy": {"route_key": "text"},
    "runtime_policy": {"simulation_mode": "runtime_replay", "publish_gate_required": True},
    "allowed_tools": ["psop.pskills.read", "psop.testing.write_diagnostics"],
    "allowed_skill_names": ["pskill-tester", "ffmpeg-video-processing"],
    "memory_policy": {
        "context_limit": 5,
        "write_candidate_types": ["artifact", "procedural", "episodic"],
        "artifact_namespace": "testing",
        "used_as_runtime_state": False,
    },
    "planner_policy": {"mode": "scenario_generation_and_gate_review"},
    "guardrail_policy": {"require_replay_evidence": True},
    "output_schema": {
        "name": "PSkillTestResult",
        "required": ["decision", "score", "coverage", "blocking_findings", "warnings", "publish_gate_summary"],
    },
}
