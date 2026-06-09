from __future__ import annotations

from copy import deepcopy
from typing import Any


SANDBOX_POLICY_SCHEMA_VERSION = "psop-agent-sandbox-policy/v1"

DEFAULT_SANDBOX_POLICY: dict[str, Any] = {
    "schema_version": SANDBOX_POLICY_SCHEMA_VERSION,
    "mode": "restricted_workspace",
    "workspace_scope": "agent_run",
    "network": "disabled",
    "filesystem": {
        "read": ["skill_resources", "run_artifacts", "pskill_source_snapshot"],
        "write": ["agent_run_workspace", "tool_result_artifacts"],
        "deny": ["runtime_state_tables", "session_token_snapshot", "run_event", "run_trace"],
    },
    "public_skill_resources": "read_only",
    "max_workspace_bytes": 50 * 1024 * 1024,
    "requires_explicit_tool_policy": True,
}


def default_sandbox_policy(agent_key: str = "") -> dict[str, Any]:
    policy = deepcopy(DEFAULT_SANDBOX_POLICY)
    if agent_key:
        policy["agent_key"] = agent_key
    return policy


def normalize_sandbox_policy(policy: dict[str, Any] | None, *, agent_key: str = "") -> dict[str, Any]:
    normalized = default_sandbox_policy(agent_key)
    if not isinstance(policy, dict):
        return normalized

    for key, value in policy.items():
        if key == "filesystem" and isinstance(value, dict):
            filesystem = dict(normalized["filesystem"])
            filesystem.update(value)
            normalized["filesystem"] = filesystem
        else:
            normalized[key] = value
    normalized["schema_version"] = str(normalized.get("schema_version") or SANDBOX_POLICY_SCHEMA_VERSION)
    if agent_key and not normalized.get("agent_key"):
        normalized["agent_key"] = agent_key
    return normalized


def sandbox_policy_summary(policy: dict[str, Any] | None, *, agent_key: str = "") -> dict[str, Any]:
    normalized = normalize_sandbox_policy(policy, agent_key=agent_key)
    filesystem = normalized.get("filesystem") if isinstance(normalized.get("filesystem"), dict) else {}
    return {
        "schema_version": normalized.get("schema_version"),
        "agent_key": normalized.get("agent_key", ""),
        "mode": normalized.get("mode"),
        "workspace_scope": normalized.get("workspace_scope"),
        "network": normalized.get("network"),
        "read_scopes": list(filesystem.get("read") or []),
        "write_scopes": list(filesystem.get("write") or []),
        "deny_scopes": list(filesystem.get("deny") or []),
        "requires_explicit_tool_policy": bool(normalized.get("requires_explicit_tool_policy")),
    }
