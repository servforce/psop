from __future__ import annotations

import mimetypes
import json
import logging
from datetime import datetime
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, Header, Query, Request, Response, WebSocket, WebSocketDisconnect, status
from fastapi import UploadFile
from fastapi.responses import StreamingResponse
from pydantic import ValidationError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.dependencies import (
    get_app_settings,
    get_db_session,
    get_job_query_service,
    get_object_store,
    get_runtime_service,
)
from app.core.config import Settings
from app.core.observability import add_metric_counter
from app.domain.compiler.models import ArtifactObject
from app.domain.jobs.schemas import RuntimeJobResponse, RuntimeJobStatsResponse
from app.domain.jobs.service import JobQueryService
from app.domain.runtime.ingest import (
    TerminalRequestBodyTooLargeError,
    TerminalEventIngestService,
    enforce_terminal_request_size,
    raise_object_store_error,
    run_object_store_io,
    safe_terminal_upload_filename,
)
from app.domain.runtime.media import (
    TerminalContentDescriptor,
    etag_matches,
    is_single_byte_range_syntax,
    parse_single_byte_range,
)
from app.domain.runtime.schemas import (
    AppendTerminalEventRequest,
    BindingRequirementResponse,
    CreateInvocationRequest,
    InvocationResponse,
    ReplayDetailResponse,
    ResolveRunBindingsRequest,
    RunCapabilityBindingResponse,
    RunResponse,
    RunTaskStatusResponse,
    SessionTokenSnapshotResponse,
    TerminalEventAppendResponse,
    TerminalEventPartInput,
    TerminalEventResponse,
    TerminalSessionDetailResponse,
    TraceEventResponse,
)
from app.domain.runtime.service import RuntimeService
from app.domain.skills.exceptions import PayloadTooLargeError, SkillsGatewayError, SkillValidationError
from app.infra.object_store import ObjectDownload, ObjectStoreService


LOGGER = logging.getLogger(__name__)


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
            except Exception:
                LOGGER.warning("run websocket send failed; disconnecting client", extra={"run_id": run_id})
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


@runs_router.get("/{run_id}/task-status", response_model=RunTaskStatusResponse)
def get_run_task_status(
    run_id: str,
    session: Session = Depends(get_db_session),
    service: RuntimeService = Depends(get_runtime_service),
) -> RunTaskStatusResponse:
    return service.get_run_task_status(session, run_id)


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
async def get_terminal_event_content(
    run_id: str,
    event_id: str,
    request: Request,
    object_store: ObjectStoreService = Depends(get_object_store),
    service: RuntimeService = Depends(get_runtime_service),
) -> Response:
    descriptor = await run_in_threadpool(
        _load_terminal_event_content_descriptor,
        request.app.state.db_manager,
        service,
        run_id,
        event_id,
    )
    return await _stream_terminal_content_response(
        request=request,
        descriptor=descriptor,
        object_store=object_store,
        error_details={"run_id": run_id, "event_id": event_id},
    )


@terminal_router.get("/sessions/{run_id}/events/{event_id}/parts/{part_id}/content")
async def get_terminal_event_part_content(
    run_id: str,
    event_id: str,
    part_id: str,
    request: Request,
    object_store: ObjectStoreService = Depends(get_object_store),
    service: RuntimeService = Depends(get_runtime_service),
) -> Response:
    descriptor = await run_in_threadpool(
        _load_terminal_event_part_content_descriptor,
        request.app.state.db_manager,
        service,
        run_id,
        event_id,
        part_id,
    )
    return await _stream_terminal_content_response(
        request=request,
        descriptor=descriptor,
        object_store=object_store,
        error_details={"run_id": run_id, "event_id": event_id, "part_id": part_id},
    )


@terminal_router.post("/sessions/{run_id}/events", response_model=TerminalEventAppendResponse, status_code=202)
async def append_terminal_event(
    run_id: str,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    settings: Settings = Depends(get_app_settings),
    object_store: ObjectStoreService = Depends(get_object_store),
    service: RuntimeService = Depends(get_runtime_service),
) -> TerminalEventAppendResponse:
    uploads: list[tuple[str, UploadFile]] = []
    admission = None
    admitted = False
    try:
        if request.headers.get("content-type", "").lower().startswith("multipart/form-data"):
            admission = getattr(request.app.state, "terminal_upload_admission", None)
            if admission is not None:
                await admission.acquire()
                admitted = True
        payload, uploads = await _parse_terminal_event_request(
            request=request,
            settings=settings,
        )
        ingest = TerminalEventIngestService(
            settings=settings,
            database_manager=request.app.state.db_manager,
            object_store=object_store,
            runtime_service=service,
        )
        return await ingest.append(
            run_id=run_id,
            payload=payload,
            uploads=uploads,
            idempotency_key=idempotency_key,
        )
    finally:
        for _, upload in uploads:
            await upload.close()
        if admitted:
            admission.release()


async def _parse_terminal_event_request(
    *,
    request: Request,
    settings: Settings,
) -> tuple[AppendTerminalEventRequest, list[tuple[str, UploadFile]]]:
    content_type = request.headers.get("content-type", "").lower()
    if content_type.startswith("multipart/form-data"):
        return await _parse_multipart_terminal_event_request(
            request=request,
            settings=settings,
        )
    try:
        body = await request.json()
    except json.JSONDecodeError as exc:
        raise SkillValidationError("terminal event JSON 请求体无效。", details={"error": str(exc)}) from exc
    if not isinstance(body, dict):
        raise SkillValidationError("terminal event 请求体必须是对象。")
    body = _normalize_json_terminal_event_body(body)
    payload = _validate_terminal_event_payload(body)
    if any(str(part.kind or "").lower() != "text" for part in payload.parts):
        raise SkillValidationError("包含二进制 part 的 terminal event 必须使用 multipart/form-data 提交。")
    return payload, []


async def _parse_multipart_terminal_event_request(
    *,
    request: Request,
    settings: Settings,
) -> tuple[AppendTerminalEventRequest, list[tuple[str, UploadFile]]]:
    enforce_terminal_request_size(request, max_bytes=settings.terminal_event_max_request_bytes)
    try:
        form = await request.form(
            max_files=settings.terminal_event_max_upload_files,
            max_fields=4,
        )
    except TerminalRequestBodyTooLargeError as exc:
        raise PayloadTooLargeError(
            "terminal event 请求体过大。",
            details={"max_bytes": exc.max_bytes, "size_bytes": exc.size_bytes},
        ) from exc
    except StarletteHTTPException as exc:
        if exc.status_code == 400 and "Too many files" in str(exc.detail):
            raise PayloadTooLargeError(
                "terminal event 上传文件数量过多。",
                details={
                    "max_files": settings.terminal_event_max_upload_files,
                    "file_count": settings.terminal_event_max_upload_files + 1,
                },
            ) from exc
        if exc.status_code == 400 and "Too many fields" in str(exc.detail):
            raise SkillValidationError("multipart terminal event 表单字段过多。") from exc
        raise
    try:
        raw_event = form.get("event")
        if not isinstance(raw_event, str) or not raw_event.strip():
            raise SkillValidationError("multipart terminal event 必须包含 event JSON 字段。")
        try:
            event_body = json.loads(raw_event)
        except json.JSONDecodeError as exc:
            raise SkillValidationError(
                "multipart terminal event.event 不是有效 JSON。",
                details={"error": str(exc)},
            ) from exc
        if not isinstance(event_body, dict):
            raise SkillValidationError("multipart terminal event.event 必须是 JSON 对象。")
        if "parts" in event_body:
            raise SkillValidationError(
                "multipart terminal event.event 不接收 parts；请将自然语言放入 text，文件作为表单文件字段提交。"
            )

        uploads: list[tuple[str, UploadFile]] = []
        for key, value in form.multi_items():
            if hasattr(value, "filename") and hasattr(value, "read"):
                uploads.append((str(key), value))  # type: ignore[arg-type]

        parsed_parts: list[TerminalEventPartInput] = []
        event_text = _terminal_event_text_from_body(event_body)
        if event_text:
            parsed_parts.append(
                TerminalEventPartInput(
                    part_id="text_1",
                    kind="text",
                    mime_type="text/plain",
                    text=event_text,
                )
            )
        if not parsed_parts and not uploads:
            raise SkillValidationError("terminal event 必须包含文本或至少一个图片、音频、视频文件。")

        event_body["parts"] = [part.model_dump(mode="json") for part in parsed_parts]
        event_body["text"] = event_text or None
        event_body.setdefault("direction", "input")
        event_body.setdefault("event_kind", "terminal.multimodal.input.v1")
        event_body.setdefault("mime_type", "multipart/mixed")
        event_body.setdefault(
            "payload_inline",
            {
                "summary": "\n".join(
                    filter(
                        None,
                        [
                            event_text,
                            *[
                                safe_terminal_upload_filename(upload.filename or "upload.bin")
                                for _, upload in uploads
                            ],
                        ],
                    )
                ),
                "part_count": len(parsed_parts) + len(uploads),
            },
        )
        return _validate_terminal_event_payload(event_body), uploads
    except BaseException:
        await form.close()
        raise


def _normalize_json_terminal_event_body(body: dict[str, Any]) -> dict[str, Any]:
    event_body = dict(body)
    event_text = _terminal_event_text_from_body(event_body)
    if event_text:
        event_body["text"] = event_text
        event_body.setdefault("payload_inline", event_text)
    if not event_body.get("parts") and event_text:
        event_body["parts"] = [
            {
                "kind": "text",
                "mime_type": "text/plain",
                "text": event_text,
            }
        ]
    event_body.setdefault("direction", "input")
    event_body.setdefault("event_kind", "terminal.multimodal.input.v1")
    event_body.setdefault("mime_type", "multipart/mixed")
    return event_body


def _validate_terminal_event_payload(body: dict[str, Any]) -> AppendTerminalEventRequest:
    try:
        return AppendTerminalEventRequest.model_validate(body)
    except ValidationError as exc:
        errors = [
            {
                "type": item.get("type"),
                "loc": list(item.get("loc") or ()),
                "msg": item.get("msg"),
            }
            for item in exc.errors(include_url=False, include_context=False, include_input=False)
        ]
        raise SkillValidationError(
            "terminal event 请求字段校验失败。",
            details={"errors": errors},
        ) from exc


def _terminal_event_text_from_body(event_body: dict[str, Any]) -> str:
    text = event_body.get("text")
    if isinstance(text, str) and text.strip():
        return text.strip()
    payload_inline = event_body.get("payload_inline")
    if isinstance(payload_inline, str) and payload_inline.strip():
        return payload_inline.strip()
    if isinstance(payload_inline, dict):
        for key in ("user_input", "text", "value", "content", "summary"):
            value = payload_inline.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def _terminal_event_content_filename(event: TerminalEventResponse) -> str:
    payload = event.payload_inline
    if isinstance(payload, dict):
        for key in ("filename", "name", "title", "object_key"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return safe_terminal_upload_filename(value)
    return f"terminal-event-{event.seq_no}"


def _terminal_part_content_filename(part: dict[str, Any]) -> str:
    metadata = part.get("metadata") if isinstance(part.get("metadata"), dict) else {}
    for source in (metadata, part):
        for key in ("filename", "name", "title", "object_key", "part_id"):
            value = source.get(key) if isinstance(source, dict) else None
            if isinstance(value, str) and value.strip():
                return safe_terminal_upload_filename(value)
    return "terminal-event-part"


def _terminal_event_content_mime_type(event: TerminalEventResponse, artifact_object: ArtifactObject) -> str:
    mime_type = artifact_object.media_type or event.mime_type or "application/octet-stream"
    if mime_type != "application/octet-stream":
        return mime_type
    guessed, _ = mimetypes.guess_type(_terminal_event_content_filename(event))
    return guessed or mime_type


def _load_terminal_event_content_descriptor(
    database_manager,
    service: RuntimeService,
    run_id: str,
    event_id: str,
) -> TerminalContentDescriptor:
    with database_manager.session() as session:
        event = service.get_terminal_event(session, run_id, event_id)
        if not event.artifact_object_id:
            raise SkillValidationError(
                "当前 Terminal Event 没有可展示的对象内容。",
                details={"run_id": run_id, "event_id": event_id},
            )
        artifact_object = session.get(ArtifactObject, event.artifact_object_id)
        if not artifact_object:
            raise SkillValidationError(
                "未找到 Terminal Event 对应的对象内容。",
                details={
                    "run_id": run_id,
                    "event_id": event_id,
                    "artifact_object_id": event.artifact_object_id,
                },
            )
        return TerminalContentDescriptor(
            artifact_object_id=artifact_object.id,
            bucket=artifact_object.bucket,
            object_key=artifact_object.object_key,
            mime_type=_terminal_event_content_mime_type(event, artifact_object),
            filename=_terminal_event_content_filename(event),
            size_bytes=artifact_object.size_bytes,
            checksum=artifact_object.checksum,
        )


def _load_terminal_event_part_content_descriptor(
    database_manager,
    service: RuntimeService,
    run_id: str,
    event_id: str,
    part_id: str,
) -> TerminalContentDescriptor:
    with database_manager.session() as session:
        part = service.get_terminal_event_part(session, run_id, event_id, part_id)
        if not part.artifact_object_id:
            raise SkillValidationError(
                "当前 Terminal Event Part 没有可展示的对象内容。",
                details={"run_id": run_id, "event_id": event_id, "part_id": part_id},
            )
        artifact_object = session.get(ArtifactObject, part.artifact_object_id)
        if not artifact_object:
            raise SkillValidationError(
                "未找到 Terminal Event Part 对应的对象内容。",
                details={
                    "run_id": run_id,
                    "event_id": event_id,
                    "part_id": part_id,
                    "artifact_object_id": part.artifact_object_id,
                },
            )
        return TerminalContentDescriptor(
            artifact_object_id=artifact_object.id,
            bucket=artifact_object.bucket,
            object_key=artifact_object.object_key,
            mime_type=part.mime_type or artifact_object.media_type or "application/octet-stream",
            filename=_terminal_part_content_filename(part.model_dump(mode="json")),
            size_bytes=artifact_object.size_bytes,
            checksum=artifact_object.checksum,
        )


async def _stream_terminal_content_response(
    *,
    request: Request,
    descriptor: TerminalContentDescriptor,
    object_store: ObjectStoreService,
    error_details: dict[str, object],
) -> Response:
    encoded_filename = quote(descriptor.filename)
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Disposition": f"inline; filename*=UTF-8''{encoded_filename}",
        "Cache-Control": "private, max-age=86400, immutable",
        "ETag": descriptor.etag,
    }
    if etag_matches(request.headers.get("if-none-match"), descriptor.etag):
        _record_terminal_content_response(status.HTTP_304_NOT_MODIFIED)
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)

    range_header = request.headers.get("range")
    if range_header and request.headers.get("if-range"):
        # If-Range requires a strong entity-tag match; weak tags and dates
        # deliberately fall back to a complete 200 response.
        if request.headers.get("if-range", "").strip() != descriptor.etag:
            range_header = None
    if range_header and not is_single_byte_range_syntax(range_header):
        _record_terminal_content_response(status.HTTP_416_RANGE_NOT_SATISFIABLE)
        return Response(
            status_code=status.HTTP_416_RANGE_NOT_SATISFIABLE,
            headers={**headers, "Content-Range": f"bytes */{descriptor.size_bytes}"},
        )

    size = descriptor.size_bytes
    if size <= 0:
        stat_object = getattr(object_store, "stat_object", None)
        if not callable(stat_object):
            raise SkillValidationError(
                "终端对象缺少有效的内容长度。",
                details={**error_details, "artifact_object_id": descriptor.artifact_object_id},
            )
        try:
            object_stat = await run_object_store_io(
                object_store,
                stat_object,
                bucket=descriptor.bucket,
                object_key=descriptor.object_key,
            )
        except Exception as exc:
            raise_object_store_error(
                exc,
                message="终端对象内容读取失败，请确认对象存储服务可用。",
                details=error_details,
            )
        size = object_stat.size_bytes

    byte_range = parse_single_byte_range(range_header, size) if range_header else None
    if range_header and byte_range is None:
        _record_terminal_content_response(status.HTTP_416_RANGE_NOT_SATISFIABLE)
        return Response(
            status_code=status.HTTP_416_RANGE_NOT_SATISFIABLE,
            headers={**headers, "Content-Range": f"bytes */{size}"},
        )

    try:
        open_download = getattr(object_store, "open_download")
        download = await run_object_store_io(
            object_store,
            open_download,
            bucket=descriptor.bucket,
            object_key=descriptor.object_key,
            byte_range=byte_range,
        )
    except Exception as exc:
        raise_object_store_error(
            exc,
            message="终端对象内容读取失败，请确认对象存储服务可用。",
            details=error_details,
        )

    response_status = status.HTTP_200_OK
    content_length = size
    if byte_range is not None:
        start, end = byte_range
        response_status = status.HTTP_206_PARTIAL_CONTENT
        content_length = end - start + 1
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        expected_content_range = headers["Content-Range"]
        if download.content_range != expected_content_range:
            await run_object_store_io(object_store, download.close)
            raise SkillsGatewayError(
                "终端对象存储未返回请求的字节范围。",
                details={**error_details, "artifact_object_id": descriptor.artifact_object_id},
            )
    if download.size_bytes != content_length:
        await run_object_store_io(object_store, download.close)
        raise SkillsGatewayError(
            "终端对象存储返回的内容长度与对象描述不一致。",
            details={**error_details, "artifact_object_id": descriptor.artifact_object_id},
        )
    headers["Content-Length"] = str(content_length)
    _record_terminal_content_response(response_status, requested_bytes=content_length)
    return StreamingResponse(
        _iterate_object_download(object_store, download, expected_bytes=content_length),
        status_code=response_status,
        media_type=descriptor.mime_type,
        headers=headers,
    )


async def _iterate_object_download(
    object_store: ObjectStoreService,
    download: ObjectDownload,
    *,
    expected_bytes: int,
):
    remaining = expected_bytes
    try:
        while remaining > 0:
            chunk = await run_object_store_io(object_store, download.read, min(256 * 1024, remaining))
            if not chunk:
                raise IOError("terminal object stream ended before Content-Length bytes were read")
            if len(chunk) > remaining:
                chunk = chunk[:remaining]
            add_metric_counter(
                "psop.terminal.content.s3.bytes",
                len(chunk),
                unit="By",
                description="Bytes read from S3 for terminal content responses",
            )
            yield chunk
            remaining -= len(chunk)
    finally:
        await run_object_store_io(object_store, download.close)


def _record_terminal_content_response(response_status: int, *, requested_bytes: int = 0) -> None:
    add_metric_counter(
        "psop.terminal.content.requests",
        attributes={"http.response.status_code": response_status},
        description="Terminal content responses by bounded HTTP status",
    )
    if requested_bytes > 0:
        add_metric_counter(
            "psop.terminal.content.requested.bytes",
            requested_bytes,
            unit="By",
            description="Bytes selected by successful terminal content requests",
        )


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
