from __future__ import annotations

import ast
from pathlib import Path

from app.app import create_app
from tests.test_skills_api import create_test_settings


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_runtime_domain_does_not_import_agent_memory_domain() -> None:
    runtime_files = sorted((PROJECT_ROOT / "backend" / "app" / "runtime").glob("*.py"))
    assert runtime_files

    violations: list[str] = []
    for path in runtime_files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module == "app.memory" or module.startswith("app.memory."):
                    violations.append(f"{path.relative_to(PROJECT_ROOT)} imports {module}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "app.memory" or alias.name.startswith("app.memory."):
                        violations.append(f"{path.relative_to(PROJECT_ROOT)} imports {alias.name}")

    assert violations == []


def test_backend_does_not_restore_domain_package_layer() -> None:
    assert not (PROJECT_ROOT / "backend" / "app" / "domain").exists()


def test_api_routes_use_pskill_and_materials_naming() -> None:
    app = create_app(create_test_settings())
    route_paths = {getattr(route, "path", "") for route in app.routes}

    forbidden_fragments = {
        "/raw-materials",
        "/agent-skills",
        "/api/v1/compiler/skills/",
    }
    violations = sorted(
        path
        for path in route_paths
        if any(fragment in path for fragment in forbidden_fragments)
    )

    assert violations == []
    assert "/api/v1/compiler/pskills/{skill_id}/compile" in route_paths
    assert "/api/v1/pskills/{skill_id}/materials" in route_paths
    assert "/api/v1/skills" in route_paths


def test_server_design_keeps_pskill_api_paths_distinct_from_skill_packages() -> None:
    design = (PROJECT_ROOT / "docs" / "PSOP服务端详细设计v1.md").read_text(encoding="utf-8")

    forbidden_fragments = {
        "/api/v1/skills/{skill_id}",
        "/api/v1/compiler/skills/",
        "/raw-materials",
        "/agent-skills",
        "raw materials",
    }
    violations = sorted(fragment for fragment in forbidden_fragments if fragment in design)

    assert violations == []
    assert "### 9.2 PSkills / Materials" in design
    assert "`GET` | `/api/v1/pskills` | PSkill 列表" in design
    assert "`GET` | `/api/v1/skills` | Skill 包列表" in design
