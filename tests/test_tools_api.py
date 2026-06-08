from __future__ import annotations

from tests.test_skills_api import create_test_client


BUILDER_ALLOWED_TOOLS = {
    "psop.pskills.get",
    "psop.materials.list",
    "psop.materials.read_analysis",
    "psop.repository.read_file",
    "psop.repository.propose_patch",
    "psop.pskill_manifest.parse",
    "psop.pskill_manifest.render",
    "psop.memory.search",
    "psop.memory.write_candidate",
}


def test_tools_api_lists_seeded_tools_and_exposes_policy_metadata() -> None:
    client, _, _ = create_test_client()

    with client:
        list_response = client.get("/api/v1/tools")
        high_write_response = client.get(
            "/api/v1/tools",
            params={"side_effect_level": "high_write", "requires_authorization": "true"},
        )
        detail_response = client.get("/api/v1/tools/psop.agent_version.activate")
        memory_detail_response = client.get("/api/v1/tools/psop.memory.write_candidate")
        media_detail_response = client.get("/api/v1/tools/psop.media.compute")
        propose_patch_detail_response = client.get("/api/v1/tools/psop.repository.propose_patch")

    tools = {item["name"]: item for item in list_response.json()}
    assert list_response.status_code == 200
    assert {
        "psop.pskills.read",
        "psop.compiler.validate_formal_v5",
        "psop.memory.search",
        "psop.memory.write_candidate",
        "psop.agent_version.activate",
        "psop.skill_version.activate",
    } | BUILDER_ALLOWED_TOOLS <= set(tools)
    assert "psop.materials.read" not in tools
    assert "psop.run_events.write_low" not in tools

    assert tools["psop.pskills.read"]["side_effect_level"] == "read"
    assert tools["psop.pskills.read"]["requires_authorization"] is False
    assert tools["psop.pskills.read"]["policy_summary"]["native_implemented"] is True
    assert tools["psop.pskills.read"]["policy_summary"]["policy_reason"] == "auto_allowed"
    assert tools["psop.media.compute"]["side_effect_level"] == "compute"
    assert tools["psop.media.compute"]["requires_authorization"] is False
    assert tools["psop.media.compute"]["policy_summary"]["native_implemented"] is False
    assert tools["psop.media.compute"]["policy_summary"]["auto_executable"] is False
    assert tools["psop.media.compute"]["policy_summary"]["policy_reason"] == "native_tool_not_implemented"
    assert tools["psop.agent_version.activate"]["side_effect_level"] == "high_write"
    assert tools["psop.agent_version.activate"]["requires_authorization"] is True
    assert tools["psop.agent_version.activate"]["policy_summary"]["policy_reason"] == "requires_tool_authorization"

    high_write_names = {item["name"] for item in high_write_response.json()}
    assert high_write_response.status_code == 200
    assert high_write_names == {
        "psop.agent_version.activate",
        "psop.repository.commit_patch",
        "psop.skill_version.activate",
    }

    detail = detail_response.json()
    assert detail_response.status_code == 200
    assert detail["name"] == "psop.agent_version.activate"
    assert detail["provider"] == "native"
    assert detail["requires_authorization"] is True
    assert detail["policy_summary"]["policy_decision"]["reason"] == "requires_authorization"
    assert detail["allowed_agent_keys"] == ["psop.governance"]
    assert detail["policy_summary"]["permission_rule"] == (
        "AgentSpec.allowed_tools ∩ SkillPackage.allowed_tools ∩ ToolPolicy.allowed_tools"
    )

    memory_detail = memory_detail_response.json()
    assert memory_detail_response.status_code == 200
    assert memory_detail["side_effect_level"] == "low_write"
    assert memory_detail["requires_authorization"] is False

    media_detail = media_detail_response.json()
    assert media_detail_response.status_code == 200
    assert media_detail["allowed_agent_keys"] == []
    assert media_detail["policy_summary"]["native_implemented"] is False

    propose_patch_detail = propose_patch_detail_response.json()
    assert propose_patch_detail_response.status_code == 200
    assert propose_patch_detail["side_effect_level"] == "low_write"
    assert propose_patch_detail["requires_authorization"] is False
    assert propose_patch_detail["allowed_agent_keys"] == ["pskill.builder"]


def test_tools_api_reports_recent_tool_call_failure_stats() -> None:
    client, _, _ = create_test_client()

    with client:
        run_response = client.post(
            "/api/v1/agent-runs",
            json={
                "agent_key": "pskill.runner",
                "owner_type": "runtime",
                "owner_id": "tool-stats-runner",
                "input_payload": {
                    "agent_decision": {
                        "decision_type": "tool_call",
                        "tool_name": "psop.repository.commit_patch",
                        "side_effect_level": "high_write",
                        "arguments_summary": {"paths": ["SKILL.md"]},
                        "expected_effect_summary": "Attempt a repository patch from a runner that cannot use it.",
                    }
                },
            },
        )
        agent_run_id = run_response.json()["id"]
        run_once_response = client.post(f"/api/v1/agent-runs/{agent_run_id}/run-once")
        detail_response = client.get("/api/v1/tools/psop.repository.commit_patch")

    assert run_response.status_code == 201
    assert run_once_response.status_code == 200
    assert run_once_response.json()["status"] == "failed"
    assert run_once_response.json()["error_message"] == "tool_not_allowed_by_agent_or_skill"

    detail = detail_response.json()
    assert detail_response.status_code == 200
    assert detail["recent_call_count"] == 1
    assert detail["failed_call_count"] == 1
    assert detail["failure_rate"] == 1.0


def test_tools_api_dry_runs_read_compute_and_explains_authorization_policy() -> None:
    client, _, _ = create_test_client()

    with client:
        read_response = client.post(
            "/api/v1/tools/psop.memory.search/test",
            json={"arguments_summary": {"query": "runtime findings"}},
        )
        compute_response = client.post(
            "/api/v1/tools/psop.compiler.validate_formal_v5/test",
            json={"arguments_summary": {"artifact_id": "artifact-1"}},
        )
        unimplemented_compute_response = client.post(
            "/api/v1/tools/psop.media.compute/test",
            json={"arguments_summary": {"operation": "extract_keyframes"}},
        )
        high_write_response = client.post(
            "/api/v1/tools/psop.agent_version.activate/test",
            json={"arguments_summary": {"agent_key": "pskill.runner", "version_id": "version-1"}},
        )

    assert read_response.status_code == 200
    read_result = read_response.json()
    assert read_result["executable"] is True
    assert read_result["dry_run"] is True
    assert read_result["policy_reason"] == "console_test_allowed"
    assert read_result["output_preview"]["status"] == "dry_run_succeeded"
    assert read_result["input_echo"] == {"query": "runtime findings"}

    assert compute_response.status_code == 200
    assert compute_response.json()["executable"] is True
    assert compute_response.json()["side_effect_level"] == "compute"

    assert unimplemented_compute_response.status_code == 200
    unimplemented_compute_result = unimplemented_compute_response.json()
    assert unimplemented_compute_result["executable"] is False
    assert unimplemented_compute_result["side_effect_level"] == "compute"
    assert unimplemented_compute_result["policy_reason"] == "native_tool_not_implemented"
    assert unimplemented_compute_result["policy_decision"]["native_implemented"] is False
    assert unimplemented_compute_result["output_preview"]["status"] == "not_executed"

    assert high_write_response.status_code == 200
    high_write_result = high_write_response.json()
    assert high_write_result["executable"] is False
    assert high_write_result["requires_authorization"] is True
    assert high_write_result["policy_reason"] == "requires_tool_authorization"
    assert high_write_result["output_preview"]["status"] == "not_executed"


def test_tools_api_rejects_invalid_side_effect_filter() -> None:
    client, _, _ = create_test_client()

    with client:
        response = client.get("/api/v1/tools", params={"side_effect_level": "unknown_write"})

    assert response.status_code == 422
    assert response.json()["code"] == "skill_validation_error"
