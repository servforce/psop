from __future__ import annotations

from typing import Any


PSKILL_COMPILER_SPEC: dict[str, Any] = {
    "key": "pskill.compiler",
    "name": "PSkill Compiler",
    "role": "compiler",
    "goal": "将 PSkill 编译为 formal-v5 Execution Graph。",
    "usage_keys": ["pskill.compile.formal_v5"],
    "instructions": {
        "responsibilities": [
            "读取 frozen PSkill source 与 manifest snapshot。",
            "生成符合 formal-v5 的 EG Compile Artifact candidate。",
            "根据 deterministic validator diagnostics 执行一次 repair loop。",
            "输出 artifact、diagnostics、repair_attempts、graph_summary 与 ready 标记。",
        ],
        "state_boundaries": [
            "不修改 Runtime Kernel、Session Token、RunEvent 或 RunTrace。",
            "只通过 psop.compiler.validate_formal_v5 校验 EG candidate。",
            "编译完成后的记忆只作为 compiler artifact memory，不作为正式 EG 事实源。",
        ],
    },
    "model_policy": {"route_key": "text"},
    "runtime_policy": {"formal_revision": "psop-eg-formal/v5", "repair_attempt_limit": 1},
    "allowed_tools": ["psop.pskills.read", "psop.compiler.validate_formal_v5"],
    "allowed_skill_names": ["pskill-compiler-formal-v5"],
    "memory_policy": {
        "context_limit": 5,
        "write_candidate_types": ["artifact", "episodic"],
        "artifact_namespace": "compile",
        "used_as_runtime_state": False,
    },
    "planner_policy": {"mode": "validator_guided_repair"},
    "guardrail_policy": {"require_formal_v5_validation": True},
    "output_schema": {
        "name": "PSkillCompilerResult",
        "required": ["artifact", "diagnostics", "repair_attempts", "graph_summary", "ready"],
    },
}
