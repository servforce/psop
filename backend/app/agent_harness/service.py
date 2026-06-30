from __future__ import annotations

import traceback
from typing import Any

from sqlalchemy.orm import Session

from app.agent_harness.agents.context import AgentBuildContext
from app.agent_harness.agents.registry import FileAgentDefinitionRegistry, default_agent_registry
from app.agent_harness.events import AgentEventWriter
from app.agent_harness.memory.file_store import FileMemoryStore
from app.agent_harness.models.factory import ChatModelFactory
from app.agent_harness.persistence.service import AgentHarnessPersistenceService
from app.agent_harness.runners.langchain_agent_executor import LangChainAgentExecutor
from app.agent_harness.sandbox.base import AgentSandboxProvider
from app.agent_harness.sandbox.provider import build_sandbox_provider
from app.agent_harness.schemas import AgentInvocation, AgentResult
from app.agent_harness.skills.loader import SkillLoader
from app.core.config import Settings


class AgentHarnessService:
    def __init__(
        self,
        *,
        settings: Settings,
        registry: FileAgentDefinitionRegistry | None = None,
        sandbox_provider: AgentSandboxProvider | None = None,
        chat_model_factory: ChatModelFactory | None = None,
        persistence_service: AgentHarnessPersistenceService | None = None,
    ) -> None:
        self.settings = settings
        self.registry = registry or default_agent_registry(settings.backend_root)
        self.sandbox_provider = sandbox_provider or build_sandbox_provider(settings)
        self.chat_model_factory = chat_model_factory
        self.persistence_service = persistence_service or AgentHarnessPersistenceService()

    def invoke(
        self,
        invocation: AgentInvocation,
        *,
        persistence_session: Session | None = None,
        persistence_context: dict[str, str] | None = None,
    ) -> AgentResult:
        if not self.settings.agent_harness_enabled:
            raise RuntimeError("Agent Harness 当前未启用。")
        package = self.registry.load(invocation.agent_key)
        definition = package.definition
        sandbox = self.sandbox_provider.acquire(
            agent_run_id=invocation.agent_run_id or invocation.workspace_id,
            input_payload=invocation.model_dump(mode="json"),
        )
        event_writer = AgentEventWriter(sandbox.events_path)
        event_writer.record(
            "agent.run.started",
            {
                "agent_key": definition.agent_key,
                "agent_version": definition.version,
                "runner": definition.runner_kind,
                "profile": self.settings.agent_harness_profile,
                "sandbox_id": sandbox.sandbox_id,
            },
        )
        try:
            skill_loader = SkillLoader(self.settings.repo_root / "skills")
            skill_metadata = [skill_loader.load_metadata(skill_name) for skill_name in definition.skills]
            memory_scope = invocation.memory_scope or definition.memory_scope or definition.agent_key
            memory_store = FileMemoryStore(sandbox.memory_path)
            memory_payload = memory_store.read(memory_scope)
            event_writer.record("agent.memory.read", {"scope": memory_scope, "keys": sorted(memory_payload.keys())})
            context = AgentBuildContext(
                settings=self.settings,
                invocation=invocation,
                definition=definition,
                system_prompt=package.read_system_prompt(),
                memory_prompt=package.read_memory_prompt(),
                skill_metadata=skill_metadata,
                sandbox=sandbox,
                event_writer=event_writer,
                memory_store=memory_store,
                memory_scope=memory_scope,
                memory_payload=memory_payload,
                skill_loader=skill_loader,
                chat_model_factory=self.chat_model_factory,
            )
            agent = package.load_factory()(context)
            result = LangChainAgentExecutor().invoke(
                agent=agent,
                invocation=invocation,
                definition=definition,
                sandbox=sandbox,
                event_writer=event_writer,
            )
            event_writer.record("agent.run.completed", {"status": result.status})
            result.events = event_writer.events
            sandbox.write_output(result.model_dump(mode="json"))
            self._persist_result(
                persistence_session=persistence_session,
                result=result,
                definition_version=definition.version,
                invocation=invocation,
                persistence_context=persistence_context,
            )
            return result
        except Exception as exc:
            event_writer.record(
                "agent.run.failed",
                {"error_type": exc.__class__.__name__, "error": str(exc), "traceback": traceback.format_exc()},
            )
            result = AgentResult(
                agent_run_id=sandbox.agent_run_id,
                agent_key=definition.agent_key,
                status="failed",
                final_output="",
                error_message=str(exc),
                events=event_writer.events,
                sandbox_path=str(sandbox.root_path),
                workspace_path=str(sandbox.workspace_path),
            )
            sandbox.write_output(result.model_dump(mode="json"))
            self._persist_result(
                persistence_session=persistence_session,
                result=result,
                definition_version=definition.version,
                invocation=invocation,
                persistence_context=persistence_context,
            )
            return result
        finally:
            self.sandbox_provider.release(sandbox.sandbox_id)

    def _persist_result(
        self,
        *,
        persistence_session: Session | None,
        result: AgentResult,
        definition_version: str,
        invocation: AgentInvocation,
        persistence_context: dict[str, str] | None,
    ) -> None:
        if persistence_session is None:
            return
        context = persistence_context or {}
        self.persistence_service.persist_result(
            persistence_session,
            result,
            agent_version=definition_version,
            related_skill_definition_id=context.get("related_skill_definition_id", ""),
            related_generation_id=context.get("related_generation_id", ""),
            related_job_id=context.get("related_job_id", ""),
            input_summary=_invocation_summary(invocation),
            model_info={"agent_key": result.agent_key},
        )


def build_agent_harness_service(settings: Settings) -> AgentHarnessService:
    return AgentHarnessService(settings=settings)


def _invocation_summary(invocation: AgentInvocation) -> dict[str, Any]:
    return {
        "agent_key": invocation.agent_key,
        "input_keys": sorted(invocation.input.keys()),
        "context_keys": sorted(invocation.context.keys()),
        "memory_scope": invocation.memory_scope or "",
        "workspace_id": invocation.workspace_id or "",
    }
