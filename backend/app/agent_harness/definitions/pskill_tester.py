from __future__ import annotations

from typing import Any


PSKILL_TESTER_SPEC: dict[str, Any] = {
    "key": "pskill.tester",
    "name": "PSkill Tester",
    "role": "tester",
    "goal": "发布前测试 PSkill、执行图、交互、安全和回归。",
    "usage_keys": ["pskill.test.pre_publish"],
    "allowed_tools": ["psop.pskills.read", "psop.testing.write_diagnostics"],
    "allowed_skill_names": ["pskill-tester", "ffmpeg-video-processing"],
    "output_schema": {"name": "PSkillTestResult"},
}
