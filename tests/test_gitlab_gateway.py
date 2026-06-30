from __future__ import annotations

import base64
import hashlib
from typing import Any

import httpx

from app.domain.skills.exceptions import SkillsGatewayError
from app.gateway.gitlab import HttpGitLabSkillSourceGateway


def _encoded(value: str) -> str:
    return base64.b64encode(value.encode("utf-8")).decode("ascii")


def test_http_gitlab_get_skill_source_accepts_frozen_commit_ref() -> None:
    gateway = HttpGitLabSkillSourceGateway(
        api_base_url="https://gitlab.example.test/api/v4",
        token="test-token",
        timeout_seconds=1,
    )
    calls: list[tuple[str, str, dict[str, object] | None]] = []

    def fake_request(
        method: str,
        path: str,
        *,
        params: dict[str, object] | None = None,
        json: dict[str, object] | None = None,
    ) -> dict[str, Any]:
        calls.append((method, path, params))
        if "/repository/branches/" in path:
            raise SkillsGatewayError("GitLab 返回错误响应。", details={"status_code": 404, "path": path})
        if "/repository/commits/frozen-sha" in path:
            return {"id": "frozen-sha"}
        if path.endswith("/repository/files/README.md"):
            return {"content": _encoded("# README\n")}
        if path.endswith("/repository/files/SKILL.md"):
            return {"content": _encoded("# SKILL\n")}
        if path.endswith("/repository/files/skill.yaml"):
            return {"content": _encoded("skill:\n  identity:\n    key: demo\n")}
        raise AssertionError(f"unexpected GitLab request: {method} {path}")

    gateway._request = fake_request  # type: ignore[method-assign]

    bundle = gateway.get_skill_source("42", "frozen-sha")

    assert bundle.head_commit_sha == "frozen-sha"
    assert bundle.source_ref == "frozen-sha"
    assert bundle.readme_content == "# README\n"
    assert bundle.skill_md_content == "# SKILL\n"
    assert any("/repository/commits/frozen-sha" in path for _, path, _ in calls)


def test_http_gitlab_get_retries_transient_server_error(monkeypatch) -> None:
    gateway = HttpGitLabSkillSourceGateway(
        api_base_url="https://gitlab.example.test/api/v4",
        token="test-token",
        timeout_seconds=1,
    )
    responses = [httpx.Response(502, text="bad gateway"), httpx.Response(200, json={"ok": True})]
    calls = []

    class FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback) -> None:
            return None

        def request(self, method, url, *, params=None, json=None):
            calls.append((method, url))
            return responses.pop(0)

    monkeypatch.setattr("app.gateway.gitlab.httpx.Client", FakeClient)
    monkeypatch.setattr("app.gateway.gitlab.time.sleep", lambda _seconds: None)

    assert gateway._request("GET", "/projects/1") == {"ok": True}
    assert len(calls) == 2


def test_http_gitlab_commit_repository_files_skips_unchanged_files() -> None:
    gateway = HttpGitLabSkillSourceGateway(
        api_base_url="https://gitlab.example.test/api/v4",
        token="test-token",
        timeout_seconds=1,
    )
    posted_payloads: list[dict[str, Any]] = []

    text = "same text\n"
    image = b"same-image"

    def fake_request(
        method: str,
        path: str,
        *,
        params: dict[str, object] | None = None,
        json: dict[str, object] | None = None,
    ) -> dict[str, Any]:
        if method == "POST":
            posted_payloads.append(json or {})
            return {"id": "new-sha"}
        if "/repository/branches/main" in path:
            return {"commit": {"id": "current-sha"}}
        if path.endswith("/repository/files/README.md"):
            return {"content": base64.b64encode(text.encode("utf-8")).decode("ascii")}
        if path.endswith("/repository/files/references%2Fframe.jpg"):
            return {"content_sha256": hashlib.sha256(image).hexdigest(), "content": ""}
        raise AssertionError(f"unexpected GitLab request: {method} {path}")

    gateway._request = fake_request  # type: ignore[method-assign]

    head = gateway.commit_repository_files(
        project_id="42",
        branch="main",
        files={"README.md": text},
        binary_files={"references/frame.jpg": image},
        commit_message="No-op",
    )

    assert head == "current-sha"
    assert posted_payloads == []


def test_http_gitlab_commit_repository_files_recovers_when_post_error_already_applied() -> None:
    gateway = HttpGitLabSkillSourceGateway(
        api_base_url="https://gitlab.example.test/api/v4",
        token="test-token",
        timeout_seconds=1,
    )
    text = "new text\n"
    post_attempted = False

    def fake_request(
        method: str,
        path: str,
        *,
        params: dict[str, object] | None = None,
        json: dict[str, object] | None = None,
    ) -> dict[str, Any]:
        nonlocal post_attempted
        if method == "POST":
            post_attempted = True
            raise SkillsGatewayError("GitLab 返回错误响应。", details={"status_code": 500, "body": "already applied"})
        if "/repository/branches/main" in path:
            return {"commit": {"id": "new-sha"}}
        if path.endswith("/repository/files/README.md"):
            if not post_attempted:
                raise SkillsGatewayError("GitLab 返回错误响应。", details={"status_code": 404, "path": path})
            return {"content_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(), "content": ""}
        raise AssertionError(f"unexpected GitLab request: {method} {path}")

    gateway._request = fake_request  # type: ignore[method-assign]

    head = gateway.commit_repository_files(
        project_id="42",
        branch="main",
        files={"README.md": text},
        commit_message="Recover",
    )

    assert head == "new-sha"
    assert post_attempted
