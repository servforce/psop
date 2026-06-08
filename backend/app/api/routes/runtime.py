from __future__ import annotations

import mimetypes
import json
import posixpath
import uuid
from datetime import datetime
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, Header, Query, Request, Response, WebSocket, WebSocketDisconnect, status
from fastapi import UploadFile
from sqlalchemy.orm import Session

from app.api.dependencies import (
    get_app_settings,
    get_agent_service,
    get_db_session,
    get_job_query_service,
    get_object_store,
    get_runtime_service,
)
from app.core.config import Settings
from app.compiler.models import ArtifactObject
from app.jobs.schemas import RuntimeJobResponse, RuntimeJobStatsResponse
from app.jobs.service import JobQueryService
from app.runtime.schemas import (
    AppendRunEventRequest,
    BindingRequirementResponse,
    CancelRunRequest,
    CreateInvocationRequest,
    InvocationResponse,
    ReplayDetailResponse,
    ReplayTraceLookupResponse,
    ResolveRunBindingsRequest,
    RunCapabilityBindingResponse,
    RunResponse,
    SessionTokenSnapshotResponse,
    RunEventAppendResponse,
    RunEventPartInput,
    RunEventPartResponse,
    RunEventResponse,
    TerminalSessionDetailResponse,
    RunTraceResponse,
)
from app.runtime.service import RuntimeService
from app.runtime.websocket import (
    TOOL_AUTHORIZATION_WS_CHANNEL,
    run_bindings_ws_message,
    run_event_ws_message,
    run_trace_ws_message,
    run_updated_ws_message,
    run_ws_hub,
    session_token_snapshot_ws_message,
    tool_authorization_ws_hub,
    tool_authorization_ws_message,
)
from app.agents.schemas import AgentToolAuthorizationResponse
from app.agents.service import AgentService
from app.pskills.exceptions import SkillValidationError, SkillsGatewayError
from app.infra.object_store import ObjectStoreService


gateway_router = APIRouter(prefix="/gateway/invocations", tags=["gateway"])
runs_router = APIRouter(prefix="/runs", tags=["runs"])
terminal_router = APIRouter(prefix="/terminal", tags=["terminal"])
replay_router = APIRouter(prefix="/replay", tags=["replay"])
runtime_router = APIRouter(prefix="/runtime", tags=["runtime"])
ws_router = APIRouter(prefix="/ws", tags=["ws"])


@gateway_router.post("", response_model=InvocationResponse, status_code=201)
def create_invocation(
    payload: CreateInvocationRequest,
    session: Session = Depends(get_db_session),
    service: RuntimeService = Depends(get_runtime_service),
) -> InvocationResponse:
    return service.create_invocation(session, payload)


@runtime_router.post("/invocations", response_model=InvocationResponse, status_code=201)
def create_runtime_invocation(
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


@runs_router.post("/{run_id}/cancel", response_model=RunResponse)
async def cancel_run(
    run_id: str,
    payload: CancelRunRequest | None = None,
    session: Session = Depends(get_db_session),
    service: RuntimeService = Depends(get_runtime_service),
    agent_service: AgentService = Depends(get_agent_service),
) -> RunResponse:
    request = payload or CancelRunRequest()
    reason = request.reason.strip() or "cancelled by user"
    previous_run = service.get_run(session, run_id)
    previous_trace_seq = previous_run.latest_trace_seq
    previous_snapshot_seq = previous_run.latest_snapshot_seq
    run = service.cancel_run(session, run_id, reason=reason)
    await _broadcast_run_traces_after(
        run_id,
        previous_trace_seq,
        session=session,
        service=service,
    )
    await _broadcast_snapshots_after(
        run_id,
        previous_snapshot_seq,
        session=session,
        service=service,
    )
    await _broadcast_run_updated(run_id, session=session, service=service)
    cancelled_authorizations = agent_service.cancel_open_tool_authorizations_for_run(session, run_id, reason=reason)
    await _broadcast_cancelled_tool_authorizations(
        session=session,
        runtime_service=service,
        authorizations=cancelled_authorizations,
    )
    return run


@runs_router.get("/{run_id}/snapshots", response_model=list[SessionTokenSnapshotResponse])
def list_snapshots(
    run_id: str,
    session: Session = Depends(get_db_session),
    service: RuntimeService = Depends(get_runtime_service),
) -> list[SessionTokenSnapshotResponse]:
    return service.list_snapshots(session, run_id)


@runs_router.get("/{run_id}/traces", response_model=list[RunTraceResponse])
def list_run_traces(
    run_id: str,
    event_type: str | None = Query(default=None),
    session: Session = Depends(get_db_session),
    service: RuntimeService = Depends(get_runtime_service),
) -> list[RunTraceResponse]:
    return service.list_run_traces(session, run_id, event_type=event_type)


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
async def resolve_run_bindings(
    run_id: str,
    payload: ResolveRunBindingsRequest,
    session: Session = Depends(get_db_session),
    service: RuntimeService = Depends(get_runtime_service),
) -> list[RunCapabilityBindingResponse]:
    previous_trace_seq = service.get_run(session, run_id).latest_trace_seq
    bindings = service.resolve_run_bindings(session, run_id, payload)
    await _broadcast_run_traces_after(
        run_id,
        previous_trace_seq,
        session=session,
        service=service,
    )
    await run_ws_hub.broadcast(run_id, run_bindings_ws_message(run_id, bindings))
    await _broadcast_run_updated(run_id, session=session, service=service)
    return bindings


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


@runs_router.get("/{run_id}/events", response_model=list[RunEventResponse])
@terminal_router.get("/sessions/{run_id}/events", response_model=list[RunEventResponse])
def list_run_events(
    run_id: str,
    from_seq: int | None = Query(default=None),
    to_seq: int | None = Query(default=None),
    session: Session = Depends(get_db_session),
    service: RuntimeService = Depends(get_runtime_service),
) -> list[RunEventResponse]:
    return service.list_run_events(session, run_id, from_seq=from_seq, to_seq=to_seq)


@runs_router.get("/{run_id}/event-parts", response_model=list[RunEventPartResponse])
def list_run_event_parts(
    run_id: str,
    session: Session = Depends(get_db_session),
    service: RuntimeService = Depends(get_runtime_service),
) -> list[RunEventPartResponse]:
    return service.list_run_event_parts(session, run_id)


@runs_router.get("/{run_id}/events/{event_id}/content")
@terminal_router.get("/sessions/{run_id}/events/{event_id}/content")
def get_run_event_content(
    run_id: str,
    event_id: str,
    request: Request,
    session: Session = Depends(get_db_session),
    object_store: ObjectStoreService = Depends(get_object_store),
    service: RuntimeService = Depends(get_runtime_service),
) -> Response:
    event = service.get_run_event(session, run_id, event_id)
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
        mime_type=_run_event_content_mime_type(event, artifact_object),
        filename=_run_event_content_filename(event),
        range_header=request.headers.get("range"),
    )


@runs_router.get("/{run_id}/events/{event_id}/parts/{part_id}/content")
@terminal_router.get("/sessions/{run_id}/events/{event_id}/parts/{part_id}/content")
def get_run_event_part_content(
    run_id: str,
    event_id: str,
    part_id: str,
    request: Request,
    session: Session = Depends(get_db_session),
    object_store: ObjectStoreService = Depends(get_object_store),
    service: RuntimeService = Depends(get_runtime_service),
) -> Response:
    part = service.get_run_event_part(session, run_id, event_id, part_id)
    if not part.artifact_object_id:
        raise SkillValidationError(
            "当前 Terminal Event Part 没有可展示的对象内容。",
            details={"run_id": run_id, "event_id": event_id, "part_id": part_id},
        )
    artifact_object = session.get(ArtifactObject, part.artifact_object_id)
    if not artifact_object:
        raise SkillValidationError(
            "未找到 Terminal Event Part 对应的对象内容。",
            details={"run_id": run_id, "event_id": event_id, "part_id": part_id, "artifact_object_id": part.artifact_object_id},
        )
    try:
        content = object_store.download_bytes(bucket=artifact_object.bucket, object_key=artifact_object.object_key)
    except Exception as exc:
        raise SkillsGatewayError(
            "终端对象内容读取失败，请确认对象存储服务可用。",
            details={"run_id": run_id, "event_id": event_id, "part_id": part_id, "error": str(exc)},
        ) from exc
    return _inline_content_response(
        content=content,
        mime_type=part.mime_type or artifact_object.media_type or "application/octet-stream",
        filename=_terminal_part_content_filename(part.model_dump(mode="json")),
        range_header=request.headers.get("range"),
    )


@runs_router.post("/{run_id}/events", response_model=RunEventAppendResponse, status_code=202)
@terminal_router.post("/sessions/{run_id}/events", response_model=RunEventAppendResponse, status_code=202)
async def append_run_event(
    run_id: str,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_app_settings),
    object_store: ObjectStoreService = Depends(get_object_store),
    service: RuntimeService = Depends(get_runtime_service),
) -> RunEventAppendResponse:
    payload = await _parse_run_event_request(
        run_id=run_id,
        request=request,
        session=session,
        settings=settings,
        object_store=object_store,
    )
    previous_run = service.get_run(session, run_id)
    previous_terminal_seq = previous_run.latest_run_event_seq
    previous_trace_seq = previous_run.latest_trace_seq
    previous_snapshot_seq = previous_run.latest_snapshot_seq
    result = service.append_run_event(session, run_id, payload, idempotency_key=idempotency_key)
    await _broadcast_run_events_after(
        run_id,
        previous_terminal_seq,
        session=session,
        service=service,
    )
    await _broadcast_run_traces_after(
        run_id,
        previous_trace_seq,
        session=session,
        service=service,
    )
    await _broadcast_snapshots_after(
        run_id,
        previous_snapshot_seq,
        session=session,
        service=service,
    )
    await _broadcast_run_updated(run_id, session=session, service=service)
    return result


async def _parse_run_event_request(
    *,
    run_id: str,
    request: Request,
    session: Session,
    settings: Settings,
    object_store: ObjectStoreService,
) -> AppendRunEventRequest:
    content_type = request.headers.get("content-type", "").lower()
    if content_type.startswith("multipart/form-data"):
        return await _parse_multipart_run_event_request(
            run_id=run_id,
            request=request,
            session=session,
            settings=settings,
            object_store=object_store,
        )
    try:
        body = await request.json()
    except json.JSONDecodeError as exc:
        raise SkillValidationError("terminal event JSON 请求体无效。", details={"error": str(exc)}) from exc
    if not isinstance(body, dict):
        raise SkillValidationError("terminal event 请求体必须是对象。")
    if "parts" in body:
        raise SkillValidationError("terminal event 请求不接收客户端 parts；请使用 text 字段和 multipart 文件字段提交。")
    body = _normalize_json_run_event_body(body)
    payload = AppendRunEventRequest.model_validate(body)
    if any(str(part.kind or "").lower() != "text" for part in payload.parts):
        raise SkillValidationError("包含二进制 part 的 terminal event 必须使用 multipart/form-data 提交。")
    return payload


async def _parse_multipart_run_event_request(
    *,
    run_id: str,
    request: Request,
    session: Session,
    settings: Settings,
    object_store: ObjectStoreService,
) -> AppendRunEventRequest:
    form = await request.form()
    raw_event = form.get("event")
    if not isinstance(raw_event, str) or not raw_event.strip():
        raise SkillValidationError("multipart terminal event 必须包含 event JSON 字段。")
    try:
        event_body = json.loads(raw_event)
    except json.JSONDecodeError as exc:
        raise SkillValidationError("multipart terminal event.event 不是有效 JSON。", details={"error": str(exc)}) from exc
    if not isinstance(event_body, dict):
        raise SkillValidationError("multipart terminal event.event 必须是 JSON 对象。")
    if "parts" in event_body:
        raise SkillValidationError("multipart terminal event.event 不接收 parts；请将自然语言放入 text，文件作为表单文件字段提交。")

    uploads: list[tuple[str, UploadFile]] = []
    for key, value in form.multi_items():
        if hasattr(value, "filename") and hasattr(value, "read"):
            uploads.append((str(key), value))  # type: ignore[arg-type]

    parsed_parts: list[RunEventPartInput] = []
    part_counts: dict[str, int] = {}
    event_text = _run_event_text_from_body(event_body)
    if event_text:
        parsed_parts.append(
            RunEventPartInput(
                part_id=_next_terminal_part_id("text", part_counts),
                kind="text",
                mime_type="text/plain",
                text=event_text,
            )
        )

    for field_name, upload in uploads:
        parsed_parts.append(
            await _store_terminal_upload_part(
                run_id=run_id,
                upload=upload,
                field_name=field_name,
                session=session,
                settings=settings,
                object_store=object_store,
                part_counts=part_counts,
            )
        )

    if not parsed_parts:
        raise SkillValidationError("terminal event 必须包含文本或至少一个图片、音频、视频文件。")

    event_body["parts"] = [part.model_dump(mode="json") for part in parsed_parts]
    event_body["text"] = event_text or None
    event_body.setdefault("direction", "input")
    event_body.setdefault("event_kind", "terminal.multimodal.input.v1")
    event_body.setdefault("mime_type", "multipart/mixed")
    event_body.setdefault("payload_inline", _multipart_event_payload(parsed_parts))
    return AppendRunEventRequest.model_validate(event_body)


def _normalize_json_run_event_body(body: dict[str, Any]) -> dict[str, Any]:
    event_body = dict(body)
    event_text = _run_event_text_from_body(event_body)
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


def _run_event_text_from_body(event_body: dict[str, Any]) -> str:
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


def _next_terminal_part_id(kind: str, counts: dict[str, int]) -> str:
    normalized_kind = kind if kind in {"text", "image", "audio", "video"} else "part"
    counts[normalized_kind] = counts.get(normalized_kind, 0) + 1
    return f"{normalized_kind}_{counts[normalized_kind]}"


async def _store_terminal_upload_part(
    *,
    run_id: str,
    upload: UploadFile,
    field_name: str,
    session: Session,
    settings: Settings,
    object_store: ObjectStoreService,
    part_counts: dict[str, int],
) -> RunEventPartInput:
    filename = _safe_terminal_upload_filename(upload.filename or "upload.bin")
    upload_mime_type = upload.content_type or "application/octet-stream"
    kind = _terminal_part_kind_for_mime_type(upload_mime_type)
    if kind not in {"image", "video", "audio"}:
        raise SkillValidationError("多模态文件仅支持 image/audio/video MIME 类型。", details={"mime_type": upload_mime_type})
    content = await upload.read()
    _validate_terminal_upload(settings=settings, filename=filename, content=content, mime_type=upload_mime_type)
    part_id = _next_terminal_part_id(kind, part_counts)
    object_key = posixpath.join("run-event-parts", run_id, f"{uuid.uuid4()}-{filename}")
    try:
        stored = object_store.upload_bytes(
            object_key=object_key,
            content=content,
            media_type=upload_mime_type,
            metadata={"filename": filename, "run_id": run_id, "source": "run_event", "part_id": part_id},
        )
    except Exception as exc:
        raise SkillsGatewayError(
            "终端文件上传到对象存储失败，请确认对象存储服务可用。",
            details={"run_id": run_id, "filename": filename, "part_id": part_id, "error": str(exc)},
        ) from exc
    artifact_object = ArtifactObject(
        bucket=stored.bucket,
        object_key=stored.object_key,
        media_type=stored.media_type,
        size_bytes=stored.size_bytes,
        checksum=stored.checksum,
        content_json={
            "kind": "run_event_part",
            "run_id": run_id,
            "part_id": part_id,
            "filename": filename,
            "metadata": stored.metadata,
        },
    )
    session.add(artifact_object)
    session.flush()
    part_metadata = {
        "filename": filename,
        "name": filename,
        "field_name": field_name,
    }
    return RunEventPartInput(
        part_id=part_id,
        kind=kind,
        mime_type=stored.media_type,
        artifact_object_id=artifact_object.id,
        size_bytes=stored.size_bytes,
        checksum=stored.checksum,
        metadata=part_metadata,
    )

def _multipart_event_payload(parts: list[RunEventPartInput]) -> dict[str, Any]:
    return {
        "summary": "\n".join(
            filter(
                None,
                [
                    part.text or str((part.metadata or {}).get("filename") or "")
                    for part in parts
                ],
            )
        ),
        "part_count": len(parts),
    }


async def _broadcast_run_events_after(
    run_id: str,
    previous_terminal_seq: int,
    *,
    session: Session,
    service: RuntimeService,
) -> None:
    events = service.list_run_events(session, run_id, from_seq=previous_terminal_seq + 1)
    for event in events:
        await run_ws_hub.broadcast(run_id, run_event_ws_message(run_id, event))


async def _broadcast_run_traces_after(
    run_id: str,
    previous_trace_seq: int,
    *,
    session: Session,
    service: RuntimeService,
) -> None:
    traces = service.list_run_traces(session, run_id)
    for trace in traces:
        if trace.seq_no > previous_trace_seq:
            await run_ws_hub.broadcast(run_id, run_trace_ws_message(run_id, trace))


async def _broadcast_snapshots_after(
    run_id: str,
    previous_snapshot_seq: int,
    *,
    session: Session,
    service: RuntimeService,
) -> None:
    snapshots = service.list_snapshots(session, run_id)
    for snapshot in snapshots:
        if snapshot.seq_no > previous_snapshot_seq:
            await run_ws_hub.broadcast(run_id, session_token_snapshot_ws_message(run_id, snapshot))


async def _broadcast_run_updated(
    run_id: str,
    *,
    session: Session,
    service: RuntimeService,
) -> None:
    await run_ws_hub.broadcast(run_id, run_updated_ws_message(run_id, service.get_run(session, run_id)))


async def _broadcast_cancelled_tool_authorizations(
    *,
    session: Session,
    runtime_service: RuntimeService,
    authorizations: list[AgentToolAuthorizationResponse],
) -> None:
    for authorization in authorizations:
        await tool_authorization_ws_hub.broadcast(
            TOOL_AUTHORIZATION_WS_CHANNEL,
            tool_authorization_ws_message(authorization, action="cancelled"),
        )
        run_event = _find_tool_authorization_response_run_event(
            session=session,
            runtime_service=runtime_service,
            authorization=authorization,
        )
        if run_event and authorization.run_id:
            await run_ws_hub.broadcast(authorization.run_id, run_event_ws_message(authorization.run_id, run_event))


def _find_tool_authorization_response_run_event(
    *,
    session: Session,
    runtime_service: RuntimeService,
    authorization: AgentToolAuthorizationResponse,
) -> RunEventResponse | None:
    if not authorization.run_id:
        return None
    events = runtime_service.list_run_events(session, authorization.run_id)
    for event in reversed(events):
        if event.event_kind != "tool_authorization_response":
            continue
        if event.source_ref.get("authorization_id") == authorization.id:
            return event
    return None


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
    if mime_type.startswith(("image/", "audio/", "video/")):
        return True
    return False


def _safe_terminal_upload_filename(filename: str) -> str:
    cleaned = filename.replace("\\", "/").split("/")[-1].strip()
    return cleaned or "upload.bin"


def _terminal_part_kind_for_mime_type(mime_type: str) -> str:
    if mime_type.startswith("image/"):
        return "image"
    if mime_type.startswith("audio/"):
        return "audio"
    if mime_type.startswith("video/"):
        return "video"
    return "text"


def _run_event_content_filename(event: RunEventResponse) -> str:
    payload = event.payload_inline
    if isinstance(payload, dict):
        for key in ("filename", "name", "title", "object_key"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return _safe_terminal_upload_filename(value)
    return f"run-event-{event.seq_no}"


def _terminal_part_content_filename(part: dict[str, Any]) -> str:
    metadata = part.get("metadata") if isinstance(part.get("metadata"), dict) else {}
    for source in (metadata, part):
        for key in ("filename", "name", "title", "object_key", "part_id"):
            value = source.get(key) if isinstance(source, dict) else None
            if isinstance(value, str) and value.strip():
                return _safe_terminal_upload_filename(value)
    return "run-event-part"


def _run_event_content_mime_type(event: RunEventResponse, artifact_object: ArtifactObject) -> str:
    mime_type = artifact_object.media_type or event.mime_type or "application/octet-stream"
    if mime_type != "application/octet-stream":
        return mime_type
    guessed, _ = mimetypes.guess_type(_run_event_content_filename(event))
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


@replay_router.get("/traces/{trace_id}", response_model=ReplayTraceLookupResponse)
def get_replay_trace(
    trace_id: str,
    session: Session = Depends(get_db_session),
    service: RuntimeService = Depends(get_runtime_service),
) -> ReplayTraceLookupResponse:
    return service.build_replay_for_trace(session, trace_id)


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
