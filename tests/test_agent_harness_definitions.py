from __future__ import annotations

import pytest

from app.agent_harness.agents.registry import FileAgentDefinitionRegistry, default_agent_registry
from app.core.config import Settings


def test_file_agent_definition_registry_loads_demo_package() -> None:
    settings = Settings(database_url="sqlite+pysqlite:///:memory:")
    package = default_agent_registry(settings.backend_root).load("demo.psop_harness_agent")

    assert package.definition.agent_key == "demo.psop_harness_agent"
    assert package.definition.runner_kind == "langchain_agent"
    assert package.definition.factory == "make_demo_agent"
    assert "PSOP Agent Harness" in package.read_system_prompt()
    assert callable(package.load_factory())


def test_file_agent_definition_registry_rejects_invalid_key(tmp_path) -> None:
    registry = FileAgentDefinitionRegistry(tmp_path)

    with pytest.raises(ValueError):
        registry.load("../bad")


def test_file_agent_definition_registry_rejects_agent_key_mismatch(tmp_path) -> None:
    agent_root = tmp_path / "demo" / "bad"
    agent_root.mkdir(parents=True)
    (agent_root / "agent.yaml").write_text("agent_key: demo.other\n", encoding="utf-8")
    registry = FileAgentDefinitionRegistry(tmp_path)

    with pytest.raises(ValueError):
        registry.load("demo.bad")
