from __future__ import annotations

import json
from collections.abc import Iterator

import pytest

from app.agents.registry import PromptRegistry
from app.domain.compiler.service import CompilerService
from app.domain.compiler.formal_v5 import validate_and_normalize_artifact
from app.domain.jobs.repository import JobRepository
from app.domain.runtime.schemas import AppendTerminalEventRequest, CreateInvocationRequest
from app.domain.runtime.service import RuntimeService
from app.domain.skills.exceptions import SkillValidationError
from app.domain.skills.schemas import CreateSkillRequest, PublishSkillRequest
from app.domain.skills.service import SkillsService
from app.domain.skills.models import SkillVersion
from app.gateway.inference import LlmCompletion
from app.infra.database import DatabaseManager
from tests.test_skills_api import (
    FakeGitLabGateway,
    FakeInferenceGateway,
    build_test_formal_v5_artifact,
    create_test_settings,
)


class FailingInferenceGateway:
    def complete(self, *, system_prompt: str, user_prompt: str, route_key: str = "default") -> LlmCompletion:
        raise RuntimeError("LLM provider unavailable")


class QueuedInferenceGateway:
    def __init__(self, contents: list[str]) -> None:
        self.contents = contents
        self.calls: list[dict[str, str]] = []

    def complete(self, *, system_prompt: str, user_prompt: str, route_key: str = "default") -> LlmCompletion:
        self.calls.append({"system_prompt": system_prompt, "user_prompt": user_prompt, "route_key": route_key})
        content = self.contents.pop(0) if self.contents else "fallback"
        return LlmCompletion(
            content=content,
            provider="fake-openai-compatible",
            model="fake-model",
            raw_response={"id": "fake-response"},
        )


@pytest.fixture
def runtime_stack() -> Iterator[tuple[DatabaseManager, FakeGitLabGateway, FakeInferenceGateway, CompilerService, SkillsService, RuntimeService]]:
    settings = create_test_settings()
    database_manager = DatabaseManager(settings.sqlalchemy_database_url)
    database_manager.create_schema()
    gitlab_gateway = FakeGitLabGateway()
    inference_gateway = FakeInferenceGateway()
    compiler_service = CompilerService(
        settings=settings,
        gitlab_gateway=gitlab_gateway,
        inference_gateway=inference_gateway,
    )
    skills_service = SkillsService(
        settings=settings,
        gitlab_gateway=gitlab_gateway,
        compiler_service=compiler_service,
    )
    runtime_service = RuntimeService(settings=settings, inference_gateway=inference_gateway)

    try:
        yield database_manager, gitlab_gateway, inference_gateway, compiler_service, skills_service, runtime_service
    finally:
        database_manager.dispose()


def test_compiler_emits_mvp_formal_v5_artifact(runtime_stack) -> None:
    database_manager, _, inference_gateway, compiler_service, skills_service, _ = runtime_stack

    with database_manager.session() as session:
        skill = skills_service.create_skill(
            session,
            CreateSkillRequest(
                key="compiler-unit",
                name="Compiler Unit",
                description="Validate compiler output.",
            ),
        )
        published = skills_service.publish_skill(
            session,
            skill_id=skill.id,
            payload=PublishSkillRequest(publish_reason="Compile unit test"),
        )

        assert published.compile_request is not None
        assert published.compile_request.status == "pending"
        compiled = process_publish_job(session, compiler_service, published.compile_request.id)

        artifact = compiler_service.get_artifact(session, compiled.artifact_id or "")
        diagnostics = compiler_service.list_diagnostics(session, published.compile_request.id)
        progress = compiler_service.get_compile_progress(session, published.compile_request.id)

    assert compiled.status == "succeeded"
    assert progress.terminal is True
    assert progress.terminal_status == "succeeded"
    assert artifact.formal_revision == "psop-eg-formal/v5"
    assert artifact.artifact is not None
    assert inference_gateway.calls[0]["system_prompt"] == PromptRegistry().load_default_compile_agent().system_prompt
    assert artifact.artifact["graph_summary"]["template"] == "formal-v5 skill workflow graph"
    assert artifact.artifact["graph_summary"]["workflow_nodes"] == [
        "instruct_collect_context",
        "evaluate_collect_context",
        "final_verify",
    ]
    assert artifact.artifact["compiler_metadata"]["agent_prompt"]["prompt_hash"]
    assert artifact.artifact["compiler_metadata"]["domain_pack"]["domain_pack_id"] == "generic"
    assert artifact.artifact["schema"]["input_name"] == "user_input"
    assert artifact.capability_summary["tools"] == []
    assert any(item.code == "compile.agent.enabled" for item in diagnostics)
    assert any(item.code == "compile.agent.prompt_pack" for item in diagnostics)


def test_compiler_records_diagnostics_for_unsupported_formal_revision(runtime_stack) -> None:
    database_manager, gitlab_gateway, _, compiler_service, skills_service, _ = runtime_stack

    with database_manager.session() as session:
        skill = skills_service.create_skill(
            session,
            CreateSkillRequest(
                key="bad-formal",
                name="Bad Formal",
                description="Validate compiler diagnostics.",
            ),
        )
        draft_version = session.get(SkillVersion, skill.current_draft_version.id)
        assert draft_version is not None
        manifest = dict(draft_version.manifest_snapshot or {})
        manifest["compile_config"] = {
            **manifest.get("compile_config", {}),
            "formal_revision": "psop-eg-formal/v0",
        }
        draft_version.manifest_snapshot = manifest
        session.flush()

        published = skills_service.publish_skill(
            session,
            skill_id=skill.id,
            payload=PublishSkillRequest(publish_reason="Expect compile failure"),
        )
        assert published.compile_request is not None
        compiled = process_publish_job(session, compiler_service, published.compile_request.id)
        diagnostics = compiler_service.list_diagnostics(session, published.compile_request.id)
        detail = skills_service.get_skill_detail(session, skill.id)
        publish_records = skills_service.list_publish_records(session, skill_id=skill.id)

    assert compiled.status == "failed"
    assert compiled.artifact_id is None
    assert detail.latest_published_version is None
    assert publish_records[0].publish_status == "failed"
    assert any(item.code == "compile.unsupported_formal_revision" for item in diagnostics)


def test_compiler_repairs_invalid_agent_json_once() -> None:
    settings = create_test_settings()
    database_manager = DatabaseManager(settings.sqlalchemy_database_url)
    database_manager.create_schema()
    gitlab_gateway = FakeGitLabGateway()
    inference_gateway = QueuedInferenceGateway(
        [
            "not-json",
            __import__("json").dumps(build_test_formal_v5_artifact(), ensure_ascii=False),
        ]
    )
    compiler_service = CompilerService(
        settings=settings,
        gitlab_gateway=gitlab_gateway,
        inference_gateway=inference_gateway,
    )
    skills_service = SkillsService(
        settings=settings,
        gitlab_gateway=gitlab_gateway,
        compiler_service=compiler_service,
    )

    try:
        with database_manager.session() as session:
            skill = skills_service.create_skill(
                session,
                CreateSkillRequest(
                    key="agent-repair",
                    name="Agent Repair",
                    description="Validate compiler repair.",
                ),
            )
            published = skills_service.publish_skill(
                session,
                skill_id=skill.id,
                payload=PublishSkillRequest(publish_reason="Repair invalid JSON"),
            )
            compiled = process_publish_job(session, compiler_service, published.compile_request.id)
            diagnostics = compiler_service.list_diagnostics(session, published.compile_request.id)
            publish_records = skills_service.list_publish_records(session, skill_id=skill.id)

        assert compiled.status == "succeeded"
        assert publish_records[0].publish_status == "published"
        assert len(inference_gateway.calls) == 2
        assert any(item.code == "compile.agent.invalid_json" for item in diagnostics)
    finally:
        database_manager.dispose()


def test_compiler_injects_selected_domain_pack() -> None:
    settings = create_test_settings()
    database_manager = DatabaseManager(settings.sqlalchemy_database_url)
    database_manager.create_schema()
    gitlab_gateway = FakeGitLabGateway()
    inference_gateway = QueuedInferenceGateway([json.dumps(build_test_formal_v5_artifact(), ensure_ascii=False)])
    compiler_service = CompilerService(
        settings=settings,
        gitlab_gateway=gitlab_gateway,
        inference_gateway=inference_gateway,
    )
    skills_service = SkillsService(
        settings=settings,
        gitlab_gateway=gitlab_gateway,
        compiler_service=compiler_service,
    )

    try:
        with database_manager.session() as session:
            skill = skills_service.create_skill(
                session,
                CreateSkillRequest(
                    key="maintenance-pack",
                    name="Maintenance Pack",
                    description="Validate equipment maintenance domain pack.",
                ),
            )
            draft_version = session.get(SkillVersion, skill.current_draft_version.id)
            assert draft_version is not None
            manifest = dict(draft_version.manifest_snapshot or {})
            manifest["compile_config"] = {
                **manifest.get("compile_config", {}),
                "domain_pack": "equipment_maintenance",
            }
            draft_version.manifest_snapshot = manifest
            session.flush()

            published = skills_service.publish_skill(
                session,
                skill_id=skill.id,
                payload=PublishSkillRequest(publish_reason="Use maintenance domain pack"),
            )
            assert published.compile_request is not None
            compiled = process_publish_job(session, compiler_service, published.compile_request.id)
            artifact = compiler_service.get_artifact(session, compiled.artifact_id or "")

        prompt_payload = json.loads(inference_gateway.calls[0]["user_prompt"])
        assert prompt_payload["domain_pack"]["domain_pack_id"] == "equipment_maintenance"
        assert "故障诊断" in prompt_payload["domain_pack"]["guidance"]
        assert artifact.artifact is not None
        assert artifact.artifact["compiler_metadata"]["domain_pack"]["domain_pack_id"] == "equipment_maintenance"
    finally:
        database_manager.dispose()


def test_compiler_falls_back_for_unknown_domain_pack() -> None:
    settings = create_test_settings()
    database_manager = DatabaseManager(settings.sqlalchemy_database_url)
    database_manager.create_schema()
    gitlab_gateway = FakeGitLabGateway()
    inference_gateway = QueuedInferenceGateway([json.dumps(build_test_formal_v5_artifact(), ensure_ascii=False)])
    compiler_service = CompilerService(
        settings=settings,
        gitlab_gateway=gitlab_gateway,
        inference_gateway=inference_gateway,
    )
    skills_service = SkillsService(
        settings=settings,
        gitlab_gateway=gitlab_gateway,
        compiler_service=compiler_service,
    )

    try:
        with database_manager.session() as session:
            skill = skills_service.create_skill(
                session,
                CreateSkillRequest(
                    key="fallback-pack",
                    name="Fallback Pack",
                    description="Validate domain pack fallback.",
                ),
            )
            draft_version = session.get(SkillVersion, skill.current_draft_version.id)
            assert draft_version is not None
            manifest = dict(draft_version.manifest_snapshot or {})
            manifest["compile_config"] = {
                **manifest.get("compile_config", {}),
                "domain_pack": "unknown_domain",
            }
            draft_version.manifest_snapshot = manifest
            session.flush()

            published = skills_service.publish_skill(
                session,
                skill_id=skill.id,
                payload=PublishSkillRequest(publish_reason="Use fallback domain pack"),
            )
            assert published.compile_request is not None
            compiled = process_publish_job(session, compiler_service, published.compile_request.id)
            diagnostics = compiler_service.list_diagnostics(session, published.compile_request.id)
            artifact = compiler_service.get_artifact(session, compiled.artifact_id or "")

        prompt_payload = json.loads(inference_gateway.calls[0]["user_prompt"])
        assert prompt_payload["domain_pack"]["domain_pack_id"] == "generic"
        assert artifact.artifact is not None
        assert artifact.artifact["compiler_metadata"]["domain_pack"]["used_default"] is True
        assert any(item.code == "compile.agent.domain_pack_fallback" for item in diagnostics)
    finally:
        database_manager.dispose()


def process_publish_job(session, compiler_service: CompilerService, compile_request_id: str):
    job = JobRepository().get_compile_job(session, compile_request_id)
    assert job is not None
    if job.status != "running":
        job.status = "running"
        job.attempt_no += 1
        session.commit()
    compiler_service.process_compile_job(session, job.id)
    return compiler_service.get_compile_request(session, compile_request_id)


def test_compiler_fails_when_agent_repair_still_invalid() -> None:
    settings = create_test_settings()
    database_manager = DatabaseManager(settings.sqlalchemy_database_url)
    database_manager.create_schema()
    gitlab_gateway = FakeGitLabGateway()
    inference_gateway = QueuedInferenceGateway(["not-json", "still-not-json"])
    compiler_service = CompilerService(
        settings=settings,
        gitlab_gateway=gitlab_gateway,
        inference_gateway=inference_gateway,
    )
    skills_service = SkillsService(
        settings=settings,
        gitlab_gateway=gitlab_gateway,
        compiler_service=compiler_service,
    )

    try:
        with database_manager.session() as session:
            skill = skills_service.create_skill(
                session,
                CreateSkillRequest(
                    key="agent-failure",
                    name="Agent Failure",
                    description="Validate compiler failure.",
                ),
            )
            published = skills_service.publish_skill(
                session,
                skill_id=skill.id,
                payload=PublishSkillRequest(publish_reason="Expect repair failure"),
            )
            compiled = process_publish_job(session, compiler_service, published.compile_request.id)
            diagnostics = compiler_service.list_diagnostics(session, published.compile_request.id)
            detail = skills_service.get_skill_detail(session, skill.id)
            publish_records = skills_service.list_publish_records(session, skill_id=skill.id)

        assert compiled.status == "failed"
        assert publish_records[0].publish_status == "failed"
        assert detail.latest_published_version is None
        assert len(inference_gateway.calls) == 2
        assert any(item.code == "compile.agent.repair_failed" for item in diagnostics)
    finally:
        database_manager.dispose()


def test_formal_v5_validator_rejects_unsafe_or_incomplete_artifacts() -> None:
    missing_start = build_test_formal_v5_artifact()
    missing_start["nodes"] = [node for node in missing_start["nodes"] if node["id"] != "start"]
    assert validate_and_normalize_artifact(missing_start).has_errors

    missing_terminal = build_test_formal_v5_artifact()
    missing_terminal["nodes"] = [node for node in missing_terminal["nodes"] if node["kind"] != "terminal"]
    assert validate_and_normalize_artifact(missing_terminal).has_errors

    unknown_actor = build_test_formal_v5_artifact()
    unknown_actor["nodes"][0]["actor"] = {"name": "runtime.exec_python"}
    unknown_result = validate_and_normalize_artifact(unknown_actor)
    assert unknown_result.has_errors
    assert any(item.code == "compile.unsupported_actor" for item in unknown_result.diagnostics)

    unknown_field = build_test_formal_v5_artifact()
    unknown_field["nodes"][0]["guard"] = {"field_exists": "unknown_root.value"}
    unknown_field_result = validate_and_normalize_artifact(unknown_field)
    assert unknown_field_result.has_errors
    assert any(item.code == "compile.formal_v5.validation_failed" for item in unknown_field_result.diagnostics)


def test_formal_v5_validator_rejects_generic_shell_without_skill_workflow() -> None:
    generic_shell = build_test_formal_v5_artifact()
    generic_shell["runtime_contract"] = {"llm_route_key": "default", "skill_instruction": "遵循 SKILL.md。"}

    result = validate_and_normalize_artifact(generic_shell)

    assert result.has_errors
    assert any(item.code == "compile.workflow.not_extracted" for item in result.diagnostics)


def test_formal_v5_validator_normalizes_common_agent_dsl_aliases() -> None:
    artifact = build_test_formal_v5_artifact()
    artifact["nodes"][0]["guard"] = {"op": "phase_is", "value": "start"}
    artifact["nodes"][1]["guard"] = {"op": "phase_is", "phase": "instruct_collect_context"}
    artifact["nodes"][1]["merge"].append({"op": "set", "path": "llm_response", "from": "observation.content"})
    artifact["nodes"][2]["guard"] = {"op": "phase_is", "phase": "evaluate_collect_context"}
    artifact["nodes"][4]["merge"][0]["path"] = "final_response"
    artifact["halt"] = {"op": "field_equals", "path": "status", "value": "success"}

    result = validate_and_normalize_artifact(artifact)

    assert not result.has_errors
    assert result.artifact is not None
    assert result.artifact["nodes"][0]["guard"] == {"phase_is": "start"}
    assert result.artifact["nodes"][1]["merge"][-1]["path"] == "observations.llm.content"
    assert result.artifact["nodes"][4]["merge"][0]["path"] == "outputs.final_response"
    assert result.artifact["halt"] == {"success": {"field_equals": {"path": "status", "value": "success"}}}


def test_runtime_service_waits_for_real_world_evidence_and_builds_replay(runtime_stack) -> None:
    database_manager, _, inference_gateway, compiler_service, skills_service, runtime_service = runtime_stack

    with database_manager.session() as session:
        skill = skills_service.create_skill(
            session,
            CreateSkillRequest(
                key="runtime-unit",
                name="Runtime Unit",
                description="Validate runtime loop.",
            ),
        )
        published = skills_service.publish_skill(
            session,
            skill_id=skill.id,
            payload=PublishSkillRequest(publish_reason="Runtime unit publish"),
        )
        process_publish_job(session, compiler_service, published.compile_request.id)

        invocation = runtime_service.create_invocation(
            session,
            CreateInvocationRequest(
                skill_key="runtime-unit",
                terminal_context={"terminal_kind": "web"},
            ),
        )
        initial_run = runtime_service.get_run(session, invocation.run_id or "")
        initial_events = runtime_service.list_terminal_events(session, invocation.run_id or "")
        appended = runtime_service.append_terminal_event(
            session,
            invocation.run_id or "",
            AppendTerminalEventRequest(
                direction="input",
                event_kind="terminal.text.input.v1",
                mime_type="text/plain",
                payload_inline="我已经完成当前步骤，并上传了现场说明。",
                external_event_id="runtime-unit-evidence-001",
            ),
        )
        run = runtime_service.get_run(session, invocation.run_id or "")
        replay = runtime_service.build_replay(session, invocation.run_id or "")
        snapshots = runtime_service.list_snapshots(session, invocation.run_id or "")
        terminal_session = runtime_service.get_terminal_session(session, invocation.run_id or "")
        terminal_events = runtime_service.list_terminal_events(session, invocation.run_id or "")
        bindings = runtime_service.list_run_bindings(session, invocation.run_id or "")

    assert invocation.status == "running"
    assert invocation.terminal_session_id
    assert initial_run.status == "waiting_input"
    assert initial_run.current_step == "collect_context"
    assert initial_run.checkpoint_id == "collect_context_evidence"
    assert initial_run.expected_inputs
    assert [event.direction for event in initial_events] == ["output"]
    assert appended.seq_no == 2
    assert run.status == "succeeded"
    assert run.latest_snapshot_seq == 5
    assert run.latest_terminal_seq == 5
    assert run.latest_trace_seq == 7
    assert run.terminal_session_id == invocation.terminal_session_id
    assert len(run.binding_summary) == 2
    assert run.latest_evaluation["decision"] == "complete"
    assert "测试任务已完成" in run.final_output
    assert "final_verify" in inference_gateway.calls[-1]["system_prompt"]
    assert [snapshot.seq_no for snapshot in snapshots] == [0, 1, 2, 3, 4, 5]
    assert terminal_session.terminal_session.id == invocation.terminal_session_id
    assert terminal_session.terminal_session.status == "closed"
    assert [event.direction for event in terminal_events] == ["output", "input", "output", "output", "output"]
    assert terminal_events[1].payload_inline == "我已经完成当前步骤，并上传了现场说明。"
    assert {binding.requirement_key for binding in bindings} == {"terminal.input", "terminal.output"}
    assert len(replay.terminal_events) == 5
    assert len(replay.bindings) == 2
    assert [item.event_type for item in replay.timeline][:5] == [
        "binding.resolved",
        "runtime.start.completed",
        "terminal.event.appended",
        "runtime.wait_checkpoint.entered",
        "gateway.inference.completed",
    ]
    assert "runtime.final.completed" in [item.event_type for item in replay.timeline]


def test_terminal_event_append_is_ordered_and_idempotent(runtime_stack) -> None:
    database_manager, _, _, compiler_service, skills_service, runtime_service = runtime_stack

    with database_manager.session() as session:
        skill = skills_service.create_skill(
            session,
            CreateSkillRequest(
                key="terminal-event-unit",
                name="Terminal Event Unit",
                description="Validate terminal transcript append.",
            ),
        )
        published = skills_service.publish_skill(
            session,
            skill_id=skill.id,
            payload=PublishSkillRequest(publish_reason="Terminal event unit publish"),
        )
        process_publish_job(session, compiler_service, published.compile_request.id)
        invocation = runtime_service.create_invocation(
            session,
            CreateInvocationRequest(
                skill_key="terminal-event-unit",
                input_envelope={"user_input": "初始输入"},
            ),
        )
        bindings = runtime_service.list_run_bindings(session, invocation.run_id or "")
        input_binding = next(item for item in bindings if item.requirement_key == "terminal.input")

        appended = runtime_service.append_terminal_event(
            session,
            invocation.run_id or "",
            AppendTerminalEventRequest(
                direction="input",
                event_kind="terminal.text.input.v1",
                mime_type="text/plain",
                payload_inline="追加输入",
                binding_id=input_binding.id,
                external_event_id="evt-001",
            ),
        )
        duplicate = runtime_service.append_terminal_event(
            session,
            invocation.run_id or "",
            AppendTerminalEventRequest(
                direction="input",
                event_kind="terminal.text.input.v1",
                mime_type="text/plain",
                payload_inline="不会重复",
                binding_id=input_binding.id,
                external_event_id="evt-001",
            ),
        )
        events = runtime_service.list_terminal_events(session, invocation.run_id or "")

        with pytest.raises(SkillValidationError):
            runtime_service.append_terminal_event(
                session,
                invocation.run_id or "",
                AppendTerminalEventRequest(
                    direction="sideways",
                    event_kind="terminal.text.input.v1",
                    payload_inline="bad",
                ),
            )

    assert appended.seq_no == 3
    assert duplicate.event_id == appended.event_id
    assert duplicate.seq_no == appended.seq_no
    assert [event.seq_no for event in events] == [1, 2, 3, 4, 5, 6]
    assert events[2].payload_inline == "追加输入"


def test_runtime_service_records_failed_run_when_llm_fails(runtime_stack) -> None:
    database_manager, _, _, compiler_service, skills_service, _ = runtime_stack
    failing_runtime = RuntimeService(settings=create_test_settings(), inference_gateway=FailingInferenceGateway())

    with database_manager.session() as session:
        skill = skills_service.create_skill(
            session,
            CreateSkillRequest(
                key="runtime-failure",
                name="Runtime Failure",
                description="Validate runtime failure trace.",
            ),
        )
        published = skills_service.publish_skill(
            session,
            skill_id=skill.id,
            payload=PublishSkillRequest(publish_reason="Runtime failure publish"),
        )
        process_publish_job(session, compiler_service, published.compile_request.id)

        invocation = failing_runtime.create_invocation(
            session,
            CreateInvocationRequest(
                skill_key="runtime-failure",
                input_envelope={"user_input": "触发失败"},
            ),
        )
        run = failing_runtime.get_run(session, invocation.run_id or "")
        trace_events = failing_runtime.list_trace_events(session, invocation.run_id or "")

    assert invocation.status == "failed"
    assert run.status == "failed"
    assert run.exit_reason == "LLM provider unavailable"
    assert trace_events[-1].event_type == "runtime.failed"
