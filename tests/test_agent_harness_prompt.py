from __future__ import annotations

from app.agent_harness.agents.demo.psop_harness_agent.prompt import apply_prompt_template
from app.agent_harness.skills.loader import SkillLoader
from app.core.config import Settings


def test_demo_prompt_contains_skill_metadata_without_full_skill_body() -> None:
    settings = Settings(database_url="sqlite+pysqlite:///:memory:")
    loader = SkillLoader(settings.repo_root / "skills")
    skill = loader.load_metadata("demo_psop_checklist")

    prompt = apply_prompt_template(
        system_prompt="系统提示",
        memory_prompt="记忆提示",
        skill_metadata=[skill],
        memory_payload={"last": "value"},
    )

    assert "系统提示" in prompt
    assert "记忆提示" in prompt
    assert "demo_psop_checklist" in prompt
    assert "将一段现场作业描述拆解为检查项" in prompt
    assert "demo_extract_check_items" in prompt
    assert "必须调用 demo_extract_check_items" not in prompt
    assert "最后将报告写入 /mnt/psop/workspace/result.md" not in prompt
