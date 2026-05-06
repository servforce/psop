from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field

from fastapi.testclient import TestClient

from app.app import create_app
from app.core.config import Settings
from app.domain.skills.exceptions import SkillsGatewayError
from app.gateway.inference import LlmCompletion
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
        self.fail_get_skill_source = False

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
        if self.fail_get_skill_source:
            raise SkillsGatewayError("GitLab 返回错误响应。")
        project = self.projects[project_id]
        assert ref in {project.default_branch, project.head_commit_sha}
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


class FakeInferenceGateway:
    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    def complete(self, *, system_prompt: str, user_prompt: str, route_key: str = "default") -> LlmCompletion:
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "route_key": route_key,
            }
        )
        if "SKILL 编译智能体" in system_prompt:
            content = json.dumps(build_test_formal_v5_artifact(), ensure_ascii=False)
        else:
            content = f"已处理输入：{user_prompt}"
        return LlmCompletion(
            content=content,
            provider="fake-openai-compatible",
            model="fake-model",
            raw_response={"id": "fake-response"},
        )


def build_test_formal_v5_artifact() -> dict:
    return {
        "artifact_version": "psop-eg-formal-v5/llm-compiler-mvp-v1",
        "formal_revision": "psop-eg-formal/v5",
        "skill": {},
        "schema": {
            "token_fields": [
                "phase",
                "input_envelope",
                "observations",
                "budgets",
                "outputs",
                "control",
                "metadata",
                "facts",
                "registers",
                "memory",
                "trace",
                "status",
            ],
            "input_name": "user_input",
            "output_name": "final_response",
        },
        "nodes": [
            {
                "id": "start",
                "kind": "start",
                "guard": {"phase_is": "start"},
                "actor": {"name": "runtime.start"},
                "merge": [
                    {"op": "set", "path": "observations.start", "from": "observation"},
                    {"op": "set", "path": "phase", "value": "input"},
                ],
                "policy": {"priority": 10},
            },
            {
                "id": "input",
                "kind": "input",
                "guard": {"phase_is": "input"},
                "actor": {"name": "runtime.input"},
                "merge": [
                    {"op": "set", "path": "observations.input", "from": "observation"},
                    {"op": "set", "path": "phase", "value": "collect_context"},
                ],
                "policy": {"priority": 20},
            },
            {
                "id": "collect_context",
                "kind": "llm",
                "guard": {"phase_is": "collect_context"},
                "actor": {"name": "agent.llm"},
                "projection": {
                    "system_template": "你正在执行 PSOP Skill：{{skill.name}} 的【收集上下文】步骤。",
                    "user_template": (
                        "用户输入：{{input.user_input}}\n"
                        "步骤目标：识别用户任务、约束和期望输出。\n"
                        "依据：SKILL.md 要求先理解用户任务。\n"
                        "前序观察：{{token.observations}}"
                    ),
                },
                "merge": [
                    {"op": "set", "path": "observations.collect_context", "from": "observation"},
                    {"op": "set", "path": "phase", "value": "inspect_constraints"},
                ],
                "policy": {"priority": 30},
            },
            {
                "id": "inspect_constraints",
                "kind": "tool",
                "guard": {"phase_is": "inspect_constraints"},
                "actor": {"name": "capability.demo_tool", "tool_name": "psop.demo.inspect_input"},
                "merge": [
                    {"op": "set", "path": "observations.inspect_constraints", "from": "observation"},
                    {"op": "set", "path": "phase", "value": "produce_guidance"},
                ],
                "policy": {"priority": 40},
            },
            {
                "id": "produce_guidance",
                "kind": "llm",
                "guard": {"phase_is": "produce_guidance"},
                "actor": {"name": "agent.llm"},
                "projection": {
                    "system_template": "你正在执行 PSOP Skill：{{skill.name}} 的【产出指导】步骤。",
                    "user_template": (
                        "用户输入：{{input.user_input}}\n"
                        "步骤目标：基于上下文和检查结果生成最终可执行答复。\n"
                        "依据：SKILL.md 要求输出清晰结果。\n"
                        "前序观察：{{token.observations}}"
                    ),
                },
                "merge": [
                    {"op": "set", "path": "observations.produce_guidance", "from": "observation"},
                    {"op": "set", "path": "phase", "value": "terminal"},
                ],
                "policy": {"priority": 50},
            },
            {
                "id": "terminal",
                "kind": "terminal",
                "guard": {"phase_is": "terminal"},
                "actor": {"name": "runtime.terminal"},
                "merge": [
                    {"op": "set", "path": "outputs.final_response", "from": "observation.final_response"},
                    {"op": "set", "path": "status", "value": "success"},
                    {"op": "set", "path": "phase", "value": "completed"},
                ],
                "policy": {"priority": 60},
            },
        ],
        "init": {"entry_node": "start"},
        "halt": {"success": {"field_equals": {"path": "status", "value": "success"}}},
        "policies": {"selection": "priority_then_order", "max_steps": 10},
        "dependency_graph_for_view": [
            {"from": "start", "to": "input"},
            {"from": "input", "to": "collect_context"},
            {"from": "collect_context", "to": "inspect_constraints"},
            {"from": "inspect_constraints", "to": "produce_guidance"},
            {"from": "produce_guidance", "to": "terminal"},
        ],
        "runtime_contract": {
            "llm_route_key": "default",
            "skill_instruction": "遵循 SKILL.md 完成任务。",
            "workflow_steps": [
                {
                    "id": "collect_context",
                    "title": "收集上下文",
                    "goal": "识别用户任务、约束和期望输出。",
                    "source_evidence": "SKILL.md 要求先理解用户任务。",
                },
                {
                    "id": "inspect_constraints",
                    "title": "检查输入约束",
                    "goal": "检查用户输入是否足够支撑执行。",
                    "source_evidence": "SKILL.md 要求在执行前确认输入条件。",
                },
                {
                    "id": "produce_guidance",
                    "title": "产出指导",
                    "goal": "基于上下文和检查结果生成最终可执行答复。",
                    "source_evidence": "SKILL.md 要求输出清晰结果。",
                },
            ],
        },
    }


def create_test_settings() -> Settings:
    return Settings(
        app_name="PSOP Backend Skills Test",
        database_url="sqlite+pysqlite:///:memory:",
        database_check_on_startup=False,
        database_auto_create_schema=True,
        gitlab_skills_group_path="skills",
        runtime_worker_enabled=False,
    )


def create_test_client() -> tuple[TestClient, FakeGitLabGateway, FakeInferenceGateway]:
    fake_gateway = FakeGitLabGateway()
    fake_inference = FakeInferenceGateway()
    client = TestClient(
        create_app(
            create_test_settings(),
            gitlab_gateway=fake_gateway,
            inference_gateway=fake_inference,
        )
    )
    return client, fake_gateway, fake_inference


def test_create_skill_initializes_gitlab_and_persists_metadata() -> None:
    client, fake_gateway, _ = create_test_client()

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
    client, _, _ = create_test_client()

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
        before_detail = client.get(f"/api/v1/skills/{skill_id}").json()
        before_skill_md = before_detail["current_draft_version"]["manifest_snapshot"]["prompt_material"]["skill_md"]

        save_response = client.put(
            f"/api/v1/skills/{skill_id}/source",
            json={
                "base_commit_sha": source_payload["head_commit_sha"],
                "readme_content": source_payload["readme_content"] + "\nUpdated from test.\n",
                "skill_md_content": source_payload["skill_md_content"] + "\n## Validation\n\n- test path\n",
                "skill_yaml_content": "skill:\n  identity:\n    key: tampered-by-user\n",
            },
        )
        after_detail = client.get(f"/api/v1/skills/{skill_id}").json()

    assert save_response.status_code == 200
    saved_payload = save_response.json()
    assert saved_payload["head_commit_sha"] != source_payload["head_commit_sha"]
    assert "Updated from test." in saved_payload["readme_content"]
    assert "tampered-by-user" not in saved_payload["skill_yaml_content"]
    after_snapshot = after_detail["current_draft_version"]["manifest_snapshot"]
    assert "source_digest" not in after_snapshot
    assert after_snapshot["prompt_material"]["skill_md"] != before_skill_md
    assert after_snapshot["prompt_material"]["skill_md"] == saved_payload["skill_md_content"]
    assert after_snapshot["prompt_material"]["readme"] == saved_payload["readme_content"]


def test_repository_tree_file_and_folder_operations() -> None:
    client, fake_gateway, _ = create_test_client()

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
        before_repo_detail = client.get(f"/api/v1/skills/{skill_id}").json()
        skill_md_response = client.get(
            f"/api/v1/skills/{skill_id}/repository/files",
            params={"path": "SKILL.md"},
        )
        skill_md_save_response = client.put(
            f"/api/v1/skills/{skill_id}/repository/files",
            json={
                "path": "SKILL.md",
                "content": skill_md_response.json()["content"] + "\n## Repo Edit\n\n- update core instruction\n",
                "base_commit_sha": skill_md_response.json()["head_commit_sha"],
            },
        )
        after_repo_detail = client.get(f"/api/v1/skills/{skill_id}").json()
        manifest_response = client.get(
            f"/api/v1/skills/{skill_id}/repository/files",
            params={"path": "skill.yaml"},
        )
        manifest_save_response = client.put(
            f"/api/v1/skills/{skill_id}/repository/files",
            json={
                "path": "skill.yaml",
                "content": "skill:\n  identity:\n    key: user-edit\n",
                "base_commit_sha": manifest_response.json()["head_commit_sha"],
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

    assert skill_md_response.status_code == 200
    assert skill_md_save_response.status_code == 200
    before_repo_skill_md = before_repo_detail["current_draft_version"]["manifest_snapshot"]["prompt_material"][
        "skill_md"
    ]
    after_repo_snapshot = after_repo_detail["current_draft_version"]["manifest_snapshot"]
    assert "source_digest" not in after_repo_snapshot
    assert after_repo_snapshot["prompt_material"]["skill_md"] != before_repo_skill_md
    assert after_repo_snapshot["prompt_material"]["skill_md"] == skill_md_save_response.json()["content"]

    assert manifest_response.status_code == 200
    assert manifest_save_response.status_code == 422
    assert manifest_save_response.json()["code"] == "skill_validation_error"


def test_save_skill_source_rejects_stale_commit_sha() -> None:
    client, fake_gateway, _ = create_test_client()

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
    client, _, _ = create_test_client()

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
        publish_payload = publish_response.json()
        compile_request_id = publish_payload["compile_request"]["id"]
        progress_response = client.get(f"/api/v1/compiler/requests/{compile_request_id}/progress")
        compile_response = client.post(f"/api/v1/compiler/requests/{compile_request_id}/retry")
        events_response = client.get(f"/api/v1/compiler/requests/{compile_request_id}/events")
        detail_response = client.get(f"/api/v1/skills/{skill_id}")
        publishes_response = client.get(f"/api/v1/skills/{skill_id}/publishes")

    assert publish_response.status_code == 202
    assert publish_payload["published_version"]["status"] == "published"
    assert publish_payload["published_commit_sha"].startswith("commit-")
    assert publish_payload["publish_record"]["publish_status"] == "compiling"
    assert publish_payload["compile_request"]["status"] == "pending"
    assert progress_response.status_code == 200
    assert progress_response.json()["stages"][0]["key"] == "source_frozen"

    assert compile_response.status_code == 200
    compile_payload = compile_response.json()
    assert compile_payload["status"] == "succeeded"
    assert compile_payload["artifact_id"]
    assert events_response.status_code == 200
    assert "event: publish.terminal" in events_response.text

    detail_payload = detail_response.json()
    assert detail_payload["latest_published_version"]["source_commit_sha"] == publish_payload["published_commit_sha"]

    publishes_payload = publishes_response.json()
    assert len(publishes_payload) == 1
    assert publishes_payload[0]["publish_reason"] == "Initial MVP publish"
    assert publishes_payload[0]["publish_status"] == "published"


def test_manual_compile_request_does_not_publish_draft() -> None:
    client, _, _ = create_test_client()

    with client:
        created = client.post(
            "/api/v1/skills",
            json={
                "key": "manual-compile",
                "name": "Manual Compile",
                "description": "Compile without publishing.",
            },
        ).json()
        skill_id = created["id"]

        compile_response = client.post(f"/api/v1/compiler/skills/{skill_id}/compile")
        compile_payload = compile_response.json()
        compile_request_id = compile_payload["id"]
        progress_response = client.get(f"/api/v1/compiler/requests/{compile_request_id}/progress")
        retry_response = client.post(f"/api/v1/compiler/requests/{compile_request_id}/retry")
        detail_response = client.get(f"/api/v1/skills/{skill_id}")

    assert compile_response.status_code == 202
    assert compile_payload["trigger_type"] == "manual"
    assert compile_payload["status"] == "pending"
    assert progress_response.status_code == 200
    assert progress_response.json()["stages"][-1]["label"] == "完成编译"
    assert retry_response.status_code == 200
    assert retry_response.json()["status"] == "succeeded"
    assert retry_response.json()["artifact_id"]
    assert detail_response.json()["latest_published_version"] is None


def test_publish_skill_records_failed_startup_when_gitlab_fails() -> None:
    client, fake_gateway, _ = create_test_client()

    with client:
        created = client.post(
            "/api/v1/skills",
            json={
                "key": "gitlab-freeze-failure",
                "name": "GitLab Freeze Failure",
                "description": "Validate publish startup failure record.",
            },
        ).json()
        skill_id = created["id"]
        fake_gateway.fail_get_skill_source = True

        publish_response = client.post(
            f"/api/v1/skills/{skill_id}/publish",
            json={"publish_reason": "Expect GitLab failure"},
        )
        publishes_response = client.get(f"/api/v1/skills/{skill_id}/publishes")

    assert publish_response.status_code == 502
    assert publish_response.json()["message"] == "GitLab 返回错误响应。"

    publishes_payload = publishes_response.json()
    assert len(publishes_payload) == 1
    assert publishes_payload[0]["publish_reason"] == "Expect GitLab failure"
    assert publishes_payload[0]["publish_status"] == "failed"
    assert publishes_payload[0]["published_commit_sha"] == created["latest_draft_head_sha"]


def test_issue_1_publish_compile_run_and_replay_vertical_slice() -> None:
    client, _, fake_inference = create_test_client()

    with client:
        created = client.post(
            "/api/v1/skills",
            json={
                "key": "issue-one-demo",
                "name": "Issue One Demo",
                "description": "Validate issue #1 vertical slice.",
            },
        ).json()

        publish_response = client.post(
            f"/api/v1/skills/{created['id']}/publish",
            json={"publish_reason": "Issue #1 acceptance publish"},
        )
        publish_payload = publish_response.json()
        compile_request_id = publish_payload["compile_request"]["id"]
        compile_response = client.post(f"/api/v1/compiler/requests/{compile_request_id}/retry")
        compile_payload = compile_response.json()
        artifact_id = compile_payload["artifact_id"]

        artifact_response = client.get(f"/api/v1/compiler/artifacts/{artifact_id}")
        edited_artifact = copy.deepcopy(artifact_response.json()["artifact"])
        edited_artifact["runtime_contract"]["workflow_steps"][0]["title"] = "人工修订上下文收集"
        update_artifact_response = client.put(
            f"/api/v1/compiler/artifacts/{artifact_id}",
            json={"artifact": edited_artifact},
        )
        invalid_artifact = copy.deepcopy(edited_artifact)
        invalid_artifact.pop("nodes")
        invalid_update_response = client.put(
            f"/api/v1/compiler/artifacts/{artifact_id}",
            json={"artifact": invalid_artifact},
        )
        invocation_response = client.post(
            "/api/v1/gateway/invocations",
            json={
                "skill_key": "issue-one-demo",
                "input_envelope": {"user_input": "请检查泵站压力异常？"},
                "gateway_type": "web",
            },
        )
        invocation_payload = invocation_response.json()
        run_id = invocation_payload["run_id"]

        run_response = client.get(f"/api/v1/runs/{run_id}")
        trace_response = client.get(f"/api/v1/runs/{run_id}/trace-events")
        binding_requirements_response = client.get(f"/api/v1/runs/{run_id}/binding-requirements")
        bindings_response = client.get(f"/api/v1/runs/{run_id}/bindings")
        terminal_session_response = client.get(f"/api/v1/terminal/sessions/{run_id}")
        terminal_events_response = client.get(f"/api/v1/terminal/sessions/{run_id}/events")
        replay_response = client.get(f"/api/v1/replay/runs/{run_id}")
        terminal_append_response = client.post(
            f"/api/v1/terminal/sessions/{run_id}/events",
            json={
                "direction": "input",
                "event_kind": "terminal.text.input.v1",
                "mime_type": "text/plain",
                "payload_inline": "追加现场确认",
                "external_event_id": "issue-one-demo-extra-input",
            },
        )
        terminal_events_after_append_response = client.get(f"/api/v1/terminal/sessions/{run_id}/events")
        jobs_response = client.get("/api/v1/runtime/jobs")

    assert publish_response.status_code == 202
    assert publish_payload["compile_request"]["status"] == "pending"
    assert compile_response.status_code == 200
    assert compile_payload["status"] == "succeeded"
    assert artifact_response.status_code == 200
    assert update_artifact_response.status_code == 200
    assert (
        update_artifact_response.json()["artifact"]["runtime_contract"]["workflow_steps"][0]["title"]
        == "人工修订上下文收集"
    )
    assert invalid_update_response.status_code == 422
    assert invalid_update_response.json()["details"]["diagnostics"][0]["code"] == "compile.formal_v5.validation_failed"
    assert artifact_response.json()["graph_summary"]["nodes"] == [
        "start",
        "input",
        "collect_context",
        "inspect_constraints",
        "produce_guidance",
        "terminal",
    ]
    assert artifact_response.json()["graph_summary"]["workflow_nodes"] == [
        "collect_context",
        "inspect_constraints",
        "produce_guidance",
    ]

    assert invocation_response.status_code == 201
    assert invocation_payload["status"] == "succeeded"
    assert invocation_payload["gateway_type"] == "terminal"
    assert invocation_payload["terminal_session_id"]
    run_payload = run_response.json()
    assert run_payload["status"] == "succeeded"
    assert run_payload["terminal_session_id"] == invocation_payload["terminal_session_id"]
    assert run_payload["latest_terminal_seq"] == 2
    assert run_payload["latest_trace_seq"] == 7
    assert len(run_payload["binding_summary"]) == 2
    assert "已处理输入" in run_payload["final_output"]
    assert fake_inference.calls[-1]["user_prompt"].startswith("用户输入：请检查泵站压力异常？")

    event_types = [event["event_type"] for event in trace_response.json()]
    assert event_types == [
        "binding.resolved",
        "runtime.start.completed",
        "runtime.input.accepted",
        "gateway.inference.completed",
        "gateway.tool.completed",
        "gateway.inference.completed",
        "runtime.final.completed",
    ]

    assert binding_requirements_response.status_code == 200
    assert {item["requirement_key"] for item in binding_requirements_response.json()} == {
        "terminal.input",
        "terminal.output",
    }
    assert bindings_response.status_code == 200
    assert {item["target_kind"] for item in bindings_response.json()} == {"web_terminal"}
    assert terminal_session_response.status_code == 200
    assert terminal_session_response.json()["terminal_session"]["id"] == invocation_payload["terminal_session_id"]
    assert terminal_events_response.status_code == 200
    assert [item["direction"] for item in terminal_events_response.json()] == ["input", "output"]
    assert terminal_append_response.status_code == 202
    assert terminal_append_response.json()["seq_no"] == 3
    assert [item["seq_no"] for item in terminal_events_after_append_response.json()] == [1, 2, 3]

    replay_payload = replay_response.json()
    assert [item["title"] for item in replay_payload["timeline"]] == [
        "终端输入",
        "绑定解析",
        "runtime.start.completed",
        "输入",
        "LLM 输出",
        "工具调用",
        "LLM 输出",
        "终端输出",
        "最终结果",
    ]
    assert len(replay_payload["terminal_events"]) == 2
    assert len(replay_payload["bindings"]) == 2
    assert replay_payload["run"]["final_output"] == run_payload["final_output"]

    jobs = jobs_response.json()
    assert {job["job_type"] for job in jobs} >= {"compile", "runtime"}
    assert all(job["status"] == "succeeded" for job in jobs)


def test_run_websocket_broadcasts_terminal_event_append() -> None:
    client, _, _ = create_test_client()

    with client:
        created = client.post(
            "/api/v1/skills",
            json={
                "key": "ws-terminal-demo",
                "name": "WS Terminal Demo",
                "description": "Validate terminal websocket broadcast.",
            },
        ).json()
        publish_response = client.post(
            f"/api/v1/skills/{created['id']}/publish",
            json={"publish_reason": "WS smoke publish"},
        )
        compile_request_id = publish_response.json()["compile_request"]["id"]
        client.post(f"/api/v1/compiler/requests/{compile_request_id}/retry")
        invocation_response = client.post(
            "/api/v1/gateway/invocations",
            json={
                "skill_key": "ws-terminal-demo",
                "input_envelope": {"user_input": "启动 WS 验证"},
                "gateway_type": "terminal",
                "terminal_context": {"terminal_kind": "web"},
            },
        )
        run_id = invocation_response.json()["run_id"]

        with client.websocket_connect(f"/ws/runs/{run_id}") as websocket:
            connected = websocket.receive_json()
            append_response = client.post(
                f"/api/v1/terminal/sessions/{run_id}/events",
                json={
                    "direction": "input",
                    "event_kind": "terminal.text.input.v1",
                    "mime_type": "text/plain",
                    "payload_inline": "WS 输入",
                    "external_event_id": "ws-terminal-demo-input",
                },
            )
            message = websocket.receive_json()

    assert invocation_response.status_code == 201
    assert connected["event_type"] == "ws.connected"
    assert append_response.status_code == 202
    assert message["event_type"] == "terminal.event.appended"
    assert message["payload"]["payload_inline"] == "WS 输入"
    assert message["seq_no"] == append_response.json()["seq_no"]


def test_delete_skill_requires_name_confirmation_and_archives_gitlab_project() -> None:
    client, fake_gateway, _ = create_test_client()

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
