from __future__ import annotations


def workspace_ref_for_agent_run(agent_run_id: str) -> str:
    return f"agent-run-workspace:{agent_run_id}"
