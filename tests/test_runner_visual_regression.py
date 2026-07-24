from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path

import pytest
from PIL import Image

from app.agent_harness.schemas import AgentInvocation, AgentInvocationAttachment
from app.agent_harness.service import AgentHarnessService
from app.core.config import Settings


FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "psop_runner" / "visual"
MANIFEST = json.loads((FIXTURE_DIR / "manifest.json").read_text(encoding="utf-8"))


def test_runner_visual_regression_fixtures_are_sanitized_and_unchanged() -> None:
    for case in MANIFEST["cases"]:
        path = FIXTURE_DIR / case["fixture"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == case["fixture_sha256"]
        with Image.open(path) as image:
            assert image.format == "PNG"
            assert not image.getexif()
            left, top, right, bottom = case["crop_box"]
            assert image.size == (right - left, bottom - top)


@pytest.mark.skipif(
    os.getenv("PSOP_RUN_VISUAL_REGRESSION") != "1",
    reason="设置 PSOP_RUN_VISUAL_REGRESSION=1 后才调用当前 multimodal model。",
)
def test_current_multimodal_model_passes_runner_incident_launch_gate(tmp_path) -> None:
    settings = Settings(
        database_url="sqlite+pysqlite:///:memory:",
        agent_harness_sandbox_root=str(tmp_path / "agent-runs"),
    )
    service = AgentHarnessService(settings=settings)
    required_passes = int(MANIFEST["launch_gate"]["minimum_passing_runs"])
    runs_per_fixture = int(MANIFEST["launch_gate"]["runs_per_fixture"])

    pass_counts: dict[str, int] = {}
    for case in MANIFEST["cases"]:
        fixture_path = FIXTURE_DIR / case["fixture"]
        observations = [
            _run_visual_case(service, fixture_path=fixture_path, terminal_seq=int(case["source_terminal_seq"]))
            for _ in range(runs_per_fixture)
        ]
        pass_counts[case["case_id"]] = sum(_passes_incident_rubric(item) for item in observations)

    assert all(count >= required_passes for count in pass_counts.values()), pass_counts


def _run_visual_case(
    service: AgentHarnessService,
    *,
    fixture_path: Path,
    terminal_seq: int,
) -> dict:
    event_ref = f"terminal_event:{terminal_seq}"
    part_ref = f"{event_ref}:image_1"
    requirements = [
        {
            "requirement_key": "io_alignment_visual",
            "description": "图片证明后置 I/O 接口与机箱开口对齐，主板已正确落位。",
            "required": True,
            "status": "missing",
            "accepted_event_refs": [],
            "evidence_options": [
                {
                    "option_key": "io_photo",
                    "kind": "image",
                    "event_kind": "terminal.multimodal.input.v1",
                    "proof_mode": "visual",
                }
            ],
        },
        {
            "requirement_key": "screw_presence_visual",
            "description": "图片证明四颗主板固定螺丝存在、落座且主板无明显翘起。",
            "required": True,
            "status": "missing",
            "accepted_event_refs": [],
            "evidence_options": [
                {
                    "option_key": "screw_photo",
                    "kind": "image",
                    "event_kind": "terminal.multimodal.input.v1",
                    "proof_mode": "visual",
                }
            ],
        },
        {
            "requirement_key": "snug_fit_attestation",
            "description": "用户文字确认四颗螺丝手动紧固至 snug fit、主板无晃动且未使用高扭矩工具。",
            "required": True,
            "status": "missing",
            "accepted_event_refs": [],
            "evidence_options": [
                {
                    "option_key": "text_confirmation",
                    "kind": "text",
                    "event_kind": "terminal.text.input.v1",
                    "proof_mode": "attestation",
                }
            ],
        },
    ]
    latest_evidence = {
        "seq_no": terminal_seq,
        "direction": "input",
        "event_kind": "terminal.multimodal.input.v1",
        "mime_type": "multipart/mixed",
        "parts": [
            {
                "part_id": "image_1",
                "kind": "image",
                "mime_type": "image/png",
                "size_bytes": fixture_path.stat().st_size,
            }
        ],
    }
    turn_context = {
        "run_id": f"visual-regression-{terminal_seq}",
        "node": {"id": "evaluate_mount_board", "kind": "llm", "actor": "agent.llm"},
        "mode": "evidence_evaluation",
        "turn_kind": "evidence_evaluation",
        "current_workflow_step": {"id": "mount_board", "title": "安装并固定主板"},
        "previous_evaluation": {},
        "current_checkpoint": {
            "checkpoint_id": "mount_board_evidence",
            "workflow_step_id": "mount_board",
            "latest_evidence_seq": terminal_seq,
        },
        "evidence_progress": {
            "checkpoint_id": "mount_board_evidence",
            "workflow_step_id": "mount_board",
            "requirements": requirements,
        },
        "latest_evidence": latest_evidence,
        "runtime_contract_slice": {
            "evidence_contract_version": "psop-evidence/v2",
            "safety_constraints": ["照片不能单独证明 snug fit、扭矩、无晃动或工具选择。"],
        },
        "output_contract": {
            "schema": "psop.runner.observation.v1",
            "allowed_decisions": ["continue", "need_more_evidence", "retry", "abort", "complete"],
        },
        "terminal_cursor": terminal_seq,
    }
    context = {
        "runtime_contract": {
            "evidence_contract_version": "psop-evidence/v2",
            "workflow_steps": [{"id": "mount_board", "title": "安装并固定主板"}],
            "expected_evidence": {"mount_board": {"requirements": requirements}},
            "safety_constraints": ["照片不能单独证明 snug fit、扭矩、无晃动或工具选择。"],
        },
        "current_checkpoint": {
            "checkpoint_id": "mount_board_evidence",
            "workflow_step_id": "mount_board",
            "evidence": [latest_evidence],
        },
        "evidence_progress": turn_context["evidence_progress"],
        "latest_evidence": latest_evidence,
        "terminal_events": [latest_evidence],
        "terminal_cursor": terminal_seq,
        "allowed_runtime": {"max_terminal_message_chars": 2000},
        "runner_turn_context": turn_context,
    }
    result = service.invoke(
        AgentInvocation(
            agent_key="psop.runner",
            input={
                "task": "assist_psop_runtime_node",
                "node": {
                    "id": "evaluate_mount_board",
                    "kind": "llm",
                    "actor": "agent.llm",
                    "mode": "evidence_evaluation",
                },
                "output_contract": turn_context["output_contract"],
                "text": (
                    "node_id=evaluate_mount_board 评估主板安装证据。\n"
                    f"<RunnerTurnContext>\n{json.dumps(turn_context, ensure_ascii=False)}\n</RunnerTurnContext>"
                ),
            },
            context=context,
            attachments=[
                AgentInvocationAttachment(
                    attachment_id=part_ref,
                    role="evidence",
                    label=f"用户现场证据（可用于 requirement ledger）：{part_ref}",
                    source_ref=part_ref,
                    terminal_event_seq=terminal_seq,
                    part_id="image_1",
                    filename=fixture_path.name,
                    media_type="image/png",
                    size_bytes=fixture_path.stat().st_size,
                    checksum=hashlib.sha256(fixture_path.read_bytes()).hexdigest(),
                    content_base64=base64.b64encode(fixture_path.read_bytes()).decode("ascii"),
                )
            ],
        )
    )
    assert result.status == "succeeded", result.error_message
    return json.loads(Path(result.sandbox_path or "", "outputs", "runner-observation.json").read_text(encoding="utf-8"))


def _passes_incident_rubric(observation: dict) -> bool:
    assessment = observation.get("evidence_assessment")
    results = assessment.get("requirement_results") if isinstance(assessment, dict) else []
    by_key = {
        str(item.get("requirement_key") or ""): item
        for item in results or []
        if isinstance(item, dict)
    }
    return (
        observation.get("decision") == "need_more_evidence"
        and by_key.get("io_alignment_visual", {}).get("status") == "accepted"
        and by_key.get("screw_presence_visual", {}).get("status") == "accepted"
        and by_key.get("snug_fit_attestation", {}).get("status") in {"missing", "rejected", "ambiguous"}
        and not by_key.get("snug_fit_attestation", {}).get("satisfied_by")
    )
