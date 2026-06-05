from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.api.dependencies import get_agent_prompt_service, get_db_session
from app.agent_prompts.schemas import (
    AgentPromptActivateRequest,
    AgentPromptBindingResponse,
    AgentPromptBindingUpdateRequest,
    AgentPromptCreateRequest,
    AgentPromptDefinitionDetailResponse,
    AgentPromptDefinitionSummaryResponse,
    AgentPromptValidationResponse,
    AgentPromptVersionCreateRequest,
    AgentPromptVersionDetailResponse,
    AgentPromptVersionFilesUpdateRequest,
)
from app.agent_prompts.service import AgentPromptService


router = APIRouter(tags=["agent-prompts"])


@router.get("/agent-prompts", response_model=list[AgentPromptDefinitionSummaryResponse])
def list_agent_prompts(
    session: Session = Depends(get_db_session),
    service: AgentPromptService = Depends(get_agent_prompt_service),
) -> list[AgentPromptDefinitionSummaryResponse]:
    return service.list_definitions(session)


@router.post("/agent-prompts", response_model=AgentPromptDefinitionDetailResponse, status_code=status.HTTP_201_CREATED)
def create_agent_prompt(
    payload: AgentPromptCreateRequest,
    session: Session = Depends(get_db_session),
    service: AgentPromptService = Depends(get_agent_prompt_service),
) -> AgentPromptDefinitionDetailResponse:
    return service.create_definition(session, payload)


@router.get("/agent-prompts/{definition_id}", response_model=AgentPromptDefinitionDetailResponse)
def get_agent_prompt(
    definition_id: str,
    version_id: str | None = Query(default=None),
    session: Session = Depends(get_db_session),
    service: AgentPromptService = Depends(get_agent_prompt_service),
) -> AgentPromptDefinitionDetailResponse:
    return service.get_definition(session, definition_id, selected_version_id=version_id)


@router.post("/agent-prompts/{definition_id}/versions", response_model=AgentPromptDefinitionDetailResponse, status_code=201)
def create_agent_prompt_version(
    definition_id: str,
    payload: AgentPromptVersionCreateRequest,
    session: Session = Depends(get_db_session),
    service: AgentPromptService = Depends(get_agent_prompt_service),
) -> AgentPromptDefinitionDetailResponse:
    return service.create_version(session, definition_id, payload)


@router.put("/agent-prompts/{definition_id}/versions/{version_id}/files", response_model=AgentPromptVersionDetailResponse)
def update_agent_prompt_version_files(
    definition_id: str,
    version_id: str,
    payload: AgentPromptVersionFilesUpdateRequest,
    session: Session = Depends(get_db_session),
    service: AgentPromptService = Depends(get_agent_prompt_service),
) -> AgentPromptVersionDetailResponse:
    return service.update_version_files(session, definition_id, version_id, payload)


@router.post("/agent-prompts/{definition_id}/versions/{version_id}/validate", response_model=AgentPromptValidationResponse)
def validate_agent_prompt_version(
    definition_id: str,
    version_id: str,
    session: Session = Depends(get_db_session),
    service: AgentPromptService = Depends(get_agent_prompt_service),
) -> AgentPromptValidationResponse:
    return service.validate_version(session, definition_id, version_id)


@router.post("/agent-prompts/{definition_id}/versions/{version_id}/publish", response_model=AgentPromptVersionDetailResponse)
def publish_agent_prompt_version(
    definition_id: str,
    version_id: str,
    session: Session = Depends(get_db_session),
    service: AgentPromptService = Depends(get_agent_prompt_service),
) -> AgentPromptVersionDetailResponse:
    return service.publish_version(session, definition_id, version_id)


@router.post("/agent-prompts/{definition_id}/versions/{version_id}/activate", response_model=AgentPromptDefinitionDetailResponse)
def activate_agent_prompt_version(
    definition_id: str,
    version_id: str,
    payload: AgentPromptActivateRequest | None = None,
    session: Session = Depends(get_db_session),
    service: AgentPromptService = Depends(get_agent_prompt_service),
) -> AgentPromptDefinitionDetailResponse:
    return service.activate_version(session, definition_id, version_id, payload or AgentPromptActivateRequest())


@router.get("/agent-prompt-bindings", response_model=list[AgentPromptBindingResponse])
def list_agent_prompt_bindings(
    session: Session = Depends(get_db_session),
    service: AgentPromptService = Depends(get_agent_prompt_service),
) -> list[AgentPromptBindingResponse]:
    return service.list_bindings(session)


@router.put("/agent-prompt-bindings/{usage_key}", response_model=AgentPromptBindingResponse)
def update_agent_prompt_binding(
    usage_key: str,
    payload: AgentPromptBindingUpdateRequest,
    session: Session = Depends(get_db_session),
    service: AgentPromptService = Depends(get_agent_prompt_service),
) -> AgentPromptBindingResponse:
    return service.update_binding(session, usage_key, payload)

