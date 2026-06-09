from __future__ import annotations

from typing import Any


PSKILL_COMPILER_SPEC: dict[str, Any] = {
    "key": "pskill.compiler",
    "name": "PSkill Compiler",
    "role": "compiler",
    "goal": "将 PSkill 编译为 formal-v5 Execution Graph。",
    "usage_keys": ["pskill.compile.formal_v5"],
    "allowed_tools": ["psop.pskills.read", "psop.compiler.validate_formal_v5"],
    "allowed_skill_names": ["pskill-compiler-formal-v5"],
    "output_schema": {"name": "PSkillCompilerResult"},
}
