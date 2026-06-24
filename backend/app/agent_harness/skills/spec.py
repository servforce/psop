from __future__ import annotations

from pydantic import BaseModel, Field


class AgentSkill(BaseModel):
    name: str
    description: str = ""
    allowed_tools: list[str] = Field(default_factory=list)
    instruction: str = ""
    path: str = ""

    @property
    def tools(self) -> list[str]:
        return list(self.allowed_tools or [])
