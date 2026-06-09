from __future__ import annotations

from typing import Any


PSKILL_BUILDER_SPEC: dict[str, Any] = {
    "key": "pskill.builder",
    "name": "PSkill Builder",
    "role": "builder",
    "goal": "将人类知识、多模态资料和专家经验构建为 PSkill draft。",
    "usage_keys": ["pskill.build.default"],
    "allowed_tools": [
        "psop.pskills.get",
        "psop.materials.list",
        "psop.materials.read_analysis",
        "psop.repository.read_file",
        "psop.repository.propose_patch",
        "psop.pskill_manifest.parse",
        "psop.pskill_manifest.render",
        "psop.memory.search",
        "psop.memory.write_candidate",
    ],
    "allowed_skill_names": ["pskill-builder", "ffmpeg-video-processing", "document-ocr-processing"],
    "output_schema": {"name": "PSkillBuilderResult"},
}
