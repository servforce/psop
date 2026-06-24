from __future__ import annotations

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
        result = agent.invoke(
            {"messages": [{"role": "user", "content": str(invocation.input.get("text") or "")}]},
            context={"agent_run_id": sandbox.agent_run_id, "sandbox_id": sandbox.sandbox_id},
        )
        final_output = _extract_final_output(result)
        return AgentResult(
            agent_run_id=sandbox.agent_run_id,
            agent_key=definition.agent_key,
            status="succeeded",
            final_output=final_output,
            structured_output={"raw_result": _jsonable(result)},
            events=event_writer.events,
            artifacts=_collect_artifacts(sandbox),
            sandbox_path=str(sandbox.root_path),
            workspace_path=str(sandbox.workspace_path),
        )


def _collect_artifacts(sandbox: AgentSandbox) -> list[AgentArtifact]:
    artifact_path = "/mnt/psop/workspace/result.md"
    try:
        resolved = sandbox.resolve_virtual_path(artifact_path)
    except ValueError:
        return []
    if not resolved.exists():
        return []
    return [AgentArtifact(artifact_type="demo_report", path=artifact_path)]


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


def _jsonable(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except TypeError:
        return str(value)
