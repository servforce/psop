from __future__ import annotations

from app.agent_harness.events import AgentEventWriter
from app.agent_harness.skills.loader import SkillLoader


def test_skill_loader_parses_frontmatter_and_records_event(tmp_path) -> None:
    skill_dir = tmp_path / "skills" / "demo_psop_checklist"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        """---
name: demo_psop_checklist
description: Demo skill
tools:
  - demo_extract_check_items
  - write_demo_report
---

# Demo

必须生成检查清单。
""",
        encoding="utf-8",
    )
    writer = AgentEventWriter(tmp_path / "events.jsonl")

    skill = SkillLoader(tmp_path / "skills").load("demo_psop_checklist", writer)

    assert skill.name == "demo_psop_checklist"
    assert skill.description == "Demo skill"
    assert skill.tools == ["demo_extract_check_items", "write_demo_report"]
    assert "必须生成检查清单" in skill.instruction
    assert writer.events[-1].event_type == "agent.skill.loaded"
    assert writer.events[-1].payload["tools"] == ["demo_extract_check_items", "write_demo_report"]
