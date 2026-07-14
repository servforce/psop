from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, Field


class AgentEvent(BaseModel):
    seq_no: int
    event_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    occurred_at: datetime


class AgentArtifact(BaseModel):
    artifact_type: str
    path: str | None = None
    inline_content: dict[str, Any] = Field(default_factory=dict)
    provenance: dict[str, Any] = Field(default_factory=dict)


class AgentModelRef(BaseModel):
    name: str | None = None
    thinking_enabled: bool = False


class AgentMiddlewareDefinition(BaseModel):
    name: str
    enabled: bool = True
    config: dict[str, Any] = Field(default_factory=dict)


class AgentDefinition(BaseModel):
    agent_key: str
    version: str = "v1"
    runner_kind: str = Field(default="langchain_agent", validation_alias=AliasChoices("runner_kind", "runner"))
    factory: str = "make_agent"
    description: str = ""
    model: AgentModelRef | None = None
    system_prompt_file: str = "system.md"
    memory_file: str | None = None
    skills: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    memory_scope: str | None = None
    middleware: list[str | AgentMiddlewareDefinition] = Field(default_factory=list)

    @property
    def runner(self) -> str:
        return self.runner_kind


class AgentInvocationAttachment(BaseModel):
    attachment_id: str
    source_ref: str = ""
    terminal_event_seq: int | None = None
    part_id: str = ""
    filename: str = ""
    media_type: str = "application/octet-stream"
    size_bytes: int = 0
    checksum: str = ""
    artifact_object_id: str = ""
    content_base64: str = Field(default="", exclude=True)

    def redacted_metadata(self) -> dict[str, Any]:
        return {
            "attachment_id": self.attachment_id,
            "source_ref": self.source_ref,
            "terminal_event_seq": self.terminal_event_seq,
            "part_id": self.part_id,
            "filename": self.filename,
            "media_type": self.media_type,
            "size_bytes": self.size_bytes,
            "checksum": self.checksum,
            "artifact_object_id": self.artifact_object_id,
            "content_base64_chars": len(self.content_base64),
        }


class AgentInvocation(BaseModel):
    agent_key: str
    input: dict[str, Any]
    context: dict[str, Any] = Field(default_factory=dict)
    attachments: list[AgentInvocationAttachment] = Field(default_factory=list)
    memory_scope: str | None = None
    agent_run_id: str | None = None
    workspace_id: str | None = None
    deadline_monotonic: float | None = Field(default=None, exclude=True)


class AgentResult(BaseModel):
    agent_run_id: str
    agent_key: str
    status: Literal["succeeded", "failed"]
    final_output: str
    structured_output: dict[str, Any] = Field(default_factory=dict)
    events: list[AgentEvent] = Field(default_factory=list)
    artifacts: list[AgentArtifact] = Field(default_factory=list)
    sandbox_path: str | None = None
    workspace_path: str | None = None
    error_message: str = ""
