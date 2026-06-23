from __future__ import annotations

from pydantic import BaseModel, Field


class AgentSkill(BaseModel):
    name: str
    description: str = ""
    tools: list[str] = Field(default_factory=list)
    instruction: str = ""
    path: str = ""
