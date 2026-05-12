from __future__ import annotations

from app.core.config import Settings
from app.infra.object_store import ObjectStoreService


class FakeS3Client:
    def __init__(self) -> None:
        self.puts: list[dict[str, object]] = []

    def head_bucket(self, *, Bucket: str) -> None:
        return None

    def create_bucket(self, *, Bucket: str) -> None:
        return None

    def put_object(self, **kwargs) -> None:
        self.puts.append(kwargs)


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

    metadata = fake_client.puts[0]["Metadata"]
    assert metadata == {"filename": "%E9%9B%A8%E4%BC%9E%E5%9B%BE%E7%89%87.png", "role": "input"}
    assert stored.metadata == metadata
    assert all(str(value).isascii() for value in metadata.values())
