from __future__ import annotations

import pytest

from app.agents.registry import DomainPackRegistry, PromptRegistry, content_hash
from app.pskills.exceptions import SkillsConfigurationError


def test_prompt_registry_loads_agent_prompt_packs() -> None:
    registry = PromptRegistry()

    compile_pack = registry.load_default_compile_agent()
    creation_pack = registry.load_agent("skill_creation/conversational_draft/v1")

    assert compile_pack.agent_id == "psop.skill_compilation.formal_v5_compile"
    assert "PSkill 编译智能体" in compile_pack.system_prompt
    assert "SKILL 编译智能体" in compile_pack.system_prompt
    assert "RunEvent transcript" in compile_pack.system_prompt
    assert "token.run_events" in compile_pack.system_prompt
    assert "token.terminal.events" in compile_pack.system_prompt
    assert compile_pack.prompt_hash
    assert creation_pack.agent_id == "psop.skill_creation.conversational_draft"
    assert creation_pack.route_key == "text"
    assert "Skill 构建智能体" in creation_pack.system_prompt
    assert "AI 协助人类完成现实任务" in creation_pack.system_prompt
    assert "物理世界 Skill" in creation_pack.system_prompt


def test_domain_pack_registry_loads_initial_packs() -> None:
    registry = DomainPackRegistry()

    generic = registry.load_domain_pack("generic")
    inspection = registry.load_domain_pack("industrial_inspection")
    maintenance = registry.load_domain_pack("equipment_maintenance")

    assert generic.key == "generic/v1"
    assert "通用 Skill 工作流指导" in generic.guidance
    assert "现场巡检" in inspection.guidance
    assert "故障诊断" in maintenance.guidance


def test_agent_asset_hash_is_stable_and_content_sensitive() -> None:
    files = {"system.md": "hello", "agent.yaml": "agent_id: demo"}

    assert content_hash(files) == content_hash(dict(reversed(list(files.items()))))
    assert content_hash(files) != content_hash({**files, "system.md": "hello!"})


def test_missing_default_prompt_pack_fails(tmp_path) -> None:
    empty_root = tmp_path / "agents"
    empty_root.mkdir()

    with pytest.raises(SkillsConfigurationError):
        PromptRegistry(root=empty_root).load_default_compile_agent()


def test_unknown_domain_pack_falls_back_to_generic() -> None:
    resolution = DomainPackRegistry().resolve("unknown_domain")

    assert resolution.used_default is True
    assert resolution.requested_ref == "unknown_domain"
    assert resolution.pack.key == "generic/v1"
    assert resolution.fallback_reason
