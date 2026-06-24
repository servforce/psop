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


class AgentInvocation(BaseModel):
    agent_key: str
    input: dict[str, Any]
    context: dict[str, Any] = Field(default_factory=dict)
    memory_scope: str | None = None
    agent_run_id: str | None = None
    workspace_id: str | None = None


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
