from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from urllib.parse import quote

from app.core.config import Settings


@dataclass(frozen=True)
class StoredObject:
    bucket: str
    object_key: str
    media_type: str
    size_bytes: int
    checksum: str
    metadata: dict[str, str] = field(default_factory=dict)


class ObjectStoreService:
    """Small S3-compatible object storage adapter used by test data uploads."""

    def __init__(self, *, settings: Settings) -> None:
        self.settings = settings
        self._client = None

    @classmethod
    def from_settings(cls, settings: Settings) -> "ObjectStoreService":
        return cls(settings=settings)

    def upload_bytes(
        self,
        *,
        object_key: str,
        content: bytes,
        media_type: str,
        metadata: dict[str, str] | None = None,
    ) -> StoredObject:
        checksum = hashlib.sha256(content).hexdigest()
        bucket = self.settings.object_store_bucket
        normalized_metadata = self._normalize_metadata(metadata or {})
        client = self._get_client()
        self._ensure_bucket(client, bucket)
        client.put_object(
            Bucket=bucket,
            Key=object_key,
            Body=content,
            ContentType=media_type,
            Metadata=normalized_metadata,
        )
        return StoredObject(
            bucket=bucket,
            object_key=object_key,
            media_type=media_type,
            size_bytes=len(content),
            checksum=checksum,
            metadata=normalized_metadata,
        )

    def _get_client(self):
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
                config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
            )
        return self._client

    @staticmethod
    def _ensure_bucket(client, bucket: str) -> None:
        try:
            client.head_bucket(Bucket=bucket)
        except Exception:
            client.create_bucket(Bucket=bucket)

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
