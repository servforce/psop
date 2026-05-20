from __future__ import annotations

import base64
from typing import Any

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
