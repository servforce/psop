from __future__ import annotations

import pytest

from app.agent_harness.events import AgentEventWriter
from app.agent_harness.skills.loader import SkillLoader


def test_skill_loader_parses_frontmatter_and_records_event(tmp_path) -> None:
    skill_dir = tmp_path / "skills" / "demo_psop_checklist"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        """---
name: demo_psop_checklist
description: Demo skill
allowed-tools:
  - demo_extract_check_items
  - write_demo_report
---

# Demo

必须生成检查清单。
""",
        encoding="utf-8",
    )
    writer = AgentEventWriter(tmp_path / "events.jsonl")

    loader = SkillLoader(tmp_path / "skills")
    metadata = loader.load_metadata("demo_psop_checklist")

    assert metadata.name == "demo_psop_checklist"
    assert metadata.description == "Demo skill"
    assert metadata.allowed_tools == ["demo_extract_check_items", "write_demo_report"]
    assert metadata.instruction == ""
    assert writer.events == []

    skill = loader.load("demo_psop_checklist", writer)

    assert skill.name == "demo_psop_checklist"
    assert skill.description == "Demo skill"
    assert skill.allowed_tools == ["demo_extract_check_items", "write_demo_report"]
    assert "必须生成检查清单" in skill.instruction
    assert writer.events[-1].event_type == "agent.skill.loaded"
    assert writer.events[-1].payload["allowed_tools"] == ["demo_extract_check_items", "write_demo_report"]


def test_skill_loader_requires_allowed_tools(tmp_path) -> None:
    skill_dir = tmp_path / "skills" / "bad_skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        """---
name: bad_skill
description: Missing allowed tools
---

# Bad
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="allowed-tools"):
        SkillLoader(tmp_path / "skills").load("bad_skill")


def test_skill_loader_loads_package_resource_and_records_event(tmp_path) -> None:
    skill_dir = tmp_path / "skills" / "demo_skill" / "core"
    skill_dir.mkdir(parents=True)
    (tmp_path / "skills" / "demo_skill" / "SKILL.md").write_text(
        """---
name: demo_skill
description: Demo skill
allowed-tools: []
---

# Demo
""",
        encoding="utf-8",
    )
    (skill_dir / "SKILL.md").write_text("# Core\n\nresource body", encoding="utf-8")
    writer = AgentEventWriter(tmp_path / "events.jsonl")

    result = SkillLoader(tmp_path / "skills").load_resource("demo_skill", "core/SKILL.md", writer, max_chars=8)

    assert result["skill_name"] == "demo_skill"
    assert result["resource_path"] == "core/SKILL.md"
    assert result["content"] == "# Core\n\n"
    assert result["truncated"] is True
    assert writer.events[-1].event_type == "agent.skill.resource.loaded"
    assert writer.events[-1].payload["resource_path"] == "core/SKILL.md"


def test_skill_loader_resource_rejects_path_escape(tmp_path) -> None:
    skill_dir = tmp_path / "skills" / "demo_skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        """---
name: demo_skill
description: Demo skill
allowed-tools: []
---

# Demo
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="resource_path"):
        SkillLoader(tmp_path / "skills").load_resource("demo_skill", "../escape.md")
