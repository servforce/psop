from __future__ import annotations

import mimetypes
import posixpath
import uuid
from datetime import datetime
from urllib.parse import quote

from fastapi import APIRouter, Depends, Header, Query, Request, Response, WebSocket, WebSocketDisconnect, status
from fastapi import File, Form, UploadFile
from sqlalchemy.orm import Session

from app.api.dependencies import (
    get_app_settings,
    get_db_session,
    get_job_query_service,
    get_object_store,
    get_runtime_service,
)
from app.core.config import Settings
from app.domain.compiler.models import ArtifactObject
from app.domain.jobs.schemas import RuntimeJobResponse, RuntimeJobStatsResponse
from app.domain.jobs.service import JobQueryService
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


@terminal_router.get("/sessions/{run_id}/events/{event_id}/content")
def get_terminal_event_content(
    run_id: str,
    event_id: str,
    request: Request,
    session: Session = Depends(get_db_session),
    object_store: ObjectStoreService = Depends(get_object_store),
    service: RuntimeService = Depends(get_runtime_service),
) -> Response:
    event = service.get_terminal_event(session, run_id, event_id)
    if not event.artifact_object_id:
        raise SkillValidationError("当前 Terminal Event 没有可展示的对象内容。", details={"run_id": run_id, "event_id": event_id})
    artifact_object = session.get(ArtifactObject, event.artifact_object_id)
    if not artifact_object:
        raise SkillValidationError(
            "未找到 Terminal Event 对应的对象内容。",
            details={"run_id": run_id, "event_id": event_id, "artifact_object_id": event.artifact_object_id},
        )
    try:
        content = object_store.download_bytes(bucket=artifact_object.bucket, object_key=artifact_object.object_key)
    except Exception as exc:
        raise SkillsGatewayError(
            "终端对象内容读取失败，请确认对象存储服务可用。",
            details={"run_id": run_id, "event_id": event_id, "error": str(exc)},
        ) from exc
    return _inline_content_response(
        content=content,
        mime_type=_terminal_event_content_mime_type(event, artifact_object),
        filename=_terminal_event_content_filename(event),
        range_header=request.headers.get("range"),
    )


@terminal_router.post("/sessions/{run_id}/events", response_model=TerminalEventAppendResponse, status_code=202)
async def append_terminal_event(
    run_id: str,
    payload: AppendTerminalEventRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: Session = Depends(get_db_session),
    service: RuntimeService = Depends(get_runtime_service),
) -> TerminalEventAppendResponse:
    previous_terminal_seq = service.get_run(session, run_id).latest_terminal_seq
    result = service.append_terminal_event(session, run_id, payload, idempotency_key=idempotency_key)
    await _broadcast_terminal_events_after(
        run_id,
        previous_terminal_seq,
        session=session,
        service=service,
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
    previous_terminal_seq = service.get_run(session, run_id).latest_terminal_seq
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
    await _broadcast_terminal_events_after(
        run_id,
        previous_terminal_seq,
        session=session,
        service=service,
    )
    return result


async def _broadcast_terminal_events_after(
    run_id: str,
    previous_terminal_seq: int,
    *,
    session: Session,
    service: RuntimeService,
) -> None:
    events = service.list_terminal_events(session, run_id, from_seq=previous_terminal_seq + 1)
    for event in events:
        await run_ws_hub.broadcast(run_id, _terminal_event_ws_message(run_id, event))


def _terminal_event_ws_message(run_id: str, event: TerminalEventResponse) -> dict:
    return {
        "event_type": "terminal.event.appended",
        "run_id": run_id,
        "invocation_id": None,
        "seq_no": event.seq_no,
        "occurred_at": event.occurred_at.isoformat(),
        "payload": event.model_dump(mode="json"),
    }


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


def _terminal_event_content_filename(event: TerminalEventResponse) -> str:
    payload = event.payload_inline
    if isinstance(payload, dict):
        for key in ("filename", "name", "title", "object_key"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return _safe_terminal_upload_filename(value)
    return f"terminal-event-{event.seq_no}"


def _terminal_event_content_mime_type(event: TerminalEventResponse, artifact_object: ArtifactObject) -> str:
    mime_type = artifact_object.media_type or event.mime_type or "application/octet-stream"
    if mime_type != "application/octet-stream":
        return mime_type
    guessed, _ = mimetypes.guess_type(_terminal_event_content_filename(event))
    return guessed or mime_type


def _inline_content_response(
    *,
    content: bytes,
    mime_type: str,
    filename: str,
    range_header: str | None,
) -> Response:
    encoded_filename = quote(filename)
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Disposition": f"inline; filename*=UTF-8''{encoded_filename}",
    }
    size = len(content)
    if not range_header:
        headers["Content-Length"] = str(size)
        return Response(content=content, media_type=mime_type, headers=headers)

    byte_range = _parse_single_byte_range(range_header, size)
    if byte_range is None:
        return Response(
            status_code=status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE,
            headers={**headers, "Content-Range": f"bytes */{size}"},
        )

    start, end = byte_range
    partial = content[start : end + 1]
    headers.update(
        {
            "Content-Range": f"bytes {start}-{end}/{size}",
            "Content-Length": str(len(partial)),
        }
    )
    return Response(
        content=partial,
        media_type=mime_type,
        headers=headers,
        status_code=status.HTTP_206_PARTIAL_CONTENT,
    )


def _parse_single_byte_range(range_header: str, size: int) -> tuple[int, int] | None:
    if size <= 0:
        return None
    unit, separator, spec = range_header.partition("=")
    if separator != "=" or unit.strip().lower() != "bytes":
        return None
    spec = spec.strip()
    if not spec or "," in spec or "-" not in spec:
        return None
    start_text, end_text = [part.strip() for part in spec.split("-", 1)]
    if not start_text:
        if not end_text.isdigit():
            return None
        suffix_length = int(end_text)
        if suffix_length <= 0:
            return None
        return max(size - suffix_length, 0), size - 1
    if not start_text.isdigit() or (end_text and not end_text.isdigit()):
        return None
    start = int(start_text)
    end = int(end_text) if end_text else size - 1
    if start >= size or end < start:
        return None
    return start, min(end, size - 1)


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


@runtime_router.get("/jobs/stats", response_model=RuntimeJobStatsResponse)
def get_runtime_job_stats(
    window_hours: int = Query(default=24, ge=1, le=720),
    session: Session = Depends(get_db_session),
    service: JobQueryService = Depends(get_job_query_service),
) -> RuntimeJobStatsResponse:
    return service.get_runtime_job_stats(session, window_hours=window_hours)


@runtime_router.get("/jobs", response_model=list[RuntimeJobResponse])
def list_runtime_jobs(
    status: str | None = Query(default=None),
    job_type: str | None = Query(default=None),
    q: str | None = Query(default=None),
    created_from: datetime | None = Query(default=None),
    created_to: datetime | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_db_session),
    service: JobQueryService = Depends(get_job_query_service),
) -> list[RuntimeJobResponse]:
    return service.list_runtime_jobs(
        session,
        status=status,
        job_type=job_type,
        q=q,
        created_from=created_from,
        created_to=created_to,
        limit=limit,
        offset=offset,
    )


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
