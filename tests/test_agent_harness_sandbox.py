from __future__ import annotations

import pytest

from app.agent_harness.sandbox.local import LocalAgentSandboxProvider
from app.core.config import Settings


def _provider(tmp_path) -> LocalAgentSandboxProvider:
    settings = Settings(
        database_url="sqlite+pysqlite:///:memory:",
        agent_harness_sandbox_root=str(tmp_path / "agent-runs"),
    )
    return LocalAgentSandboxProvider(settings)


def test_local_sandbox_reads_writes_and_searches_virtual_paths(tmp_path) -> None:
    sandbox = _provider(tmp_path).acquire(agent_run_id="run-1", input_payload={"x": 1})

    written = sandbox.write_text("/mnt/psop/workspace/result.md", "hello\nneedle\n")

    assert written == "/mnt/psop/workspace/result.md"
    assert sandbox.read_text("/mnt/psop/workspace/result.md") == "hello\nneedle\n"
    assert "/mnt/psop/workspace/result.md" in sandbox.glob("/mnt/psop/workspace", "*.md")
    assert sandbox.grep("/mnt/psop/workspace", "needle")[0]["line"] == 2
    assert (tmp_path / "agent-runs" / "run-1" / "input.json").exists()


@pytest.mark.parametrize("path", ["../escape.md", "/tmp/escape.md", "/mnt/psop/workspace/../escape.md"])
def test_local_sandbox_rejects_path_escape(tmp_path, path) -> None:
    sandbox = _provider(tmp_path).acquire(agent_run_id="run-1")

    with pytest.raises(ValueError):
        sandbox.write_text(path, "bad")


def test_local_sandbox_rejects_symlink_escape(tmp_path) -> None:
    sandbox = _provider(tmp_path).acquire(agent_run_id="run-1")
    outside = tmp_path / "outside"
    outside.mkdir()
    (sandbox.workspace_path / "link").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError):
        sandbox.write_text("/mnt/psop/workspace/link/escape.md", "bad")


def test_local_sandbox_release_keeps_artifacts(tmp_path) -> None:
    provider = _provider(tmp_path)
    sandbox = provider.acquire(agent_run_id="run-1")
    sandbox.write_text("/mnt/psop/workspace/result.md", "ok")

    provider.release(sandbox.sandbox_id)

    assert provider.get(sandbox.sandbox_id) is None
    assert (tmp_path / "agent-runs" / "run-1" / "workspace" / "result.md").read_text(encoding="utf-8") == "ok"
