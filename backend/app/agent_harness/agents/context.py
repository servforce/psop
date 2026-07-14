from __future__ import annotations

from dataclasses import dataclass, field
import time
from typing import Any

from app.agent_harness.events import AgentEventWriter
from app.agent_harness.errors import AgentDeadlineExceededError
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
    _created_models: list[Any] = field(default_factory=list, init=False, repr=False)

    def create_model(self) -> Any:
        if self.chat_model_factory is not None:
            model = self.chat_model_factory(self.definition)
        else:
            model_ref = self.definition.model
            thinking_enabled = model_ref.thinking_enabled if model_ref is not None else self.settings.llm_text_enable_thinking
            model_name = model_ref.name if model_ref is not None else None
            has_image_attachment = any(
                str(attachment.media_type or "").lower().startswith("image/")
                for attachment in self.invocation.attachments
            )
            model_kwargs: dict[str, Any] = {}
            if self.definition.agent_key == "psop.runner":
                model_kwargs = {
                    "timeout": self._remaining_provider_timeout(),
                    "max_retries": 0,
                }
            model = create_chat_model(
                settings=self.settings,
                name=model_name,
                thinking_enabled=thinking_enabled,
                attach_tracing=False,
                multimodal=has_image_attachment,
                **model_kwargs,
            )
        self._created_models.append(model)
        return model

    def refresh_provider_deadline(self) -> None:
        if self.definition.agent_key != "psop.runner":
            return
        timeout = self._remaining_provider_timeout()
        for model in self._created_models:
            self._set_model_timeout(model, timeout)

    def _remaining_provider_timeout(self) -> float:
        remaining = (
            self.invocation.deadline_monotonic - time.monotonic()
            if self.invocation.deadline_monotonic is not None
            else float(self.settings.runtime_step_timeout_seconds)
        )
        if remaining <= 0:
            raise AgentDeadlineExceededError("Agent invocation exceeded its runtime step deadline.")
        return max(0.001, min(float(self.settings.llm_timeout_seconds), remaining))

    @staticmethod
    def _set_model_timeout(model: Any, timeout: float) -> None:
        if hasattr(model, "request_timeout"):
            try:
                model.request_timeout = timeout
            except Exception:
                pass
        root_client = getattr(model, "root_client", None)
        if root_client is not None and callable(getattr(root_client, "with_options", None)):
            try:
                updated = root_client.with_options(timeout=timeout)
                model.root_client = updated
                if hasattr(model, "client") and hasattr(updated, "chat"):
                    model.client = updated.chat.completions
            except Exception:
                pass
        root_async_client = getattr(model, "root_async_client", None)
        if root_async_client is not None and callable(getattr(root_async_client, "with_options", None)):
            try:
                updated_async = root_async_client.with_options(timeout=timeout)
                model.root_async_client = updated_async
                if hasattr(model, "async_client") and hasattr(updated_async, "chat"):
                    model.async_client = updated_async.chat.completions
            except Exception:
                pass
