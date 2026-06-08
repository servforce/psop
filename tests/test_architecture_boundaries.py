from __future__ import annotations

import ast
from pathlib import Path


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
