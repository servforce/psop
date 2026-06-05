from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.agents.schemas import (
    AgentDefinitionDetailResponse,
    AgentDefinitionSummaryResponse,
    AgentEventResponse,
    AgentRunResponse,
    AgentToolAuthorizationResponse,
    AgentVersionSummaryResponse,
    AppendAgentEventRequest,
    CreateAgentRunRequest,
    CreateToolAuthorizationRequest,
    ToolAuthorizationDecisionRequest,
)
from app.agents.service import AgentService
from app.api.dependencies import get_agent_service, get_db_session


agents_router = APIRouter(tags=["agents"])
agent_runs_router = APIRouter(prefix="/agent-runs", tags=["agent-runs"])
tool_authorizations_router = APIRouter(prefix="/tool-authorizations", tags=["tool-authorizations"])


@agents_router.get("/agents", response_model=list[AgentDefinitionSummaryResponse])
def list_agents(
    session: Session = Depends(get_db_session),
    service: AgentService = Depends(get_agent_service),
) -> list[AgentDefinitionSummaryResponse]:
    return service.list_definitions(session)


@agents_router.get("/agents/{agent_key}", response_model=AgentDefinitionDetailResponse)
def get_agent(
    agent_key: str,
    session: Session = Depends(get_db_session),
    service: AgentService = Depends(get_agent_service),
) -> AgentDefinitionDetailResponse:
    return service.get_definition(session, agent_key)


@agents_router.get("/agents/{agent_key}/versions", response_model=list[AgentVersionSummaryResponse])
def list_agent_versions(
    agent_key: str,
    session: Session = Depends(get_db_session),
    service: AgentService = Depends(get_agent_service),
) -> list[AgentVersionSummaryResponse]:
    return service.list_versions(session, agent_key)


@agent_runs_router.post("", response_model=AgentRunResponse, status_code=status.HTTP_201_CREATED)
def create_agent_run(
    payload: CreateAgentRunRequest,
    session: Session = Depends(get_db_session),
    service: AgentService = Depends(get_agent_service),
) -> AgentRunResponse:
    return service.create_run(session, payload)


@agent_runs_router.get("", response_model=list[AgentRunResponse])
def list_agent_runs(
    agent_key: str | None = Query(default=None),
    status: str | None = Query(default=None),
    owner_type: str | None = Query(default=None),
    owner_id: str | None = Query(default=None),
    session: Session = Depends(get_db_session),
    service: AgentService = Depends(get_agent_service),
) -> list[AgentRunResponse]:
    return service.list_runs(
        session,
        agent_key=agent_key,
        status=status,
        owner_type=owner_type,
        owner_id=owner_id,
    )


@agent_runs_router.get("/{agent_run_id}", response_model=AgentRunResponse)
def get_agent_run(
    agent_run_id: str,
    session: Session = Depends(get_db_session),
    service: AgentService = Depends(get_agent_service),
) -> AgentRunResponse:
    return service.get_run(session, agent_run_id)


@agent_runs_router.get("/{agent_run_id}/events", response_model=list[AgentEventResponse])
def list_agent_run_events(
    agent_run_id: str,
    session: Session = Depends(get_db_session),
    service: AgentService = Depends(get_agent_service),
) -> list[AgentEventResponse]:
    return service.list_events(session, agent_run_id)


@agent_runs_router.post("/{agent_run_id}/events", response_model=AgentEventResponse, status_code=status.HTTP_201_CREATED)
def append_agent_run_event(
    agent_run_id: str,
    payload: AppendAgentEventRequest,
    session: Session = Depends(get_db_session),
    service: AgentService = Depends(get_agent_service),
) -> AgentEventResponse:
    return service.append_event(session, agent_run_id, payload)


@agent_runs_router.get("/{agent_run_id}/tool-authorizations", response_model=list[AgentToolAuthorizationResponse])
def list_agent_run_tool_authorizations(
    agent_run_id: str,
    session: Session = Depends(get_db_session),
    service: AgentService = Depends(get_agent_service),
) -> list[AgentToolAuthorizationResponse]:
    return service.list_tool_authorizations(session, agent_run_id=agent_run_id)


@tool_authorizations_router.post(
    "",
    response_model=AgentToolAuthorizationResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_tool_authorization(
    payload: CreateToolAuthorizationRequest,
    session: Session = Depends(get_db_session),
    service: AgentService = Depends(get_agent_service),
) -> AgentToolAuthorizationResponse:
    return service.create_tool_authorization(session, payload)


@tool_authorizations_router.get("", response_model=list[AgentToolAuthorizationResponse])
def list_tool_authorizations(
    status: str | None = Query(default=None),
    session: Session = Depends(get_db_session),
    service: AgentService = Depends(get_agent_service),
) -> list[AgentToolAuthorizationResponse]:
    return service.list_tool_authorizations(session, status=status)


@tool_authorizations_router.get("/{authorization_id}", response_model=AgentToolAuthorizationResponse)
def get_tool_authorization(
    authorization_id: str,
    session: Session = Depends(get_db_session),
    service: AgentService = Depends(get_agent_service),
) -> AgentToolAuthorizationResponse:
    return service.get_tool_authorization(session, authorization_id)


@tool_authorizations_router.post("/{authorization_id}/approve", response_model=AgentToolAuthorizationResponse)
def approve_tool_authorization(
    authorization_id: str,
    payload: ToolAuthorizationDecisionRequest | None = None,
    session: Session = Depends(get_db_session),
    service: AgentService = Depends(get_agent_service),
) -> AgentToolAuthorizationResponse:
    return service.approve_tool_authorization(session, authorization_id, payload or ToolAuthorizationDecisionRequest())


@tool_authorizations_router.post("/{authorization_id}/reject", response_model=AgentToolAuthorizationResponse)
def reject_tool_authorization(
    authorization_id: str,
    payload: ToolAuthorizationDecisionRequest | None = None,
    session: Session = Depends(get_db_session),
    service: AgentService = Depends(get_agent_service),
) -> AgentToolAuthorizationResponse:
    return service.reject_tool_authorization(session, authorization_id, payload or ToolAuthorizationDecisionRequest())
