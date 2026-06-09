from __future__ import annotations

from typing import Any


PSKILL_RUNNER_SPEC: dict[str, Any] = {
    "key": "pskill.runner",
    "name": "PSkill Runner",
    "role": "runner",
    "goal": "在 RuntimeService 主权边界内为运行节点生成 observation。",
    "usage_keys": ["pskill.run.node"],
    "allowed_tools": ["psop.runtime.read"],
    "allowed_skill_names": [
        "pskill-runner-field-assistant",
        "pskill-runner-evidence-evaluator",
        "ffmpeg-video-processing",
    ],
    "output_schema": {"name": "RuntimeAgentObservation"},
}
