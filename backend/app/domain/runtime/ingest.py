from __future__ import annotations

import asyncio
import hashlib
import logging
import posixpath
import socket
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import partial
from typing import Any, BinaryIO, Callable, TypeVar

from fastapi import Request, UploadFile
from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from starlette.concurrency import run_in_threadpool

from app.core.config import Settings
from app.core.observability import add_metric_counter, record_metric_histogram
from app.domain.compiler.models import ArtifactObject
from app.domain.runtime.schemas import (
    AppendTerminalEventRequest,
    TerminalEventAppendResponse,
    TerminalEventPartInput,
)
from app.domain.runtime.service import RuntimeService
from app.domain.skills.exceptions import (
    PayloadTooLargeError,
    SkillNotFoundError,
    SkillsError,
    SkillsGatewayError,
    SkillsGatewayTimeoutError,
    SkillValidationError,
)
from app.infra.database import DatabaseManager
from app.infra.object_store import ObjectStoreService, StoredObject


LOGGER = logging.getLogger(__name__)
UPLOAD_CHUNK_SIZE = 256 * 1024
_T = TypeVar("_T")
_FALLBACK_OBJECT_IO_EXECUTOR = ThreadPoolExecutor(max_workers=8, thread_name_prefix="psop-object-store-fallback")


class TerminalRequestBodyTooLargeError(OSError):
    """Internal parser error that makes Starlette close multipart temp files."""

    def __init__(self, *, max_bytes: int, size_bytes: int) -> None:
        super().__init__("terminal event request body exceeded its configured limit")
        self.max_bytes = max_bytes
        self.size_bytes = size_bytes


class ObjectStoreIOCancelledError(asyncio.CancelledError):
    def __init__(self, result: Any = None) -> None:
        super().__init__("object-store I/O completed after request cancellation")
        self.result = result


@dataclass(frozen=True)
class InspectedUpload:
    field_name: str
    upload: UploadFile
    filename: str
    mime_type: str
    kind: str
    part_id: str
    size_bytes: int
    checksum: str


@dataclass(frozen=True)
class UploadedTerminalPart:
    inspected: InspectedUpload
    stored: StoredObject


def enforce_terminal_request_size(request: Request, *, max_bytes: int) -> None:
    """Reject oversized multipart bodies, including requests without Content-Length."""

    content_length = request.headers.get("content-length")
    if content_length:
        try:
            declared_size = int(content_length)
        except ValueError:
            declared_size = 0
        if declared_size > max_bytes:
            raise PayloadTooLargeError(
                "terminal event 请求体过大。",
                details={"max_bytes": max_bytes, "size_bytes": declared_size},
            )

    original_receive = request._receive  # noqa: SLF001 - route-local streaming limit
    received_bytes = 0

    async def limited_receive() -> dict[str, Any]:
        nonlocal received_bytes
        message = await original_receive()
        if message.get("type") == "http.request":
            received_bytes += len(message.get("body") or b"")
            if received_bytes > max_bytes:
                # MultiPartParser catches OSError and closes every temporary file
                # it has already created before re-raising this exception.
                raise TerminalRequestBodyTooLargeError(
                    max_bytes=max_bytes,
                    size_bytes=received_bytes,
                )
        return message

    request._receive = limited_receive  # type: ignore[method-assign]  # noqa: SLF001


async def run_object_store_io(
    object_store: ObjectStoreService,
    operation: Callable[..., _T],
    /,
    *args: Any,
    **kwargs: Any,
) -> _T:
    runner = getattr(object_store, "run_io", None)
    if callable(runner):
        task = asyncio.create_task(runner(operation, *args, **kwargs))
    else:
        loop = asyncio.get_running_loop()
        task = asyncio.ensure_future(
            loop.run_in_executor(
                _FALLBACK_OBJECT_IO_EXECUTOR,
                partial(operation, *args, **kwargs),
            )
        )
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        # Cancelling an asyncio Future does not stop its blocking thread. Wait
        # for the operation before request cleanup closes the spooled file/body.
        result = None
        try:
            result = await task
        except Exception:
            pass
        close_result = getattr(result, "close", None)
        if callable(close_result):
            if callable(runner):
                close_task = asyncio.create_task(runner(close_result))
            else:
                close_task = asyncio.ensure_future(
                    loop.run_in_executor(_FALLBACK_OBJECT_IO_EXECUTOR, close_result)
                )
            try:
                await asyncio.shield(close_task)
            except Exception:
                LOGGER.exception("failed to close object-store result after cancellation")
        raise ObjectStoreIOCancelledError(result) from None


def call_object_store_io(
    object_store: ObjectStoreService,
    operation: Callable[..., _T],
    /,
    *args: Any,
    **kwargs: Any,
) -> _T:
    runner = getattr(object_store, "call_io", None)
    if callable(runner):
        return runner(operation, *args, **kwargs)
    return _FALLBACK_OBJECT_IO_EXECUTOR.submit(operation, *args, **kwargs).result()


class TerminalEventIngestService:
    """Persist a terminal event without holding a DB connection during S3 I/O."""

    def __init__(
        self,
        *,
        settings: Settings,
        database_manager: DatabaseManager,
        object_store: ObjectStoreService,
        runtime_service: RuntimeService,
    ) -> None:
        self.settings = settings
        self.database_manager = database_manager
        self.object_store = object_store
        self.runtime_service = runtime_service

    async def append(
        self,
        *,
        run_id: str,
        payload: AppendTerminalEventRequest,
        uploads: list[tuple[str, UploadFile]],
        idempotency_key: str | None,
    ) -> TerminalEventAppendResponse:
        started_at = time.monotonic()
        outcome = "error"
        try:
            result = await self._append(
                run_id=run_id,
                payload=payload,
                uploads=uploads,
                idempotency_key=idempotency_key,
            )
            outcome = "success"
            return result
        finally:
            record_metric_histogram(
                "psop.terminal.upload.duration",
                max(0.0, time.monotonic() - started_at),
                attributes={"outcome": outcome},
                unit="s",
                description="Terminal event upload ingest duration",
            )

    async def _append(
        self,
        *,
        run_id: str,
        payload: AppendTerminalEventRequest,
        uploads: list[tuple[str, UploadFile]],
        idempotency_key: str | None,
    ) -> TerminalEventAppendResponse:
        self._validate_upload_count(uploads)
        external_event_id = payload.external_event_id or idempotency_key
        existing = await run_in_threadpool(self._preflight, run_id, external_event_id)
        if existing is not None:
            return existing
        if not external_event_id:
            LOGGER.warning("terminal event missing external_event_id", extra={"run_id": run_id})

        inspected = await self._inspect_uploads(payload=payload, uploads=uploads)
        uploaded: list[UploadedTerminalPart] = []
        try:
            for item in inspected:
                stored = await self._upload(run_id=run_id, item=item)
                uploaded.append(UploadedTerminalPart(inspected=item, stored=stored))
                add_metric_counter(
                    "psop.terminal.object.bytes",
                    stored.size_bytes,
                    attributes={"media_kind": item.kind},
                    unit="By",
                    description="Bytes uploaded into terminal event objects",
                )
            try:
                result, duplicate = await run_in_threadpool(
                    self._persist,
                    run_id,
                    payload,
                    uploaded,
                    idempotency_key,
                )
            except IntegrityError:
                result = await run_in_threadpool(self._load_duplicate, run_id, external_event_id)
                if result is None:
                    raise
                duplicate = True
            if duplicate:
                await self._cleanup(uploaded)
            return result
        except BaseException:
            await self._cleanup(uploaded)
            raise

    def _preflight(
        self,
        run_id: str,
        external_event_id: str | None,
    ) -> TerminalEventAppendResponse | None:
        with self.database_manager.session() as session:
            run = self.runtime_service.repository.get_run(session, run_id)
            if not run:
                raise SkillNotFoundError("未找到 Run。", details={"run_id": run_id})
            if external_event_id:
                existing = self.runtime_service.repository.get_terminal_event_by_external_id(
                    session,
                    run_id=run_id,
                    external_event_id=external_event_id,
                )
                if existing:
                    return self._response_for_existing(session, run_id, existing.id)
            self._validate_run_accepts_input(session, run_id, run.status)
        return None

    async def _inspect_uploads(
        self,
        *,
        payload: AppendTerminalEventRequest,
        uploads: list[tuple[str, UploadFile]],
    ) -> list[InspectedUpload]:
        counts: dict[str, int] = {}
        for part in payload.parts:
            kind = str(part.kind or "").lower()
            counts[kind] = counts.get(kind, 0) + 1

        inspected: list[InspectedUpload] = []
        total_size = 0
        for field_name, upload in uploads:
            filename = safe_terminal_upload_filename(upload.filename or "upload.bin")
            mime_type = (upload.content_type or "application/octet-stream").split(";", 1)[0].strip().lower()
            kind = terminal_part_kind_for_mime_type(mime_type)
            if kind not in {"image", "audio", "video"}:
                raise SkillValidationError(
                    "多模态文件仅支持 image/audio/video MIME 类型。",
                    details={"mime_type": mime_type},
                )
            counts[kind] = counts.get(kind, 0) + 1
            part_id = f"{kind}_{counts[kind]}"
            size_bytes, checksum = await run_object_store_io(
                self.object_store,
                self._inspect_stream,
                upload.file,
                self.settings.terminal_event_max_file_bytes,
                self.settings.terminal_event_max_total_file_bytes,
                total_size,
            )
            if size_bytes == 0:
                raise SkillValidationError("上传文件不能为空。", details={"filename": filename})
            total_size += size_bytes
            add_metric_counter(
                "psop.terminal.upload.bytes",
                size_bytes,
                attributes={"media_kind": kind},
                unit="By",
                description="Bytes accepted from terminal event uploads",
            )
            inspected.append(
                InspectedUpload(
                    field_name=field_name,
                    upload=upload,
                    filename=filename,
                    mime_type=mime_type,
                    kind=kind,
                    part_id=part_id,
                    size_bytes=size_bytes,
                    checksum=checksum,
                )
            )
        return inspected

    def _validate_upload_count(self, uploads: list[tuple[str, UploadFile]]) -> None:
        if len(uploads) > self.settings.terminal_event_max_upload_files:
            raise PayloadTooLargeError(
                "terminal event 上传文件数量过多。",
                details={
                    "max_files": self.settings.terminal_event_max_upload_files,
                    "file_count": len(uploads),
                },
            )

    async def _upload(self, *, run_id: str, item: InspectedUpload) -> StoredObject:
        object_key = posixpath.join(
            "terminal-event-parts",
            run_id,
            f"{uuid.uuid4()}-{item.filename}",
        )
        metadata = {
            "filename": item.filename,
            "run_id": run_id,
            "source": "terminal_event",
            "part_id": item.part_id,
        }
        try:
            upload_stream = getattr(self.object_store, "upload_stream", None)
            if callable(upload_stream):
                return await run_object_store_io(
                    self.object_store,
                    upload_stream,
                    object_key=object_key,
                    stream=item.upload.file,
                    media_type=item.mime_type,
                    size_bytes=item.size_bytes,
                    checksum=item.checksum,
                    metadata=metadata,
                )

            def upload_bytes_compat() -> StoredObject:
                item.upload.file.seek(0)
                return self.object_store.upload_bytes(
                    object_key=object_key,
                    content=item.upload.file.read(),
                    media_type=item.mime_type,
                    metadata=metadata,
                )
            return await run_object_store_io(self.object_store, upload_bytes_compat)
        except ObjectStoreIOCancelledError as exc:
            # The blocking upload has finished before run_object_store_io
            # re-raises cancellation. Remove the UUID key whether it completed
            # or failed partially so cancellation cannot leave an orphan.
            await self._cleanup_object_key(
                bucket=getattr(exc.result, "bucket", self.settings.object_store_bucket),
                object_key=object_key,
                run_id=run_id,
            )
            raise
        except SkillsError:
            await self._cleanup_object_key(
                bucket=self.settings.object_store_bucket,
                object_key=object_key,
                run_id=run_id,
            )
            raise
        except Exception as exc:
            await self._cleanup_object_key(
                bucket=self.settings.object_store_bucket,
                object_key=object_key,
                run_id=run_id,
            )
            raise_object_store_error(
                exc,
                message="终端文件上传到对象存储失败，请确认对象存储服务可用。",
                details={"run_id": run_id, "filename": item.filename, "part_id": item.part_id},
            )

    def _persist(
        self,
        run_id: str,
        payload: AppendTerminalEventRequest,
        uploaded: list[UploadedTerminalPart],
        idempotency_key: str | None,
    ) -> tuple[TerminalEventAppendResponse, bool]:
        external_event_id = payload.external_event_id or idempotency_key
        with self.database_manager.session() as session:
            try:
                self.runtime_service.job_repository.get_runtime_job_by_dedupe_key_for_update(
                    session,
                    f"job:runtime:{run_id}",
                )
                run = self.runtime_service.repository.get_run_for_update(session, run_id)
                if not run:
                    raise SkillNotFoundError("未找到 Run。", details={"run_id": run_id})
                if external_event_id:
                    existing = self.runtime_service.repository.get_terminal_event_by_external_id(
                        session,
                        run_id=run_id,
                        external_event_id=external_event_id,
                    )
                    if existing:
                        return self._response_for_existing(session, run_id, existing.id), True
                self._validate_run_accepts_input(session, run_id, run.status)

                parts = list(payload.parts)
                for item in uploaded:
                    stored = item.stored
                    inspected = item.inspected
                    artifact_object = ArtifactObject(
                        bucket=stored.bucket,
                        object_key=stored.object_key,
                        media_type=stored.media_type,
                        size_bytes=stored.size_bytes,
                        checksum=stored.checksum,
                        content_json={
                            "kind": "terminal_event_part",
                            "run_id": run_id,
                            "part_id": inspected.part_id,
                            "filename": inspected.filename,
                            "metadata": stored.metadata,
                        },
                    )
                    session.add(artifact_object)
                    session.flush()
                    parts.append(
                        TerminalEventPartInput(
                            part_id=inspected.part_id,
                            kind=inspected.kind,
                            mime_type=stored.media_type,
                            artifact_object_id=artifact_object.id,
                            size_bytes=stored.size_bytes,
                            checksum=stored.checksum,
                            metadata={
                                "filename": inspected.filename,
                                "name": inspected.filename,
                                "field_name": inspected.field_name,
                            },
                        )
                    )
                persisted_payload = payload.model_copy(update={"parts": parts})
                result = self.runtime_service.append_terminal_event(
                    session,
                    run_id,
                    persisted_payload,
                    idempotency_key=idempotency_key,
                )
                uploaded_artifact_ids = {
                    part.artifact_object_id
                    for part in parts
                    if part.artifact_object_id
                }
                persisted_artifact_ids = {
                    part.artifact_object_id
                    for part in result.event.parts
                    if part.artifact_object_id
                }
                duplicate = bool(uploaded_artifact_ids) and not uploaded_artifact_ids.issubset(
                    persisted_artifact_ids
                )
                return result, duplicate
            except BaseException:
                session.rollback()
                raise

    def _load_duplicate(
        self,
        run_id: str,
        external_event_id: str | None,
    ) -> TerminalEventAppendResponse | None:
        if not external_event_id:
            return None
        with self.database_manager.session() as session:
            existing = self.runtime_service.repository.get_terminal_event_by_external_id(
                session,
                run_id=run_id,
                external_event_id=external_event_id,
            )
            if not existing:
                return None
            return self._response_for_existing(session, run_id, existing.id)

    def _response_for_existing(self, session, run_id: str, event_id: str) -> TerminalEventAppendResponse:
        event = self.runtime_service.get_terminal_event(session, run_id, event_id)
        return TerminalEventAppendResponse(
            accepted=True,
            event_id=event.id,
            seq_no=event.seq_no,
            event=event,
        )

    def _validate_run_accepts_input(self, session, run_id: str, run_status: str) -> None:
        if run_status in {"succeeded", "failed", "cancelled", "aborted"}:
            raise SkillValidationError(
                "Run 已结束，不能继续追加终端输入。",
                details={"run_id": run_id, "status": run_status},
            )
        terminal_session = self.runtime_service.repository.get_terminal_session_for_run(session, run_id)
        if not terminal_session or terminal_session.status != "open":
            raise SkillValidationError(
                "当前 Run 没有可用的 Terminal Session。",
                details={"run_id": run_id},
            )

    async def _cleanup(self, uploaded: list[UploadedTerminalPart]) -> None:
        delete = getattr(self.object_store, "delete_object", None)
        if not callable(delete):
            return
        try:
            referenced = await run_in_threadpool(self._referenced_uploaded_objects, uploaded)
        except Exception:
            add_metric_counter(
                "psop.terminal.upload.cleanup.failures",
                description="Failed terminal upload object cleanup attempts",
            )
            LOGGER.exception("failed to verify terminal upload references before cleanup")
            return
        for item in uploaded:
            object_ref = (item.stored.bucket, item.stored.object_key)
            if object_ref in referenced:
                continue
            try:
                await run_object_store_io(
                    self.object_store,
                    delete,
                    bucket=item.stored.bucket,
                    object_key=item.stored.object_key,
                )
            except Exception:
                add_metric_counter(
                    "psop.terminal.upload.cleanup.failures",
                    description="Failed terminal upload object cleanup attempts",
                )
                LOGGER.exception(
                    "failed to clean up terminal upload",
                    extra={
                        "bucket": item.stored.bucket,
                        "object_key": item.stored.object_key,
                    },
                )

    def _referenced_uploaded_objects(
        self,
        uploaded: list[UploadedTerminalPart],
    ) -> set[tuple[str, str]]:
        if not uploaded or self.database_manager is None:
            return set()
        conditions = [
            and_(
                ArtifactObject.bucket == item.stored.bucket,
                ArtifactObject.object_key == item.stored.object_key,
            )
            for item in uploaded
        ]
        with self.database_manager.session() as session:
            rows = session.execute(
                select(ArtifactObject.bucket, ArtifactObject.object_key).where(or_(*conditions))
            ).all()
        return {(str(bucket), str(object_key)) for bucket, object_key in rows}

    async def _cleanup_object_key(self, *, bucket: str, object_key: str, run_id: str) -> None:
        delete = getattr(self.object_store, "delete_object", None)
        if not callable(delete):
            return
        try:
            await run_object_store_io(
                self.object_store,
                delete,
                bucket=bucket,
                object_key=object_key,
            )
        except Exception:
            add_metric_counter(
                "psop.terminal.upload.cleanup.failures",
                description="Failed terminal upload object cleanup attempts",
            )
            LOGGER.exception(
                "failed to clean up cancelled terminal upload",
                extra={"run_id": run_id, "bucket": bucket, "object_key": object_key},
            )

    @staticmethod
    def _inspect_stream(
        stream: BinaryIO,
        max_file_bytes: int,
        max_total_bytes: int,
        existing_total_bytes: int,
    ) -> tuple[int, str]:
        digest = hashlib.sha256()
        size_bytes = 0
        stream.seek(0)
        try:
            while True:
                chunk = stream.read(UPLOAD_CHUNK_SIZE)
                if not chunk:
                    break
                size_bytes += len(chunk)
                if size_bytes > max_file_bytes:
                    raise PayloadTooLargeError(
                        "terminal event 单个上传文件过大。",
                        details={"max_bytes": max_file_bytes, "size_bytes": size_bytes},
                    )
                if existing_total_bytes + size_bytes > max_total_bytes:
                    raise PayloadTooLargeError(
                        "terminal event 上传文件总量过大。",
                        details={
                            "max_bytes": max_total_bytes,
                            "size_bytes": existing_total_bytes + size_bytes,
                        },
                    )
                digest.update(chunk)
        finally:
            stream.seek(0)
        return size_bytes, digest.hexdigest()


def safe_terminal_upload_filename(filename: str) -> str:
    cleaned = filename.replace("\\", "/").split("/")[-1].strip()
    return cleaned or "upload.bin"


def terminal_part_kind_for_mime_type(mime_type: str) -> str:
    if mime_type.startswith("image/"):
        return "image"
    if mime_type.startswith("audio/"):
        return "audio"
    if mime_type.startswith("video/"):
        return "video"
    return "file"


def is_object_store_timeout(exc: BaseException) -> bool:
    current: BaseException | None = exc
    checked = 0
    while current is not None and checked < 5:
        if isinstance(current, (TimeoutError, socket.timeout)):
            return True
        if current.__class__.__name__ in {
            "ConnectTimeoutError",
            "ReadTimeoutError",
        }:
            return True
        response = getattr(current, "response", None)
        if isinstance(response, dict):
            error = response.get("Error") if isinstance(response.get("Error"), dict) else {}
            metadata = response.get("ResponseMetadata") if isinstance(response.get("ResponseMetadata"), dict) else {}
            if str(error.get("Code") or "") in {
                "RequestTimeout",
                "RequestTimeoutException",
                "GatewayTimeout",
            }:
                return True
            if metadata.get("HTTPStatusCode") in {408, 504}:
                return True
        current = current.__cause__ or current.__context__
        checked += 1
    return False


def raise_object_store_error(
    exc: Exception,
    *,
    message: str,
    details: dict[str, object],
) -> None:
    LOGGER.warning(
        "object-store operation failed",
        extra={
            "object_store_error_type": exc.__class__.__name__,
            "object_store_context": details,
        },
        exc_info=exc,
    )
    if is_object_store_timeout(exc):
        raise SkillsGatewayTimeoutError(message, details=details) from exc
    raise SkillsGatewayError(message, details=details) from exc
