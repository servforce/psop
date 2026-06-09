from __future__ import annotations

from typing import Any


PSKILL_EVALUATOR_SPEC: dict[str, Any] = {
    "key": "pskill.evaluator",
    "name": "PSkill Evaluator",
    "role": "evaluator",
    "goal": "评估已完成 Run，进行质量归因并给出优化建议。",
    "usage_keys": ["pskill.evaluate.run"],
    "allowed_tools": ["psop.runtime.read", "psop.evaluations.write_diagnostics"],
    "allowed_skill_names": ["pskill-run-evaluator"],
    "output_schema": {"name": "RunEvaluationResult"},
}
