from __future__ import annotations

import json
from collections.abc import Iterator

import pytest

from app.agents.registry import PromptRegistry
from app.compiler.service import CompilerService
from app.compiler.formal_v5 import validate_and_normalize_artifact
from app.jobs.repository import JobRepository
from app.memory.models import AgentMemoryEntry
from app.runtime.schemas import AppendTerminalEventRequest, CreateInvocationRequest
from app.runtime.service import RuntimeService
from app.pskills.exceptions import SkillsGatewayError, SkillValidationError
from app.pskills.schemas import CreateSkillRequest, PublishSkillRequest
from app.pskills.service import SkillsService
from app.pskills.models import PSkillVersion
from app.gateway.inference import LlmCompletion
from app.infra.database import DatabaseManager
from tests.test_skills_api import (
    FakeGitLabGateway,
    FakeInferenceGateway,
    build_test_formal_v5_artifact,
    create_test_settings,
)


class FailingInferenceGateway:
    def complete(self, *, system_prompt: str, user_prompt: str, route_key: str = "text") -> LlmCompletion:
        raise RuntimeError("LLM provider unavailable")


class FailingSkillsGatewayInferenceGateway:
    details = {
        "status_code": 500,
        "provider": "aliyun",
        "api_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen3.7-plus",
        "route_key": "text",
        "body": json.dumps(
            {
                "error": {
                    "message": "Too many requests. Your requests are being throttled due to system capacity limits.",
                    "type": "ServiceUnavailable",
                    "code": "ServiceUnavailable",
                },
                "id": "chatcmpl-test",
                "request_id": "request-test",
            }
        ),
        "api_key": "should-not-leak",
    }

    def complete(self, *, system_prompt: str, user_prompt: str, route_key: str = "text") -> LlmCompletion:
        raise SkillsGatewayError("LLM Inference Gateway 返回错误响应。", details=self.details)


class QueuedInferenceGateway:
    def __init__(self, contents: list[str]) -> None:
        self.contents = contents
        self.calls: list[dict[str, str]] = []

    def complete(self, *, system_prompt: str, user_prompt: str, route_key: str = "text") -> LlmCompletion:
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
    prompt_payload = json.loads(inference_gateway.calls[0]["user_prompt"])
    assert "runtime_language_rule" in prompt_payload["workflow_compilation_contract"]
    assert "reason、terminal_message" in prompt_payload["workflow_compilation_contract"]["runtime_language_rule"]
    assert "用户可见自然语言必须使用简体中文" in inference_gateway.calls[0]["system_prompt"]
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


def test_compiler_spans_include_publish_compile_provenance_context(runtime_stack, monkeypatch) -> None:
    database_manager, _, _, compiler_service, skills_service, _ = runtime_stack
    captured_spans: list[dict] = []

    class CapturedSpan:
        def __init__(self, name: str, attributes: dict) -> None:
            self.name = name
            self.attributes = dict(attributes)
            captured_spans.append({"name": name, "attributes": self.attributes})

        def __enter__(self):
            return self

        def __exit__(self, *_exc_info):
            return False

        def set_attribute(self, key: str, value) -> None:
            self.attributes[key] = value

        def add_event(self, _name: str, attributes: dict | None = None) -> None:
            return None

        def record_exception(self, _exception: Exception) -> None:
            return None

        def set_status(self, _status) -> None:
            return None

    def fake_start_span(name: str, **attributes):
        return CapturedSpan(name, attributes)

    monkeypatch.setattr("app.compiler.service.start_span", fake_start_span)

    with database_manager.session() as session:
        skill = skills_service.create_skill(
            session,
            CreateSkillRequest(
                key="compiler-span-provenance",
                name="Compiler Span Provenance",
                description="Validate Compiler OTel span correlation.",
            ),
        )
        published = skills_service.publish_skill(
            session,
            skill_id=skill.id,
            payload=PublishSkillRequest(publish_reason="Compiler span provenance publish"),
        )
        assert published.compile_request is not None
        compile_request_id = published.compile_request.id
        pskill_version_id = published.published_version.id
        source_commit_sha = published.published_version.source_commit_sha
        compiled = process_publish_job(session, compiler_service, compile_request_id)
        artifact_id = compiled.artifact_id or ""

    assert compiled.status == "succeeded"
    assert artifact_id
    expected_names = {
        "job.compile",
        "compile.source_load",
        "compile.manifest_check",
        "compile.agent",
        "compile.agent.invoke",
        "compile.validate",
        "compile.emit",
    }
    compiler_spans = [span for span in captured_spans if span["name"] in expected_names]
    assert {span["name"] for span in compiler_spans} == expected_names

    for span in compiler_spans:
        attributes = span["attributes"]
        assert attributes["compile_request_id"] == compile_request_id
        assert attributes["skill_id"] == skill.id
        assert attributes["pskill_definition_id"] == skill.id
        assert attributes["pskill_version_id"] == pskill_version_id
        assert attributes["skill_version_id"] == pskill_version_id
        assert attributes["source_commit_sha"] == source_commit_sha

    invoke_span = next(span for span in compiler_spans if span["name"] == "compile.agent.invoke")
    validate_span = next(span for span in compiler_spans if span["name"] == "compile.validate")
    emit_span = next(span for span in compiler_spans if span["name"] == "compile.emit")
    job_span = next(span for span in compiler_spans if span["name"] == "job.compile")

    assert invoke_span["attributes"]["attempt"] == 1
    assert validate_span["attributes"]["attempt"] == 1
    assert emit_span["attributes"]["compile_artifact_id"] == artifact_id
    assert emit_span["attributes"]["artifact_object_id"]
    assert job_span["attributes"]["compile_artifact_id"] == artifact_id
    assert job_span["attributes"]["compile_status"] == "succeeded"


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
        draft_version = session.get(PSkillVersion, skill.current_draft_version.id)
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
            draft_version = session.get(PSkillVersion, skill.current_draft_version.id)
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
            draft_version = session.get(PSkillVersion, skill.current_draft_version.id)
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


def build_test_abort_formal_v5_artifact() -> dict:
    artifact = build_test_formal_v5_artifact()
    artifact["nodes"].append(
        {
            "id": "terminal_abort",
            "kind": "terminal",
            "guard": {"phase_is": "terminal_abort"},
            "actor": {"name": "runtime.terminal"},
            "merge": [
                {"op": "set", "path": "outputs.final_response", "from": "observation.final_response"},
                {"op": "set", "path": "status", "value": "aborted"},
                {"op": "set", "path": "phase", "value": "aborted"},
            ],
            "policy": {"priority": 50},
        }
    )
    artifact["halt"] = {
        "success": {"field_equals": {"path": "status", "value": "success"}},
        "aborted": {"field_equals": {"path": "status", "value": "aborted"}},
    }
    artifact["dependency_graph_for_view"].append({"from": "evaluate_collect_context", "to": "terminal_abort"})
    return artifact


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
    generic_shell["runtime_contract"] = {"llm_route_key": "text", "skill_instruction": "遵循 SKILL.md。"}

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


def test_formal_v5_validator_rejects_observation_merge_into_runtime_kernel_state() -> None:
    artifact = build_test_formal_v5_artifact()
    artifact["nodes"][1]["merge"].append({"op": "set", "path": "status", "from": "observation.status"})

    result = validate_and_normalize_artifact(artifact)

    assert result.has_errors
    assert result.artifact is None
    assert any(item.code == "compile.runtime_kernel_sovereignty_violation" for item in result.diagnostics)


def test_formal_v5_validator_rejects_unknown_merge_source() -> None:
    artifact = build_test_formal_v5_artifact()
    artifact["nodes"][1]["merge"].append({"op": "set", "path": "outputs.final_response", "from": "model.answer"})

    result = validate_and_normalize_artifact(artifact)

    assert result.has_errors
    assert result.artifact is None
    assert any(
        item.code == "compile.formal_v5.validation_failed"
        and item.location == {"path": "nodes[1].merge[1].from"}
        for item in result.diagnostics
    )


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
        session.add(
            AgentMemoryEntry(
                namespace="runtime",
                memory_type="semantic",
                agent_key="pskill.runner",
                status="active",
                title="runtime-memory-boundary-sentinel",
                content="Agent memory must not become Runtime Session Token state.",
                source_refs=[{"kind": "agent_run", "id": "memory-boundary-sentinel-run"}],
                confidence=90,
            )
        )
        session.flush()

        invocation = runtime_service.create_invocation(
            session,
            CreateInvocationRequest(
                skill_key="runtime-unit",
                terminal_context={"terminal_kind": "web"},
            ),
        )
        initial_run = runtime_service.get_run(session, invocation.run_id or "")
        initial_events = runtime_service.list_terminal_events(session, invocation.run_id or "")
        legacy_snapshots = runtime_service.repository.list_snapshots(session, invocation.run_id or "")
        legacy_token = json.loads(json.dumps(legacy_snapshots[-1].token_payload, ensure_ascii=False))
        legacy_observation = legacy_token.setdefault("observations", {}).setdefault("instruct_collect_context", {})
        legacy_observation["input"] = {
            "system_prompt": "legacy-system-prompt",
            "user_prompt": "legacy-user-prompt " * 1000,
        }
        legacy_snapshots[-1].token_payload = legacy_token
        session.flush()

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
        trace_events = runtime_service.list_trace_events(session, invocation.run_id or "")
        terminal_session = runtime_service.get_terminal_session(session, invocation.run_id or "")
        terminal_events = runtime_service.list_terminal_events(session, invocation.run_id or "")
        bindings = runtime_service.list_run_bindings(session, invocation.run_id or "")
        runner_runs = runtime_service.agent_service.list_runs(
            session,
            agent_key="pskill.runner",
            owner_type="runtime_run",
            owner_id=invocation.run_id or "",
        )
        runner_events_by_run = {
            item.id: runtime_service.agent_service.list_events(session, item.id)
            for item in runner_runs
        }
        runner_model_calls_by_run = {
            item.id: runtime_service.agent_service.list_model_calls(session, item.id)
            for item in runner_runs
        }

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
    assert snapshots[-1].token_payload["budgets"]["llm_input_tokens"] == 30
    assert snapshots[-1].token_payload["budgets"]["llm_output_tokens"] == 15
    assert all(snapshot.token_payload.get("memory", {}) == {} for snapshot in snapshots)
    serialized_snapshots = json.dumps(
        [snapshot.token_payload for snapshot in snapshots],
        ensure_ascii=False,
        sort_keys=True,
    )
    assert "runtime-memory-boundary-sentinel" not in serialized_snapshots
    assert "memory-boundary-sentinel-run" not in serialized_snapshots
    assert "input" not in snapshots[-1].token_payload["observations"]["instruct_collect_context"]
    assert "request" not in snapshots[-1].token_payload["observations"]["instruct_collect_context"]
    assert "_trace_request" not in snapshots[-1].token_payload["observations"]["instruct_collect_context"]
    assert snapshots[-1].token_payload["observations"]["instruct_collect_context"]["input_summary"]["user_chars"] > 0
    runtime_llm_calls = inference_gateway.calls[1:]
    assert runtime_llm_calls
    assert len(runner_runs) == len(runtime_llm_calls) == 3
    runner_run_ids = {item.id for item in runner_runs}
    assert all(item.agent_key == "pskill.runner" for item in runner_runs)
    assert all(item.status == "succeeded" for item in runner_runs)
    assert all(item.run_id == invocation.run_id for item in runner_runs)
    assert all(item.output_payload["schema"] == "RuntimeAgentObservation" for item in runner_runs)
    assert all(item.output_payload["observation"]["schema"] == "RuntimeAgentObservation" for item in runner_runs)
    assert all("_trace_request" not in item.output_payload["observation"] for item in runner_runs)
    assert all("request" not in item.output_payload["observation"] for item in runner_runs)
    assert all(item.input_payload["runtime_context"]["latest_snapshot_seq"] >= 0 for item in runner_runs)
    assert all(
        {"agent.run.created", "runtime.node.started", "runtime.agent.model_call.completed", "runtime.agent.observation.returned"}
        <= {event.event_type for event in runner_events_by_run[item.id]}
        for item in runner_runs
    )
    assert all(calls and calls[0].provider == "llm_inference_gateway" for calls in runner_model_calls_by_run.values())
    assert all("平台级输出语言要求" in call["system_prompt"] for call in runtime_llm_calls)
    assert all("JSON 字段名和 decision/next_phase 等协议枚举值保持英文协议值" in call["system_prompt"] for call in runtime_llm_calls)
    assert all("reason、terminal_message、final_response、summary" in call["system_prompt"] for call in runtime_llm_calls)
    assert all("legacy-user-prompt" not in call["user_prompt"] for call in inference_gateway.calls[1:])
    llm_trace_payloads = [
        event.payload for event in trace_events if event.event_type == "gateway.inference.completed"
    ]
    llm_trace_agent_run_ids = {
        event.agent_run_id for event in trace_events if event.event_type == "gateway.inference.completed"
    }
    assert llm_trace_agent_run_ids == runner_run_ids
    assert "input" not in llm_trace_payloads[0]["observation"]
    assert "_trace_request" not in llm_trace_payloads[0]["observation"]
    assert llm_trace_payloads[0]["observation"]["input_summary"]["system_prompt_hash"]
    assert llm_trace_payloads[0]["observation"]["input_summary"]["route_key"] == "text"
    assert llm_trace_payloads[0]["observation"]["output"]["content"]
    assert llm_trace_payloads[0]["observation"]["usage"]["total_tokens"] == 15
    trace_request = llm_trace_payloads[0]["observation"]["request"]
    assert trace_request["headers"]["Authorization"] == "Bearer [redacted]"
    assert trace_request["body"]["messages"][0]["role"] == "system"
    assert "平台级输出语言要求" in trace_request["body"]["messages"][0]["content"]
    assert trace_request["body"]["messages"][1]["content"] == runtime_llm_calls[0]["user_prompt"]
    assert terminal_session.terminal_session.id == invocation.terminal_session_id
    assert terminal_session.terminal_session.status == "closed"
    assert [event.direction for event in terminal_events] == ["output", "input", "output", "output", "output"]
    assert terminal_events[1].payload_inline == "我已经完成当前步骤，并上传了现场说明。"
    assert terminal_events[0].agent_run_id in runner_run_ids
    assert terminal_events[1].agent_run_id is None
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


def test_runtime_run_traces_capture_current_otel_context(runtime_stack, monkeypatch) -> None:
    database_manager, _, _, compiler_service, skills_service, runtime_service = runtime_stack
    monkeypatch.setattr(
        "app.runtime.service.current_trace_context",
        lambda: {
            "trace_id": "1" * 32,
            "span_id": "2" * 16,
        },
    )

    with database_manager.session() as session:
        skill = skills_service.create_skill(
            session,
            CreateSkillRequest(
                key="runtime-otel-context",
                name="Runtime OTel Context",
                description="Validate RunTrace OTel correlation.",
            ),
        )
        published = skills_service.publish_skill(
            session,
            skill_id=skill.id,
            payload=PublishSkillRequest(publish_reason="Runtime OTel context publish"),
        )
        process_publish_job(session, compiler_service, published.compile_request.id)

        invocation = runtime_service.create_invocation(
            session,
            CreateInvocationRequest(
                skill_key="runtime-otel-context",
                input_envelope={"user_input": "请检查现场任务。"},
                terminal_context={"terminal_kind": "web"},
            ),
        )
        traces = runtime_service.list_run_traces(session, invocation.run_id or "")
        replay = runtime_service.build_replay(session, invocation.run_id or "")

    assert traces
    assert {trace.trace_id for trace in traces} == {"1" * 32}
    assert {trace.span_id for trace in traces} == {"2" * 16}
    assert {trace.trace_id for trace in replay.run_traces} == {"1" * 32}
    assert {trace.span_id for trace in replay.run_traces} == {"2" * 16}


def test_runtime_spans_include_replay_provenance_context(runtime_stack, monkeypatch) -> None:
    database_manager, _, _, compiler_service, skills_service, runtime_service = runtime_stack
    captured_spans: list[dict] = []

    class CapturedSpan:
        def __init__(self, name: str, attributes: dict) -> None:
            self.name = name
            self.attributes = dict(attributes)

        def __enter__(self):
            captured_spans.append({"name": self.name, "attributes": self.attributes})
            return self

        def __exit__(self, *_exc_info) -> None:
            return None

        def set_attribute(self, key: str, value) -> None:
            self.attributes[key] = value

        def record_exception(self, _exception: Exception) -> None:
            return None

        def set_status(self, _status) -> None:
            return None

    def fake_start_span(name: str, **attributes):
        return CapturedSpan(name, attributes)

    with database_manager.session() as session:
        skill = skills_service.create_skill(
            session,
            CreateSkillRequest(
                key="runtime-span-provenance",
                name="Runtime Span Provenance",
                description="Validate Runtime OTel span correlation.",
            ),
        )
        published = skills_service.publish_skill(
            session,
            skill_id=skill.id,
            payload=PublishSkillRequest(publish_reason="Runtime span provenance publish"),
        )
        compile_request_id = published.compile_request.id
        compile_request = process_publish_job(session, compiler_service, compile_request_id)
        artifact_id = compile_request.artifact_id or ""

        monkeypatch.setattr("app.runtime.service.start_span", fake_start_span)
        invocation = runtime_service.create_invocation(
            session,
            CreateInvocationRequest(
                skill_key="runtime-span-provenance",
                input_envelope={"user_input": "请检查现场任务。"},
                terminal_context={"terminal_kind": "web"},
            ),
        )
        snapshots = runtime_service.list_snapshots(session, invocation.run_id or "")

    runtime_span_names = {"runtime.loop", "runtime.actor", "gateway.inference"}
    runtime_spans = [span for span in captured_spans if span["name"] in runtime_span_names]
    assert {span["name"] for span in runtime_spans} == runtime_span_names
    assert snapshots
    snapshot_seq_by_id = {snapshot.id: snapshot.seq_no for snapshot in snapshots}
    for span in runtime_spans:
        attributes = span["attributes"]
        assert attributes["run_id"] == invocation.run_id
        assert attributes["invocation_id"] == invocation.id
        assert attributes["skill_id"] == skill.id
        assert attributes["pskill_definition_id"] == skill.id
        assert attributes["pskill_version_id"] == published.published_version.id
        assert attributes["skill_version_id"] == published.published_version.id
        assert attributes["compile_artifact_id"] == artifact_id
        assert attributes["compile_request_id"] == compile_request_id
        assert attributes["session_token_id"] in snapshot_seq_by_id
        assert attributes["session_token_seq"] == snapshot_seq_by_id[attributes["session_token_id"]]
    actor_span = next(span for span in runtime_spans if span["name"] == "runtime.actor")
    assert actor_span["attributes"]["node_id"]
    assert actor_span["attributes"]["node_kind"]
    inference_span = next(span for span in runtime_spans if span["name"] == "gateway.inference")
    assert inference_span["attributes"]["route_key"] == "text"


def test_runtime_service_treats_abort_decision_as_semantic_abort() -> None:
    settings = create_test_settings()
    database_manager = DatabaseManager(settings.sqlalchemy_database_url)
    database_manager.create_schema()
    gitlab_gateway = FakeGitLabGateway()
    inference_gateway = QueuedInferenceGateway(
        [
            json.dumps(build_test_abort_formal_v5_artifact(), ensure_ascii=False),
            "请先核对电源额定功率，并上传现场证据。",
            json.dumps(
                {
                    "decision": "abort",
                    "reason": "电源额定功率低于当前硬件估算功耗，继续装机会带来安全风险。",
                    "next_phase": "terminal_abort",
                    "terminal_message": "已中止：电源功率不足，请先更换合适电源后再继续。",
                },
                ensure_ascii=False,
            ),
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
    runtime_service = RuntimeService(settings=settings, inference_gateway=inference_gateway)

    try:
        with database_manager.session() as session:
            skill = skills_service.create_skill(
                session,
                CreateSkillRequest(
                    key="runtime-abort",
                    name="Runtime Abort",
                    description="Validate semantic abort path.",
                ),
            )
            published = skills_service.publish_skill(
                session,
                skill_id=skill.id,
                payload=PublishSkillRequest(publish_reason="Runtime abort publish"),
            )
            process_publish_job(session, compiler_service, published.compile_request.id)

            invocation = runtime_service.create_invocation(
                session,
                CreateInvocationRequest(
                    skill_key="runtime-abort",
                    terminal_context={"terminal_kind": "web"},
                ),
            )
            initial_run = runtime_service.get_run(session, invocation.run_id or "")

            runtime_service.append_terminal_event(
                session,
                invocation.run_id or "",
                AppendTerminalEventRequest(
                    direction="input",
                    event_kind="terminal.text.input.v1",
                    mime_type="text/plain",
                    payload_inline="现场电源只有 300W，低于整机估算功耗。",
                    external_event_id="runtime-abort-evidence-001",
                ),
            )
            run = runtime_service.get_run(session, invocation.run_id or "")
            refreshed_invocation = runtime_service.get_invocation(session, invocation.id)
            terminal_session = runtime_service.get_terminal_session(session, invocation.run_id or "")
            terminal_events = runtime_service.list_terminal_events(session, invocation.run_id or "")
            trace_events = runtime_service.list_trace_events(session, invocation.run_id or "")
            snapshots = runtime_service.list_snapshots(session, invocation.run_id or "")
            replay = runtime_service.build_replay(session, invocation.run_id or "")

            with pytest.raises(SkillValidationError):
                runtime_service.append_terminal_event(
                    session,
                    invocation.run_id or "",
                    AppendTerminalEventRequest(
                        direction="input",
                        event_kind="terminal.text.input.v1",
                        payload_inline="不应接受新输入",
                    ),
                )
    finally:
        database_manager.dispose()

    assert initial_run.status == "waiting_input"
    assert refreshed_invocation.status == "aborted"
    assert run.status == "aborted"
    assert run.runtime_phase == "aborted"
    assert "电源功率不足" in run.final_output
    assert "电源功率不足" in run.exit_reason
    assert run.ended_at is not None
    assert terminal_session.terminal_session.status == "closed"
    assert [event.direction for event in terminal_events] == ["output", "input", "output", "output"]
    assert terminal_events[-1].source_ref["node_id"] == "terminal_abort"
    assert snapshots[-1].token_payload["status"] == "aborted"
    assert snapshots[-1].token_payload["control"]["abort"]["next_phase"] == "terminal_abort"
    assert "runtime.aborted" in [item.event_type for item in trace_events]
    assert "runtime.failed" not in [item.event_type for item in trace_events]
    assert "已中止" in [item.title for item in replay.timeline]


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
        terminal_events = failing_runtime.list_terminal_events(session, invocation.run_id or "")

    assert invocation.status == "failed"
    assert run.status == "failed"
    assert run.exit_reason == "LLM provider unavailable"
    assert trace_events[-1].event_type == "runtime.failed"
    assert terminal_events[-1].direction == "output"
    assert terminal_events[-1].event_kind == "terminal.text.output.v1"
    assert terminal_events[-1].external_event_id == f"runtime:{invocation.run_id}:failed"
    assert terminal_events[-1].trace_event_id == trace_events[-1].id
    assert "Runtime 执行失败" in terminal_events[-1].payload_inline
    assert "当前运行已停止" in terminal_events[-1].payload_inline
    assert "调试运行" not in terminal_events[-1].payload_inline
    assert "LLM provider unavailable" in terminal_events[-1].payload_inline


def test_runtime_service_records_gateway_error_details_in_failed_trace_payload(runtime_stack) -> None:
    database_manager, _, _, compiler_service, skills_service, _ = runtime_stack
    failing_runtime = RuntimeService(settings=create_test_settings(), inference_gateway=FailingSkillsGatewayInferenceGateway())

    with database_manager.session() as session:
        skill = skills_service.create_skill(
            session,
            CreateSkillRequest(
                key="runtime-gateway-failure",
                name="Runtime Gateway Failure",
                description="Validate gateway failure trace details.",
            ),
        )
        published = skills_service.publish_skill(
            session,
            skill_id=skill.id,
            payload=PublishSkillRequest(publish_reason="Runtime gateway failure publish"),
        )
        process_publish_job(session, compiler_service, published.compile_request.id)

        invocation = failing_runtime.create_invocation(
            session,
            CreateInvocationRequest(
                skill_key="runtime-gateway-failure",
                input_envelope={"user_input": "触发 gateway 失败"},
            ),
        )
        trace_events = failing_runtime.list_trace_events(session, invocation.run_id or "")

    payload = trace_events[-1].payload
    details = payload["error_details"]
    assert trace_events[-1].event_type == "runtime.failed"
    assert payload["error"] == "LLM Inference Gateway 返回错误响应。"
    assert payload["error_type"] == "SkillsGatewayError"
    assert payload["error_code"] == "skills_gateway_error"
    assert payload["status_code"] == 502
    assert payload["recoverable"] is False
    assert details["status_code"] == 500
    assert details["provider"] == "aliyun"
    assert details["model"] == "qwen3.7-plus"
    assert details["route_key"] == "text"
    assert details["request_id"] == "request-test"
    assert details["provider_error_code"] == "ServiceUnavailable"
    assert "Too many requests" in details["provider_error_message"]
    assert details["body_json"]["error"]["type"] == "ServiceUnavailable"
    assert details["api_key"] == "[redacted]"


def test_runtime_service_recovers_when_single_terminal_message_processing_fails(runtime_stack) -> None:
    database_manager, _, _, compiler_service, skills_service, runtime_service = runtime_stack
    failing_runtime = RuntimeService(settings=create_test_settings(), inference_gateway=FailingSkillsGatewayInferenceGateway())

    with database_manager.session() as session:
        skill = skills_service.create_skill(
            session,
            CreateSkillRequest(
                key="runtime-message-recover",
                name="Runtime Message Recover",
                description="Validate recoverable terminal message failures.",
            ),
        )
        published = skills_service.publish_skill(
            session,
            skill_id=skill.id,
            payload=PublishSkillRequest(publish_reason="Runtime message recover publish"),
        )
        process_publish_job(session, compiler_service, published.compile_request.id)

        invocation = runtime_service.create_invocation(
            session,
            CreateInvocationRequest(
                skill_key="runtime-message-recover",
                terminal_context={"terminal_kind": "web"},
            ),
        )
        first_run = runtime_service.get_run(session, invocation.run_id or "")
        failing_runtime.append_terminal_event(
            session,
            invocation.run_id or "",
            AppendTerminalEventRequest(
                direction="input",
                event_kind="terminal.text.input.v1",
                mime_type="text/plain",
                payload_inline="第一条输入触发服务端异常",
                external_event_id="runtime-message-recover-failed-input",
            ),
        )
        recovered_run = runtime_service.get_run(session, invocation.run_id or "")
        trace_events = runtime_service.list_trace_events(session, invocation.run_id or "")
        terminal_session = runtime_service.get_terminal_session(session, invocation.run_id or "")
        terminal_events = runtime_service.list_terminal_events(session, invocation.run_id or "")
        snapshots = runtime_service.list_snapshots(session, invocation.run_id or "")

        retry = runtime_service.append_terminal_event(
            session,
            invocation.run_id or "",
            AppendTerminalEventRequest(
                direction="input",
                event_kind="terminal.text.input.v1",
                mime_type="text/plain",
                payload_inline="我已经完成当前步骤，并上传了现场说明。",
                external_event_id="runtime-message-recover-retry-input",
            ),
        )
        final_run = runtime_service.get_run(session, invocation.run_id or "")

    assert first_run.status == "waiting_input"
    assert recovered_run.status == "waiting_input"
    assert recovered_run.exit_reason == ""
    assert recovered_run.ended_at is None
    assert terminal_session.terminal_session.status == "open"
    assert trace_events[-1].event_type == "runtime.message_processing.failed"
    assert trace_events[-1].payload["recoverable"] is True
    assert trace_events[-1].payload["error_type"] == "SkillsGatewayError"
    assert trace_events[-1].payload["error_details"]["request_id"] == "request-test"
    assert trace_events[-1].payload["error_details"]["provider_error_type"] == "ServiceUnavailable"
    assert terminal_events[-1].direction == "output"
    assert terminal_events[-1].trace_event_id == trace_events[-1].id
    assert terminal_events[-1].payload_inline == "刚才服务器开小差了，请您重试！"
    assert snapshots[-1].token_payload["status"] == "waiting"
    assert snapshots[-1].token_payload["metadata"]["terminal_cursor"] == terminal_events[-1].seq_no
    assert retry.seq_no > terminal_events[-1].seq_no
    assert final_run.status == "succeeded"
