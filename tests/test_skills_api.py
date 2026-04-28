from __future__ import annotations

from dataclasses import dataclass, field

from fastapi.testclient import TestClient

from app.app import create_app
from app.core.config import Settings
from app.gateway.gitlab import GitLabProjectInfo, RepositoryFile, RepositoryTreeEntry, SkillSourceBundle


@dataclass
class _FakeProject:
    project_id: str
    name: str
    path: str
    default_branch: str
    repository_url: str
    head_commit_sha: str
    readme_content: str
    skill_md_content: str
    skill_yaml_content: str
    files: dict[str, str] = field(default_factory=dict)
    archived: bool = False


class FakeGitLabGateway:
    def __init__(self) -> None:
        self.projects: dict[str, _FakeProject] = {}
        self.project_counter = 0
        self.commit_counter = 0

    def _next_project_id(self) -> str:
        self.project_counter += 1
        return str(self.project_counter)

    def _next_commit_sha(self) -> str:
        self.commit_counter += 1
        return f"commit-{self.commit_counter:04d}"

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
        assert group_path == "skills"
        project_id = self._next_project_id()
        project = _FakeProject(
            project_id=project_id,
            name=project_name,
            path=project_path,
            default_branch=default_branch,
            repository_url=f"https://gitlab.example.local/{group_path}/{project_path}",
            head_commit_sha=self._next_commit_sha(),
            readme_content=initial_readme,
            skill_md_content=initial_skill_md,
            skill_yaml_content=initial_skill_yaml,
            files={
                "README.md": initial_readme,
                "SKILL.md": initial_skill_md,
                "skill.yaml": initial_skill_yaml,
            },
        )
        self.projects[project_id] = project
        return GitLabProjectInfo(
            project_id=project.project_id,
            name=project.name,
            path=project.path,
            repository_url=project.repository_url,
            default_branch=project.default_branch,
            head_commit_sha=project.head_commit_sha,
        )

    def get_branch_head(self, project_id: str, branch: str) -> str:
        project = self.projects[project_id]
        assert branch == project.default_branch
        return project.head_commit_sha

    def get_skill_source(self, project_id: str, ref: str) -> SkillSourceBundle:
        project = self.projects[project_id]
        assert ref == project.default_branch
        return SkillSourceBundle(
            readme_content=project.files["README.md"],
            skill_md_content=project.files["SKILL.md"],
            skill_yaml_content=project.files["skill.yaml"],
            source_ref=project.default_branch,
            head_commit_sha=project.head_commit_sha,
        )

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
        project = self.projects[project_id]
        assert branch == project.default_branch
        assert commit_message
        project.readme_content = readme_content
        project.skill_md_content = skill_md_content
        project.skill_yaml_content = skill_yaml_content
        project.files["README.md"] = readme_content
        project.files["SKILL.md"] = skill_md_content
        project.files["skill.yaml"] = skill_yaml_content
        project.head_commit_sha = self._next_commit_sha()
        return project.head_commit_sha

    def list_repository_tree(self, project_id: str, ref: str, path: str | None = None) -> list[RepositoryTreeEntry]:
        project = self.projects[project_id]
        assert ref == project.default_branch
        prefix = f"{path.rstrip('/')}/" if path else ""
        children: dict[str, RepositoryTreeEntry] = {}

        for file_path in project.files:
            if prefix and not file_path.startswith(prefix):
                continue
            remainder = file_path[len(prefix) :]
            if not remainder:
                continue
            name = remainder.split("/", 1)[0]
            is_tree = "/" in remainder
            child_path = f"{prefix}{name}" if prefix else name
            children[child_path] = RepositoryTreeEntry(
                id=child_path,
                name=name,
                path=child_path,
                type="tree" if is_tree else "blob",
                mode="040000" if is_tree else "100644",
            )

        return list(children.values())

    def get_repository_file(self, project_id: str, ref: str, file_path: str) -> RepositoryFile:
        project = self.projects[project_id]
        assert ref == project.default_branch
        return RepositoryFile(
            file_path=file_path,
            file_name=file_path.rsplit("/", 1)[-1],
            content=project.files[file_path],
            ref=project.default_branch,
            head_commit_sha=project.head_commit_sha,
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
        project = self.projects[project_id]
        assert branch == project.default_branch
        assert commit_message
        if action == "create":
            assert file_path not in project.files
        if action == "update":
            assert file_path in project.files
        project.files[file_path] = content
        project.head_commit_sha = self._next_commit_sha()
        return project.head_commit_sha

    def update_project_name(self, project_id: str, name: str) -> None:
        self.projects[project_id].name = name

    def archive_project(self, project_id: str) -> None:
        self.projects[project_id].archived = True


def create_test_settings() -> Settings:
    return Settings(
        app_name="PSOP Backend Skills Test",
        database_url="sqlite+pysqlite:///:memory:",
        database_check_on_startup=False,
        database_auto_create_schema=True,
        gitlab_skills_group_path="skills",
    )


def create_test_client() -> tuple[TestClient, FakeGitLabGateway]:
    fake_gateway = FakeGitLabGateway()
    client = TestClient(create_app(create_test_settings(), gitlab_gateway=fake_gateway))
    return client, fake_gateway


def test_create_skill_initializes_gitlab_and_persists_metadata() -> None:
    client, fake_gateway = create_test_client()

    with client:
        response = client.post(
            "/api/v1/skills",
            json={
                "key": "equipment-diagnosis",
                "name": "Equipment Diagnosis",
                "description": "Diagnose equipment issues from operator input.",
            },
        )

    assert response.status_code == 201
    payload = response.json()
    assert payload["key"] == "equipment-diagnosis"
    assert payload["gitlab_group_path"] == "skills"
    assert payload["current_draft_version"]["status"] == "draft"
    assert payload["current_draft_version"]["source_commit_sha"].startswith("commit-")
    assert len(fake_gateway.projects) == 1


def test_get_and_save_skill_source() -> None:
    client, _ = create_test_client()

    with client:
        created = client.post(
            "/api/v1/skills",
            json={
                "key": "diagnosis-assistant",
                "name": "Diagnosis Assistant",
                "description": "Assist engineers with diagnostics.",
            },
        ).json()

        skill_id = created["id"]
        source_response = client.get(f"/api/v1/skills/{skill_id}/source")
        assert source_response.status_code == 200
        source_payload = source_response.json()
        assert "skill:" in source_payload["skill_yaml_content"]

        save_response = client.put(
            f"/api/v1/skills/{skill_id}/source",
            json={
                "base_commit_sha": source_payload["head_commit_sha"],
                "readme_content": source_payload["readme_content"] + "\nUpdated from test.\n",
                "skill_md_content": source_payload["skill_md_content"] + "\n## Validation\n\n- test path\n",
                "skill_yaml_content": source_payload["skill_yaml_content"],
            },
        )

    assert save_response.status_code == 200
    saved_payload = save_response.json()
    assert saved_payload["head_commit_sha"] != source_payload["head_commit_sha"]
    assert "Updated from test." in saved_payload["readme_content"]


def test_repository_tree_file_and_folder_operations() -> None:
    client, fake_gateway = create_test_client()

    with client:
        created = client.post(
            "/api/v1/skills",
            json={
                "key": "repo-browser",
                "name": "Repo Browser",
                "description": "Browse skill source files.",
            },
        ).json()
        skill_id = created["id"]

        tree_response = client.get(f"/api/v1/skills/{skill_id}/repository/tree")
        create_folder_response = client.post(
            f"/api/v1/skills/{skill_id}/repository/folders",
            json={"path": "examples"},
        )
        create_file_response = client.post(
            f"/api/v1/skills/{skill_id}/repository/files",
            json={"path": "examples/demo.md", "content": "# Demo\n"},
        )
        nested_tree_response = client.get(
            f"/api/v1/skills/{skill_id}/repository/tree",
            params={"path": "examples"},
        )
        file_response = client.get(
            f"/api/v1/skills/{skill_id}/repository/files",
            params={"path": "examples/demo.md"},
        )
        save_response = client.put(
            f"/api/v1/skills/{skill_id}/repository/files",
            json={
                "path": "examples/demo.md",
                "content": "# Demo\n\nUpdated.\n",
                "base_commit_sha": file_response.json()["head_commit_sha"],
            },
        )

    assert tree_response.status_code == 200
    assert {entry["name"] for entry in tree_response.json()["entries"]} >= {"README.md", "SKILL.md", "skill.yaml"}

    assert create_folder_response.status_code == 201
    assert fake_gateway.projects[created["gitlab_project_id"]].files["examples/.gitkeep"] == ""

    assert create_file_response.status_code == 201
    assert create_file_response.json()["file_path"] == "examples/demo.md"

    assert nested_tree_response.status_code == 200
    assert {entry["name"] for entry in nested_tree_response.json()["entries"]} >= {".gitkeep", "demo.md"}

    assert file_response.status_code == 200
    assert file_response.json()["content"] == "# Demo\n"

    assert save_response.status_code == 200
    assert save_response.json()["content"].endswith("Updated.\n")


def test_save_skill_source_rejects_stale_commit_sha() -> None:
    client, fake_gateway = create_test_client()

    with client:
        created = client.post(
            "/api/v1/skills",
            json={
                "key": "repair-planner",
                "name": "Repair Planner",
                "description": "Plan repair actions.",
            },
        ).json()
        skill_id = created["id"]
        source_payload = client.get(f"/api/v1/skills/{skill_id}/source").json()
        fake_gateway.commit_skill_source(
            project_id=created["gitlab_project_id"],
            branch=created["default_branch"],
            readme_content=source_payload["readme_content"],
            skill_md_content=source_payload["skill_md_content"],
            skill_yaml_content=source_payload["skill_yaml_content"],
            commit_message="External change",
        )

        save_response = client.put(
            f"/api/v1/skills/{skill_id}/source",
            json={
                "base_commit_sha": source_payload["head_commit_sha"],
                "readme_content": source_payload["readme_content"],
                "skill_md_content": source_payload["skill_md_content"],
                "skill_yaml_content": source_payload["skill_yaml_content"],
            },
        )

    assert save_response.status_code == 409
    error_payload = save_response.json()
    assert error_payload["code"] == "skill_source_conflict"


def test_publish_skill_creates_published_version_and_record() -> None:
    client, _ = create_test_client()

    with client:
        created = client.post(
            "/api/v1/skills",
            json={
                "key": "field-support",
                "name": "Field Support",
                "description": "Support field operators.",
            },
        ).json()
        skill_id = created["id"]

        publish_response = client.post(
            f"/api/v1/skills/{skill_id}/publish",
            json={"publish_reason": "Initial MVP publish"},
        )
        detail_response = client.get(f"/api/v1/skills/{skill_id}")
        publishes_response = client.get(f"/api/v1/skills/{skill_id}/publishes")

    assert publish_response.status_code == 200
    publish_payload = publish_response.json()
    assert publish_payload["published_version"]["status"] == "published"
    assert publish_payload["published_commit_sha"].startswith("commit-")

    detail_payload = detail_response.json()
    assert detail_payload["latest_published_version"]["source_commit_sha"] == publish_payload["published_commit_sha"]

    publishes_payload = publishes_response.json()
    assert len(publishes_payload) == 1
    assert publishes_payload[0]["publish_reason"] == "Initial MVP publish"


def test_delete_skill_requires_name_confirmation_and_archives_gitlab_project() -> None:
    client, fake_gateway = create_test_client()

    with client:
        created = client.post(
            "/api/v1/skills",
            json={
                "key": "delete-me",
                "name": "Delete Me",
                "description": "Archive this project.",
            },
        ).json()
        skill_id = created["id"]

        mismatch_response = client.request(
            "DELETE",
            f"/api/v1/skills/{skill_id}",
            json={"confirmation_name": "Wrong Name"},
        )
        delete_response = client.request(
            "DELETE",
            f"/api/v1/skills/{skill_id}",
            json={"confirmation_name": "Delete Me"},
        )
        list_response = client.get("/api/v1/skills")
        archived_response = client.get("/api/v1/skills?status=archived")

    assert mismatch_response.status_code == 422
    assert mismatch_response.json()["code"] == "skill_validation_error"

    assert delete_response.status_code == 200
    delete_payload = delete_response.json()
    assert delete_payload["status"] == "archived"
    assert fake_gateway.projects[created["gitlab_project_id"]].archived is True

    assert all(skill["id"] != skill_id for skill in list_response.json())
    assert any(skill["id"] == skill_id for skill in archived_response.json())
