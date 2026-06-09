from __future__ import annotations

from app.agent_harness.guardrails import OutputGuardrail, ToolGuardrail


def test_output_guardrail_rejects_missing_required_output_schema_fields() -> None:
    result = OutputGuardrail().check(
        agent_key="pskill.runner",
        output_payload={"decision": "need_more_evidence", "reason": "Evidence is incomplete."},
        spec={
            "output_schema": {
                "name": "RuntimeAgentObservation",
                "required": ["decision", "reason", "terminal_message", "evidence_refs"],
            }
        },
    )

    assert result.passed is False
    assert [item.code for item in result.findings] == [
        "output_schema_required_missing",
        "output_schema_required_missing",
    ]
    assert [item.path for item in result.findings] == [
        "output_payload.terminal_message",
        "output_payload.evidence_refs",
    ]


def test_output_guardrail_accepts_declared_required_output_schema_fields() -> None:
    result = OutputGuardrail().check(
        agent_key="pskill.runner",
        output_payload={
            "decision": "need_more_evidence",
            "reason": "Evidence is incomplete.",
            "terminal_message": "请补充现场照片。",
            "evidence_refs": [],
        },
        spec={
            "output_schema": {
                "name": "RuntimeAgentObservation",
                "required": ["decision", "reason", "terminal_message", "evidence_refs"],
            }
        },
    )

    assert result.passed is True
    assert result.findings == []


def test_output_guardrail_accepts_governance_policy_compliant_proposal() -> None:
    result = OutputGuardrail().check(
        agent_key="psop.governance",
        output_payload={
            "proposal_type": "agent_spec_update",
            "target": {"kind": "agent", "agent_key": "pskill.runner"},
            "problem_statement": "Runner evidence quality needs stricter checks.",
            "evidence_refs": [{"kind": "run_evaluation", "id": "evaluation-1"}],
            "proposed_changes": [{"kind": "agent_spec_patch", "description": "Tighten evidence rubric."}],
            "risk_assessment": {"risk_level": "medium", "requires_human_review": True},
            "required_tests": [{"kind": "regression", "description": "Replay failing run."}],
            "activation_plan": {
                "strategy": "test_review_canary_rollback",
                "direct_activation_allowed": False,
                "steps": ["run_regression_tests", "submit_human_review", "activate_or_rollback"],
            },
        },
        spec={
            "guardrail_policy": {
                "require_evidence_refs": True,
                "require_rollback_plan": True,
                "deny_direct_activation_without_authorization": True,
                "deny_tool_permission_expansion": True,
                "require_reviewable_patch_and_tests": True,
            }
        },
    )

    assert result.passed is True
    assert result.findings == []


def test_output_guardrail_rejects_governance_policy_violations() -> None:
    result = OutputGuardrail().check(
        agent_key="psop.governance",
        output_payload={
            "proposal_type": "agent_spec_update",
            "target": {"kind": "agent", "agent_key": "pskill.runner"},
            "problem_statement": "Activate a runner update immediately.",
            "evidence_refs": [],
            "proposed_changes": [
                {
                    "kind": "agent_version_activation",
                    "agent_key": "pskill.runner",
                    "direct_activation_allowed": True,
                    "permission_expansion_performed": True,
                }
            ],
            "risk_assessment": {"risk_level": "high"},
            "required_tests": [],
            "activation_plan": {"strategy": "direct", "direct_activation_allowed": True},
        },
        spec={
            "guardrail_policy": {
                "require_evidence_refs": True,
                "require_rollback_plan": True,
                "deny_direct_activation_without_authorization": True,
                "deny_tool_permission_expansion": True,
                "require_reviewable_patch_and_tests": True,
            }
        },
    )

    assert result.passed is False
    assert {item.code for item in result.findings} == {
        "output_evidence_refs_required",
        "output_rollback_plan_required",
        "output_direct_activation_denied",
        "output_required_tests_required",
        "output_tool_permission_expansion_denied",
    }


def test_output_guardrail_requires_reviewable_patch_for_ready_builder_output() -> None:
    result = OutputGuardrail().check(
        agent_key="pskill.builder",
        output_payload={
            "draft_summary": "Draft is ready but does not include patch details.",
            "files": [],
            "manifest_patch": {},
            "ready_for_human_review": True,
        },
        spec={"guardrail_policy": {"require_reviewable_patch": True}},
    )

    assert result.passed is False
    assert result.findings[0].code == "output_reviewable_patch_required"


def test_output_guardrail_accepts_builder_business_wait_without_patch() -> None:
    result = OutputGuardrail().check(
        agent_key="pskill.builder",
        output_payload={
            "draft_summary": "Need a device nameplate before building a safe patch.",
            "files": [],
            "manifest_patch": {},
            "clarifying_questions": ["请补充设备铭牌照片。"],
            "ready_for_human_review": False,
        },
        spec={"guardrail_policy": {"require_reviewable_patch": True}},
    )

    assert result.passed is True
    assert result.business_wait_state == "clarifying_questions"


def test_output_guardrail_rejects_builder_direct_publish() -> None:
    result = OutputGuardrail().check(
        agent_key="pskill.builder",
        output_payload={
            "draft_summary": "Published directly.",
            "files": [{"path": "SKILL.md", "change_type": "modify"}],
            "manifest_patch": {},
            "published": True,
            "ready_for_human_review": True,
        },
        spec={"guardrail_policy": {"deny_direct_publish": True, "require_reviewable_patch": True}},
    )

    assert result.passed is False
    assert result.findings[0].code == "output_direct_publish_denied"


def test_output_guardrail_requires_replayable_evidence_refs_when_policy_declares_it() -> None:
    result = OutputGuardrail().check(
        agent_key="pskill.runner",
        output_payload={
            "decision": "need_more_evidence",
            "reason": "Evidence is incomplete.",
            "terminal_message": "请补充现场照片。",
            "evidence_refs": [{"kind": "pskill_material", "id": "material-1"}],
        },
        spec={"guardrail_policy": {"require_replayable_evidence_refs": True}},
    )

    assert result.passed is False
    assert result.findings[0].code == "output_replayable_evidence_refs_required"
    assert result.findings[0].path == "output_payload.evidence_refs"


def test_output_guardrail_accepts_replayable_evidence_refs_when_policy_declares_it() -> None:
    result = OutputGuardrail().check(
        agent_key="pskill.runner",
        output_payload={
            "decision": "need_more_evidence",
            "reason": "Evidence is incomplete.",
            "terminal_message": "请补充现场照片。",
            "evidence_refs": [{"kind": "run_event", "id": "run-event-1"}],
        },
        spec={"guardrail_policy": {"require_replayable_evidence_refs": True}},
    )

    assert result.passed is True
    assert result.findings == []


def test_output_guardrail_requires_replay_evidence_when_policy_declares_it() -> None:
    result = OutputGuardrail().check(
        agent_key="pskill.tester",
        output_payload={
            "decision": "require_human_review",
            "score": 72,
            "coverage": {"scenario_count": 1},
            "blocking_findings": [],
            "warnings": [{"code": "coverage.warning", "message": "Only one scenario is covered."}],
            "publish_gate_summary": "需要人工 review。",
        },
        spec={"guardrail_policy": {"require_replay_evidence": True}},
    )

    assert result.passed is False
    assert result.findings[0].code == "output_replay_evidence_required"
    assert result.findings[0].path == "output_payload"


def test_output_guardrail_accepts_nested_replay_evidence_when_policy_declares_it() -> None:
    result = OutputGuardrail().check(
        agent_key="pskill.tester",
        output_payload={
            "decision": "require_human_review",
            "score": 72,
            "coverage": {
                "scenario_count": 1,
                "evidence_refs": [{"kind": "run_trace", "id": "trace-test-1"}],
            },
            "blocking_findings": [],
            "warnings": [{"code": "coverage.warning", "message": "Only one scenario is covered."}],
            "publish_gate_summary": "需要人工 review。",
        },
        spec={"guardrail_policy": {"require_replay_evidence": True}},
    )

    assert result.passed is True
    assert result.findings == []


def test_output_guardrail_rejects_runtime_state_mutation_intent_when_policy_declares_it() -> None:
    result = OutputGuardrail().check(
        agent_key="pskill.runner",
        output_payload={
            "decision": "proceed",
            "reason": "I will update run_event and session_token_snapshot directly.",
            "terminal_message": "Continuing.",
            "evidence_refs": [{"kind": "run_event", "id": "run-event-1"}],
        },
        spec={"guardrail_policy": {"deny_runtime_state_mutation": True}},
    )

    assert result.passed is False
    assert result.findings[0].code == "output_runtime_state_sovereignty_violation"
    assert result.findings[0].path == "output_payload.reason"


def test_output_guardrail_allows_negated_runtime_boundary_statement() -> None:
    result = OutputGuardrail().check(
        agent_key="psop.governance",
        output_payload={
            "proposal_type": "agent_spec_update",
            "proposed_changes": [
                {
                    "kind": "governance_boundary",
                    "description": "不能直接修改 Runtime Kernel，只能生成提案和验证计划。",
                }
            ],
        },
        spec={"guardrail_policy": {"deny_runtime_state_mutation": True}},
    )

    assert result.passed is True
    assert result.findings == []


def test_tool_guardrail_blocks_runner_runtime_state_mutation_intent() -> None:
    result = ToolGuardrail().check(
        agent_key="pskill.runner",
        tool_name="psop.runtime.read",
        arguments_summary={"target": "session_token_snapshot", "operation": "write token_payload"},
        expected_effect_summary="write run_event directly",
    )

    assert result.passed is False
    assert result.findings[0].code == "tool_runtime_state_sovereignty_violation"


def test_tool_guardrail_allows_runner_runtime_read_context() -> None:
    result = ToolGuardrail().check(
        agent_key="pskill.runner",
        tool_name="psop.runtime.read",
        arguments_summary={"snapshot_limit": 1, "run_event_limit": 5, "run_trace_limit": 5},
        expected_effect_summary="Read persisted Runtime facts as context.",
    )

    assert result.passed is True
    assert result.findings == []
