from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.agent_harness.agents.psop.runner.schemas import RUNNER_OBSERVATION_SCHEMA
from app.agent_harness.schemas import AgentArtifact, AgentInvocation, AgentResult
from app.domain.compiler.models import ArtifactObject, EgCompileArtifact, SkillCompileRequest
from app.domain.runtime.schemas import CreateInvocationRequest
from app.domain.runtime.service import RuntimeService
from app.domain.skills.models import SkillDefinition, SkillVersion
from app.gateway.inference import LlmCompletion
from app.infra.database import DatabaseManager
from tests.test_skills_api import build_test_formal_v5_artifact, create_test_settings


class QueuedInferenceGateway:
    def __init__(self, contents: list[str]) -> None:
        self.contents = contents
        self.calls: list[dict[str, str]] = []

    def complete(self, *, system_prompt: str, user_prompt: str, route_key: str = "text") -> LlmCompletion:
        self.calls.append({"system_prompt": system_prompt, "user_prompt": user_prompt, "route_key": route_key})
        return LlmCompletion(
            content=self.contents.pop(0) if self.contents else "fallback",
            provider="fake",
            model="fake-model",
            raw_response={},
        )


class FailingInferenceGateway:
    def complete(self, *, system_prompt: str, user_prompt: str, route_key: str = "text") -> LlmCompletion:
        raise RuntimeError("LLM gateway should not be called")


class RecordingRunnerHarness:
    def __init__(self, root: Path, *, include_reference_image: bool = False, fail: bool = False) -> None:
        self.root = root
        self.include_reference_image = include_reference_image
        self.fail = fail
        self.invocations: list[AgentInvocation] = []

    def invoke(
        self,
        invocation: AgentInvocation,
        *,
        persistence_session=None,
        persistence_context: dict[str, str] | None = None,
    ) -> AgentResult:
        self.invocations.append(invocation)
        sandbox_root = self.root / f"runner-{len(self.invocations)}"
        outputs = sandbox_root / "outputs"
        outputs.mkdir(parents=True, exist_ok=True)
        if self.fail:
            return AgentResult(
                agent_run_id=invocation.agent_run_id or "runner-failed",
                agent_key=invocation.agent_key,
                status="failed",
                final_output="",
                sandbox_path=str(sandbox_root),
                workspace_path=str(sandbox_root / "workspace"),
                error_message="scripted runner failure",
            )
        observation = self._observation(invocation)
        (outputs / "runner-observation.json").write_text(
            json.dumps(observation, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return AgentResult(
            agent_run_id=invocation.agent_run_id or "runner-succeeded",
            agent_key=invocation.agent_key,
            status="succeeded",
            final_output="",
            sandbox_path=str(sandbox_root),
            workspace_path=str(sandbox_root / "workspace"),
            artifacts=[
                AgentArtifact(
                    artifact_type="runner_observation",
                    path="sandbox://outputs/runner-observation.json",
                    provenance={"schema": RUNNER_OBSERVATION_SCHEMA},
                )
            ],
        )

    def _observation(self, invocation: AgentInvocation) -> dict[str, Any]:
        node_id = str(invocation.context.get("node", {}).get("id") or "instruct_collect_context")
        reference_images: list[dict[str, Any]] = []
        if self.include_reference_image:
            candidates = invocation.context.get("step_reference_images")
            if isinstance(candidates, list) and candidates:
                reference_images.append(
                    {
                        "reference_image_ref": str(candidates[0]["reference_image_ref"]),
                        "title": "参考照片",
                        "caption": "请按参考照片补充当前步骤证据。",
                        "source_ref": "skill-draft/assets/reference.png",
                        "display_order": 1,
                    }
                )
        return {
            "schema": RUNNER_OBSERVATION_SCHEMA,
            "node_id": node_id,
            "decision": "need_more_evidence",
            "terminal_message": "请补充当前步骤照片，并确认现场安全条件。",
            "reason": "需要更多现场证据。",
            "next_phase": "waiting",
            "wait_reason": "等待用户补充证据。",
            "expected_inputs": ["text", "image"],
            "evidence_assessment": {
                "accepted_event_refs": [],
                "rejected_event_refs": [],
                "missing_evidence": ["现场照片"],
                "unsafe_or_ambiguous_facts": [],
            },
            "reference_images": reference_images,
            "safety_flags": [],
            "final_response": "",
            "source_refs": [],
            "confidence": "medium",
        }


def test_runtime_without_agent_binding_keeps_llm_gateway_path() -> None:
    settings = create_test_settings()
    database_manager = DatabaseManager(settings.sqlalchemy_database_url)
    database_manager.create_schema()
    gateway = QueuedInferenceGateway(["请补充当前步骤照片。"])
    harness = RecordingRunnerHarness(Path(settings.repo_root) / ".tmp-runner-unused")
    runtime_service = RuntimeService(
        settings=settings,
        inference_gateway=gateway,
        agent_harness_service=harness,
    )

    try:
        with database_manager.session() as session:
            _install_skill_artifact(session, key="legacy-runtime", artifact=build_test_formal_v5_artifact())
            invocation = runtime_service.create_invocation(
                session,
                CreateInvocationRequest(skill_key="legacy-runtime", terminal_context={"terminal_kind": "web"}),
            )
            run = runtime_service.get_run(session, invocation.run_id or "")
    finally:
        database_manager.dispose()

    assert run.status == "waiting_input"
    assert len(gateway.calls) == 1
    assert harness.invocations == []


def test_runtime_with_agent_binding_invokes_runner_agent(tmp_path) -> None:
    settings = create_test_settings()
    database_manager = DatabaseManager(settings.sqlalchemy_database_url)
    database_manager.create_schema()
    harness = RecordingRunnerHarness(tmp_path)
    runtime_service = RuntimeService(
        settings=settings,
        inference_gateway=FailingInferenceGateway(),
        agent_harness_service=harness,
    )

    try:
        with database_manager.session() as session:
            _install_skill_artifact(session, key="runner-runtime", artifact=_runner_artifact())
            invocation = runtime_service.create_invocation(
                session,
                CreateInvocationRequest(skill_key="runner-runtime", terminal_context={"terminal_kind": "web"}),
            )
            run = runtime_service.get_run(session, invocation.run_id or "")
            trace_events = runtime_service.list_trace_events(session, invocation.run_id or "")
            terminal_events = runtime_service.list_terminal_events(session, invocation.run_id or "")
    finally:
        database_manager.dispose()

    runner_trace = next(event for event in trace_events if event.event_type == "gateway.inference.completed")
    assert run.status == "waiting_input"
    assert len(harness.invocations) == 1
    assert harness.invocations[0].agent_key == "psop.runner"
    assert harness.invocations[0].context["node"]["id"] == "instruct_collect_context"
    assert runner_trace.payload["agent_key"] == "psop.runner"
    assert runner_trace.payload["agent_run_id"]
    assert any(event.event_kind == "terminal.text.output.v1" for event in terminal_events)


def test_runtime_runner_reference_image_outputs_multimodal(tmp_path) -> None:
    settings = create_test_settings()
    database_manager = DatabaseManager(settings.sqlalchemy_database_url)
    database_manager.create_schema()
    harness = RecordingRunnerHarness(tmp_path, include_reference_image=True)
    runtime_service = RuntimeService(
        settings=settings,
        inference_gateway=FailingInferenceGateway(),
        agent_harness_service=harness,
    )

    try:
        with database_manager.session() as session:
            image_object = ArtifactObject(
                bucket="test-bucket",
                object_key="internal/reference.png",
                media_type="image/png",
                size_bytes=12,
                checksum="sha256-reference",
            )
            session.add(image_object)
            session.flush()
            _install_skill_artifact(
                session,
                key="runner-runtime-image",
                artifact=_runner_artifact(reference_image_object_id=image_object.id),
            )
            invocation = runtime_service.create_invocation(
                session,
                CreateInvocationRequest(skill_key="runner-runtime-image", terminal_context={"terminal_kind": "web"}),
            )
            terminal_events = runtime_service.list_terminal_events(session, invocation.run_id or "")
            trace_events = runtime_service.list_trace_events(session, invocation.run_id or "")
    finally:
        database_manager.dispose()

    multimodal = next(event for event in terminal_events if event.event_kind == "terminal.multimodal.output.v1")
    image_part = next(part for part in multimodal.parts if part.kind == "image")
    runner_trace = next(event for event in trace_events if event.event_type == "gateway.inference.completed")
    assert multimodal.mime_type == "multipart/mixed"
    assert multimodal.payload_inline == {
        "summary": "请补充当前步骤照片，并确认现场安全条件。",
        "reference_image_count": 1,
    }
    assert image_part.artifact_object_id
    assert image_part.metadata["reference_image_ref"] == "collect_context:ppe-example"
    assert "object_key" not in json.dumps(multimodal.payload_inline, ensure_ascii=False)
    assert runner_trace.payload["reference_images"][0]["artifact_object_id"] == image_part.artifact_object_id


def _install_skill_artifact(session, *, key: str, artifact: dict[str, Any]) -> None:
    definition = SkillDefinition(
        key=key,
        name=key,
        description="Runner runtime integration fixture.",
        gitlab_project_id=f"project-{key}",
        repository_url=f"https://gitlab.example.local/skills/{key}",
    )
    session.add(definition)
    session.flush()
    version = SkillVersion(
        skill_definition_id=definition.id,
        version_no=1,
        status="published",
        source_ref="main",
        source_commit_sha="commit-sha",
        manifest_snapshot={},
        runtime_policy_snapshot={},
    )
    session.add(version)
    session.flush()
    definition.latest_published_version_id = version.id
    artifact_object = ArtifactObject(
        bucket="test-bucket",
        object_key=f"artifacts/{key}.json",
        media_type="application/json",
        content_json=artifact,
    )
    session.add(artifact_object)
    session.flush()
    compile_request = SkillCompileRequest(
        skill_definition_id=definition.id,
        skill_version_id=version.id,
        trigger_type="test",
        source_commit_sha="commit-sha",
        status="succeeded",
        dedupe_key=f"compile:{key}",
    )
    session.add(compile_request)
    session.flush()
    eg_artifact = EgCompileArtifact(
        skill_compile_request_id=compile_request.id,
        skill_version_id=version.id,
        artifact_object_id=artifact_object.id,
        formal_revision=str(artifact.get("formal_revision") or "psop-eg-formal/v5"),
        artifact_version=str(artifact.get("artifact_version") or "test"),
        graph_summary=artifact.get("graph_summary") or {},
        capability_summary=artifact.get("capability_summary") or {},
        status="ready",
    )
    session.add(eg_artifact)
    session.flush()


def _runner_artifact(reference_image_object_id: str | None = None) -> dict[str, Any]:
    artifact = build_test_formal_v5_artifact()
    for node in artifact["nodes"]:
        if node.get("kind") == "llm" and node.get("actor", {}).get("name") == "agent.llm":
            node["agent_binding"] = {
                "agent_key": "psop.runner",
                "output_schema": RUNNER_OBSERVATION_SCHEMA,
            }
    if reference_image_object_id:
        artifact["runtime_contract"]["workflow_steps"][0]["reference_images"] = [
            {
                "reference_image_ref": "collect_context:ppe-example",
                "workflow_step_id": "collect_context",
                "title": "PPE 示例",
                "caption": "用于提示用户补充同类现场照片。",
                "source_ref": "skill-draft/assets/reference.png",
                "display_order": 1,
                "artifact_object_id": reference_image_object_id,
                "mime_type": "image/png",
            }
        ]
    else:
        artifact["runtime_contract"]["workflow_steps"][0]["reference_images"] = []
    return artifact
