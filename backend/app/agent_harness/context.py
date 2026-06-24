from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.agent_harness.events import AgentEventWriter
from app.agent_harness.memory.store import MemoryStore
from app.agent_harness.models.factory import ChatModelFactory, create_chat_model
from app.agent_harness.sandbox.base import AgentSandbox
from app.agent_harness.schemas import AgentDefinition, AgentInvocation
from app.agent_harness.skills.loader import SkillLoader
from app.agent_harness.skills.spec import AgentSkill
from app.core.config import Settings


@dataclass(slots=True)
class AgentBuildContext:
    settings: Settings
    invocation: AgentInvocation
    definition: AgentDefinition
    system_prompt: str
    memory_prompt: str
    skill_metadata: list[AgentSkill]
    sandbox: AgentSandbox
    event_writer: AgentEventWriter
    memory_store: MemoryStore
    memory_scope: str
    memory_payload: dict[str, Any]
    skill_loader: SkillLoader
    chat_model_factory: ChatModelFactory | None = None

    def create_model(self) -> Any:
        if self.chat_model_factory is not None:
            return self.chat_model_factory(self.definition)
        model_ref = self.definition.model
        thinking_enabled = model_ref.thinking_enabled if model_ref is not None else self.settings.llm_text_enable_thinking
        model_name = model_ref.name if model_ref is not None else None
        return create_chat_model(
            settings=self.settings,
            name=model_name,
            thinking_enabled=thinking_enabled,
            attach_tracing=False,
        )
