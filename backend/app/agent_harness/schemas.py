from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


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


class AgentDefinition(BaseModel):
    agent_key: str
    version: str = "v1"
    runner: str = "deepagents"
    route_key: str = "text"
    description: str = ""
    skills: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    memory_scope: str | None = None


class AgentInvocation(BaseModel):
    agent_key: str
    input: dict[str, Any]
    context: dict[str, Any] = Field(default_factory=dict)
    memory_scope: str | None = None
    workspace_id: str | None = None
    use_mock_model: bool = False


class AgentResult(BaseModel):
    agent_run_id: str
    agent_key: str
    status: Literal["succeeded", "failed"]
    final_output: str
    structured_output: dict[str, Any] = Field(default_factory=dict)
    events: list[AgentEvent] = Field(default_factory=list)
    artifacts: list[AgentArtifact] = Field(default_factory=list)
    workspace_path: str | None = None
    error_message: str = ""
