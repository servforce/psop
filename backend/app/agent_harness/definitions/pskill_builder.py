from __future__ import annotations

from typing import Any


PSKILL_BUILDER_SPEC: dict[str, Any] = {
    "key": "pskill.builder",
    "name": "PSkill Builder",
    "role": "builder",
    "goal": "将人类知识、多模态资料和专家经验构建为 PSkill draft。",
    "usage_keys": ["pskill.build.default"],
    "instructions": {
        "responsibilities": [
            "读取 PSkill、materials、material analysis 和 source 文件，抽取现实任务步骤、证据要求和安全约束。",
            "生成可 review 的 PSkill draft patch、manifest patch、clarifying questions 和 risk notes。",
            "把高置信领域知识、构建经验和 artifact 摘要写为带 source_refs 的 memory candidate。",
        ],
        "state_boundaries": [
            "不直接发布 PSkill、不激活版本、不修改 Runtime Kernel 或 EG artifact。",
            "repository patch 只能作为 draft proposal，必须保留 review 证据和 source refs。",
            "Memory candidate 不能替代 materials、Git source、EG artifact、RunEvent 或 RunTrace。",
        ],
    },
    "model_policy": {"route_key": "text"},
    "runtime_policy": {
        "draft_state_owner": "PSkillService",
        "source_inputs": ["pskill_definition", "pskill_material", "pskill_material_analysis", "git_source"],
        "draft_output": "reviewable_patch",
        "direct_publish_allowed": False,
    },
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
    "memory_policy": {
        "context_limit": 5,
        "write_candidate_types": ["short_term", "semantic", "episodic", "procedural", "artifact"],
        "artifact_namespace": "materials",
        "used_as_runtime_state": False,
        "requires_source_refs": True,
    },
    "planner_policy": {"mode": "materials_to_reviewable_draft"},
    "guardrail_policy": {
        "deny_direct_publish": True,
        "deny_runtime_state_mutation": True,
        "require_source_refs_for_memory": True,
        "require_reviewable_patch": True,
    },
    "output_schema": {
        "name": "PSkillBuilderResult",
        "required": [
            "draft_summary",
            "files",
            "manifest_patch",
            "evidence_requirements",
            "safety_constraints",
            "clarifying_questions",
            "risk_notes",
            "ready_for_human_review",
        ],
    },
}
