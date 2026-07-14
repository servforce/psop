from __future__ import annotations

from collections.abc import Generator

from fastapi import Request
from sqlalchemy.orm import Session

from app.agent_harness.service import AgentHarnessService
from app.agent_harness.persistence.query_service import AgentRunQueryService
from app.core.config import Settings
from app.domain.agent_prompts.service import AgentPromptService
from app.domain.compiler.service import CompilerService
from app.domain.jobs.service import JobQueryService
from app.domain.runtime.events import NoopRuntimeEventSink, RuntimeEventSink
from app.domain.runtime.service import RuntimeService
from app.domain.skill_tests.service import SkillTestService
from app.domain.skills.service import SkillsService
from app.gateway.asr import AsrGateway
from app.gateway.inference import LlmInferenceGateway, OpenAICompatibleInferenceGateway
from app.gateway.gitlab import GitLabSkillSourceGateway
from app.infra.database import DatabaseManager
from app.infra.object_store import ObjectStoreService


def get_app_settings(request: Request) -> Settings:
    return request.app.state.settings  # type: ignore[return-value]


def get_database_manager(request: Request) -> DatabaseManager:
    return request.app.state.db_manager  # type: ignore[return-value]


def get_gitlab_gateway(request: Request) -> GitLabSkillSourceGateway:
    return request.app.state.gitlab_gateway  # type: ignore[return-value]


def get_inference_gateway(request: Request) -> LlmInferenceGateway:
    return request.app.state.inference_gateway  # type: ignore[return-value]


def get_asr_gateway(request: Request) -> AsrGateway:
    return request.app.state.asr_gateway  # type: ignore[return-value]


def get_object_store(request: Request) -> ObjectStoreService:
    return request.app.state.object_store  # type: ignore[return-value]


def get_agent_harness_service(request: Request) -> AgentHarnessService:
    return request.app.state.agent_harness_service  # type: ignore[return-value]


def get_runtime_event_sink(request: Request) -> RuntimeEventSink:
    return getattr(request.app.state, "runtime_event_sink", NoopRuntimeEventSink())  # type: ignore[return-value]


def get_db_session(request: Request) -> Generator[Session, None, None]:
    database_manager: DatabaseManager = get_database_manager(request)
    with database_manager.session() as session:
        yield session


def get_agent_prompt_service(_: Request) -> AgentPromptService:
    return AgentPromptService()


def get_skills_service(request: Request) -> SkillsService:
    compiler_service = get_compiler_service(request)
    return SkillsService(
        settings=get_app_settings(request),
        gitlab_gateway=get_gitlab_gateway(request),
        compiler_service=compiler_service,
        inference_gateway=get_inference_gateway(request),
        asr_gateway=get_asr_gateway(request),
        object_store=get_object_store(request),
        agent_prompt_service=get_agent_prompt_service(request),
        agent_harness_service=get_agent_harness_service(request),
    )


def get_compiler_service(request: Request) -> CompilerService:
    return CompilerService(
        settings=get_app_settings(request),
        gitlab_gateway=get_gitlab_gateway(request),
        inference_gateway=get_inference_gateway(request),
        agent_harness_service=get_agent_harness_service(request),
        object_store=get_object_store(request),
    )


def get_runtime_service(request: Request) -> RuntimeService:
    return RuntimeService(
        settings=get_app_settings(request),
        inference_gateway=get_inference_gateway(request),
        object_store=get_object_store(request),
        agent_harness_service=get_agent_harness_service(request),
        runtime_event_sink=get_runtime_event_sink(request),
    )


def get_job_query_service(_: Request) -> JobQueryService:
    return JobQueryService()


def get_agent_run_query_service(_: Request) -> AgentRunQueryService:
    return AgentRunQueryService()


def get_skill_test_service(request: Request) -> SkillTestService:
    return SkillTestService(
        settings=get_app_settings(request),
        inference_gateway=get_inference_gateway(request),
        object_store=get_object_store(request),
        agent_harness_service=get_agent_harness_service(request),
    )
