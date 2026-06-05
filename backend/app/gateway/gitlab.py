from __future__ import annotations

import base64
from dataclasses import dataclass
import logging
import time
from typing import Protocol
from urllib.parse import quote

import httpx

from app.core.config import Settings
from app.core.observability import record_span_exception, set_span_attributes, start_span
from app.pskills.exceptions import SkillsConfigurationError, SkillsGatewayError

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class GitLabProjectInfo:
    project_id: str
    name: str
    path: str
    repository_url: str
    default_branch: str
    head_commit_sha: str


@dataclass(slots=True)
class SkillSourceBundle:
    readme_content: str
    skill_md_content: str
    skill_yaml_content: str
    source_ref: str
    head_commit_sha: str


@dataclass(slots=True)
class RepositoryTreeEntry:
    id: str
    name: str
    path: str
    type: str
    mode: str | None = None


@dataclass(slots=True)
class RepositoryFile:
    file_path: str
    file_name: str
    content: str
    ref: str
    head_commit_sha: str


class GitLabSkillSourceGateway(Protocol):
    def create_skill_project(
        self,
        *,
        group_path: str,
        project_name: str,
        project_path: str,
        default_branch: str,
        initial_readme: str,
        initial_skill_md: str,
        initial_skill_yaml: str,
    ) -> GitLabProjectInfo:
        ...

    def get_branch_head(self, project_id: str, branch: str) -> str:
        ...

    def get_skill_source(self, project_id: str, ref: str) -> SkillSourceBundle:
        ...

    def list_repository_tree(self, project_id: str, ref: str, path: str | None = None) -> list[RepositoryTreeEntry]:
        ...

    def get_repository_file(self, project_id: str, ref: str, file_path: str) -> RepositoryFile:
        ...

    def commit_repository_file(
        self,
        *,
        project_id: str,
        branch: str,
        file_path: str,
        content: str,
        action: str,
        commit_message: str,
    ) -> str:
        ...

    def commit_repository_files(
        self,
        *,
        project_id: str,
        branch: str,
        files: dict[str, str],
        binary_files: dict[str, bytes] | None = None,
        commit_message: str,
    ) -> str:
        ...

    def commit_skill_source(
        self,
        *,
        project_id: str,
        branch: str,
        readme_content: str,
        skill_md_content: str,
        skill_yaml_content: str,
        commit_message: str,
    ) -> str:
        ...

    def update_project_name(self, project_id: str, name: str) -> None:
        ...

    def archive_project(self, project_id: str) -> None:
        ...


class HttpGitLabSkillSourceGateway:
    """Minimal GitLab REST client for the Skills Management MVP."""

    def __init__(
        self,
        *,
        api_base_url: str,
        token: str | None,
        timeout_seconds: float,
    ) -> None:
        self.api_base_url = api_base_url.rstrip("/")
        self.token = token
        self.timeout_seconds = timeout_seconds

    @classmethod
    def from_settings(cls, settings: Settings) -> "HttpGitLabSkillSourceGateway":
        return cls(
            api_base_url=settings.gitlab_api_base_url,
            token=settings.gitlab_token,
            timeout_seconds=settings.gitlab_timeout_seconds,
        )

    def _headers(self) -> dict[str, str]:
        if not self.token:
            raise SkillsConfigurationError("未配置 GitLab Token，无法执行 Skills 管理链路。")

        return {"Private-Token": self.token}

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, object] | None = None,
        json: dict[str, object] | None = None,
    ) -> dict | list:
        url = f"{self.api_base_url}{path}"

        started_at = time.perf_counter()
        try:
            with start_span("gateway.gitlab", http_method=method, gitlab_path=path, api_base_url=self.api_base_url) as span:
                with httpx.Client(timeout=self.timeout_seconds, headers=self._headers()) as client:
                    response = client.request(method, url, params=params, json=json)
                elapsed_ms = int((time.perf_counter() - started_at) * 1000)
                set_span_attributes(span, {"http.status_code": response.status_code, "duration_ms": elapsed_ms})
                LOGGER.info(
                    "GitLab request completed",
                    extra={
                        "http_method": method,
                        "gitlab_path": path,
                        "status_code": response.status_code,
                        "duration_ms": elapsed_ms,
                    },
                )
        except httpx.HTTPError as exc:
            elapsed_ms = int((time.perf_counter() - started_at) * 1000)
            with start_span(
                "gateway.gitlab.error",
                http_method=method,
                gitlab_path=path,
                api_base_url=self.api_base_url,
                duration_ms=elapsed_ms,
            ) as span:
                record_span_exception(span, exc)
            LOGGER.warning(
                "GitLab request failed",
                extra={
                    "http_method": method,
                    "gitlab_path": path,
                    "error_type": exc.__class__.__name__,
                    "duration_ms": elapsed_ms,
                },
            )
            raise SkillsGatewayError("访问 GitLab 失败。", details={"error": str(exc)}) from exc

        if response.status_code >= 400:
            LOGGER.warning(
                "GitLab returned error response",
                extra={"http_method": method, "gitlab_path": path, "status_code": response.status_code},
            )
            raise SkillsGatewayError(
                "GitLab 返回错误响应。",
                details={
                    "status_code": response.status_code,
                    "body": response.text,
                    "path": path,
                },
            )

        payload = response.json()
        if not isinstance(payload, (dict, list)):
            raise SkillsGatewayError("GitLab 返回了不可识别的响应格式。")
        return payload

    def _get_group_id(self, group_path: str) -> int:
        payload = self._request("GET", f"/groups/{quote(group_path, safe='')}")
        if not isinstance(payload, dict) or "id" not in payload:
            raise SkillsGatewayError("无法解析 GitLab group 信息。", details={"group_path": group_path})
        return int(payload["id"])

    def create_skill_project(
        self,
        *,
        group_path: str,
        project_name: str,
        project_path: str,
        default_branch: str,
        initial_readme: str,
        initial_skill_md: str,
        initial_skill_yaml: str,
    ) -> GitLabProjectInfo:
        group_id = self._get_group_id(group_path)
        payload = self._request(
            "POST",
            "/projects",
            json={
                "name": project_name,
                "path": project_path,
                "namespace_id": group_id,
                "initialize_with_readme": True,
                "default_branch": default_branch,
            },
        )
        if not isinstance(payload, dict):
            raise SkillsGatewayError("GitLab project 创建响应格式错误。")

        project_id = str(payload["id"])
        actual_branch = str(payload.get("default_branch") or default_branch)

        self._request(
            "POST",
            f"/projects/{quote(project_id, safe='')}/repository/commits",
            json={
                "branch": actual_branch,
                "commit_message": "Initialize skill source via PSOP WEB IDE",
                "actions": [
                    {
                        "action": "update",
                        "file_path": "README.md",
                        "content": initial_readme,
                    },
                    {
                        "action": "create",
                        "file_path": "SKILL.md",
                        "content": initial_skill_md,
                    },
                    {
                        "action": "create",
                        "file_path": "skill.yaml",
                        "content": initial_skill_yaml,
                    },
                ],
            },
        )

        head_commit_sha = self.get_branch_head(project_id, actual_branch)
        repository_url = str(payload.get("web_url") or payload.get("http_url_to_repo") or "")

        return GitLabProjectInfo(
            project_id=project_id,
            name=str(payload.get("name") or project_name),
            path=str(payload.get("path") or project_path),
            repository_url=repository_url,
            default_branch=actual_branch,
            head_commit_sha=head_commit_sha,
        )

    def get_branch_head(self, project_id: str, branch: str) -> str:
        payload = self._request(
            "GET",
            f"/projects/{quote(project_id, safe='')}/repository/branches/{quote(branch, safe='')}",
        )
        if not isinstance(payload, dict):
            raise SkillsGatewayError("GitLab branch 查询响应格式错误。")

        commit = payload.get("commit")
        if not isinstance(commit, dict) or "id" not in commit:
            raise SkillsGatewayError("GitLab branch 查询缺少 commit 信息。")

        return str(commit["id"])

    def _resolve_ref_head(self, project_id: str, ref: str) -> str:
        try:
            return self.get_branch_head(project_id, ref)
        except SkillsGatewayError as branch_error:
            if branch_error.details.get("status_code") != 404:
                raise

        payload = self._request(
            "GET",
            f"/projects/{quote(project_id, safe='')}/repository/commits/{quote(ref, safe='')}",
        )
        if not isinstance(payload, dict) or "id" not in payload:
            raise SkillsGatewayError("GitLab commit 查询响应格式错误。", details={"ref": ref})

        return str(payload["id"])

    def _get_file_content(self, project_id: str, ref: str, file_path: str) -> str:
        payload = self._request(
            "GET",
            f"/projects/{quote(project_id, safe='')}/repository/files/{quote(file_path, safe='')}",
            params={"ref": ref},
        )
        if not isinstance(payload, dict) or "content" not in payload:
            raise SkillsGatewayError("GitLab 文件读取响应格式错误。", details={"file_path": file_path})

        try:
            return base64.b64decode(str(payload["content"])).decode("utf-8")
        except Exception as exc:  # pragma: no cover - defensive decode guard
            raise SkillsGatewayError(
                "GitLab 文件内容解码失败。",
                details={"file_path": file_path, "error": str(exc)},
            ) from exc

    def get_skill_source(self, project_id: str, ref: str) -> SkillSourceBundle:
        head_commit_sha = self._resolve_ref_head(project_id, ref)
        return SkillSourceBundle(
            readme_content=self._get_file_content(project_id, ref, "README.md"),
            skill_md_content=self._get_file_content(project_id, ref, "SKILL.md"),
            skill_yaml_content=self._get_file_content(project_id, ref, "skill.yaml"),
            source_ref=ref,
            head_commit_sha=head_commit_sha,
        )

    def list_repository_tree(self, project_id: str, ref: str, path: str | None = None) -> list[RepositoryTreeEntry]:
        params: dict[str, object] = {"ref": ref, "per_page": 100}
        if path:
            params["path"] = path

        payload = self._request(
            "GET",
            f"/projects/{quote(project_id, safe='')}/repository/tree",
            params=params,
        )
        if not isinstance(payload, list):
            raise SkillsGatewayError("GitLab repository tree 查询响应格式错误。")

        entries: list[RepositoryTreeEntry] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            entries.append(
                RepositoryTreeEntry(
                    id=str(item.get("id") or item.get("path") or ""),
                    name=str(item.get("name") or ""),
                    path=str(item.get("path") or ""),
                    type=str(item.get("type") or ""),
                    mode=str(item["mode"]) if item.get("mode") is not None else None,
                )
            )
        return entries

    def get_repository_file(self, project_id: str, ref: str, file_path: str) -> RepositoryFile:
        content = self._get_file_content(project_id, ref, file_path)
        return RepositoryFile(
            file_path=file_path,
            file_name=file_path.rsplit("/", 1)[-1],
            content=content,
            ref=ref,
            head_commit_sha=self.get_branch_head(project_id, ref),
        )

    def commit_repository_file(
        self,
        *,
        project_id: str,
        branch: str,
        file_path: str,
        content: str,
        action: str,
        commit_message: str,
    ) -> str:
        self._request(
            "POST",
            f"/projects/{quote(project_id, safe='')}/repository/commits",
            json={
                "branch": branch,
                "commit_message": commit_message,
                "actions": [
                    {
                        "action": action,
                        "file_path": file_path,
                        "content": content,
                    },
                ],
            },
        )
        return self.get_branch_head(project_id, branch)

    def commit_repository_files(
        self,
        *,
        project_id: str,
        branch: str,
        files: dict[str, str],
        binary_files: dict[str, bytes] | None = None,
        commit_message: str,
    ) -> str:
        actions = []
        for file_path, content in files.items():
            action = "update" if self._repository_file_exists(project_id, branch, file_path) else "create"
            actions.append({"action": action, "file_path": file_path, "content": content})
        for file_path, content in (binary_files or {}).items():
            action = "update" if self._repository_file_exists(project_id, branch, file_path) else "create"
            actions.append(
                {
                    "action": action,
                    "file_path": file_path,
                    "content": base64.b64encode(content).decode("ascii"),
                    "encoding": "base64",
                }
            )
        self._request(
            "POST",
            f"/projects/{quote(project_id, safe='')}/repository/commits",
            json={
                "branch": branch,
                "commit_message": commit_message,
                "actions": actions,
            },
        )
        return self.get_branch_head(project_id, branch)

    def _repository_file_exists(self, project_id: str, ref: str, file_path: str) -> bool:
        try:
            self._request(
                "GET",
                f"/projects/{quote(project_id, safe='')}/repository/files/{quote(file_path, safe='')}",
                params={"ref": ref},
            )
            return True
        except SkillsGatewayError as exc:
            if exc.details.get("status_code") == 404:
                return False
            raise

    def commit_skill_source(
        self,
        *,
        project_id: str,
        branch: str,
        readme_content: str,
        skill_md_content: str,
        skill_yaml_content: str,
        commit_message: str,
    ) -> str:
        self._request(
            "POST",
            f"/projects/{quote(project_id, safe='')}/repository/commits",
            json={
                "branch": branch,
                "commit_message": commit_message,
                "actions": [
                    {"action": "update", "file_path": "README.md", "content": readme_content},
                    {"action": "update", "file_path": "SKILL.md", "content": skill_md_content},
                    {"action": "update", "file_path": "skill.yaml", "content": skill_yaml_content},
                ],
            },
        )
        return self.get_branch_head(project_id, branch)

    def update_project_name(self, project_id: str, name: str) -> None:
        self._request(
            "PUT",
            f"/projects/{quote(project_id, safe='')}",
            json={"name": name},
        )

    def archive_project(self, project_id: str) -> None:
        self._request("POST", f"/projects/{quote(project_id, safe='')}/archive")
