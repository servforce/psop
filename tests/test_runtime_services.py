from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from app.agent_harness.persistence.models import AgentArtifactRecord, AgentEventRecord, AgentRunRecord
from app.agent_harness.models.scripted_compiler_chat_model import ScriptedCompilerChatModel
from app.agent_harness.models.scripted_runner_chat_model import ScriptedRunnerChatModel
from app.agent_harness.service import AgentHarnessService
from app.agents.registry import PromptRegistry
from app.domain.compiler.models import ArtifactObject
from app.domain.compiler.service import CompilerService, _extract_reference_image_candidates
from app.domain.compiler.formal_v5 import validate_and_normalize_artifact
from app.domain.jobs.repository import JobRepository
from app.domain.runtime.models import Run, SessionTokenSnapshot
from app.domain.runtime.schemas import AppendTerminalEventRequest, CreateInvocationRequest, TerminalEventPartInput
from app.domain.runtime.service import RuntimeService
from app.domain.skills.exceptions import SkillsGatewayError, SkillValidationError
from app.domain.skills.schemas import CreateSkillRequest, PublishSkillRequest
from app.domain.skills.service import SkillsService
from app.domain.skills.models import SkillVersion
from app.gateway.inference import LlmCompletion
from app.infra.database import DatabaseManager
from tests.test_skills_api import (
    FakeGitLabGateway,
    FakeInferenceGateway,
    FakeObjectStore,
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


class RaisingAgentHarnessService:
    def __init__(self, exc: Exception) -> None:
        self.exc = exc

    def invoke(self, *args, **kwargs):
        raise self.exc


class RecordingRuntimeEventSink:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    def publish(self, event: dict[str, object]) -> None:
        self.events.append(event)


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
def runtime_stack(tmp_path) -> Iterator[tuple[DatabaseManager, FakeGitLabGateway, FakeInferenceGateway, CompilerService, SkillsService, RuntimeService]]:
    settings = create_test_settings().model_copy(
        update={"agent_harness_sandbox_root": str(tmp_path / "agent-runs")}
    )
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
    agent_harness_service = AgentHarnessService(
        settings=settings,
        chat_model_factory=lambda _definition: ScriptedRunnerChatModel(),
    )
    runtime_service = RuntimeService(
        settings=settings,
        inference_gateway=inference_gateway,
        agent_harness_service=agent_harness_service,
    )

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


def test_runtime_projects_compiled_runner_turn_contract_without_inference(runtime_stack) -> None:
    _, _, _, _, _, runtime_service = runtime_stack
    artifact_payload = build_test_formal_v5_artifact()
    artifact_payload["skill"] = {
        "key": "computer-installation",
        "name": "安装电脑主机",
        "description": "指导用户完成台式电脑主机安装。",
        "version_no": 3,
    }
    node = next(item for item in artifact_payload["nodes"] if item["id"] == "instruct_collect_context")
    run = Run(id="run-turn-context", latest_snapshot_seq=0)
    token = {
        "phase": "instruct_collect_context",
        "input_envelope": {},
        "facts": {},
        "observations": {},
        "control": {},
        "terminal": {"events": []},
        "trace": [],
        "metadata": {"terminal_cursor": 0},
    }

    context = runtime_service._build_runner_context(
        run=run,
        node=node,
        token=token,
        artifact_payload=artifact_payload,
    )
    turn_context = runtime_service._build_runner_turn_context(
        run=run,
        node=node,
        mode="terminal_guidance",
        context=context,
    )

    assert turn_context["turn_kind"] == "first_step_instruction"
    assert turn_context["task_identity"] == {
        "skill_key": "computer-installation",
        "name": "安装电脑主机",
        "description": "指导用户完成台式电脑主机安装。",
        "version": 3,
    }
    assert turn_context["stage_position"] == {
        "current": 1,
        "total": 1,
        "workflow_step_id": "collect_context",
    }
    assert turn_context["current_workflow_step"]["title"] == "收集上下文"
    assert turn_context["previous_evaluation"] == {}
    assert turn_context["runtime_contract_slice"]["applicability"] == artifact_payload["runtime_contract"]["applicability"]

    legacy_node = json.loads(json.dumps(node, ensure_ascii=False))
    legacy_node["interaction"].pop("runner_turn_kind")
    legacy_context = runtime_service._build_runner_context(
        run=run,
        node=legacy_node,
        token=token,
        artifact_payload=artifact_payload,
    )
    legacy_turn_context = runtime_service._build_runner_turn_context(
        run=run,
        node=legacy_node,
        mode="terminal_guidance",
        context=legacy_context,
    )
    assert legacy_turn_context["turn_kind"] == ""


def test_compiler_can_use_psop_compiler_agent_harness(tmp_path) -> None:
    settings = create_test_settings().model_copy(
        update={"agent_harness_sandbox_root": str(tmp_path / "agent-runs")}
    )
    database_manager = DatabaseManager(settings.sqlalchemy_database_url)
    database_manager.create_schema()
    gitlab_gateway = FakeGitLabGateway()
    agent_harness_service = AgentHarnessService(
        settings=settings,
        chat_model_factory=lambda _definition: ScriptedCompilerChatModel(),
    )
    compiler_service = CompilerService(
        settings=settings,
        gitlab_gateway=gitlab_gateway,
        inference_gateway=FailingInferenceGateway(),
        agent_harness_service=agent_harness_service,
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
                    name="Compiler Harness",
                    description="Validate psop.compiler harness integration.",
                ),
            )
            published = skills_service.publish_skill(
                session,
                skill_id=skill.id,
                payload=PublishSkillRequest(publish_reason="Compile through harness"),
            )
            compiled = process_publish_job(session, compiler_service, published.compile_request.id)
            artifact = compiler_service.get_artifact(session, compiled.artifact_id or "")
            diagnostics = compiler_service.list_diagnostics(session, published.compile_request.id)

        assert compiled.status == "succeeded"
        assert artifact.formal_revision == "psop-eg-formal/v5"
        assert artifact.artifact is not None
        assert artifact.artifact["compiler_metadata"]["agent_prompt"]["agent_key"] == "psop.compiler"
        assert any(item.code == "compile.agent.prompt_pack" for item in diagnostics)
    finally:
        database_manager.dispose()


def test_compiler_reference_images_are_preserved_but_runner_outputs_text_only(tmp_path) -> None:
    settings = create_test_settings().model_copy(
        update={"agent_harness_sandbox_root": str(tmp_path / "agent-runs")}
    )
    database_manager = DatabaseManager(settings.sqlalchemy_database_url)
    database_manager.create_schema()
    gitlab_gateway = FakeGitLabGateway()
    object_store = FakeObjectStore()
    compiler_harness = AgentHarnessService(
        settings=settings,
        chat_model_factory=lambda _definition: ScriptedCompilerChatModel(),
    )
    compiler_service = CompilerService(
        settings=settings,
        gitlab_gateway=gitlab_gateway,
        inference_gateway=FailingInferenceGateway(),
        agent_harness_service=compiler_harness,
        object_store=object_store,
    )
    skills_service = SkillsService(
        settings=settings,
        gitlab_gateway=gitlab_gateway,
        compiler_service=compiler_service,
    )
    runner_harness = AgentHarnessService(
        settings=settings,
        chat_model_factory=lambda _definition: ScriptedRunnerChatModel(),
    )
    runtime_service = RuntimeService(
        settings=settings,
        inference_gateway=FailingInferenceGateway(),
        object_store=object_store,
        agent_harness_service=runner_harness,
    )

    try:
        with database_manager.session() as session:
            skill = skills_service.create_skill(
                session,
                CreateSkillRequest(
                    name="Compiler Reference Images",
                    description="Validate Skill reference image output flow.",
                ),
            )
            project = gitlab_gateway.projects[skill.gitlab_project_id]
            gitlab_gateway.commit_repository_files(
                project_id=skill.gitlab_project_id,
                branch=skill.default_branch,
                files={
                    "README.md": "# Reference Skill\n\n忽略外部图片 ![bad](https://example.test/outside.jpg)\n",
                    "SKILL.md": "# Reference Skill\n\n请按参考图角度确认现场。\n\n![现场概览](references/site-overview.jpg)\n",
                    "skill.yaml": str(project.files["skill.yaml"]),
                },
                binary_files={"references/site-overview.jpg": b"jpeg-reference-bytes"},
                commit_message="Add reference image",
            )
            published = skills_service.publish_skill(
                session,
                skill_id=skill.id,
                payload=PublishSkillRequest(publish_reason="Compile reference images"),
            )
            compiled = process_publish_job(session, compiler_service, published.compile_request.id)
            artifact = compiler_service.get_artifact(session, compiled.artifact_id or "")
            assert artifact.artifact is not None
            reference_images = artifact.artifact["runtime_contract"]["workflow_steps"][0]["reference_images"]
            reference_image = reference_images[0]
            reference_object = session.get(ArtifactObject, reference_image["artifact_object_id"])

            assert len(reference_images) == 1
            assert reference_image["reference_image_ref"].startswith("skill-reference://steps/collect_context/")
            assert reference_image["title"] == "现场概览"
            assert reference_image["mime_type"] == "image/jpeg"
            assert reference_object is not None
            assert reference_object.media_type == "image/jpeg"
            assert object_store.objects[(reference_object.bucket, reference_object.object_key)] == b"jpeg-reference-bytes"

            invocation = runtime_service.create_invocation(
                session,
                CreateInvocationRequest(
                    skill_key=skill.key,
                    terminal_context={"terminal_kind": "web"},
                ),
            )
            runtime_service.append_terminal_event(
                session,
                invocation.run_id or "",
                AppendTerminalEventRequest(
                    direction="input",
                    event_kind="terminal.text.input.v1",
                    mime_type="text/plain",
                    payload_inline="现场说明已提交。",
                    external_event_id="compiler-reference-images-input-001",
                ),
            )
            runtime_service.process_run(session, invocation.run_id or "")
            terminal_events = runtime_service.list_terminal_events(session, invocation.run_id or "")

        multimodal_events = [event for event in terminal_events if event.event_kind == "terminal.multimodal.output.v1"]
        text_outputs = [event for event in terminal_events if event.event_kind == "terminal.text.output.v1"]
        assert not multimodal_events
        assert text_outputs
        assert all(part.kind == "text" for event in text_outputs for part in event.parts)
    finally:
        database_manager.dispose()


def test_compiler_reference_image_parser_ignores_unsafe_markdown_links() -> None:
    candidates = _extract_reference_image_candidates(
        {
            "README.md": "\n".join(
                [
                    "![good](./references/good.png)",
                    "![external](https://example.test/good.png)",
                    "![data](data:image/png;base64,AAAA)",
                    "![outside](../references/outside.png)",
                    "![escape](references/../outside.png)",
                    "![wrong-dir](examples/good.png)",
                    "![text](references/not-image.txt)",
                    "![absolute](/references/absolute.png)",
                    r"![backslash](references\\bad.png)",
                ]
            ),
            "SKILL.md": "![nested](references/nested/photo.webp?cache=1#view)",
        }
    )

    assert [item["reference_path"] for item in candidates] == [
        "references/good.png",
        "references/nested/photo.webp",
    ]


def test_runtime_service_can_use_psop_runner_agent_harness(tmp_path) -> None:
    settings = create_test_settings().model_copy(
        update={"agent_harness_sandbox_root": str(tmp_path / "agent-runs")}
    )
    database_manager = DatabaseManager(settings.sqlalchemy_database_url)
    database_manager.create_schema()
    gitlab_gateway = FakeGitLabGateway()
    compile_gateway = FakeInferenceGateway()
    compiler_service = CompilerService(
        settings=settings,
        gitlab_gateway=gitlab_gateway,
        inference_gateway=compile_gateway,
    )
    skills_service = SkillsService(
        settings=settings,
        gitlab_gateway=gitlab_gateway,
        compiler_service=compiler_service,
    )
    agent_harness_service = AgentHarnessService(
        settings=settings,
        chat_model_factory=lambda _definition: ScriptedRunnerChatModel(),
    )
    runtime_service = RuntimeService(
        settings=settings,
        inference_gateway=FailingInferenceGateway(),
        agent_harness_service=agent_harness_service,
    )

    try:
        with database_manager.session() as session:
            skill = skills_service.create_skill(
                session,
                CreateSkillRequest(
                    name="Runtime Runner Harness",
                    description="Validate psop.runner harness integration.",
                ),
            )
            published = skills_service.publish_skill(
                session,
                skill_id=skill.id,
                payload=PublishSkillRequest(publish_reason="Runtime through runner harness"),
            )
            process_publish_job(session, compiler_service, published.compile_request.id)

            invocation = runtime_service.create_invocation(
                session,
                CreateInvocationRequest(
                    skill_key=skill.key,
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
                    payload_inline="我已经完成当前步骤，并上传了现场说明。",
                    external_event_id="runtime-runner-harness-evidence-001",
                ),
            )
            runtime_service.process_run(session, invocation.run_id or "")
            run = runtime_service.get_run(session, invocation.run_id or "")
            trace_events = runtime_service.list_trace_events(session, invocation.run_id or "")
            terminal_events = runtime_service.list_terminal_events(session, invocation.run_id or "")
            runner_payloads = [
                event.payload for event in trace_events if event.event_type == "runtime.agent.completed"
            ]
            agent_run_id = (
                str(runner_payloads[0]["observation"]["runner"]["agent_run_id"])
                if runner_payloads
                else ""
            )
            agent_run_record = session.get(AgentRunRecord, agent_run_id)
            agent_event_count = (
                session.query(AgentEventRecord).filter(AgentEventRecord.agent_run_id == agent_run_id).count()
            )
            agent_artifact_count = (
                session.query(AgentArtifactRecord).filter(AgentArtifactRecord.agent_run_id == agent_run_id).count()
            )
    finally:
        database_manager.dispose()

    assert initial_run.status == "waiting_runtime"
    assert run.status == "succeeded"
    assert "测试任务已完成" in run.final_output
    assert any(event.event_type == "runtime.agent.completed" for event in trace_events)
    assert not any(event.event_type == "gateway.inference.completed" for event in trace_events)
    assert runner_payloads
    assert runner_payloads[0]["observation"]["runner"]["agent_key"] == "psop.runner"
    assert runner_payloads[0]["observation"]["runner"]["agent_run_id"]
    assert agent_run_record is not None
    assert agent_run_record.related_runtime_run_id == run.id
    assert agent_event_count > 0
    assert agent_artifact_count > 0
    assert [event.direction for event in terminal_events] == ["input", "output", "output"]
    assert terminal_events[-1].source_ref["node_id"] == "terminal"
    assert "final_verify" not in [
        event.source_ref.get("node_id")
        for event in terminal_events
        if event.direction == "output"
    ]


def test_runtime_runner_passes_uploaded_image_as_agent_attachment(tmp_path) -> None:
    settings = create_test_settings().model_copy(
        update={"agent_harness_sandbox_root": str(tmp_path / "agent-runs")}
    )
    database_manager = DatabaseManager(settings.sqlalchemy_database_url)
    database_manager.create_schema()
    gitlab_gateway = FakeGitLabGateway()
    inference_gateway = FakeInferenceGateway()
    object_store = FakeObjectStore()
    object_store.objects[("test-bucket", "terminal/site.jpg")] = b"image-bytes"
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
    runtime_service = RuntimeService(
        settings=settings,
        inference_gateway=inference_gateway,
        object_store=object_store,
        agent_harness_service=AgentHarnessService(
            settings=settings,
            chat_model_factory=lambda _definition: ScriptedRunnerChatModel(),
        ),
    )

    try:
        with database_manager.session() as session:
            skill = skills_service.create_skill(
                session,
                CreateSkillRequest(
                    name="Runtime Runner Image Attachment",
                    description="Validate uploaded images are passed to psop.runner.",
                ),
            )
            published = skills_service.publish_skill(
                session,
                skill_id=skill.id,
                payload=PublishSkillRequest(publish_reason="Runtime runner image attachment"),
            )
            process_publish_job(session, compiler_service, published.compile_request.id)
            invocation = runtime_service.create_invocation(
                session,
                CreateInvocationRequest(
                    skill_key=skill.key,
                    terminal_context={"terminal_kind": "web"},
                ),
            )
            image_object = ArtifactObject(
                bucket="test-bucket",
                object_key="terminal/site.jpg",
                media_type="image/jpeg",
                size_bytes=len(b"image-bytes"),
                checksum="sha256-image",
            )
            session.add(image_object)
            session.flush()
            runtime_service.append_terminal_event(
                session,
                invocation.run_id or "",
                AppendTerminalEventRequest(
                    direction="input",
                    event_kind="terminal.multimodal.input.v1",
                    mime_type="multipart/mixed",
                    payload_inline={"text": "请根据现场图片判断。"},
                    parts=[
                        TerminalEventPartInput(
                            part_id="text_1",
                            kind="text",
                            mime_type="text/plain",
                            text="请根据现场图片判断。",
                        ),
                        TerminalEventPartInput(
                            part_id="image_1",
                            kind="image",
                            mime_type="image/jpeg",
                            artifact_object_id=image_object.id,
                            size_bytes=len(b"image-bytes"),
                            checksum="sha256-image",
                            metadata={"filename": "site.jpg"},
                        ),
                    ],
                    external_event_id="runtime-runner-image-attachment-001",
                ),
            )
            runtime_service.process_run(session, invocation.run_id or "")
            run = runtime_service.get_run(session, invocation.run_id or "")
            image_agent_runs = (
                session.query(AgentRunRecord)
                .filter(AgentRunRecord.related_runtime_run_id == run.id)
                .filter(AgentRunRecord.input_summary["image_attachment_count"].as_integer() == 1)
                .all()
            )
            assert image_agent_runs
            image_agent_run = image_agent_runs[0]
            prepared_event = (
                session.query(AgentEventRecord)
                .filter(AgentEventRecord.agent_run_id == image_agent_run.id)
                .filter(AgentEventRecord.event_type == "agent.multimodal.attachments.prepared")
                .one()
            )
            trace_events = runtime_service.list_trace_events(session, invocation.run_id or "")
            image_agent_run_summary = dict(image_agent_run.input_summary)
            prepared_payload = dict(prepared_event.payload)
            sandbox_input = Path(image_agent_run.sandbox_path, "input.json").read_text(encoding="utf-8")
    finally:
        database_manager.dispose()

    serialized_event = json.dumps(prepared_payload, ensure_ascii=False)
    assert run.status == "succeeded"
    assert image_agent_run_summary["image_attachment_count"] == 1
    assert image_agent_run_summary["attachment_source_refs"] == ["terminal_event:1:image_1"]
    assert prepared_payload["attachments"][0]["source_ref"] == "terminal_event:1:image_1"
    assert "aW1hZ2UtYnl0ZXM=" not in sandbox_input
    assert "terminal/site.jpg" not in sandbox_input
    assert "aW1hZ2UtYnl0ZXM=" not in serialized_event
    assert not any(event.event_type == "runtime.runner.attachment.warning" for event in trace_events)


def test_runtime_runner_attachment_warning_when_object_store_unavailable(tmp_path) -> None:
    settings = create_test_settings().model_copy(
        update={"agent_harness_sandbox_root": str(tmp_path / "agent-runs")}
    )
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
    runtime_service = RuntimeService(
        settings=settings,
        inference_gateway=inference_gateway,
        object_store=None,
        agent_harness_service=AgentHarnessService(
            settings=settings,
            chat_model_factory=lambda _definition: ScriptedRunnerChatModel(),
        ),
    )

    try:
        with database_manager.session() as session:
            skill = skills_service.create_skill(
                session,
                CreateSkillRequest(
                    name="Runtime Runner Image Attachment Warning",
                    description="Validate unavailable attachments warn without failing Runtime.",
                ),
            )
            published = skills_service.publish_skill(
                session,
                skill_id=skill.id,
                payload=PublishSkillRequest(publish_reason="Runtime runner attachment warning"),
            )
            process_publish_job(session, compiler_service, published.compile_request.id)
            invocation = runtime_service.create_invocation(
                session,
                CreateInvocationRequest(
                    skill_key=skill.key,
                    terminal_context={"terminal_kind": "web"},
                ),
            )
            image_object = ArtifactObject(
                bucket="test-bucket",
                object_key="terminal/missing-store.jpg",
                media_type="image/jpeg",
                size_bytes=10,
                checksum="sha256-image",
            )
            session.add(image_object)
            session.flush()
            runtime_service.append_terminal_event(
                session,
                invocation.run_id or "",
                AppendTerminalEventRequest(
                    direction="input",
                    event_kind="terminal.multimodal.input.v1",
                    mime_type="multipart/mixed",
                    payload_inline={"text": "请根据现场图片判断。"},
                    parts=[
                        TerminalEventPartInput(
                            part_id="text_1",
                            kind="text",
                            mime_type="text/plain",
                            text="请根据现场图片判断。",
                        ),
                        TerminalEventPartInput(
                            part_id="image_1",
                            kind="image",
                            mime_type="image/jpeg",
                            artifact_object_id=image_object.id,
                            size_bytes=10,
                            checksum="sha256-image",
                            metadata={"filename": "site.jpg"},
                        ),
                    ],
                    external_event_id="runtime-runner-image-attachment-warning-001",
                ),
            )
            runtime_service.process_run(session, invocation.run_id or "")
            run = runtime_service.get_run(session, invocation.run_id or "")
            trace_events = runtime_service.list_trace_events(session, invocation.run_id or "")
    finally:
        database_manager.dispose()

    warning_events = [event for event in trace_events if event.event_type == "runtime.runner.attachment.warning"]
    assert run.status == "succeeded"
    assert warning_events
    assert warning_events[0].payload["source_ref"] == "terminal_event:1:image_1"
    assert warning_events[0].payload["reason"] == "object_store_unavailable"


def test_compiler_candidate_diagnostics_filters_standard_search_availability_notes() -> None:
    diagnostics = CompilerService._candidate_diagnostics(
        [
            {"message": "行业标准检索服务不可用，compiler 不因此阻塞。"},
            {"message": "LightRAG connection refused while checking standard search."},
            {"message": "standard search unavailable: connection refused."},
            {
                "severity": "warning",
                "code": "compile.agent.candidate_diagnostic",
                "message": "source_map 缺少 completion criteria 的明确来源。",
            },
            {
                "severity": "info",
                "code": "compile.agent.standard_reference",
                "message": "frozen source 已固化 GB/T 相关安全引用，可作为 source evidence。",
            },
            {"message": "workspace read connection refused, unrelated external endpoint."},
        ]
    )

    messages = [item.message for item in diagnostics]
    assert messages == [
        "source_map 缺少 completion criteria 的明确来源。",
        "frozen source 已固化 GB/T 相关安全引用，可作为 source evidence。",
        "workspace read connection refused, unrelated external endpoint.",
    ]
    assert diagnostics[0].severity == "warning"
    assert diagnostics[1].severity == "info"


def test_compiler_records_diagnostics_for_unsupported_formal_revision(runtime_stack) -> None:
    database_manager, gitlab_gateway, _, compiler_service, skills_service, _ = runtime_stack

    with database_manager.session() as session:
        skill = skills_service.create_skill(
            session,
            CreateSkillRequest(
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


def test_runtime_list_runs_filters_multiple_statuses(runtime_stack) -> None:
    database_manager, _, _, compiler_service, skills_service, runtime_service = runtime_stack

    with database_manager.session() as session:
        skill = skills_service.create_skill(
            session,
            CreateSkillRequest(
                name="List Runs Statuses",
                description="Validate filtering runs by multiple statuses.",
            ),
        )
        published = skills_service.publish_skill(
            session,
            skill_id=skill.id,
            payload=PublishSkillRequest(publish_reason="List runs status filter test"),
        )
        process_publish_job(session, compiler_service, published.compile_request.id)

        waiting_invocation = runtime_service.create_invocation(
            session,
            CreateInvocationRequest(skill_key=skill.key),
        )
        failed_invocation = runtime_service.create_invocation(
            session,
            CreateInvocationRequest(skill_key=skill.key),
        )
        excluded_invocation = runtime_service.create_invocation(
            session,
            CreateInvocationRequest(skill_key=skill.key),
        )
        session.get(Run, waiting_invocation.run_id).status = "waiting_input"
        session.get(Run, failed_invocation.run_id).status = "failed"
        session.get(Run, excluded_invocation.run_id).status = "succeeded"
        session.flush()

        runs = runtime_service.list_runs(session, status=["waiting_input", "failed"])

    assert {run.id for run in runs} == {waiting_invocation.run_id, failed_invocation.run_id}


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


def test_runtime_service_waits_for_real_world_evidence_and_builds_replay(runtime_stack) -> None:
    database_manager, _, inference_gateway, compiler_service, skills_service, runtime_service = runtime_stack

    with database_manager.session() as session:
        skill = skills_service.create_skill(
            session,
            CreateSkillRequest(
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
                skill_key=skill.key,
                terminal_context={"terminal_kind": "web"},
            ),
        )
        initial_run = runtime_service.get_run(session, invocation.run_id or "")
        initial_job = JobRepository().get_runtime_job_by_dedupe_key(session, f"job:runtime:{invocation.run_id}")
        initial_job_status = initial_job.status if initial_job else ""
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
        runtime_service.process_run(session, invocation.run_id or "")
        run = runtime_service.get_run(session, invocation.run_id or "")
        replay = runtime_service.build_replay(session, invocation.run_id or "")
        snapshots = runtime_service.list_snapshots(session, invocation.run_id or "")
        trace_events = runtime_service.list_trace_events(session, invocation.run_id or "")
        terminal_session = runtime_service.get_terminal_session(session, invocation.run_id or "")
        terminal_events = runtime_service.list_terminal_events(session, invocation.run_id or "")
        bindings = runtime_service.list_run_bindings(session, invocation.run_id or "")

    assert invocation.status == "running"
    assert invocation.terminal_session_id
    assert initial_run.status == "waiting_runtime"
    assert initial_run.runtime_phase == "start"
    assert initial_run.latest_terminal_seq == 0
    assert initial_job is not None
    assert initial_job_status == "pending"
    assert initial_run.current_step == ""
    assert initial_run.checkpoint_id == ""
    assert initial_run.expected_inputs == []
    assert initial_events == []
    assert appended.seq_no == 1
    assert run.status == "succeeded"
    assert run.latest_snapshot_seq == 5
    assert run.latest_terminal_seq == 3
    assert run.latest_trace_seq >= 7
    assert run.terminal_session_id == invocation.terminal_session_id
    assert len(run.binding_summary) == 2
    assert run.latest_evaluation["decision"] == "complete"
    assert "测试任务已完成" in run.final_output
    assert len(inference_gateway.calls) == 1
    assert [snapshot.seq_no for snapshot in snapshots] == [0, 1, 2, 3, 4, 5]
    assert snapshots[-1].token_payload["budgets"]["llm_input_tokens"] > 0
    assert snapshots[-1].token_payload["budgets"]["llm_output_tokens"] > 0
    assert "input" not in snapshots[-1].token_payload["observations"]["instruct_collect_context"]
    assert "request" not in snapshots[-1].token_payload["observations"]["instruct_collect_context"]
    assert "_trace_request" not in snapshots[-1].token_payload["observations"]["instruct_collect_context"]
    assert "legacy-user-prompt" not in json.dumps(snapshots[-1].token_payload, ensure_ascii=False)
    agent_trace_payloads = [
        event.payload for event in trace_events if event.event_type == "runtime.agent.completed"
    ]
    assert agent_trace_payloads
    assert not any(event.event_type == "gateway.inference.completed" for event in trace_events)
    assert "input" not in agent_trace_payloads[0]["observation"]
    assert "_trace_request" not in agent_trace_payloads[0]["observation"]
    assert agent_trace_payloads[0]["observation"]["runner"]["agent_key"] == "psop.runner"
    assert agent_trace_payloads[0]["observation"]["runner"]["agent_run_id"]
    assert agent_trace_payloads[0]["observation"]["usage"]["total_tokens"] > 0
    assert terminal_session.terminal_session.id == invocation.terminal_session_id
    assert terminal_session.terminal_session.status == "closed"
    assert [event.direction for event in terminal_events] == ["input", "output", "output"]
    output_events = [event for event in terminal_events if event.direction == "output"]
    assert [[part.kind for part in event.parts] for event in output_events] == [["text"], ["text"]]
    assert [event.parts[0].part_id for event in output_events] == ["text_1", "text_1"]
    assert output_events[0].parts[0].text == output_events[0].payload_inline
    assert "测试任务已完成" in output_events[-1].parts[0].text
    assert terminal_events[1].source_ref["node_id"] == "evaluate_collect_context"
    assert terminal_events[-1].source_ref["node_id"] == "terminal"
    assert terminal_events[0].payload_inline == "我已经完成当前步骤，并上传了现场说明。"
    assert snapshots[2].token_payload["control"]["terminal_consumption"][0]["seq_no"] == appended.seq_no
    assert {binding.requirement_key for binding in bindings} == {"terminal.input", "terminal.output"}
    assert len(replay.terminal_events) == 3
    assert len(replay.bindings) == 2
    assert "binding.resolved" in [item.event_type for item in replay.timeline]
    assert "terminal.event.appended" in [item.event_type for item in replay.timeline]
    assert "runtime.start.completed" in [item.event_type for item in replay.timeline]
    assert "runtime.wait_checkpoint.entered" in [item.event_type for item in replay.timeline]
    assert "runtime.agent.completed" in [item.event_type for item in replay.timeline]
    assert "runtime.final.completed" in [item.event_type for item in replay.timeline]


def test_runtime_task_status_projects_waiting_stage_and_current_evidence(runtime_stack) -> None:
    database_manager, _, _, compiler_service, skills_service, runtime_service = runtime_stack

    with database_manager.session() as session:
        skill = skills_service.create_skill(
            session,
            CreateSkillRequest(
                name="Runtime Task Status",
                description="Validate the terminal task status projection.",
            ),
        )
        published = skills_service.publish_skill(
            session,
            skill_id=skill.id,
            payload=PublishSkillRequest(publish_reason="Task status projection test"),
        )
        process_publish_job(session, compiler_service, published.compile_request.id)
        invocation = runtime_service.create_invocation(
            session,
            CreateInvocationRequest(
                skill_key=skill.key,
                terminal_context={"terminal_kind": "web"},
            ),
        )
        runtime_service.process_run(session, invocation.run_id or "")
        waiting_status = runtime_service.get_run_task_status(session, invocation.run_id or "")

        snapshot = runtime_service.repository.get_latest_snapshot(session, invocation.run_id or "")
        assert snapshot is not None
        token = json.loads(json.dumps(snapshot.token_payload, ensure_ascii=False))
        requirements = token["control"]["evidence_progress"]["requirements"]
        requirements[0].update(
            {
                "status": "accepted",
                "reason": "文字说明已确认。",
                "accepted_event_refs": ["terminal_event:1"],
            }
        )
        requirements[1].update({"status": "ambiguous", "reason": "图片角度不足。"})
        snapshot.token_payload = token
        session.commit()
        partial_status = runtime_service.get_run_task_status(session, invocation.run_id or "")

        forked = runtime_service.fork_invocation_from_snapshot(
            session,
            source_run_id=invocation.run_id or "",
            snapshot_seq=snapshot.seq_no,
            terminal_seq=0,
            terminal_context={"terminal_kind": "web"},
        )
        forked_status = runtime_service.get_run_task_status(session, forked.run_id or "")

        runtime_service.cancel_run(session, invocation.run_id or "", reason="operator cancelled")
        cancelled_status = runtime_service.get_run_task_status(session, invocation.run_id or "")

    assert waiting_status.activity_status == "waiting_input"
    assert waiting_status.current_stage_id == "collect_context"
    assert waiting_status.stages[0].status == "waiting_input"
    assert waiting_status.current_checkpoint is not None
    assert waiting_status.current_checkpoint.total_requirements == 3
    assert waiting_status.current_checkpoint.accepted_requirements == 0
    assert {item.status for item in waiting_status.current_checkpoint.requirements} == {"missing"}

    assert partial_status.current_checkpoint is not None
    assert partial_status.current_checkpoint.accepted_requirements == 1
    assert [item.status for item in partial_status.current_checkpoint.requirements[:2]] == ["accepted", "ambiguous"]
    assert partial_status.current_checkpoint.requirements[0].reason == "文字说明已确认。"
    assert "accepted_event_refs" not in partial_status.model_dump_json()
    assert forked_status.progress == partial_status.progress
    assert forked_status.current_stage_id == partial_status.current_stage_id
    assert forked_status.current_checkpoint == partial_status.current_checkpoint

    assert cancelled_status.run_status == "cancelled"
    assert cancelled_status.activity_status == "cancelled"
    assert cancelled_status.stages[0].status == "cancelled"
    assert cancelled_status.stages[0].status_reason == "operator cancelled"


def test_runtime_task_status_projects_multistage_finalizing_terminal_and_legacy_states(runtime_stack) -> None:
    database_manager, _, _, compiler_service, skills_service, runtime_service = runtime_stack

    with database_manager.session() as session:
        skill = skills_service.create_skill(
            session,
            CreateSkillRequest(
                name="Runtime Task Status States",
                description="Validate multistage and compatibility task status projection.",
            ),
        )
        published = skills_service.publish_skill(
            session,
            skill_id=skill.id,
            payload=PublishSkillRequest(publish_reason="Task status states test"),
        )
        process_publish_job(session, compiler_service, published.compile_request.id)
        invocation = runtime_service.create_invocation(
            session,
            CreateInvocationRequest(
                skill_key=skill.key,
                terminal_context={"terminal_kind": "web"},
            ),
        )
        run = runtime_service.repository.get_run(session, invocation.run_id or "")
        snapshot = runtime_service.repository.get_latest_snapshot(session, invocation.run_id or "")
        artifact = runtime_service.repository.get_artifact(session, run.compile_artifact_id if run else "")
        artifact_object = session.get(ArtifactObject, artifact.artifact_object_id if artifact else "")
        assert run is not None
        assert snapshot is not None
        assert artifact_object is not None

        artifact_payload = json.loads(json.dumps(artifact_object.content_json, ensure_ascii=False))
        artifact_payload["runtime_contract"]["workflow_steps"].append(
            {"id": "verify_power", "title": "检查供电", "goal": "确认供电状态。"}
        )
        artifact_payload["runtime_contract"]["expected_evidence"]["verify_power"] = [
            {"requirement_key": "power_photo", "description": "供电状态照片", "kind": "image"}
        ]
        artifact_object.content_json = artifact_payload

        token = json.loads(json.dumps(snapshot.token_payload, ensure_ascii=False))
        token["status"] = "running"
        token["phase"] = "instruct_verify_power"
        token["observations"]["evaluate_collect_context"] = {"decision": "proceed"}
        token.setdefault("control", {}).pop("wait", None)
        snapshot.token_payload = token
        run.status = "running"
        run.runtime_phase = "instruct_verify_power"
        session.commit()
        advancing_status = runtime_service.get_run_task_status(session, run.id)

        run.status = "failed"
        run.exit_reason = "power check failed"
        session.commit()
        failed_status = runtime_service.get_run_task_status(session, run.id)

        run.status = "aborted"
        run.exit_reason = "unsafe environment"
        session.commit()
        aborted_status = runtime_service.get_run_task_status(session, run.id)

        final_token = json.loads(json.dumps(snapshot.token_payload, ensure_ascii=False))
        final_token["phase"] = "final_verify"
        final_token["observations"]["evaluate_verify_power"] = {"decision": "complete"}
        snapshot.token_payload = final_token
        run.status = "running"
        run.runtime_phase = "final_verify"
        run.exit_reason = ""
        session.commit()
        finalizing_status = runtime_service.get_run_task_status(session, run.id)

        legacy_payload = json.loads(json.dumps(artifact_object.content_json, ensure_ascii=False))
        legacy_payload["runtime_contract"].pop("workflow_steps", None)
        artifact_object.content_json = legacy_payload
        session.commit()
        legacy_status = runtime_service.get_run_task_status(session, run.id)

    assert advancing_status.progress.model_dump() == {"completed": 1, "total": 2, "percent": 50}
    assert [stage.status for stage in advancing_status.stages] == ["completed", "in_progress"]
    assert advancing_status.current_stage_id == "verify_power"
    assert failed_status.activity_status == "failed"
    assert failed_status.stages[1].status == "failed"
    assert failed_status.stages[1].status_reason == "power check failed"
    assert aborted_status.activity_status == "aborted"
    assert aborted_status.stages[1].status == "aborted"
    assert finalizing_status.activity_status == "finalizing"
    assert finalizing_status.current_stage_id == ""
    assert finalizing_status.progress.percent == 100
    assert [stage.status for stage in finalizing_status.stages] == ["completed", "completed"]
    assert legacy_status.stages == []
    assert legacy_status.progress.total == 0
    assert legacy_status.current_stage_id == ""


def test_runtime_service_batches_inputs_that_arrive_before_first_checkpoint(runtime_stack) -> None:
    database_manager, _, _, compiler_service, skills_service, runtime_service = runtime_stack

    with database_manager.session() as session:
        skill = skills_service.create_skill(
            session,
            CreateSkillRequest(
                name="Runtime Early Input Batch",
                description="Validate early terminal inputs are delivered once the first checkpoint exists.",
            ),
        )
        published = skills_service.publish_skill(
            session,
            skill_id=skill.id,
            payload=PublishSkillRequest(publish_reason="Runtime early input batch publish"),
        )
        process_publish_job(session, compiler_service, published.compile_request.id)

        invocation = runtime_service.create_invocation(
            session,
            CreateInvocationRequest(
                skill_key=skill.key,
                terminal_context={"terminal_kind": "web"},
            ),
        )
        first = runtime_service.append_terminal_event(
            session,
            invocation.run_id or "",
            AppendTerminalEventRequest(
                direction="input",
                event_kind="terminal.text.input.v1",
                mime_type="text/plain",
                payload_inline="配置清单：CPU、主板、显卡、电源。",
                external_event_id="runtime-early-input-batch-1",
            ),
        )
        second = runtime_service.append_terminal_event(
            session,
            invocation.run_id or "",
            AppendTerminalEventRequest(
                direction="input",
                event_kind="terminal.text.input.v1",
                mime_type="text/plain",
                payload_inline="兼容性自查结果：已逐项确认。",
                external_event_id="runtime-early-input-batch-2",
            ),
        )
        runtime_service.process_run(session, invocation.run_id or "")
        run = runtime_service.get_run(session, invocation.run_id or "")
        terminal_events = runtime_service.list_terminal_events(session, invocation.run_id or "")
        trace_events = runtime_service.list_trace_events(session, invocation.run_id or "")
        snapshots = runtime_service.list_snapshots(session, invocation.run_id or "")

    wait_traces = [event for event in trace_events if event.event_type == "runtime.wait_checkpoint.entered"]
    agent_nodes = [
        event.payload["node_id"]
        for event in trace_events
        if event.event_type == "runtime.agent.completed"
    ]
    consumed = snapshots[2].token_payload["control"]["terminal_consumption"]
    delivered_evidence = snapshots[2].token_payload["control"]["wait"]["evidence"]

    assert run.status == "succeeded"
    assert [event.direction for event in terminal_events] == ["input", "input", "output", "output"]
    assert terminal_events[2].source_ref["node_id"] == "evaluate_collect_context"
    assert [item["seq_no"] for item in consumed] == [first.seq_no, second.seq_no]
    assert [item["seq_no"] for item in delivered_evidence] == [first.seq_no, second.seq_no]
    assert [event.payload["resumed_from_existing_input"] for event in wait_traces] == [True]
    assert agent_nodes.count("evaluate_collect_context") == 1
    assert "instruct_collect_context" in agent_nodes


def test_runtime_service_treats_abort_decision_as_semantic_abort(tmp_path) -> None:
    settings = create_test_settings().model_copy(
        update={"agent_harness_sandbox_root": str(tmp_path / "agent-runs")}
    )
    database_manager = DatabaseManager(settings.sqlalchemy_database_url)
    database_manager.create_schema()
    gitlab_gateway = FakeGitLabGateway()
    inference_gateway = QueuedInferenceGateway([json.dumps(build_test_abort_formal_v5_artifact(), ensure_ascii=False)])
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
    agent_harness_service = AgentHarnessService(
        settings=settings,
        chat_model_factory=lambda _definition: ScriptedRunnerChatModel(
            observation_overrides_by_node={
                "evaluate_collect_context": {
                    "decision": "abort",
                    "next_phase": "terminal_abort",
                    "terminal_message": "已中止：电源功率不足，请先更换合适电源后再继续。",
                    "final_response": "已中止：电源功率不足，请先更换合适电源后再继续。",
                    "reason": "电源额定功率低于当前硬件估算功耗，继续装机会带来安全风险。",
                }
            }
        ),
    )
    runtime_service = RuntimeService(
        settings=settings,
        inference_gateway=inference_gateway,
        agent_harness_service=agent_harness_service,
    )

    try:
        with database_manager.session() as session:
            skill = skills_service.create_skill(
                session,
                CreateSkillRequest(
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
                    skill_key=skill.key,
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
            runtime_service.process_run(session, invocation.run_id or "")
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

    assert initial_run.status == "waiting_runtime"
    assert refreshed_invocation.status == "aborted"
    assert run.status == "aborted"
    assert run.runtime_phase == "aborted"
    assert "电源功率不足" in run.final_output
    assert "电源功率不足" in run.exit_reason
    assert run.ended_at is not None
    assert terminal_session.terminal_session.status == "closed"
    assert [event.direction for event in terminal_events] == ["input", "output"]
    assert terminal_events[-1].source_ref["node_id"] == "terminal_abort"
    assert [part.kind for part in terminal_events[-1].parts] == ["text"]
    assert "电源功率不足" in terminal_events[-1].parts[0].text
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
                skill_key=skill.key,
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
        run_model = runtime_service.repository.get_run(session, invocation.run_id or "")
        job = JobRepository().get_runtime_job_by_dedupe_key(session, f"job:runtime:{invocation.run_id}")

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

    assert appended.seq_no == 1
    assert duplicate.event_id == appended.event_id
    assert duplicate.seq_no == appended.seq_no
    assert [event.seq_no for event in events] == [1]
    assert events[0].payload_inline == "追加输入"
    assert run_model is not None
    assert run_model.latest_terminal_seq == 1
    assert job is not None
    assert job.status == "pending"


def test_terminal_event_append_refreshes_stale_run_before_assigning_seq(runtime_stack) -> None:
    database_manager, _, _, compiler_service, skills_service, runtime_service = runtime_stack

    with database_manager.session() as session:
        skill = skills_service.create_skill(
            session,
            CreateSkillRequest(
                name="Terminal Event Stale Run",
                description="Validate stale Run seq refresh.",
            ),
        )
        published = skills_service.publish_skill(
            session,
            skill_id=skill.id,
            payload=PublishSkillRequest(publish_reason="Terminal event stale run publish"),
        )
        process_publish_job(session, compiler_service, published.compile_request.id)
        invocation = runtime_service.create_invocation(
            session,
            CreateInvocationRequest(skill_key=skill.key),
        )
        run_id = invocation.run_id or ""

    with database_manager.session() as stale_session:
        stale_run = runtime_service.repository.get_run(stale_session, run_id)
        assert stale_run is not None
        assert stale_run.latest_terminal_seq == 0
        stale_session.commit()

        with database_manager.session() as writer_session:
            first = runtime_service.append_terminal_event(
                writer_session,
                run_id,
                AppendTerminalEventRequest(
                    direction="input",
                    event_kind="terminal.text.input.v1",
                    mime_type="text/plain",
                    payload_inline="第一条输入",
                    external_event_id="stale-run-first",
                ),
            )

        assert first.seq_no == 1
        assert stale_run.latest_terminal_seq == 0

        second = runtime_service.append_terminal_event(
            stale_session,
            run_id,
            AppendTerminalEventRequest(
                direction="input",
                event_kind="terminal.text.input.v1",
                mime_type="text/plain",
                payload_inline="第二条输入",
                external_event_id="stale-run-second",
            ),
        )
        events = runtime_service.list_terminal_events(stale_session, run_id)
        refreshed_run = runtime_service.repository.get_run(stale_session, run_id)

    assert second.seq_no == 2
    assert [event.seq_no for event in events] == [1, 2]
    assert refreshed_run is not None
    assert refreshed_run.latest_terminal_seq == 2


def test_runtime_service_publishes_terminal_trace_and_task_status_events_after_commit(runtime_stack) -> None:
    database_manager, _, _, compiler_service, skills_service, runtime_service = runtime_stack
    sink = RecordingRuntimeEventSink()
    service = RuntimeService(
        settings=runtime_service.settings,
        inference_gateway=runtime_service.inference_gateway,
        agent_harness_service=runtime_service.agent_harness_service,
        runtime_event_sink=sink,
    )

    with database_manager.session() as session:
        skill = skills_service.create_skill(
            session,
            CreateSkillRequest(
                name="Runtime Event Sink",
                description="Validate runtime event sink publishing.",
            ),
        )
        published = skills_service.publish_skill(
            session,
            skill_id=skill.id,
            payload=PublishSkillRequest(publish_reason="Runtime event sink publish"),
        )
        process_publish_job(session, compiler_service, published.compile_request.id)
        invocation = service.create_invocation(
            session,
            CreateInvocationRequest(
                skill_key=skill.key,
                terminal_context={"terminal_kind": "web"},
            ),
        )
        create_events = list(sink.events)
        sink.events.clear()

        appended = service.append_terminal_event(
            session,
            invocation.run_id or "",
            AppendTerminalEventRequest(
                direction="input",
                event_kind="terminal.text.input.v1",
                mime_type="text/plain",
                payload_inline="现场输入",
                external_event_id="runtime-event-sink-input",
            ),
        )
        append_events = list(sink.events)
        sink.events.clear()
        service.process_run(session, invocation.run_id or "")
        process_events = list(sink.events)
        terminal_events = service.list_terminal_events(session, invocation.run_id or "")
        hydrated_task_status_event = service.runtime_event_envelope(
            session,
            event_type="run.task_status.updated",
            run_id=invocation.run_id or "",
            seq_no=0,
        )

    assert any(event["event_type"] == "trace.event.appended" for event in create_events)
    assert [event["event_type"] for event in append_events] == ["terminal.event.appended"]
    assert append_events[0]["seq_no"] == appended.seq_no
    assert append_events[0]["payload"]["payload_inline"] == "现场输入"
    output_messages = [event for event in process_events if event["event_type"] == "terminal.event.appended"]
    output_events = [event for event in terminal_events if event.direction == "output"]
    assert len(output_messages) == len(output_events)
    assert [event["payload"]["direction"] for event in output_messages] == ["output"] * len(output_messages)
    assert [event["seq_no"] for event in output_messages] == sorted(event["seq_no"] for event in output_messages)
    assert any(event["event_type"] == "trace.event.appended" for event in process_events)
    task_status_messages = [event for event in process_events if event["event_type"] == "run.task_status.updated"]
    assert task_status_messages
    assert task_status_messages[-1]["payload"]["run_status"] == "succeeded"
    assert task_status_messages[-1]["payload"]["progress"] == {"completed": 1, "total": 1, "percent": 100}
    assert task_status_messages[-1]["payload"]["snapshot_seq"] == task_status_messages[-2]["payload"]["snapshot_seq"]
    assert task_status_messages[-1]["payload"]["updated_at"] >= task_status_messages[-2]["payload"]["updated_at"]
    assert hydrated_task_status_event is not None
    assert hydrated_task_status_event["event_type"] == "run.task_status.updated"
    assert hydrated_task_status_event["payload"]["run_status"] == "succeeded"


def test_runtime_service_publish_uses_precommit_upper_bounds(runtime_stack) -> None:
    database_manager, _, _, compiler_service, skills_service, runtime_service = runtime_stack
    sink = RecordingRuntimeEventSink()
    service = RuntimeService(
        settings=runtime_service.settings,
        inference_gateway=runtime_service.inference_gateway,
        agent_harness_service=runtime_service.agent_harness_service,
        runtime_event_sink=sink,
    )

    with database_manager.session() as session:
        skill = skills_service.create_skill(
            session,
            CreateSkillRequest(
                name="Runtime Event Upper Bound",
                description="Validate runtime event publish upper bounds.",
            ),
        )
        published = skills_service.publish_skill(
            session,
            skill_id=skill.id,
            payload=PublishSkillRequest(publish_reason="Runtime event upper bound publish"),
        )
        process_publish_job(session, compiler_service, published.compile_request.id)
        invocation = service.create_invocation(
            session,
            CreateInvocationRequest(skill_key=skill.key),
        )
        run_id = invocation.run_id or ""
        service.append_terminal_event(
            session,
            run_id,
            AppendTerminalEventRequest(
                direction="input",
                event_kind="terminal.text.input.v1",
                mime_type="text/plain",
                payload_inline="已发布输入",
                external_event_id="upper-bound-first",
            ),
        )

    sink.events.clear()
    with database_manager.session() as stale_session:
        stale_run = service.repository.get_run(stale_session, run_id)
        assert stale_run is not None
        previous_terminal_seq = stale_run.latest_terminal_seq
        previous_trace_seq = stale_run.latest_trace_seq
        stale_session.commit()

        with database_manager.session() as writer_session:
            service.append_terminal_event(
                writer_session,
                run_id,
                AppendTerminalEventRequest(
                    direction="input",
                    event_kind="terminal.text.input.v1",
                    mime_type="text/plain",
                    payload_inline="并发输入",
                    external_event_id="upper-bound-concurrent",
                ),
            )
            writer_run = service.repository.get_run(writer_session, run_id)
            assert writer_run is not None
            service._append_trace_event(
                writer_session,
                run=writer_run,
                phase="test",
                event_type="runtime.concurrent_test",
                payload={"source": "concurrent_writer"},
            )
            writer_session.commit()

        sink.events.clear()
        returned_terminal_seq, returned_trace_seq = service._commit_and_publish(
            stale_session,
            run_id=run_id,
            previous_terminal_seq=previous_terminal_seq,
            previous_trace_seq=previous_trace_seq,
        )
        published_events = list(sink.events)
        terminal_events = service.list_terminal_events(stale_session, run_id)

    assert returned_terminal_seq == previous_terminal_seq
    assert returned_trace_seq == previous_trace_seq
    assert [event.seq_no for event in terminal_events] == [1, 2]
    assert not [event for event in published_events if event["event_type"] == "terminal.event.appended"]
    assert not [event for event in published_events if event["event_type"] == "trace.event.appended"]


def test_runtime_service_does_not_reuse_terminal_input_across_checkpoints(tmp_path) -> None:
    settings = create_test_settings().model_copy(
        update={"agent_harness_sandbox_root": str(tmp_path / "agent-runs")}
    )
    database_manager = DatabaseManager(settings.sqlalchemy_database_url)
    database_manager.create_schema()
    gitlab_gateway = FakeGitLabGateway()
    compile_gateway = FakeInferenceGateway()
    compiler_service = CompilerService(
        settings=settings,
        gitlab_gateway=gitlab_gateway,
        inference_gateway=compile_gateway,
    )
    skills_service = SkillsService(
        settings=settings,
        gitlab_gateway=gitlab_gateway,
        compiler_service=compiler_service,
    )
    agent_harness_service = AgentHarnessService(
        settings=settings,
        chat_model_factory=lambda _definition: ScriptedRunnerChatModel(
            observation_overrides_by_node={
                "evaluate_collect_context": {
                    "decision": "continue",
                    "next_phase": "second_step",
                    "terminal_message": "第一阶段证据已确认，可以进入第二阶段。",
                    "reason": "电源型号信息已经满足第一阶段验证。",
                    "expected_inputs": [],
                    "evidence_assessment": {
                        "accepted_event_refs": ["terminal_event:1"],
                        "rejected_event_refs": [],
                        "missing_evidence": [],
                        "unsafe_or_ambiguous_facts": [],
                    },
                },
                "evaluate_second_step": {
                    "decision": "need_more_evidence",
                    "terminal_message": "不应使用上一阶段输入评估第二阶段。",
                    "reason": "第二阶段缺少新的现场证据。",
                    "wait_reason": "等待第二阶段证据。",
                    "expected_inputs": ["text"],
                },
                "instruct_second_step": {
                    "decision": "need_more_evidence",
                    "terminal_message": "请提交第二阶段照片，或逐项文字确认现场状态。",
                    "reason": "第二阶段需要新的现场证据。",
                    "wait_reason": "等待逐项文字确认或照片。",
                    "expected_inputs": ["text", "image"],
                },
            }
        ),
    )
    runtime_service = RuntimeService(
        settings=settings,
        inference_gateway=FailingInferenceGateway(),
        agent_harness_service=agent_harness_service,
    )

    try:
        with database_manager.session() as session:
            skill = skills_service.create_skill(
                session,
                CreateSkillRequest(
                    name="Runtime Checkpoint Consumption",
                    description="Validate checkpoint-scoped terminal input consumption.",
                ),
            )
            published = skills_service.publish_skill(
                session,
                skill_id=skill.id,
                payload=PublishSkillRequest(publish_reason="Runtime checkpoint consumption"),
            )
            compiled = process_publish_job(session, compiler_service, published.compile_request.id)
            artifact = compiler_service.get_artifact(session, compiled.artifact_id or "")
            artifact_object = session.get(ArtifactObject, artifact.artifact_object_id)
            assert artifact_object is not None
            artifact_payload = json.loads(json.dumps(artifact_object.content_json, ensure_ascii=False))
            artifact_payload = _add_second_wait_checkpoint_to_artifact(artifact_payload)
            artifact_object.content_json = artifact_payload
            session.flush()

            invocation = runtime_service.create_invocation(
                session,
                CreateInvocationRequest(
                    skill_key=skill.key,
                    terminal_context={"terminal_kind": "web"},
                ),
            )
            runtime_service.append_terminal_event(
                session,
                invocation.run_id or "",
                AppendTerminalEventRequest(
                    direction="input",
                    event_kind="terminal.text.input.v1",
                    mime_type="text/plain",
                    payload_inline="海韵 focus gx-1200",
                    external_event_id="runtime-checkpoint-consumption-input-1",
                ),
            )
            runtime_service.process_run(session, invocation.run_id or "")
            run = runtime_service.get_run(session, invocation.run_id or "")
            terminal_events = runtime_service.list_terminal_events(session, invocation.run_id or "")
            trace_events = runtime_service.list_trace_events(session, invocation.run_id or "")
            snapshots = runtime_service.list_snapshots(session, invocation.run_id or "")
    finally:
        database_manager.dispose()

    latest_token = snapshots[-1].token_payload
    wait = latest_token["control"]["wait"]
    consumption = latest_token["control"]["terminal_consumption"]
    wait_traces = [event for event in trace_events if event.event_type == "runtime.wait_checkpoint.entered"]
    collect_eval_payload = next(
        event.payload["observation"]
        for event in trace_events
        if event.event_type == "runtime.agent.completed" and event.payload["node_id"] == "evaluate_collect_context"
    )
    agent_node_ids = [
        event.payload["node_id"]
        for event in trace_events
        if event.event_type == "runtime.agent.completed"
    ]

    assert run.status == "waiting_input"
    assert run.checkpoint_id == "second_step_evidence"
    assert wait["checkpoint_id"] == "second_step_evidence"
    assert wait["status"] == "waiting"
    assert wait["reason"] == "等待逐项文字确认或照片。"
    assert wait["expected_inputs"] == [{"kind": "text"}, {"kind": "image"}]
    assert wait["evidence"] == []
    assert wait["input_window"]["policy"] == "checkpoint_scoped"
    assert wait["input_window"]["accept_after_seq"] == 2
    evaluate_second_step_node = next(node for node in artifact_payload["nodes"] if node["id"] == "evaluate_second_step")
    runner_context = runtime_service._build_runner_context(
        run=run,
        node=evaluate_second_step_node,
        token=latest_token,
        artifact_payload=artifact_payload,
    )
    runner_turn_context = runtime_service._build_runner_turn_context(
        run=run,
        node=evaluate_second_step_node,
        mode="evidence_evaluation",
        context=runner_context,
    )
    assert runner_turn_context["current_checkpoint"]["expected_inputs"] == [{"kind": "text"}, {"kind": "image"}]
    assert collect_eval_payload["next_phase"] == "second_step"
    assert collect_eval_payload["runner"]["suggested_next_phase"] == "second_step"
    assert latest_token["control"]["latest_evaluation"]["resolved_next_phase"] == "instruct_second_step"
    assert consumption == [
        {
            "seq_no": 1,
            "event_id": terminal_events[0].id,
            "checkpoint_id": "collect_context_evidence",
            "workflow_step_id": "collect_context",
            "consumed_at": consumption[0]["consumed_at"],
        }
    ]
    assert "evaluate_second_step" not in agent_node_ids
    assert [event.payload["resumed_from_existing_input"] for event in wait_traces] == [True, False]
    assert [event.direction for event in terminal_events] == ["input", "output", "output"]


def test_runtime_new_checkpoint_does_not_consume_same_turn_trigger_input(tmp_path) -> None:
    settings = create_test_settings().model_copy(
        update={"agent_harness_sandbox_root": str(tmp_path / "agent-runs")}
    )
    database_manager = DatabaseManager(settings.sqlalchemy_database_url)
    database_manager.create_schema()
    gitlab_gateway = FakeGitLabGateway()
    compile_gateway = FakeInferenceGateway()
    compiler_service = CompilerService(
        settings=settings,
        gitlab_gateway=gitlab_gateway,
        inference_gateway=compile_gateway,
    )
    skills_service = SkillsService(
        settings=settings,
        gitlab_gateway=gitlab_gateway,
        compiler_service=compiler_service,
    )
    agent_harness_service = AgentHarnessService(
        settings=settings,
        chat_model_factory=lambda _definition: ScriptedRunnerChatModel(
            observation_overrides_by_node={
                "evaluate_collect_context": {
                    "decision": "continue",
                    "next_phase": "second_step",
                    "terminal_message": "第一阶段证据已确认，可以进入第二阶段。",
                    "reason": "第一阶段证据满足要求。",
                    "expected_inputs": [],
                    "evidence_assessment": {
                        "accepted_event_refs": ["terminal_event:1"],
                        "rejected_event_refs": [],
                        "missing_evidence": [],
                        "unsafe_or_ambiguous_facts": [],
                    },
                },
                "instruct_second_step": {
                    "decision": "need_more_evidence",
                    "terminal_message": "请提交第二阶段照片。",
                    "reason": "第二阶段需要新的现场证据。",
                    "wait_reason": "等待第二阶段照片。",
                    "expected_inputs": ["image"],
                },
                "evaluate_second_step": {
                    "decision": "need_more_evidence",
                    "terminal_message": "不应使用继续评估第二阶段。",
                    "reason": "第二阶段缺少新的现场证据。",
                    "wait_reason": "等待第二阶段证据。",
                    "expected_inputs": ["image"],
                },
            }
        ),
    )
    runtime_service = RuntimeService(
        settings=settings,
        inference_gateway=FailingInferenceGateway(),
        agent_harness_service=agent_harness_service,
    )

    try:
        with database_manager.session() as session:
            skill = skills_service.create_skill(
                session,
                CreateSkillRequest(
                    name="Runtime Same Turn Trigger",
                    description="Validate new checkpoints ignore the input that triggered the instruct node.",
                ),
            )
            published = skills_service.publish_skill(
                session,
                skill_id=skill.id,
                payload=PublishSkillRequest(publish_reason="Runtime same turn trigger"),
            )
            compiled = process_publish_job(session, compiler_service, published.compile_request.id)
            artifact = compiler_service.get_artifact(session, compiled.artifact_id or "")
            artifact_object = session.get(ArtifactObject, artifact.artifact_object_id)
            assert artifact_object is not None
            artifact_object.content_json = _add_second_wait_checkpoint_to_artifact(
                json.loads(json.dumps(artifact_object.content_json, ensure_ascii=False))
            )
            session.flush()

            invocation = runtime_service.create_invocation(
                session,
                CreateInvocationRequest(
                    skill_key=skill.key,
                    terminal_context={"terminal_kind": "web"},
                ),
            )
            run_id = invocation.run_id or ""
            runtime_service.append_terminal_event(
                session,
                run_id,
                AppendTerminalEventRequest(
                    direction="input",
                    event_kind="terminal.text.input.v1",
                    mime_type="text/plain",
                    payload_inline="第一阶段证据",
                    external_event_id="runtime-same-turn-trigger-input-1",
                ),
            )
            runtime_service.process_run(session, run_id)

            source_snapshot = next(
                snapshot
                for snapshot in runtime_service.repository.list_snapshots(session, run_id)
                if snapshot.token_payload.get("phase") == "instruct_second_step"
                and snapshot.selection_summary.get("selected") == "evaluate_collect_context"
            )
            run_model = session.get(Run, run_id)
            assert run_model is not None
            next_snapshot_seq = run_model.latest_snapshot_seq + 1
            run_model.latest_snapshot_seq = next_snapshot_seq
            run_model.status = "waiting_input"
            run_model.runtime_phase = "instruct_second_step"
            session.add(
                SessionTokenSnapshot(
                    run_id=run_id,
                    seq_no=next_snapshot_seq,
                    token_payload=json.loads(json.dumps(source_snapshot.token_payload, ensure_ascii=False)),
                    enabled_set=["instruct_second_step"],
                    selection_summary={"selected": None, "reason": "test rewind to pending instruct"},
                    snapshot_hash=runtime_service._hash_payload(source_snapshot.token_payload),
                )
            )
            session.flush()

            before_trace_seq = max((event.seq_no for event in runtime_service.list_trace_events(session, run_id)), default=0)
            continue_event = runtime_service.append_terminal_event(
                session,
                run_id,
                AppendTerminalEventRequest(
                    direction="input",
                    event_kind="terminal.text.input.v1",
                    mime_type="text/plain",
                    payload_inline="继续",
                    external_event_id="runtime-same-turn-trigger-continue",
                ),
            ).event
            runtime_service.process_run(session, run_id)
            run = runtime_service.get_run(session, run_id)
            terminal_events = runtime_service.list_terminal_events(session, run_id)
            trace_events = runtime_service.list_trace_events(session, run_id)
            snapshots = runtime_service.list_snapshots(session, run_id)
    finally:
        database_manager.dispose()

    new_output_events = [
        event
        for event in terminal_events
        if event.direction == "output" and event.seq_no > continue_event.seq_no
    ]
    new_agent_nodes = [
        event.payload["node_id"]
        for event in trace_events
        if event.seq_no > before_trace_seq and event.event_type == "runtime.agent.completed"
    ]
    wait_traces = [
        event
        for event in trace_events
        if event.seq_no > before_trace_seq and event.event_type == "runtime.wait_checkpoint.entered"
    ]
    latest_token = snapshots[-1].token_payload
    wait = latest_token["control"]["wait"]
    consumed_seqs = [
        item["seq_no"]
        for item in latest_token["control"].get("terminal_consumption", [])
        if isinstance(item, dict)
    ]

    assert run.status == "waiting_input"
    assert len(new_output_events) == 1
    assert new_output_events[0].source_ref["node_id"] == "instruct_second_step"
    assert "不应使用继续评估第二阶段" not in str(new_output_events[0].payload_inline)
    assert new_agent_nodes == ["instruct_second_step"]
    assert wait["checkpoint_id"] == "second_step_evidence"
    assert wait["status"] == "waiting"
    assert wait["evidence"] == []
    assert wait["input_window"]["accept_after_seq"] == continue_event.seq_no
    assert wait_traces[-1].payload["resumed_from_existing_input"] is False
    assert wait_traces[-1].payload["wait"]["input_window"]["accept_after_seq"] == continue_event.seq_no
    assert continue_event.seq_no not in consumed_seqs


def test_runtime_evidence_progress_preserves_accepted_requirements_when_later_result_marks_missing() -> None:
    artifact_payload = build_test_formal_v5_artifact()
    artifact_payload["runtime_contract"]["expected_evidence"]["collect_context"] = [
        {"description": "桌面截图", "kind": "image"},
        {"description": "设备管理器截图（无未知设备）", "kind": "image"},
        {"description": "磁盘管理截图（分区完整）", "kind": "image"},
        {"description": "任务管理器性能页（内存频率）", "kind": "image"},
    ]
    wait = {
        "checkpoint_id": "collect_context_evidence",
        "workflow_step_id": "collect_context",
    }
    token = {
        "control": {
            "wait": wait,
            "evidence_progress": RuntimeService._build_evidence_progress(
                wait=wait,
                artifact_payload=artifact_payload,
                updated_by_node="instruct_collect_context",
            ),
        }
    }
    node = {"id": "evaluate_collect_context"}

    RuntimeService._merge_evidence_progress_from_observation(
        token=token,
        node=node,
        artifact_payload=artifact_payload,
        observation={
            "evidence_assessment": {
                "requirement_results": [
                    {
                        "requirement_key": "evidence_1",
                        "status": "accepted",
                        "event_refs": ["terminal_event:113"],
                        "reason": "桌面截图通过。",
                    },
                    {
                        "requirement_key": "evidence_3",
                        "status": "accepted",
                        "event_refs": ["terminal_event:116"],
                        "reason": "磁盘管理截图通过。",
                    },
                    {
                        "requirement_key": "evidence_4",
                        "status": "accepted",
                        "event_refs": ["terminal_event:118"],
                        "reason": "任务管理器截图通过。",
                    },
                ]
            }
        },
    )
    RuntimeService._merge_evidence_progress_from_observation(
        token=token,
        node=node,
        artifact_payload=artifact_payload,
        observation={
            "evidence_assessment": {
                "requirement_results": [
                    {
                        "requirement_key": "evidence_2",
                        "status": "rejected",
                        "event_refs": ["terminal_event:120"],
                        "reason": "设备管理器仍有未知设备。",
                    }
                ]
            }
        },
    )
    RuntimeService._merge_evidence_progress_from_observation(
        token=token,
        node=node,
        artifact_payload=artifact_payload,
        observation={
            "evidence_assessment": {
                "requirement_results": [
                    {
                        "requirement_key": "evidence_2",
                        "status": "accepted",
                        "event_refs": ["terminal_event:124"],
                        "reason": "设备管理器已无未知设备。",
                    },
                    {
                        "requirement_key": "evidence_1",
                        "status": "missing",
                        "event_refs": [],
                        "reason": "模型本轮未重新引用桌面截图。",
                    },
                    {
                        "requirement_key": "evidence_3",
                        "status": "missing",
                        "event_refs": [],
                        "reason": "模型本轮未重新引用磁盘管理截图。",
                    },
                    {
                        "requirement_key": "evidence_4",
                        "status": "ambiguous",
                        "event_refs": [],
                        "reason": "模型本轮未重新引用任务管理器截图。",
                    },
                ]
            }
        },
    )

    requirements = {
        item["requirement_key"]: item
        for item in token["control"]["evidence_progress"]["requirements"]
    }
    assert {key: item["status"] for key, item in requirements.items()} == {
        "evidence_1": "accepted",
        "evidence_2": "accepted",
        "evidence_3": "accepted",
        "evidence_4": "accepted",
    }
    assert requirements["evidence_1"]["accepted_event_refs"] == ["terminal_event:113"]
    assert requirements["evidence_2"]["rejected_event_refs"] == ["terminal_event:120"]
    assert requirements["evidence_2"]["accepted_event_refs"] == ["terminal_event:124"]
    assert requirements["evidence_3"]["accepted_event_refs"] == ["terminal_event:116"]
    assert requirements["evidence_4"]["accepted_event_refs"] == ["terminal_event:118"]


def test_runtime_evidence_progress_initializes_new_checkpoint_independently() -> None:
    artifact_payload = build_test_formal_v5_artifact()
    artifact_payload["runtime_contract"]["expected_evidence"]["second_step"] = [
        {"description": "第二阶段照片", "kind": "image"}
    ]
    wait = {
        "checkpoint_id": "second_step_evidence",
        "workflow_step_id": "second_step",
    }

    progress = RuntimeService._build_evidence_progress(
        wait=wait,
        artifact_payload=artifact_payload,
        updated_by_node="instruct_second_step",
    )

    assert progress["checkpoint_id"] == "second_step_evidence"
    assert progress["workflow_step_id"] == "second_step"
    assert progress["requirements"] == [
        {
            "requirement_key": "evidence_1",
            "description": "第二阶段照片",
            "kind": "image",
            "status": "missing",
            "accepted_event_refs": [],
            "rejected_event_refs": [],
            "latest_event_refs": [],
            "reason": "",
            "updated_at": progress["requirements"][0]["updated_at"],
            "updated_by_node": "instruct_second_step",
        }
    ]


def test_runtime_missing_evaluation_transition_fails_before_success_output(runtime_stack) -> None:
    database_manager, _, _, compiler_service, skills_service, runtime_service = runtime_stack

    with database_manager.session() as session:
        skill = skills_service.create_skill(
            session,
            CreateSkillRequest(
                name="Runtime Missing Transition",
                description="Validate missing evaluation transition recovery.",
            ),
        )
        published = skills_service.publish_skill(
            session,
            skill_id=skill.id,
            payload=PublishSkillRequest(publish_reason="Runtime missing transition"),
        )
        compiled = process_publish_job(session, compiler_service, published.compile_request.id)
        artifact = compiler_service.get_artifact(session, compiled.artifact_id or "")
        artifact_object = session.get(ArtifactObject, artifact.artifact_object_id)
        assert artifact_object is not None
        artifact_payload = json.loads(json.dumps(artifact_object.content_json, ensure_ascii=False))
        artifact_payload["dependency_graph_for_view"] = [
            edge
            for edge in artifact_payload["dependency_graph_for_view"]
            if edge != {"from": "evaluate_collect_context", "to": "final_verify"}
        ]
        artifact_object.content_json = artifact_payload
        session.flush()

        invocation = runtime_service.create_invocation(
            session,
            CreateInvocationRequest(
                skill_key=skill.key,
                terminal_context={"terminal_kind": "web"},
            ),
        )
        runtime_service.append_terminal_event(
            session,
            invocation.run_id or "",
            AppendTerminalEventRequest(
                direction="input",
                event_kind="terminal.text.input.v1",
                mime_type="text/plain",
                payload_inline="我已经完成当前步骤，并上传了现场说明。",
                external_event_id="runtime-missing-transition-input",
            ),
        )
        runtime_service.process_run(session, invocation.run_id or "")
        run = runtime_service.get_run(session, invocation.run_id or "")
        terminal_events = runtime_service.list_terminal_events(session, invocation.run_id or "")
        trace_events = runtime_service.list_trace_events(session, invocation.run_id or "")

    output_texts = [str(event.payload_inline or "") for event in terminal_events if event.direction == "output"]
    failure_traces = [event for event in trace_events if event.event_type == "runtime.message_processing.failed"]

    assert run.status == "waiting_input"
    assert failure_traces
    assert "has no runtime transition" in failure_traces[-1].payload["error"]
    assert "刚才服务器开小差了，请您重试！" in output_texts
    assert not any("已确认当前证据，可以继续最终核验" in text for text in output_texts)


def _add_second_wait_checkpoint_to_artifact(artifact_payload: dict) -> dict:
    nodes = list(artifact_payload["nodes"])
    final_index = next(index for index, node in enumerate(nodes) if node["id"] == "final_verify")
    nodes[final_index:final_index] = [
        {
            "id": "instruct_second_step",
            "kind": "llm",
            "guard": {"phase_is": "instruct_second_step"},
            "actor": {"name": "agent.llm"},
            "interaction": {
                "runner_turn_kind": "step_instruction",
                "output_to_terminal": True,
                "wait_after_output": True,
                "checkpoint_id": "second_step_evidence",
                "workflow_step_id": "second_step",
                "wait_reason": "等待用户提交第二阶段证据。",
                "expected_inputs": [
                    {
                        "kind": "image",
                        "event_kind": "terminal.image.input.v1",
                    }
                ],
                "resume_phase": "evaluate_second_step",
            },
            "projection": {
                "system_template": "输出当前现实步骤指令。second_step",
                "user_template": "请用户提交第二阶段证据。当前 Token：{{token}}",
            },
            "merge": [
                {
                    "op": "set",
                    "path": "observations.instruct_second_step",
                    "from": "observation",
                }
            ],
            "policy": {"priority": 35},
        },
        {
            "id": "evaluate_second_step",
            "kind": "llm",
            "guard": {"phase_is": "evaluate_second_step"},
            "actor": {"name": "agent.llm"},
            "interaction": {"runner_turn_kind": "evidence_evaluation", "evaluation": True},
            "projection": {
                "system_template": "只输出 JSON decision。evaluate_second_step",
                "user_template": "根据 token.control.wait.evidence 判断 second_step 是否完成。当前 Token：{{token}}",
            },
            "merge": [
                {
                    "op": "set",
                    "path": "observations.evaluate_second_step",
                    "from": "observation",
                },
                {
                    "op": "set",
                    "path": "phase",
                    "from": "observation.next_phase",
                },
            ],
            "policy": {"priority": 36},
        },
    ]
    artifact_payload["nodes"] = nodes
    artifact_payload["dependency_graph_for_view"] = [
        {"from": "start", "to": "instruct_collect_context"},
        {"from": "instruct_collect_context", "to": "evaluate_collect_context"},
        {"from": "evaluate_collect_context", "to": "instruct_second_step"},
        {"from": "instruct_second_step", "to": "evaluate_second_step"},
        {"from": "evaluate_second_step", "to": "final_verify"},
        {"from": "final_verify", "to": "terminal"},
    ]
    runtime_contract = artifact_payload["runtime_contract"]
    runtime_contract.setdefault("workflow_steps", []).append(
        {
            "id": "second_step",
            "title": "第二阶段",
            "goal": "等待第二阶段现场证据。",
            "source_evidence": "测试用第二阶段。",
        }
    )
    runtime_contract.setdefault("expected_evidence", {})["second_step"] = [
        {
            "kind": "text",
            "event_kind": "terminal.text.input.v1",
        }
    ]
    runtime_contract.setdefault("wait_checkpoints", []).append(
        {
            "checkpoint_id": "second_step_evidence",
            "workflow_step_id": "second_step",
            "expected_inputs": [{"kind": "image"}],
        }
    )
    return artifact_payload


def test_runtime_job_running_append_marks_rerun_and_finish_requeues_unsynced_input(runtime_stack) -> None:
    database_manager, _, _, compiler_service, skills_service, runtime_service = runtime_stack

    with database_manager.session() as session:
        skill = skills_service.create_skill(
            session,
            CreateSkillRequest(
                name="Runtime Job Rerun",
                description="Validate runtime job rerun marker.",
            ),
        )
        published = skills_service.publish_skill(
            session,
            skill_id=skill.id,
            payload=PublishSkillRequest(publish_reason="Runtime job rerun publish"),
        )
        process_publish_job(session, compiler_service, published.compile_request.id)
        invocation = runtime_service.create_invocation(
            session,
            CreateInvocationRequest(
                skill_key=skill.key,
                terminal_context={"terminal_kind": "web"},
            ),
        )
        runtime_service.append_terminal_event(
            session,
            invocation.run_id or "",
            AppendTerminalEventRequest(
                direction="input",
                event_kind="terminal.text.input.v1",
                mime_type="text/plain",
                payload_inline="第一条输入",
                external_event_id="runtime-job-rerun-input-1",
            ),
        )
        job = JobRepository().get_runtime_job_by_dedupe_key(session, f"job:runtime:{invocation.run_id}")
        assert job is not None
        job.status = "running"
        job.attempt_no = 4
        session.commit()

        runtime_service.append_terminal_event(
            session,
            invocation.run_id or "",
            AppendTerminalEventRequest(
                direction="input",
                event_kind="terminal.text.input.v1",
                mime_type="text/plain",
                payload_inline="运行中追加输入",
                external_event_id="runtime-job-rerun-input-2",
            ),
        )
        session.refresh(job)
        assert job.status == "running"
        assert job.attempt_no == 4
        assert (job.payload or {}).get("rerun_requested") is True
        run_model = runtime_service.repository.get_run(session, invocation.run_id or "")
        assert run_model is not None
        snapshot = runtime_service.repository.list_snapshots(session, invocation.run_id or "")[-1]
        runtime_service._finish_runtime_job_turn(
            session=session,
            job=job,
            run=run_model,
            token=snapshot.token_payload,
            status="succeeded",
        )
        assert job.status == "pending"
        assert job.attempt_no == 4
        assert job.payload == {"run_id": invocation.run_id}
        assert job.available_at is not None


def test_runtime_service_records_failed_run_when_runner_fails(runtime_stack) -> None:
    database_manager, _, _, compiler_service, skills_service, _ = runtime_stack
    failing_runtime = RuntimeService(
        settings=create_test_settings(),
        inference_gateway=FailingInferenceGateway(),
        agent_harness_service=RaisingAgentHarnessService(RuntimeError("runner provider unavailable")),
    )

    with database_manager.session() as session:
        skill = skills_service.create_skill(
            session,
            CreateSkillRequest(
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
                skill_key=skill.key,
                input_envelope={"user_input": "触发失败"},
            ),
        )
        failing_runtime.append_terminal_event(
            session,
            invocation.run_id or "",
            AppendTerminalEventRequest(
                direction="input",
                event_kind="terminal.text.input.v1",
                mime_type="text/plain",
                payload_inline="触发失败",
                external_event_id="runtime-failure-input",
            ),
        )
        failing_runtime.process_run(session, invocation.run_id or "")
        refreshed_invocation = failing_runtime.get_invocation(session, invocation.id)
        run = failing_runtime.get_run(session, invocation.run_id or "")
        trace_events = failing_runtime.list_trace_events(session, invocation.run_id or "")
        terminal_events = failing_runtime.list_terminal_events(session, invocation.run_id or "")

    assert refreshed_invocation.status == "failed"
    assert run.status == "failed"
    assert run.exit_reason == "runner provider unavailable"
    assert trace_events[-1].event_type == "runtime.failed"
    assert terminal_events[-1].direction == "output"
    assert terminal_events[-1].event_kind == "terminal.text.output.v1"
    assert terminal_events[-1].external_event_id == f"runtime:{invocation.run_id}:failed"
    assert terminal_events[-1].trace_event_id == trace_events[-1].id
    assert [part.kind for part in terminal_events[-1].parts] == ["text"]
    assert "Runtime 执行失败" in terminal_events[-1].parts[0].text
    assert "Runtime 执行失败" in terminal_events[-1].payload_inline
    assert "当前运行已停止" in terminal_events[-1].payload_inline
    assert "调试运行" not in terminal_events[-1].payload_inline
    assert "runner provider unavailable" in terminal_events[-1].payload_inline


def test_runtime_service_records_runner_gateway_error_details_in_failed_trace_payload(runtime_stack) -> None:
    database_manager, _, _, compiler_service, skills_service, _ = runtime_stack
    failing_runtime = RuntimeService(
        settings=create_test_settings(),
        inference_gateway=FailingInferenceGateway(),
        agent_harness_service=RaisingAgentHarnessService(
            SkillsGatewayError("LLM Inference Gateway 返回错误响应。", details=FailingSkillsGatewayInferenceGateway.details)
        ),
    )

    with database_manager.session() as session:
        skill = skills_service.create_skill(
            session,
            CreateSkillRequest(
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
                skill_key=skill.key,
                input_envelope={"user_input": "触发 gateway 失败"},
            ),
        )
        failing_runtime.append_terminal_event(
            session,
            invocation.run_id or "",
            AppendTerminalEventRequest(
                direction="input",
                event_kind="terminal.text.input.v1",
                mime_type="text/plain",
                payload_inline="触发 gateway 失败",
                external_event_id="runtime-gateway-failure-input",
            ),
        )
        failing_runtime.process_run(session, invocation.run_id or "")
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
    failing_runtime = RuntimeService(
        settings=create_test_settings(),
        inference_gateway=FailingInferenceGateway(),
        agent_harness_service=RaisingAgentHarnessService(
            SkillsGatewayError("LLM Inference Gateway 返回错误响应。", details=FailingSkillsGatewayInferenceGateway.details)
        ),
    )

    with database_manager.session() as session:
        skill = skills_service.create_skill(
            session,
            CreateSkillRequest(
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
                skill_key=skill.key,
                terminal_context={"terminal_kind": "web"},
            ),
        )
        first_run = runtime_service.get_run(session, invocation.run_id or "")
        runtime_service.process_run(session, invocation.run_id or "")
        snapshots_before_failure = runtime_service.list_snapshots(session, invocation.run_id or "")
        wait_reason_before_failure = snapshots_before_failure[-1].token_payload["control"]["wait"]["reason"]
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
        failing_runtime.process_run(session, invocation.run_id or "")
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
        runtime_service.process_run(session, invocation.run_id or "")
        final_run = runtime_service.get_run(session, invocation.run_id or "")

    assert first_run.status == "waiting_runtime"
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
    assert [part.kind for part in terminal_events[-1].parts] == ["text"]
    assert terminal_events[-1].parts[0].text == "刚才服务器开小差了，请您重试！"
    assert snapshots[-1].token_payload["status"] == "waiting"
    assert snapshots[-1].token_payload["metadata"]["terminal_cursor"] == terminal_events[-1].seq_no
    assert snapshots[-1].token_payload["control"]["wait"]["reason"] == wait_reason_before_failure
    assert snapshots[-1].token_payload["control"]["wait"]["reason"] != "刚才服务器开小差了，请您重试！"
    assert snapshots[-1].token_payload["control"]["wait"]["recoverable_errors"]
    assert retry.seq_no > terminal_events[-1].seq_no
    assert final_run.status == "succeeded"
