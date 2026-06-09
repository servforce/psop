from __future__ import annotations

from app.agent_harness.tools import AgentToolHarness, ToolPolicy


def test_tool_policy_defaults_authorized_unknown_mcp_tools_to_runtime_authorization() -> None:
    policy = ToolPolicy({"psop.runtime.read": "read"})

    decision = policy.check(
        tool_name="mcp.ticketing.create_ticket",
        tool_provider="mcp",
        requested_side_effect_level=None,
        effective_allowed_tools={"mcp.ticketing.create_ticket"},
    )

    assert decision.allowed is True
    assert decision.side_effect_level == "external_action"
    assert decision.requires_authorization is True
    assert decision.reason == "mcp_requires_authorization"


def test_tool_policy_blocks_unknown_mcp_tools_outside_agent_and_skill_scope() -> None:
    policy = ToolPolicy({"psop.runtime.read": "read"})

    decision = policy.check(
        tool_name="mcp.ticketing.create_ticket",
        tool_provider="mcp",
        requested_side_effect_level=None,
        effective_allowed_tools={"psop.runtime.read"},
    )

    assert decision.allowed is False
    assert decision.reason == "tool_not_allowed_by_agent_or_skill"


def test_tool_policy_still_blocks_unknown_native_tools_as_unregistered() -> None:
    policy = ToolPolicy({"psop.runtime.read": "read"})

    decision = policy.check(
        tool_name="psop.run_events.write_low",
        tool_provider="native",
        requested_side_effect_level="low_write",
        effective_allowed_tools={"psop.run_events.write_low"},
    )

    assert decision.allowed is False
    assert decision.side_effect_level == "low_write"
    assert decision.reason == "tool_not_registered"


def test_agent_tool_harness_preserves_static_effective_tool_intersection_for_prompts() -> None:
    harness = AgentToolHarness(ToolPolicy({"psop.runtime.read": "read"}))
    spec = {"allowed_tools": ["psop.runtime.read", "mcp.ticketing.create_ticket"]}
    active_tools = {"psop.runtime.read", "mcp.ticketing.create_ticket"}

    assert harness.effective_allowed_tools(spec=spec, active_tools=active_tools) == {"psop.runtime.read"}
    assert harness.policy_scope_for_decision(spec=spec, active_tools=active_tools, tool_provider="mcp") == {
        "psop.runtime.read",
        "mcp.ticketing.create_ticket",
    }
