from __future__ import annotations

import io

import pytest

from app.core.config import Settings
from app.infra.object_store import ObjectStoreService


class FakeS3Client:
    def __init__(self) -> None:
        self.uploads: list[dict[str, object]] = []
        self.objects: dict[tuple[str, str], tuple[bytes, dict[str, object]]] = {}
        self.gets: list[dict[str, object]] = []
        self.deleted: list[tuple[str, str]] = []

    def head_bucket(self, *, Bucket: str) -> None:
        return None

    def create_bucket(self, *, Bucket: str) -> None:
        return None

    def upload_fileobj(self, **kwargs) -> None:
        content = kwargs["Fileobj"].read()
        self.uploads.append(kwargs)
        self.objects[(kwargs["Bucket"], kwargs["Key"])] = (content, kwargs["ExtraArgs"])

    def head_object(self, *, Bucket: str, Key: str) -> dict[str, object]:
        content, extra = self.objects[(Bucket, Key)]
        return {
            "ContentLength": len(content),
            "ContentType": extra["ContentType"],
            "ETag": '"fake-etag"',
            "Metadata": extra["Metadata"],
        }

    def get_object(self, **kwargs) -> dict[str, object]:
        self.gets.append(kwargs)
        content, extra = self.objects[(kwargs["Bucket"], kwargs["Key"])]
        if "Range" in kwargs:
            start_text, end_text = str(kwargs["Range"]).removeprefix("bytes=").split("-", 1)
            total_size = len(content)
            content = content[int(start_text) : int(end_text) + 1]
            content_range = f"bytes {start_text}-{end_text}/{total_size}"
        else:
            content_range = ""
        return {
            "Body": io.BytesIO(content),
            "ContentLength": len(content),
            "ContentType": extra["ContentType"],
            "ETag": '"fake-etag"',
            "ContentRange": content_range,
            "Metadata": extra["Metadata"],
        }

    def delete_object(self, *, Bucket: str, Key: str) -> None:
        self.deleted.append((Bucket, Key))
        self.objects.pop((Bucket, Key), None)


class _S3ResponseError(RuntimeError):
    def __init__(self, *, code: str, status_code: int) -> None:
        super().__init__(code)
        self.response = {
            "Error": {"Code": code},
            "ResponseMetadata": {"HTTPStatusCode": status_code},
        }


class MissingBucketS3Client(FakeS3Client):
    def __init__(self) -> None:
        super().__init__()
        self.created: list[str] = []

    def head_bucket(self, *, Bucket: str) -> None:
        if Bucket not in self.created:
            raise _S3ResponseError(code="NoSuchBucket", status_code=404)

    def create_bucket(self, *, Bucket: str) -> None:
        self.created.append(Bucket)


class ForbiddenBucketS3Client(FakeS3Client):
    def __init__(self) -> None:
        super().__init__()
        self.created: list[str] = []

    def head_bucket(self, *, Bucket: str) -> None:
        raise _S3ResponseError(code="AccessDenied", status_code=403)

    def create_bucket(self, *, Bucket: str) -> None:
        self.created.append(Bucket)


def test_object_store_encodes_non_ascii_metadata_values_for_s3_headers() -> None:
    service = ObjectStoreService(
        settings=Settings(
            object_store_endpoint="http://object-store.local",
            object_store_bucket="psop-test",
        )
    )
    fake_client = FakeS3Client()
    service._client = fake_client

    stored = service.upload_bytes(
        object_key="skill-tests/case/雨伞图片.png",
        content=b"fake-image",
        media_type="image/png",
        metadata={"filename": "雨伞图片.png", "role": "input"},
    )

    metadata = fake_client.uploads[0]["ExtraArgs"]["Metadata"]
    assert metadata == {"filename": "%E9%9B%A8%E4%BC%9E%E5%9B%BE%E7%89%87.png", "role": "input"}
    assert stored.metadata == metadata
    assert all(str(value).isascii() for value in metadata.values())
    service.close()


def test_object_store_supports_stat_range_stream_and_delete() -> None:
    service = ObjectStoreService(
        settings=Settings(
            object_store_endpoint="http://object-store.local",
            object_store_bucket="psop-test",
        )
    )
    fake_client = FakeS3Client()
    service._client = fake_client

    stored = service.upload_stream(
        object_key="terminal/photo.png",
        stream=io.BytesIO(b"0123456789"),
        media_type="image/png",
    )
    stat = service.stat_object(bucket=stored.bucket, object_key=stored.object_key)
    download = service.open_download(
        bucket=stored.bucket,
        object_key=stored.object_key,
        byte_range=(2, 5),
    )
    try:
        content = download.read()
    finally:
        download.close()
    service.delete_object(bucket=stored.bucket, object_key=stored.object_key)

    assert stored.size_bytes == 10
    assert stat.size_bytes == 10
    assert stat.media_type == "image/png"
    assert fake_client.gets[-1]["Range"] == "bytes=2-5"
    assert content == b"2345"
    assert fake_client.deleted == [("psop-test", "terminal/photo.png")]
    service.close()


def test_object_store_creates_only_an_explicitly_missing_bucket_and_caches_result() -> None:
    service = ObjectStoreService(
        settings=Settings(
            object_store_endpoint="http://object-store.local",
            object_store_bucket="psop-test",
        )
    )
    fake_client = MissingBucketS3Client()
    service._client = fake_client

    for object_key in ("first.txt", "second.txt"):
        service.upload_bytes(
            object_key=object_key,
            content=b"content",
            media_type="text/plain",
        )

    assert fake_client.created == ["psop-test"]
    service.close()


def test_object_store_does_not_treat_authentication_failure_as_missing_bucket() -> None:
    service = ObjectStoreService(
        settings=Settings(
            object_store_endpoint="http://object-store.local",
            object_store_bucket="psop-test",
        )
    )
    fake_client = ForbiddenBucketS3Client()
    service._client = fake_client

    with pytest.raises(_S3ResponseError):
        service.upload_bytes(
            object_key="forbidden.txt",
            content=b"content",
            media_type="text/plain",
        )

    assert fake_client.created == []
    service.close()
