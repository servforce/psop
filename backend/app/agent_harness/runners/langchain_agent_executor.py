from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from collections.abc import Callable
from typing import Any

from app.agent_harness.events import AgentEventWriter
from app.agent_harness.errors import AgentDeadlineExceededError
from app.agent_harness.sandbox.base import AgentSandbox
from app.agent_harness.schemas import AgentArtifact, AgentDefinition, AgentInvocation, AgentResult


@dataclass(frozen=True, slots=True)
class RequiredArtifactContract:
    artifact_type: str
    artifact_ref: str
    continuation_prompt: str
    max_continuations: int = 2
    required_skill_names: tuple[str, ...] = ()
    required_skill_resources: tuple[tuple[str, str], ...] = ()
    required_tool_names: tuple[str, ...] = ()
    missing_interactions_prompt: str = ""


REQUIRED_ARTIFACTS_BY_AGENT = {
    "psop.builder": RequiredArtifactContract(
        artifact_type="skill_draft_candidate",
        artifact_ref="sandbox://outputs/builder-result.json",
        continuation_prompt=(
            "你还没有生成必需产物 sandbox://outputs/builder-result.json。"
            "现在必须立即调用 psop.builder.submit_candidate，参数必须是完整 candidate。"
            "不要回复自然语言说明，不要只说将要提交。"
        ),
    ),
    "psop.compiler": RequiredArtifactContract(
        artifact_type="eg_compile_candidate",
        artifact_ref="sandbox://outputs/compiler-result.json",
        continuation_prompt=(
            "你还没有生成必需产物 sandbox://outputs/compiler-result.json。"
            "现在必须立即调用 psop.compiler.submit_candidate。"
            "如果已有 scaffold 返回的 candidate_ref，参数必须优先使用 {\"candidate_ref\":\"...\"}；"
            "只有没有 candidate_ref 时才提供完整 candidate。"
            "不要回复自然语言说明，不要只说将要提交。"
        ),
        required_skill_names=(
            "psop-compiler",
        ),
        required_skill_resources=(
            ("psop-compiler", "core/SKILL.md"),
            ("psop-compiler", "contract/SKILL.md"),
            ("psop-compiler", "mapping/SKILL.md"),
            ("psop-compiler", "review/SKILL.md"),
        ),
        required_tool_names=(
            "psop.compiler.read_skill_source",
            "psop.compiler.read_manifest_snapshot",
            "psop.compiler.read_allowed_runtime",
            "psop.compiler.read_domain_pack",
            "psop.compiler.build_formal_v5_scaffold",
            "psop.compiler.validate_formal_v5",
            "psop.compiler.submit_candidate",
        ),
        missing_interactions_prompt=(
            "你已经生成 compiler candidate，但尚未完成 psop.compiler 的必需上下文或审查步骤。"
            "现在必须补齐缺失的 load_skill / load_skill_resource / psop.compiler tool 调用。"
            "如果补齐后发现 candidate 需要调整，必须重新调用 psop.compiler.validate_formal_v5 "
            "和 psop.compiler.submit_candidate 覆盖 sandbox outputs；提交时优先使用 candidate_ref。"
            "如果无需调整，完成缺失调用后即可结束。"
            "不要回复自然语言说明，不要只说将要补齐。"
        ),
    ),
    "psop.runner": RequiredArtifactContract(
        artifact_type="runner_observation",
        artifact_ref="sandbox://outputs/runner-observation.json",
        continuation_prompt=(
            "你还没有生成必需产物 sandbox://outputs/runner-observation.json。"
            "现在必须立即调用 psop.runner.submit_observation，参数必须是完整 RunnerObservation。"
            "不要回复自然语言说明，不要只说将要提交。"
        ),
        required_tool_names=(
            "psop.runner.submit_observation",
        ),
        missing_interactions_prompt=(
            "你已经生成 runner observation，但尚未通过 psop.runner.submit_observation 提交正式 observation。"
            "现在必须调用 psop.runner.submit_observation 覆盖 sandbox outputs。"
            "如果补齐后 observation 需要调整，必须重新调用 psop.runner.submit_observation 覆盖 sandbox outputs。"
            "不要回复自然语言说明，不要只说将要补齐。"
        ),
    ),
}


class LangChainAgentExecutor:
    def invoke(
        self,
        *,
        agent: Any,
        invocation: AgentInvocation,
        definition: AgentDefinition,
        sandbox: AgentSandbox,
        event_writer: AgentEventWriter,
        before_provider_call: Callable[[], None] | None = None,
    ) -> AgentResult:
        messages: list[Any] = [_initial_user_message(invocation)]
        if invocation.attachments:
            event_writer.record(
                "agent.multimodal.attachments.prepared",
                {
                    "attachment_count": len(invocation.attachments),
                    "image_attachment_count": sum(
                        1
                        for attachment in invocation.attachments
                        if str(attachment.media_type or "").lower().startswith("image/")
                    ),
                    "attachments": [attachment.redacted_metadata() for attachment in invocation.attachments],
                },
            )
        result = None
        artifacts: list[AgentArtifact] = []
        required_artifact = _required_artifact_contract(definition)
        for continuation_index in range(_max_continuations(definition) + 1):
            _check_deadline(invocation)
            if before_provider_call is not None:
                before_provider_call()
            result = agent.invoke(
                {"messages": messages},
                context={"agent_run_id": sandbox.agent_run_id, "sandbox_id": sandbox.sandbox_id},
            )
            _check_deadline(invocation)
            artifacts = _collect_artifacts(sandbox)
            if required_artifact is None:
                break
            missing_interactions = _missing_required_interactions(event_writer.events, required_artifact)
            if _has_artifact(artifacts, required_artifact.artifact_type) and not _has_missing_interactions(
                missing_interactions
            ):
                break
            if continuation_index >= _max_continuations(definition):
                break
            final_output = _extract_final_output(result)
            if not _has_artifact(artifacts, required_artifact.artifact_type):
                event_writer.record(
                    "agent.required_artifact.missing",
                    {
                        "artifact_type": required_artifact.artifact_type,
                        "artifact_ref": required_artifact.artifact_ref,
                        "continuation_index": continuation_index + 1,
                        "final_output": final_output[:1000],
                    },
                )
                continuation_prompt = required_artifact.continuation_prompt
            else:
                event_writer.record(
                    "agent.required_interaction.missing",
                    {
                        "artifact_type": required_artifact.artifact_type,
                        "artifact_ref": required_artifact.artifact_ref,
                        "missing_skills": missing_interactions["skills"],
                        "missing_skill_resources": missing_interactions["skill_resources"],
                        "missing_tools": missing_interactions["tools"],
                        "continuation_index": continuation_index + 1,
                        "final_output": final_output[:1000],
                    },
                )
                continuation_prompt = _missing_interactions_prompt(required_artifact, missing_interactions)
            messages = _messages_from_result(result) or messages
            messages = [*messages, {"role": "user", "content": continuation_prompt}]
        if result is None:
            result = {}
        final_output = _extract_final_output(result)
        if required_artifact is not None and not _has_artifact(artifacts, required_artifact.artifact_type):
            return AgentResult(
                agent_run_id=sandbox.agent_run_id,
                agent_key=definition.agent_key,
                status="failed",
                final_output=final_output,
                structured_output={"raw_result": _jsonable(result)},
                events=event_writer.events,
                artifacts=artifacts,
                sandbox_path=str(sandbox.root_path),
                workspace_path=str(sandbox.workspace_path),
                error_message=f"Agent 未生成必需 artifact：{required_artifact.artifact_ref}。",
            )
        missing_interactions = (
            _missing_required_interactions(event_writer.events, required_artifact) if required_artifact is not None else {}
        )
        if required_artifact is not None and _has_missing_interactions(missing_interactions):
            return AgentResult(
                agent_run_id=sandbox.agent_run_id,
                agent_key=definition.agent_key,
                status="failed",
                final_output=final_output,
                structured_output={"raw_result": _jsonable(result)},
                events=event_writer.events,
                artifacts=artifacts,
                sandbox_path=str(sandbox.root_path),
                workspace_path=str(sandbox.workspace_path),
                error_message=(
                    "Agent 未完成必需交互："
                    f"skills={missing_interactions['skills']}; "
                    f"skill_resources={missing_interactions['skill_resources']}; "
                    f"tools={missing_interactions['tools']}。"
                ),
            )
        return AgentResult(
            agent_run_id=sandbox.agent_run_id,
            agent_key=definition.agent_key,
            status="succeeded",
            final_output=final_output,
            structured_output={"raw_result": _jsonable(result)},
            events=event_writer.events,
            artifacts=artifacts,
            sandbox_path=str(sandbox.root_path),
            workspace_path=str(sandbox.workspace_path),
        )


def _check_deadline(invocation: AgentInvocation) -> None:
    if invocation.deadline_monotonic is None:
        return
    if time.monotonic() >= invocation.deadline_monotonic:
        raise AgentDeadlineExceededError("Agent invocation exceeded its runtime step deadline.")


def _initial_user_message(invocation: AgentInvocation) -> dict[str, Any]:
    text = str(invocation.input.get("text") or "")
    image_attachments = [
        attachment
        for attachment in invocation.attachments
        if str(attachment.media_type or "").lower().startswith("image/") and attachment.content_base64
    ]
    if not image_attachments:
        return {"role": "user", "content": text}
    content_parts: list[dict[str, Any]] = [{"type": "text", "text": text}]
    for attachment in image_attachments:
        label = attachment.label or (
            "步骤参考图（仅用于对照，不是用户证据）"
            if attachment.role == "reference"
            else f"用户现场证据：{attachment.source_ref or attachment.attachment_id}"
        )
        content_parts.append({"type": "text", "text": f"[{attachment.role}] {label}"})
        content_parts.append(
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:{attachment.media_type};base64,{attachment.content_base64}",
                },
            }
        )
    return {"role": "user", "content": content_parts}


def _collect_artifacts(sandbox: AgentSandbox) -> list[AgentArtifact]:
    artifacts: list[AgentArtifact] = []
    builder_result_path = "/mnt/psop/outputs/builder-result.json"
    try:
        builder_result = sandbox.resolve_virtual_path(builder_result_path)
    except ValueError:
        builder_result = None
    if builder_result is not None and builder_result.exists():
        content_hash = hashlib.sha256(builder_result.read_bytes()).hexdigest()
        artifacts.append(
            AgentArtifact(
                artifact_type="skill_draft_candidate",
                path="sandbox://outputs/builder-result.json",
                provenance={"content_hash": content_hash},
            )
        )
    compiler_result = _resolve_optional(sandbox, "/mnt/psop/outputs/compiler-result.json")
    if compiler_result is not None and compiler_result.exists():
        provenance = _json_file_provenance(compiler_result)
        artifacts.append(
            AgentArtifact(
                artifact_type="eg_compile_candidate",
                path="sandbox://outputs/compiler-result.json",
                provenance=provenance,
            )
        )
    compiler_eg_artifact = _resolve_optional(sandbox, "/mnt/psop/outputs/eg.compile.artifact.json")
    if compiler_eg_artifact is not None and compiler_eg_artifact.exists():
        provenance = _json_file_provenance(compiler_eg_artifact)
        artifacts.append(
            AgentArtifact(
                artifact_type="eg_compile_artifact_candidate",
                path="sandbox://outputs/eg.compile.artifact.json",
                provenance=provenance,
            )
        )
    runner_observation = _resolve_optional(sandbox, "/mnt/psop/outputs/runner-observation.json")
    if runner_observation is not None and runner_observation.exists():
        provenance = _runner_observation_provenance(runner_observation)
        artifacts.append(
            AgentArtifact(
                artifact_type="runner_observation",
                path="sandbox://outputs/runner-observation.json",
                provenance=provenance,
            )
        )
    skill_draft_root = _resolve_optional(sandbox, "/mnt/psop/outputs/skill-draft")
    if skill_draft_root is not None and skill_draft_root.is_dir():
        file_paths = sorted(path for path in skill_draft_root.rglob("*") if path.is_file())
        artifacts.append(
            AgentArtifact(
                artifact_type="skill_draft_files",
                path="sandbox://outputs/skill-draft",
                provenance={
                    "content_hash": _directory_hash(skill_draft_root),
                    "file_count": len(file_paths),
                    "files": [path.relative_to(skill_draft_root).as_posix() for path in file_paths],
                },
            )
        )

    artifact_path = "/mnt/psop/workspace/result.md"
    resolved = _resolve_optional(sandbox, artifact_path)
    if resolved is None:
        return artifacts
    if not resolved.exists():
        return artifacts
    artifacts.append(AgentArtifact(artifact_type="demo_report", path=artifact_path))
    return artifacts


def _max_continuations(definition: AgentDefinition) -> int:
    contract = _required_artifact_contract(definition)
    return contract.max_continuations if contract is not None else 0


def _required_artifact_contract(definition: AgentDefinition) -> RequiredArtifactContract | None:
    return REQUIRED_ARTIFACTS_BY_AGENT.get(definition.agent_key)


def _has_artifact(artifacts: list[AgentArtifact], artifact_type: str) -> bool:
    return any(artifact.artifact_type == artifact_type for artifact in artifacts)


def _missing_required_interactions(events, contract: RequiredArtifactContract) -> dict[str, list[str]]:
    loaded_skills = {
        str(event.payload.get("skill_name") or "")
        for event in events
        if event.event_type == "agent.skill.loaded"
    }
    loaded_resources = {
        (str(event.payload.get("skill_name") or ""), str(event.payload.get("resource_path") or ""))
        for event in events
        if event.event_type == "agent.skill.resource.loaded"
    }
    completed_tools = {
        str(event.payload.get("tool_name") or "")
        for event in events
        if event.event_type == "agent.tool.completed"
    }
    return {
        "skills": sorted(skill_name for skill_name in contract.required_skill_names if skill_name not in loaded_skills),
        "skill_resources": sorted(
            f"{skill_name}:{resource_path}"
            for skill_name, resource_path in contract.required_skill_resources
            if (skill_name, resource_path) not in loaded_resources
        ),
        "tools": sorted(tool_name for tool_name in contract.required_tool_names if tool_name not in completed_tools),
    }


def _has_missing_interactions(missing: dict[str, list[str]]) -> bool:
    return bool(missing.get("skills") or missing.get("skill_resources") or missing.get("tools"))


def _missing_interactions_prompt(contract: RequiredArtifactContract, missing: dict[str, list[str]]) -> str:
    details = json.dumps(missing, ensure_ascii=False, indent=2)
    prompt = contract.missing_interactions_prompt or "你尚未完成必需的 Agent 交互。现在必须补齐缺失项。"
    return f"{prompt}\n\n缺失项：\n{details}"


def _resolve_optional(sandbox: AgentSandbox, virtual_path: str):
    try:
        return sandbox.resolve_virtual_path(virtual_path)
    except ValueError:
        return None


def _directory_hash(root):
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _json_file_provenance(path) -> dict[str, Any]:
    provenance: dict[str, Any] = {"content_hash": hashlib.sha256(path.read_bytes()).hexdigest()}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return provenance
    if isinstance(payload, dict):
        artifact = payload.get("artifact") if isinstance(payload.get("artifact"), dict) else payload
        provenance["formal_revision"] = str(artifact.get("formal_revision") or "")
        nodes = artifact.get("nodes")
        if isinstance(nodes, list):
            provenance["node_count"] = len(nodes)
        runtime_contract = artifact.get("runtime_contract")
        workflow_steps = runtime_contract.get("workflow_steps") if isinstance(runtime_contract, dict) else None
        if isinstance(workflow_steps, list):
            provenance["workflow_step_count"] = len(workflow_steps)
    return provenance


def _runner_observation_provenance(path) -> dict[str, Any]:
    provenance: dict[str, Any] = {"content_hash": hashlib.sha256(path.read_bytes()).hexdigest()}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return provenance
    if isinstance(payload, dict):
        provenance["schema"] = str(payload.get("schema") or "")
        provenance["node_id"] = str(payload.get("node_id") or "")
        provenance["decision"] = str(payload.get("decision") or "")
        provenance["runtime_decision"] = str(payload.get("runtime_decision") or "")
        source_refs = payload.get("source_refs")
        if isinstance(source_refs, list):
            provenance["source_ref_count"] = len(source_refs)
    return provenance


def _extract_final_output(result: Any) -> str:
    if isinstance(result, dict):
        messages = result.get("messages")
        if isinstance(messages, list) and messages:
            content = getattr(messages[-1], "content", None)
            if content is not None:
                return str(content)
        if result.get("output"):
            return str(result["output"])
    return str(result)


def _messages_from_result(result: Any) -> list[Any]:
    if isinstance(result, dict):
        messages = result.get("messages")
        if isinstance(messages, list):
            return messages
    return []


def _jsonable(value: Any) -> Any:
    return _redact_sensitive_payload(value)


def _redact_sensitive_payload(value: Any) -> Any:
    if isinstance(value, str):
        return _redact_data_urls(value)
    if isinstance(value, dict):
        return {str(key): _redact_sensitive_payload(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_sensitive_payload(item) for item in value]
    if isinstance(value, tuple):
        return [_redact_sensitive_payload(item) for item in value]
    content = getattr(value, "content", None)
    if content is not None:
        return {
            "type": value.__class__.__name__,
            "content": _redact_sensitive_payload(content),
        }
    try:
        json.dumps(value)
        return value
    except TypeError:
        return _redact_data_urls(str(value))


def _redact_data_urls(value: str) -> str:
    return re.sub(r"data:[^;,\s]+;base64,[A-Za-z0-9+/=_-]+", "data:[redacted];base64,[redacted]", value)
