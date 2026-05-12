from __future__ import annotations

import posixpath
import uuid

from fastapi import APIRouter, Depends, Header, Query, WebSocket, WebSocketDisconnect
from fastapi import File, Form, UploadFile
from sqlalchemy.orm import Session

from app.api.dependencies import get_app_settings, get_db_session, get_object_store, get_runtime_service
from app.core.config import Settings
from app.domain.compiler.models import ArtifactObject
from app.domain.jobs.schemas import RuntimeJobResponse
from app.domain.runtime.schemas import (
    AppendTerminalEventRequest,
    BindingRequirementResponse,
    CreateInvocationRequest,
    InvocationResponse,
    ReplayDetailResponse,
    ResolveRunBindingsRequest,
    RunCapabilityBindingResponse,
    RunResponse,
    SessionTokenSnapshotResponse,
    TerminalEventAppendResponse,
    TerminalEventResponse,
    TerminalSessionDetailResponse,
    TraceEventResponse,
)
from app.domain.runtime.service import RuntimeService
from app.domain.skills.exceptions import SkillValidationError, SkillsGatewayError
from app.infra.object_store import ObjectStoreService


gateway_router = APIRouter(prefix="/gateway/invocations", tags=["gateway"])
runs_router = APIRouter(prefix="/runs", tags=["runs"])
terminal_router = APIRouter(prefix="/terminal", tags=["terminal"])
replay_router = APIRouter(prefix="/replay", tags=["replay"])
runtime_router = APIRouter(prefix="/runtime", tags=["runtime"])
ws_router = APIRouter(prefix="/ws", tags=["ws"])


class RunWebSocketHub:
    def __init__(self) -> None:
        self._connections: dict[str, set[WebSocket]] = {}

    async def connect(self, run_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections.setdefault(run_id, set()).add(websocket)

    def disconnect(self, run_id: str, websocket: WebSocket) -> None:
        connections = self._connections.get(run_id)
        if not connections:
            return
        connections.discard(websocket)
        if not connections:
            self._connections.pop(run_id, None)

    async def broadcast(self, run_id: str, event: dict) -> None:
        connections = list(self._connections.get(run_id, set()))
        for websocket in connections:
            try:
                await websocket.send_json(event)
            except RuntimeError:
                self.disconnect(run_id, websocket)


run_ws_hub = RunWebSocketHub()


@gateway_router.post("", response_model=InvocationResponse, status_code=201)
def create_invocation(
    payload: CreateInvocationRequest,
    session: Session = Depends(get_db_session),
    service: RuntimeService = Depends(get_runtime_service),
) -> InvocationResponse:
    return service.create_invocation(session, payload)


@gateway_router.get("", response_model=list[InvocationResponse])
def list_invocations(
    skill_key: str | None = Query(default=None),
    status: str | None = Query(default=None),
    session: Session = Depends(get_db_session),
    service: RuntimeService = Depends(get_runtime_service),
) -> list[InvocationResponse]:
    return service.list_invocations(session, skill_key=skill_key, status=status)


@gateway_router.get("/{invocation_id}", response_model=InvocationResponse)
def get_invocation(
    invocation_id: str,
    session: Session = Depends(get_db_session),
    service: RuntimeService = Depends(get_runtime_service),
) -> InvocationResponse:
    return service.get_invocation(session, invocation_id)


@runs_router.get("", response_model=list[RunResponse])
def list_runs(
    status: str | None = Query(default=None),
    skill_id: str | None = Query(default=None),
    session: Session = Depends(get_db_session),
    service: RuntimeService = Depends(get_runtime_service),
) -> list[RunResponse]:
    return service.list_runs(session, status=status, skill_id=skill_id)


@runs_router.get("/{run_id}", response_model=RunResponse)
def get_run(
    run_id: str,
    session: Session = Depends(get_db_session),
    service: RuntimeService = Depends(get_runtime_service),
) -> RunResponse:
    return service.get_run(session, run_id)


@runs_router.get("/{run_id}/snapshots", response_model=list[SessionTokenSnapshotResponse])
def list_snapshots(
    run_id: str,
    session: Session = Depends(get_db_session),
    service: RuntimeService = Depends(get_runtime_service),
) -> list[SessionTokenSnapshotResponse]:
    return service.list_snapshots(session, run_id)


@runs_router.get("/{run_id}/trace-events", response_model=list[TraceEventResponse])
def list_trace_events(
    run_id: str,
    event_type: str | None = Query(default=None),
    session: Session = Depends(get_db_session),
    service: RuntimeService = Depends(get_runtime_service),
) -> list[TraceEventResponse]:
    return service.list_trace_events(session, run_id, event_type=event_type)


@runs_router.get("/{run_id}/binding-requirements", response_model=list[BindingRequirementResponse])
def list_binding_requirements(
    run_id: str,
    session: Session = Depends(get_db_session),
    service: RuntimeService = Depends(get_runtime_service),
) -> list[BindingRequirementResponse]:
    return service.list_binding_requirements(session, run_id)


@runs_router.get("/{run_id}/bindings", response_model=list[RunCapabilityBindingResponse])
def list_run_bindings(
    run_id: str,
    session: Session = Depends(get_db_session),
    service: RuntimeService = Depends(get_runtime_service),
) -> list[RunCapabilityBindingResponse]:
    return service.list_run_bindings(session, run_id)


@runs_router.post("/{run_id}/bindings/resolve", response_model=list[RunCapabilityBindingResponse])
def resolve_run_bindings(
    run_id: str,
    payload: ResolveRunBindingsRequest,
    session: Session = Depends(get_db_session),
    service: RuntimeService = Depends(get_runtime_service),
) -> list[RunCapabilityBindingResponse]:
    return service.resolve_run_bindings(session, run_id, payload)


@runs_router.get("/{run_id}/bindings/{binding_id}", response_model=RunCapabilityBindingResponse)
def get_run_binding(
    run_id: str,
    binding_id: str,
    session: Session = Depends(get_db_session),
    service: RuntimeService = Depends(get_runtime_service),
) -> RunCapabilityBindingResponse:
    return service.get_run_binding(session, run_id, binding_id)


@terminal_router.get("/sessions/{run_id}", response_model=TerminalSessionDetailResponse)
def get_terminal_session(
    run_id: str,
    session: Session = Depends(get_db_session),
    service: RuntimeService = Depends(get_runtime_service),
) -> TerminalSessionDetailResponse:
    return service.get_terminal_session(session, run_id)


@terminal_router.get("/sessions/{run_id}/events", response_model=list[TerminalEventResponse])
def list_terminal_events(
    run_id: str,
    from_seq: int | None = Query(default=None),
    to_seq: int | None = Query(default=None),
    session: Session = Depends(get_db_session),
    service: RuntimeService = Depends(get_runtime_service),
) -> list[TerminalEventResponse]:
    return service.list_terminal_events(session, run_id, from_seq=from_seq, to_seq=to_seq)


@terminal_router.post("/sessions/{run_id}/events", response_model=TerminalEventAppendResponse, status_code=202)
async def append_terminal_event(
    run_id: str,
    payload: AppendTerminalEventRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: Session = Depends(get_db_session),
    service: RuntimeService = Depends(get_runtime_service),
) -> TerminalEventAppendResponse:
    result = service.append_terminal_event(session, run_id, payload, idempotency_key=idempotency_key)
    await run_ws_hub.broadcast(
        run_id,
        {
            "event_type": "terminal.event.appended",
            "run_id": run_id,
            "invocation_id": None,
            "seq_no": result.seq_no,
            "occurred_at": result.event.occurred_at.isoformat(),
            "payload": result.event.model_dump(mode="json"),
        },
    )
    return result


@terminal_router.post("/sessions/{run_id}/files", response_model=TerminalEventAppendResponse, status_code=202)
async def upload_terminal_file(
    run_id: str,
    file: UploadFile = File(...),
    caption: str | None = Form(default=None),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_app_settings),
    object_store: ObjectStoreService = Depends(get_object_store),
    service: RuntimeService = Depends(get_runtime_service),
) -> TerminalEventAppendResponse:
    terminal_session = service.get_terminal_session(session, run_id).terminal_session
    if terminal_session.status != "open":
        raise SkillValidationError("当前 Terminal Session 已关闭，不能上传文件。", details={"run_id": run_id})

    filename = _safe_terminal_upload_filename(file.filename or "upload.bin")
    mime_type = file.content_type or "application/octet-stream"
    content = await file.read()
    _validate_terminal_upload(settings=settings, filename=filename, content=content, mime_type=mime_type)

    object_key = posixpath.join("terminal-uploads", run_id, f"{uuid.uuid4()}-{filename}")
    try:
        stored = object_store.upload_bytes(
            object_key=object_key,
            content=content,
            media_type=mime_type,
            metadata={
                "filename": filename,
                "run_id": run_id,
                "source": "terminal",
            },
        )
    except Exception as exc:
        raise SkillsGatewayError(
            "终端文件上传到对象存储失败，请确认对象存储服务可用。",
            details={"run_id": run_id, "filename": filename, "error": str(exc)},
        ) from exc
    event_payload = {
        "filename": filename,
        "name": filename,
        "description": caption or "",
        "caption": caption or "",
        "size_bytes": stored.size_bytes,
        "checksum": stored.checksum,
        "object_key": stored.object_key,
    }
    artifact_object = ArtifactObject(
        bucket=stored.bucket,
        object_key=stored.object_key,
        media_type=stored.media_type,
        size_bytes=stored.size_bytes,
        checksum=stored.checksum,
        content_json={
            "kind": "terminal_upload",
            "run_id": run_id,
            "filename": filename,
            "caption": caption or "",
            "metadata": stored.metadata,
        },
    )
    session.add(artifact_object)
    session.flush()
    result = service.append_terminal_event(
        session,
        run_id,
        AppendTerminalEventRequest(
            direction="input",
            event_kind=_terminal_upload_event_kind(stored.media_type),
            mime_type=stored.media_type,
            payload_inline=event_payload,
            artifact_object_id=artifact_object.id,
            external_event_id=idempotency_key or f"terminal-upload:{run_id}:{uuid.uuid4()}",
        ),
    )
    await run_ws_hub.broadcast(
        run_id,
        {
            "event_type": "terminal.event.appended",
            "run_id": run_id,
            "invocation_id": None,
            "seq_no": result.seq_no,
            "occurred_at": result.event.occurred_at.isoformat(),
            "payload": result.event.model_dump(mode="json"),
        },
    )
    return result


def _validate_terminal_upload(*, settings: Settings, filename: str, content: bytes, mime_type: str) -> None:
    if not filename:
        raise SkillValidationError("上传文件名不能为空。")
    if not content:
        raise SkillValidationError("上传文件不能为空。")
    if len(content) > settings.test_data_max_upload_bytes:
        raise SkillValidationError("上传文件过大。", details={"max_bytes": settings.test_data_max_upload_bytes})
    if not _is_allowed_terminal_upload_mime_type(mime_type):
        raise SkillValidationError("不支持的终端输入 MIME 类型。", details={"mime_type": mime_type})


def _is_allowed_terminal_upload_mime_type(mime_type: str) -> bool:
    if mime_type.startswith(("text/", "image/", "audio/", "video/")):
        return True
    return mime_type in {"application/json", "application/pdf", "application/octet-stream"}


def _safe_terminal_upload_filename(filename: str) -> str:
    cleaned = filename.replace("\\", "/").split("/")[-1].strip()
    return cleaned or "upload.bin"


def _terminal_upload_event_kind(mime_type: str) -> str:
    if mime_type.startswith("image/"):
        return "terminal.image.input.v1"
    if mime_type.startswith("audio/"):
        return "terminal.audio.input.v1"
    if mime_type.startswith("video/"):
        return "terminal.video.input.v1"
    return "terminal.file.input.v1"


@replay_router.get("/runs", response_model=list[RunResponse])
def list_replay_runs(
    skill_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
    session: Session = Depends(get_db_session),
    service: RuntimeService = Depends(get_runtime_service),
) -> list[RunResponse]:
    return service.list_runs(session, skill_id=skill_id, status=status)


@replay_router.get("/runs/{run_id}", response_model=ReplayDetailResponse)
def get_replay(
    run_id: str,
    session: Session = Depends(get_db_session),
    service: RuntimeService = Depends(get_runtime_service),
) -> ReplayDetailResponse:
    return service.build_replay(session, run_id)


@runtime_router.get("/jobs", response_model=list[RuntimeJobResponse])
def list_runtime_jobs(
    status: str | None = Query(default=None),
    job_type: str | None = Query(default=None),
    session: Session = Depends(get_db_session),
    service: RuntimeService = Depends(get_runtime_service),
) -> list[RuntimeJobResponse]:
    return service.list_runtime_jobs(session, status=status, job_type=job_type)


@ws_router.websocket("/runs/{run_id}")
async def run_events_websocket(websocket: WebSocket, run_id: str) -> None:
    await run_ws_hub.connect(run_id, websocket)
    try:
        await websocket.send_json(
            {
                "event_type": "ws.connected",
                "run_id": run_id,
                "invocation_id": None,
                "seq_no": 0,
                "occurred_at": None,
                "payload": {"message": "connected"},
            }
        )
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        run_ws_hub.disconnect(run_id, websocket)
