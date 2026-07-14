from __future__ import annotations

import asyncio
import hashlib
import io
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from functools import partial
from typing import Any, BinaryIO, Callable, TypeVar
from urllib.parse import quote

from app.core.config import Settings
from app.core.observability import add_metric_up_down_counter, record_metric_histogram


_T = TypeVar("_T")


@dataclass(frozen=True)
class StoredObject:
    bucket: str
    object_key: str
    media_type: str
    size_bytes: int
    checksum: str
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ObjectStat:
    bucket: str
    object_key: str
    media_type: str
    size_bytes: int
    etag: str
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass
class ObjectDownload:
    """A closeable S3 response body returned by ``open_download``."""

    body: Any
    size_bytes: int
    media_type: str
    etag: str
    content_range: str = ""
    metadata: dict[str, str] = field(default_factory=dict)

    def read(self, size: int = -1) -> bytes:
        return self.body.read(size)

    def close(self) -> None:
        close = getattr(self.body, "close", None)
        if callable(close):
            close()


class ObjectStoreService:
    """S3-compatible object storage adapter with a dedicated blocking-I/O pool."""

    def __init__(self, *, settings: Settings) -> None:
        self.settings = settings
        self._client = None
        self._executor = ThreadPoolExecutor(
            max_workers=max(1, settings.terminal_object_store_io_workers),
            thread_name_prefix="psop-object-store",
        )
        self._client_lock = threading.Lock()
        self._bucket_lock = threading.Lock()
        self._ready_buckets: set[str] = set()

    @classmethod
    def from_settings(cls, settings: Settings) -> "ObjectStoreService":
        return cls(settings=settings)

    async def run_io(self, operation: Callable[..., _T], /, *args: Any, **kwargs: Any) -> _T:
        """Run a blocking object-store operation outside the event-loop/default pool."""

        loop = asyncio.get_running_loop()
        submitted_at = time.perf_counter()
        return await loop.run_in_executor(
            self._executor,
            partial(self._run_instrumented_io, submitted_at, operation, args, kwargs),
        )

    def call_io(self, operation: Callable[..., _T], /, *args: Any, **kwargs: Any) -> _T:
        """Run an operation on the dedicated pool from a synchronous endpoint."""

        submitted_at = time.perf_counter()
        return self._executor.submit(
            self._run_instrumented_io,
            submitted_at,
            operation,
            args,
            kwargs,
        ).result()

    @staticmethod
    def _run_instrumented_io(
        submitted_at: float,
        operation: Callable[..., _T],
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> _T:
        record_metric_histogram(
            "psop.object_store.io.queue_wait",
            max(0.0, time.perf_counter() - submitted_at),
            unit="s",
            description="Time spent waiting for an object-store I/O executor thread",
        )
        add_metric_up_down_counter(
            "psop.object_store.io.in_flight",
            1,
            description="Object-store I/O operations currently executing",
        )
        try:
            return operation(*args, **kwargs)
        finally:
            add_metric_up_down_counter(
                "psop.object_store.io.in_flight",
                -1,
                description="Object-store I/O operations currently executing",
            )

    def close(self) -> None:
        self._executor.shutdown(wait=True, cancel_futures=True)

    def upload_stream(
        self,
        *,
        object_key: str,
        stream: BinaryIO,
        media_type: str,
        size_bytes: int | None = None,
        checksum: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> StoredObject:
        resolved_size, resolved_checksum = self._stream_size_and_checksum(
            stream,
            size_bytes=size_bytes,
            checksum=checksum,
        )
        bucket = self.settings.object_store_bucket
        normalized_metadata = self._normalize_metadata(metadata or {})
        client = self._get_client()
        self._ensure_bucket(client, bucket)
        stream.seek(0)
        from boto3.s3.transfer import TransferConfig

        client.upload_fileobj(
            Fileobj=stream,
            Bucket=bucket,
            Key=object_key,
            ExtraArgs={
                "ContentType": media_type,
                "Metadata": normalized_metadata,
            },
            Config=TransferConfig(use_threads=False),
        )
        return StoredObject(
            bucket=bucket,
            object_key=object_key,
            media_type=media_type,
            size_bytes=resolved_size,
            checksum=resolved_checksum,
            metadata=normalized_metadata,
        )

    def upload_bytes(
        self,
        *,
        object_key: str,
        content: bytes,
        media_type: str,
        metadata: dict[str, str] | None = None,
    ) -> StoredObject:
        return self.upload_stream(
            object_key=object_key,
            stream=io.BytesIO(content),
            media_type=media_type,
            size_bytes=len(content),
            checksum=hashlib.sha256(content).hexdigest(),
            metadata=metadata,
        )

    def stat_object(self, *, bucket: str, object_key: str) -> ObjectStat:
        response = self._get_client().head_object(Bucket=bucket, Key=object_key)
        return ObjectStat(
            bucket=bucket,
            object_key=object_key,
            media_type=str(response.get("ContentType") or "application/octet-stream"),
            size_bytes=int(response.get("ContentLength") or 0),
            etag=str(response.get("ETag") or "").strip('"'),
            metadata={str(key): str(value) for key, value in (response.get("Metadata") or {}).items()},
        )

    def open_download(
        self,
        *,
        bucket: str,
        object_key: str,
        byte_range: tuple[int, int] | None = None,
    ) -> ObjectDownload:
        request: dict[str, Any] = {"Bucket": bucket, "Key": object_key}
        if byte_range is not None:
            request["Range"] = f"bytes={byte_range[0]}-{byte_range[1]}"
        response = self._get_client().get_object(**request)
        body = response.get("Body")
        if body is None:
            body = io.BytesIO()
        return ObjectDownload(
            body=body,
            size_bytes=int(response.get("ContentLength") or 0),
            media_type=str(response.get("ContentType") or "application/octet-stream"),
            etag=str(response.get("ETag") or "").strip('"'),
            content_range=str(response.get("ContentRange") or ""),
            metadata={str(key): str(value) for key, value in (response.get("Metadata") or {}).items()},
        )

    def download_bytes(self, *, bucket: str, object_key: str) -> bytes:
        download = self.open_download(bucket=bucket, object_key=object_key)
        try:
            return download.read()
        finally:
            download.close()

    def delete_object(self, *, bucket: str, object_key: str) -> None:
        self._get_client().delete_object(Bucket=bucket, Key=object_key)

    def _get_client(self):
        if self._client is None:
            with self._client_lock:
                if self._client is None:
                    import boto3
                    from botocore.config import Config

                    self._client = boto3.client(
                        "s3",
                        endpoint_url=self.settings.object_store_endpoint,
                        aws_access_key_id=self.settings.object_store_access_key,
                        aws_secret_access_key=self.settings.object_store_secret_key,
                        region_name=self.settings.object_store_region,
                        use_ssl=self.settings.object_store_secure,
                        config=Config(
                            signature_version="s3v4",
                            s3={"addressing_style": "path"},
                            connect_timeout=self.settings.object_store_connect_timeout_seconds,
                            read_timeout=self.settings.object_store_read_timeout_seconds,
                            retries={
                                "mode": "standard",
                                "total_max_attempts": self.settings.object_store_total_max_attempts,
                            },
                            max_pool_connections=self.settings.object_store_max_pool_connections,
                            tcp_keepalive=True,
                        ),
                    )
        return self._client

    def _ensure_bucket(self, client, bucket: str) -> None:
        if bucket in self._ready_buckets:
            return
        with self._bucket_lock:
            if bucket in self._ready_buckets:
                return
            try:
                client.head_bucket(Bucket=bucket)
            except Exception as exc:
                if not self._is_missing_bucket_error(exc):
                    raise
                if not self.settings.object_store_auto_create_bucket:
                    raise
                client.create_bucket(Bucket=bucket)
            self._ready_buckets.add(bucket)

    @staticmethod
    def _is_missing_bucket_error(exc: Exception) -> bool:
        response = getattr(exc, "response", None)
        if not isinstance(response, dict):
            return False
        error = response.get("Error") if isinstance(response.get("Error"), dict) else {}
        metadata = response.get("ResponseMetadata") if isinstance(response.get("ResponseMetadata"), dict) else {}
        code = str(error.get("Code") or "")
        status_code = metadata.get("HTTPStatusCode")
        return code in {"404", "NoSuchBucket", "NotFound"} or status_code == 404

    @staticmethod
    def _stream_size_and_checksum(
        stream: BinaryIO,
        *,
        size_bytes: int | None,
        checksum: str | None,
    ) -> tuple[int, str]:
        if size_bytes is not None and checksum:
            return size_bytes, checksum
        digest = hashlib.sha256()
        resolved_size = 0
        stream.seek(0)
        while True:
            chunk = stream.read(256 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            resolved_size += len(chunk)
        stream.seek(0)
        return size_bytes if size_bytes is not None else resolved_size, checksum or digest.hexdigest()

    @staticmethod
    def _normalize_metadata(metadata: dict[str, str]) -> dict[str, str]:
        normalized: dict[str, str] = {}
        for key, value in metadata.items():
            normalized_key = ObjectStoreService._ascii_metadata_value(str(key))
            normalized[normalized_key] = ObjectStoreService._ascii_metadata_value(str(value))
        return normalized

    @staticmethod
    def _ascii_metadata_value(value: str) -> str:
        try:
            value.encode("ascii")
        except UnicodeEncodeError:
            return quote(value, safe="-_.~")
        return value
