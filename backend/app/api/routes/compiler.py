from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.dependencies import (
    get_agent_service,
    get_app_settings,
    get_compiler_service,
    get_database_manager,
    get_db_session,
    get_gitlab_gateway,
    get_inference_gateway,
)
from app.agents.schemas import AgentEventResponse
from app.agents.service import AgentService
from app.core.config import Settings
from app.compiler.schemas import (
    CompileArtifactResponse,
    CompileArtifactUpdateRequest,
    CompileArtifactValidationResponse,
    CompileDiagnosticResponse,
    CompileRequestResponse,
    PublishProgressResponse,
)
from app.compiler.service import CompilerService
from app.gateway.inference import LlmInferenceGateway
from app.gateway.gitlab import GitLabSkillSourceGateway
from app.infra.database import DatabaseManager


router = APIRouter(prefix="/compiler", tags=["compiler"])


@router.get("/requests", response_model=list[CompileRequestResponse])
def list_compile_requests(
    pskill_id: str | None = Query(default=None),
    skill_id: str | None = Query(default=None, deprecated=True),
    status: str | None = Query(default=None),
    session: Session = Depends(get_db_session),
    service: CompilerService = Depends(get_compiler_service),
) -> list[CompileRequestResponse]:
    return service.list_compile_requests(session, pskill_id=pskill_id or skill_id, status=status)


@router.post("/pskills/{pskill_id}/compile", response_model=CompileRequestResponse, status_code=status.HTTP_202_ACCEPTED)
def create_pskill_compile_request(
    pskill_id: str,
    session: Session = Depends(get_db_session),
    service: CompilerService = Depends(get_compiler_service),
) -> CompileRequestResponse:
    return service.create_manual_compile_request_for_pskill(session, pskill_id=pskill_id)


@router.get("/requests/{compile_request_id}", response_model=CompileRequestResponse)
def get_compile_request(
    compile_request_id: str,
    session: Session = Depends(get_db_session),
    service: CompilerService = Depends(get_compiler_service),
) -> CompileRequestResponse:
    return service.get_compile_request(session, compile_request_id)


@router.post("/requests/{compile_request_id}/retry", response_model=CompileRequestResponse)
def retry_compile_request(
    compile_request_id: str,
    session: Session = Depends(get_db_session),
    service: CompilerService = Depends(get_compiler_service),
) -> CompileRequestResponse:
    service.process_compile_job_for_request(session, compile_request_id)
    return service.get_compile_request(session, compile_request_id)


@router.get("/requests/{compile_request_id}/progress", response_model=PublishProgressResponse)
def get_compile_progress(
    compile_request_id: str,
    session: Session = Depends(get_db_session),
    service: CompilerService = Depends(get_compiler_service),
) -> PublishProgressResponse:
    return service.get_compile_progress(session, compile_request_id)


@router.get("/requests/{compile_request_id}/agent-events", response_model=list[AgentEventResponse])
def list_compile_agent_events(
    compile_request_id: str,
    session: Session = Depends(get_db_session),
    compiler_service: CompilerService = Depends(get_compiler_service),
    agent_service: AgentService = Depends(get_agent_service),
) -> list[AgentEventResponse]:
    compile_request = compiler_service.get_compile_request(session, compile_request_id)
    if not compile_request.agent_run_id:
        return []
    return agent_service.list_events(session, compile_request.agent_run_id)


@router.get("/requests/{compile_request_id}/events")
async def stream_compile_events(
    compile_request_id: str,
    request: Request,
    settings: Settings = Depends(get_app_settings),
    database_manager: DatabaseManager = Depends(get_database_manager),
    gitlab_gateway: GitLabSkillSourceGateway = Depends(get_gitlab_gateway),
    inference_gateway: LlmInferenceGateway = Depends(get_inference_gateway),
) -> StreamingResponse:
    async def event_generator():
        last_payload = ""
        while True:
            if await request.is_disconnected():
                break

            with database_manager.session() as session:
                service = CompilerService(
                    settings=settings,
                    gitlab_gateway=gitlab_gateway,
                    inference_gateway=inference_gateway,
                )
                progress = service.get_compile_progress(session, compile_request_id)

            payload = progress.model_dump(mode="json")
            encoded = json.dumps(payload, ensure_ascii=False)
            if encoded != last_payload:
                event_name = "publish.terminal" if progress.terminal else "publish.progress"
                yield f"event: {event_name}\ndata: {encoded}\n\n"
                last_payload = encoded
            if progress.terminal:
                break
            await asyncio.sleep(1)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/requests/{compile_request_id}/diagnostics", response_model=list[CompileDiagnosticResponse])
def list_compile_diagnostics(
    compile_request_id: str,
    session: Session = Depends(get_db_session),
    service: CompilerService = Depends(get_compiler_service),
) -> list[CompileDiagnosticResponse]:
    return service.list_diagnostics(session, compile_request_id)


@router.get("/artifacts/{compile_artifact_id}", response_model=CompileArtifactResponse)
def get_compile_artifact(
    compile_artifact_id: str,
    session: Session = Depends(get_db_session),
    service: CompilerService = Depends(get_compiler_service),
) -> CompileArtifactResponse:
    return service.get_artifact(session, compile_artifact_id)


@router.post("/artifacts/{compile_artifact_id}/validate", response_model=CompileArtifactValidationResponse)
def validate_compile_artifact(
    compile_artifact_id: str,
    session: Session = Depends(get_db_session),
    service: CompilerService = Depends(get_compiler_service),
) -> CompileArtifactValidationResponse:
    return service.validate_artifact(session, compile_artifact_id)


@router.put("/artifacts/{compile_artifact_id}", response_model=CompileArtifactResponse)
def update_compile_artifact(
    compile_artifact_id: str,
    request: CompileArtifactUpdateRequest,
    session: Session = Depends(get_db_session),
    service: CompilerService = Depends(get_compiler_service),
) -> CompileArtifactResponse:
    return service.update_artifact(session, compile_artifact_id, request)
