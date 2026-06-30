from __future__ import annotations

import hashlib
import json
from typing import Any

from app.agent_harness.events import AgentEventWriter
from app.agent_harness.sandbox.base import AgentSandbox
from app.agent_harness.schemas import AgentArtifact, AgentDefinition, AgentInvocation, AgentResult


class LangChainAgentExecutor:
    def invoke(
        self,
        *,
        agent: Any,
        invocation: AgentInvocation,
        definition: AgentDefinition,
        sandbox: AgentSandbox,
        event_writer: AgentEventWriter,
    ) -> AgentResult:
        messages: list[Any] = [{"role": "user", "content": str(invocation.input.get("text") or "")}]
        result = None
        artifacts: list[AgentArtifact] = []
        for continuation_index in range(_max_continuations(definition) + 1):
            result = agent.invoke(
                {"messages": messages},
                context={"agent_run_id": sandbox.agent_run_id, "sandbox_id": sandbox.sandbox_id},
            )
            artifacts = _collect_artifacts(sandbox)
            if not _requires_skill_draft_candidate(definition) or _has_artifact(artifacts, "skill_draft_candidate"):
                break
            if continuation_index >= _max_continuations(definition):
                break
            final_output = _extract_final_output(result)
            event_writer.record(
                "agent.required_artifact.missing",
                {
                    "artifact_type": "skill_draft_candidate",
                    "artifact_ref": "sandbox://outputs/builder-result.json",
                    "continuation_index": continuation_index + 1,
                    "final_output": final_output[:1000],
                },
            )
            messages = _messages_from_result(result) or messages
            messages = [
                *messages,
                {
                    "role": "user",
                    "content": (
                        "你还没有生成必需产物 sandbox://outputs/builder-result.json。"
                        "现在必须立即调用 psop.builder.submit_candidate，参数必须是完整 candidate。"
                        "不要回复自然语言说明，不要只说将要提交。"
                    ),
                },
            ]
        if result is None:
            result = {}
        final_output = _extract_final_output(result)
        if _requires_skill_draft_candidate(definition) and not _has_artifact(artifacts, "skill_draft_candidate"):
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
                error_message="Agent 未生成必需 artifact：sandbox://outputs/builder-result.json。",
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


def _requires_skill_draft_candidate(definition: AgentDefinition) -> bool:
    return definition.agent_key == "psop.builder"


def _max_continuations(definition: AgentDefinition) -> int:
    return 2 if _requires_skill_draft_candidate(definition) else 0


def _has_artifact(artifacts: list[AgentArtifact], artifact_type: str) -> bool:
    return any(artifact.artifact_type == artifact_type for artifact in artifacts)


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
    try:
        json.dumps(value)
        return value
    except TypeError:
        return str(value)
