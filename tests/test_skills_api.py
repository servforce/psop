from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.app import create_app
from app.core.config import Settings
from app.domain.skill_tests.service import SkillTestService
from app.domain.skills.exceptions import SkillsGatewayError
from app.gateway.inference import LlmCompletion
from app.infra.object_store import StoredObject
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
        elif route_key == "skill-test-judge":
            content = json.dumps(
                {
                    "status": "passed",
                    "confidence": 0.93,
                    "reason": "实际输出满足预期语义。",
                    "evidence_refs": [{"kind": "terminal_event", "seq_no": 4}],
                    "missing_evidence": "",
                },
                ensure_ascii=False,
            )
        elif "final_verify" in system_prompt or "final_verify" in user_prompt:
            content = json.dumps(
                {
                    "decision": "complete",
                    "reason": "最终完成标准已验证。",
                    "next_phase": "terminal",
                    "terminal_message": "测试任务已完成，现场步骤已验证。",
                },
                ensure_ascii=False,
            )
        elif "只输出 JSON decision" in system_prompt or "JSON decision" in user_prompt:
            content = json.dumps(
                {
                    "decision": "proceed",
                    "reason": "现场证据满足当前步骤完成标准。",
                    "next_phase": "final_verify",
                    "terminal_message": "已确认这一步完成，继续最终核验。",
                },
                ensure_ascii=False,
            )
        else:
            content = "请先完成当前现实步骤，并提交文本、图片或文件作为现场证据。"
        return LlmCompletion(
            content=content,
            provider="fake-openai-compatible",
            model="fake-model",
            raw_response={"id": "fake-response"},
            usage={
                "input_tokens": 10,
                "output_tokens": 5,
                "total_tokens": 15,
                "raw": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            },
        )


class FakeObjectStore:
    def __init__(self) -> None:
        self.uploads: list[dict[str, object]] = []

    def upload_bytes(
        self,
        *,
        object_key: str,
        content: bytes,
        media_type: str,
        metadata: dict[str, str] | None = None,
    ) -> StoredObject:
        self.uploads.append(
            {
                "object_key": object_key,
                "content": content,
                "media_type": media_type,
                "metadata": metadata or {},
            }
        )
        return StoredObject(
            bucket="test-bucket",
            object_key=object_key,
            media_type=media_type,
            size_bytes=len(content),
            checksum=f"sha256-{len(content)}",
            metadata=metadata or {},
        )


class FailingObjectStore(FakeObjectStore):
    def upload_bytes(self, **_) -> StoredObject:
        raise RuntimeError("object store offline")


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
                "terminal",
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
                    {"op": "set", "path": "phase", "value": "instruct_collect_context"},
                ],
                "policy": {"priority": 10},
            },
            {
                "id": "instruct_collect_context",
                "kind": "llm",
                "guard": {"phase_is": "instruct_collect_context"},
                "actor": {"name": "agent.llm"},
                "interaction": {
                    "output_to_terminal": True,
                    "wait_after_output": True,
                    "checkpoint_id": "collect_context_evidence",
                    "workflow_step_id": "collect_context",
                    "wait_reason": "等待用户提交当前真实场景的说明或多模态证据。",
                    "expected_inputs": [
                        {"kind": "text", "event_kind": "terminal.text.input.v1"},
                        {"kind": "image", "event_kind": "terminal.image.input.v1"},
                        {"kind": "file", "event_kind": "terminal.file.input.v1"},
                    ],
                    "resume_phase": "evaluate_collect_context",
                },
                "projection": {
                    "system_template": "输出当前现实步骤指令。collect_context",
                    "user_template": (
                        "步骤目标：识别用户任务、约束和期望输出。\n"
                        "依据：SKILL.md 要求先理解用户任务。\n"
                        "当前 Token：{{token}}"
                    ),
                },
                "merge": [
                    {"op": "set", "path": "observations.instruct_collect_context", "from": "observation"},
                ],
                "policy": {"priority": 20},
            },
            {
                "id": "evaluate_collect_context",
                "kind": "llm",
                "guard": {"phase_is": "evaluate_collect_context"},
                "actor": {"name": "agent.llm"},
                "interaction": {"evaluation": True},
                "projection": {
                    "system_template": "只输出 JSON decision。evaluate_collect_context",
                    "user_template": (
                        "根据 token.control.wait.evidence 判断 collect_context 是否完成。\n"
                        "必须输出 JSON decision。当前 Token：{{token}}"
                    ),
                },
                "merge": [
                    {"op": "set", "path": "observations.evaluate_collect_context", "from": "observation"},
                    {"op": "set", "path": "phase", "from": "observation.next_phase"},
                ],
                "policy": {"priority": 30},
            },
            {
                "id": "final_verify",
                "kind": "llm",
                "guard": {"phase_is": "final_verify"},
                "actor": {"name": "agent.llm"},
                "interaction": {"evaluation": True},
                "projection": {
                    "system_template": "只输出 JSON decision。final_verify",
                    "user_template": "根据 completion_criteria 与当前 Token 做最终验证。当前 Token：{{token}}",
                },
                "merge": [
                    {"op": "set", "path": "observations.final_verify", "from": "observation"},
                    {"op": "set", "path": "phase", "from": "observation.next_phase"},
                    {"op": "set", "path": "outputs.final_response", "from": "observation.terminal_message"},
                ],
                "policy": {"priority": 40},
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
                "policy": {"priority": 50},
            },
        ],
        "init": {"entry_node": "start"},
        "halt": {"success": {"field_equals": {"path": "status", "value": "success"}}},
        "policies": {"selection": "priority_then_order", "max_steps": 10},
        "dependency_graph_for_view": [
            {"from": "start", "to": "instruct_collect_context"},
            {"from": "instruct_collect_context", "to": "evaluate_collect_context"},
            {"from": "evaluate_collect_context", "to": "final_verify"},
            {"from": "final_verify", "to": "terminal"},
        ],
        "runtime_contract": {
            "llm_route_key": "default",
            "skill_instruction": "遵循 SKILL.md 完成任务。",
            "execution_goal": "帮助用户在现实世界完成当前 Skill 目标。",
            "applicability": {
                "applies_when": ["用户处在真实任务现场并可提交证据。"],
                "does_not_apply_when": ["任务存在不可控安全风险或用户无法提供现场反馈。"],
            },
            "workflow_steps": [
                {
                    "id": "collect_context",
                    "title": "收集上下文",
                    "goal": "识别用户任务、约束和期望输出。",
                    "source_evidence": "SKILL.md 要求先理解用户任务。",
                },
            ],
            "expected_evidence": {
                "collect_context": [
                    {"kind": "text", "event_kind": "terminal.text.input.v1"},
                    {"kind": "image", "event_kind": "terminal.image.input.v1"},
                    {"kind": "file", "event_kind": "terminal.file.input.v1"},
                ]
            },
            "safety_constraints": ["如果用户证据显示存在安全风险，应中止或要求人工介入。"],
            "wait_checkpoints": [
                {
                    "checkpoint_id": "collect_context_evidence",
                    "workflow_step_id": "collect_context",
                    "expected_inputs": [
                        {"kind": "text"},
                        {"kind": "image"},
                        {"kind": "file"},
                    ],
                }
            ],
            "completion_criteria": ["所有必须的现实步骤已经由证据验证完成。"],
            "recovery_paths": [{"when": "evidence_insufficient", "action": "request_more_evidence"}],
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
    fake_object_store = FakeObjectStore()
    client = TestClient(
        create_app(
            create_test_settings(),
            gitlab_gateway=fake_gateway,
            inference_gateway=fake_inference,
            object_store=fake_object_store,
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

        initial_run_response = client.get(f"/api/v1/runs/{run_id}")
        binding_requirements_response = client.get(f"/api/v1/runs/{run_id}/binding-requirements")
        bindings_response = client.get(f"/api/v1/runs/{run_id}/bindings")
        terminal_session_response = client.get(f"/api/v1/terminal/sessions/{run_id}")
        terminal_events_response = client.get(f"/api/v1/terminal/sessions/{run_id}/events")
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
        run_response = client.get(f"/api/v1/runs/{run_id}")
        trace_response = client.get(f"/api/v1/runs/{run_id}/trace-events")
        terminal_events_after_append_response = client.get(f"/api/v1/terminal/sessions/{run_id}/events")
        replay_response = client.get(f"/api/v1/replay/runs/{run_id}")
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
        "instruct_collect_context",
        "evaluate_collect_context",
        "final_verify",
        "terminal",
    ]
    assert artifact_response.json()["graph_summary"]["workflow_nodes"] == [
        "instruct_collect_context",
        "evaluate_collect_context",
        "final_verify",
    ]

    assert invocation_response.status_code == 201
    assert invocation_payload["status"] == "running"
    assert invocation_payload["gateway_type"] == "terminal"
    assert invocation_payload["terminal_session_id"]
    initial_run_payload = initial_run_response.json()
    assert initial_run_payload["status"] == "waiting_input"
    assert initial_run_payload["current_step"] == "collect_context"
    assert initial_run_payload["checkpoint_id"] == "collect_context_evidence"
    run_payload = run_response.json()
    assert run_payload["status"] == "succeeded"
    assert run_payload["terminal_session_id"] == invocation_payload["terminal_session_id"]
    assert run_payload["latest_terminal_seq"] == 6
    assert run_payload["latest_trace_seq"] == 7
    assert len(run_payload["binding_summary"]) == 2
    assert "测试任务已完成" in run_payload["final_output"]
    assert "final_verify" in fake_inference.calls[-1]["system_prompt"]

    event_types = [event["event_type"] for event in trace_response.json()]
    assert event_types == [
        "binding.resolved",
        "runtime.start.completed",
        "runtime.wait_checkpoint.entered",
        "gateway.inference.completed",
        "gateway.inference.completed",
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
    assert [item["seq_no"] for item in terminal_events_after_append_response.json()] == [1, 2, 3, 4, 5, 6]

    replay_payload = replay_response.json()
    assert [item["title"] for item in replay_payload["timeline"]][:6] == [
        "终端输入",
        "绑定解析",
        "runtime.start.completed",
        "终端输出",
        "等待现场证据",
        "LLM 输出",
    ]
    assert len(replay_payload["terminal_events"]) == 6
    assert len(replay_payload["bindings"]) == 2
    assert replay_payload["run"]["final_output"] == run_payload["final_output"]

    jobs = jobs_response.json()
    assert {job["job_type"] for job in jobs} >= {"compile", "runtime"}
    assert all(job["status"] == "succeeded" for job in jobs)


def test_skill_debug_invocation_uses_runtime_without_skill_test_case() -> None:
    client, _, _ = create_test_client()

    with client:
        created = client.post(
            "/api/v1/skills",
            json={
                "key": "skill-debug-terminal",
                "name": "Skill Debug Terminal",
                "description": "Validate direct skill debug terminal flow.",
            },
        ).json()
        publish_response = client.post(
            f"/api/v1/skills/{created['id']}/publish",
            json={"publish_reason": "Debug terminal publish"},
        )
        compile_request_id = publish_response.json()["compile_request"]["id"]
        client.post(f"/api/v1/compiler/requests/{compile_request_id}/retry")

        invocation_response = client.post(
            "/api/v1/gateway/invocations",
            json={
                "skill_key": "skill-debug-terminal",
                "version_selector": "latest",
                "gateway_type": "terminal",
                "terminal_context": {
                    "terminal_kind": "web",
                    "operator_mode": "debug",
                    "debug_context": {
                        "kind": "skill_debug",
                        "skill_id": created["id"],
                    },
                },
                "input_envelope": {"user_input": "启动模拟终端调试"},
            },
        )
        invocation = invocation_response.json()
        run_id = invocation["run_id"]
        persisted_invocation_response = client.get(f"/api/v1/gateway/invocations/{invocation['id']}")
        initial_run_response = client.get(f"/api/v1/runs/{run_id}")
        upload_response = client.post(
            f"/api/v1/terminal/sessions/{run_id}/files",
            data={"caption": "现场证据已确认"},
            files={"file": ("debug-photo.png", b"debug-image", "image/png")},
        )
        final_run_response = client.get(f"/api/v1/runs/{run_id}")
        terminal_events_response = client.get(f"/api/v1/terminal/sessions/{run_id}/events")
        replay_response = client.get(f"/api/v1/replay/runs/{run_id}")
        old_cases_response = client.get(f"/api/v1/skills/{created['id']}/test-cases", params={"mode": "debug"})
        test_jobs_response = client.get("/api/v1/runtime/jobs", params={"job_type": "skill_test_timeline_driver"})

    assert invocation_response.status_code == 201
    assert invocation["run_id"]
    assert invocation["terminal_context"]["operator_mode"] == "debug"
    assert invocation["terminal_context"]["debug_context"] == {
        "kind": "skill_debug",
        "skill_id": created["id"],
    }
    persisted_context = persisted_invocation_response.json()["terminal_context"]
    assert persisted_context["operator_mode"] == "debug"
    assert persisted_context["debug_context"]["kind"] == "skill_debug"
    assert initial_run_response.json()["status"] == "waiting_input"
    assert upload_response.status_code == 202
    assert upload_response.json()["event"]["event_kind"] == "terminal.image.input.v1"
    assert upload_response.json()["event"]["mime_type"] == "image/png"
    assert len(upload_response.json()["event"]["artifact_object_id"]) == 36
    assert upload_response.json()["event"]["payload_inline"]["object_key"].startswith(f"terminal-uploads/{run_id}/")
    assert upload_response.json()["event"]["payload_inline"]["caption"] == "现场证据已确认"
    assert final_run_response.json()["status"] == "succeeded"
    assert any(event["event_kind"] == "terminal.image.input.v1" for event in terminal_events_response.json())
    assert replay_response.status_code == 200
    assert replay_response.json()["run"]["id"] == run_id
    assert len(replay_response.json()["terminal_events"]) >= 3
    assert old_cases_response.status_code == 404
    assert test_jobs_response.status_code == 200
    assert test_jobs_response.json() == []


def test_terminal_file_upload_returns_json_error_when_object_store_unavailable() -> None:
    fake_gateway = FakeGitLabGateway()
    fake_inference = FakeInferenceGateway()
    client = TestClient(
        create_app(
            create_test_settings(),
            gitlab_gateway=fake_gateway,
            inference_gateway=fake_inference,
            object_store=FailingObjectStore(),
        )
    )

    with client:
        created = client.post(
            "/api/v1/skills",
            json={
                "key": "terminal-upload-object-store-failure",
                "name": "Terminal Upload Object Store Failure",
                "description": "Validate upload failure is surfaced as JSON.",
            },
        ).json()
        publish_response = client.post(
            f"/api/v1/skills/{created['id']}/publish",
            json={"publish_reason": "Upload failure publish"},
        )
        compile_request_id = publish_response.json()["compile_request"]["id"]
        client.post(f"/api/v1/compiler/requests/{compile_request_id}/retry")
        invocation_response = client.post(
            "/api/v1/gateway/invocations",
            json={
                "skill_key": "terminal-upload-object-store-failure",
                "gateway_type": "terminal",
                "terminal_context": {"terminal_kind": "web", "operator_mode": "debug"},
                "input_envelope": {},
            },
        )
        run_id = invocation_response.json()["run_id"]
        upload_response = client.post(
            f"/api/v1/terminal/sessions/{run_id}/files",
            data={"caption": "图片证据"},
            files={"file": ("fault.jpg", b"image-bytes", "image/jpeg")},
        )

    assert upload_response.status_code == 502
    payload = upload_response.json()
    assert payload["code"] == "skills_gateway_error"
    assert "对象存储" in payload["message"]
    assert payload["details"]["filename"] == "fault.jpg"


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


def test_skill_test_scenario_asset_timeline_run_review_and_fork() -> None:
    client, _, fake_inference = create_test_client()

    timeline = {
        "schema_version": "psop-skill-test-timeline/v1",
        "duration_ms": 5000,
        "lanes": [
            {"id": "input.text", "kind": "input", "label": "文本"},
            {"id": "input.image", "kind": "input", "label": "图片"},
            {"id": "expected.semantic", "kind": "output", "label": "语义输出"},
        ],
        "events": [
            {
                "id": "initial_fault_context",
                "lane_id": "input.text",
                "at_ms": 0,
                "event_kind": "terminal.text.input.v1",
                "mime_type": "text/plain",
                "payload_inline": "请检查这把伞如何修复",
            },
            {
                "id": "expect_completion",
                "lane_id": "expected.semantic",
                "at_ms": 0,
                "expectation": "系统应确认现场步骤已完成。",
            },
        ],
    }

    with client:
        created = client.post(
            "/api/v1/skills",
            json={
                "key": "skill-test-scenario",
                "name": "Skill Test Scenario",
                "description": "Validate black-box timeline scenario flow.",
            },
        ).json()
        publish_response = client.post(
            f"/api/v1/skills/{created['id']}/publish",
            json={"publish_reason": "Scenario test publish"},
        )
        compile_request_id = publish_response.json()["compile_request"]["id"]
        client.post(f"/api/v1/compiler/requests/{compile_request_id}/retry")

        scenario_response = client.post(
            f"/api/v1/skills/{created['id']}/test-scenarios",
            json={
                "name": "雨伞维修时序场景",
                "description": "时间轴驱动输入，时间点以前判断输出。",
                "duration_ms": 5000,
                "timeline": timeline,
                "judge_policy": {"route_key": "skill-test-judge", "confidence_threshold": 0.7},
            },
        )
        scenario = scenario_response.json()
        upload_response = client.post(
            f"/api/v1/skills/{created['id']}/test-scenarios/{scenario['id']}/assets",
            data={"name": "伞骨图片", "description": "测试图片", "lane_id": "input.image"},
            files={"file": ("umbrella.png", b"fake-image", "image/png")},
        )
        assets_response = client.get(f"/api/v1/skills/{created['id']}/test-scenarios/{scenario['id']}/assets")
        old_case_response = client.get(f"/api/v1/skills/{created['id']}/test-cases")
        old_runs_response = client.get("/api/v1/skill-test-runs/not-found")

        start_response = client.post(f"/api/v1/skills/{created['id']}/test-scenarios/{scenario['id']}/runs", json={})
        scenario_run = start_response.json()
        terminal_events_response = client.get(f"/api/v1/terminal/sessions/{scenario_run['run_id']}/events")
        jobs_response = client.get("/api/v1/runtime/jobs")
        evaluate_response = client.post(f"/api/v1/skill-test-scenario-runs/{scenario_run['id']}/evaluate")
        review_response = client.get(f"/api/v1/skill-test-scenario-runs/{scenario_run['id']}/review")
        list_response = client.get(f"/api/v1/skills/{created['id']}/test-scenarios")
        runs_response = client.get(f"/api/v1/skills/{created['id']}/test-scenarios/{scenario['id']}/runs")

        review = review_response.json()
        cursor = review["cursor_anchors"][-1]
        fork_response = client.post(
            f"/api/v1/skill-test-scenario-runs/{scenario_run['id']}/fork-scenario",
            json={"cursor": cursor, "name": "从切面继续的场景"},
        )
        fork_debug_response = client.post(
            f"/api/v1/skill-test-scenario-runs/{scenario_run['id']}/fork-debug",
            json={"cursor": cursor},
        )

    assert scenario_response.status_code == 201
    assert scenario["timeline"]["schema_version"] == "psop-skill-test-timeline/v1"
    assert upload_response.status_code == 201
    assert upload_response.json()["mime_type"] == "image/png"
    assert assets_response.json()[0]["id"] == upload_response.json()["id"]
    assert old_case_response.status_code == 404
    assert old_runs_response.status_code == 404

    assert start_response.status_code == 202
    assert scenario_run["driver_status"] == "completed"
    assert scenario_run["driver_cursor"] == 1
    assert scenario_run["result_summary"]["total"] == 1
    assert scenario_run["result_summary"]["passed"] == 1
    assert scenario_run["status"] == "passed"
    assert [event["event_id"] for event in scenario_run["driver_events"]] == ["initial_fault_context"]

    terminal_events = terminal_events_response.json()
    scripted_inputs = [event for event in terminal_events if event["direction"] == "input"]
    assert [event["payload_inline"] for event in scripted_inputs] == ["请检查这把伞如何修复"]
    assert scripted_inputs[0]["external_event_id"] == (
        f"skill-test-scenario-run:{scenario_run['id']}:timeline:initial_fault_context"
    )
    assert any(event["direction"] == "output" and "测试任务已完成" in str(event["payload_inline"]) for event in terminal_events)
    assert any(job["job_type"] == "skill_test_timeline_driver" and job["status"] == "succeeded" for job in jobs_response.json())
    assert any(call["route_key"] == "skill-test-judge" for call in fake_inference.calls)

    assert evaluate_response.status_code == 200
    assert evaluate_response.json()["status"] == "passed"
    assert review_response.status_code == 200
    assert review["scenario"]["id"] == scenario["id"]
    assert review["scenario_run"]["id"] == scenario_run["id"]
    assert review["expectation_evaluations"][0]["expectation_id"] == "expect_completion"
    assert review["expectation_evaluations"][0]["status"] == "passed"
    judge_raw_response = review["expectation_evaluations"][0]["raw_response"]
    assert judge_raw_response["request"]["route_key"] == "skill-test-judge"
    assert judge_raw_response["request"]["prompt_payload"]["expectation"] == "系统应确认现场步骤已完成。"
    assert judge_raw_response["request"]["prompt_payload"]["run_status"] == "succeeded"
    assert judge_raw_response["request"]["user_prompt"] == json.dumps(
        judge_raw_response["request"]["prompt_payload"],
        ensure_ascii=False,
        sort_keys=True,
    )
    assert judge_raw_response["parsed"]["status"] == "passed"
    assert judge_raw_response["content"]
    assert review["replay_timeline"]
    assert list_response.json()[0]["latest_run"]["id"] == scenario_run["id"]
    assert runs_response.json()[0]["id"] == scenario_run["id"]

    assert fork_response.status_code == 201
    forked = fork_response.json()
    assert forked["fork_seed"]["source_scenario_run_id"] == scenario_run["id"]
    assert forked["fork_seed"]["terminal_seq"] == cursor["terminal_seq"]
    assert fork_debug_response.status_code == 201
    assert fork_debug_response.json()["terminal_context"]["operator_mode"] == "debug"
    assert fork_debug_response.json()["terminal_context"]["debug_context"]["kind"] == "skill_debug"


def test_skill_test_scenario_fork_uses_selected_timeline_time() -> None:
    client, _, _ = create_test_client()

    timeline = {
        "schema_version": "psop-skill-test-timeline/v1",
        "duration_ms": 10000,
        "lanes": [
            {"id": "input.text", "kind": "input", "label": "文本"},
            {"id": "expected.semantic", "kind": "output", "label": "语义输出"},
        ],
        "events": [
            {
                "id": "early_input",
                "lane_id": "input.text",
                "at_ms": 1000,
                "payload_inline": "早期输入",
            },
            {
                "id": "middle_input",
                "lane_id": "input.text",
                "at_ms": 3000,
                "payload_inline": "中段输入",
            },
            {
                "id": "late_input",
                "lane_id": "input.text",
                "at_ms": 8000,
                "payload_inline": "后续输入",
            },
            {
                "id": "expect_after_late",
                "lane_id": "expected.semantic",
                "at_ms": 9000,
                "expectation": "系统应处理后续输入。",
            },
        ],
    }

    with client:
        created = client.post(
            "/api/v1/skills",
            json={
                "key": "skill-test-scenario-fork-selected-time",
                "name": "Skill Test Scenario Fork Selected Time",
                "description": "Validate forked timeline respects the selected review playhead.",
            },
        ).json()
        publish_response = client.post(
            f"/api/v1/skills/{created['id']}/publish",
            json={"publish_reason": "Scenario fork selected time publish"},
        )
        compile_request_id = publish_response.json()["compile_request"]["id"]
        client.post(f"/api/v1/compiler/requests/{compile_request_id}/retry")
        scenario_response = client.post(
            f"/api/v1/skills/{created['id']}/test-scenarios",
            json={
                "name": "按选中时间 Fork 的场景",
                "duration_ms": 10000,
                "timeline": timeline,
            },
        )
        scenario = scenario_response.json()
        start_response = client.post(f"/api/v1/skills/{created['id']}/test-scenarios/{scenario['id']}/runs", json={})
        scenario_run = start_response.json()
        fork_response = client.post(
            f"/api/v1/skill-test-scenario-runs/{scenario_run['id']}/fork-scenario",
            json={"cursor": {"time_ms": 4000, "terminal_seq": 7, "snapshot_seq": 3}, "name": "从 4s 继续"},
        )

    assert scenario_response.status_code == 201
    assert start_response.status_code == 202
    assert fork_response.status_code == 201

    forked = fork_response.json()
    assert forked["duration_ms"] == 6000
    assert forked["fork_seed"]["time_ms"] == 4000
    assert forked["fork_seed"]["terminal_seq"] == 7
    assert [(event["id"], event["at_ms"]) for event in forked["timeline"]["events"]] == [
        ("fork_late_input", 4000),
        ("fork_expect_after_late", 5000),
    ]
    assert [event["payload_inline"] for event in forked["timeline"]["events"] if event["lane_id"] == "input.text"] == ["后续输入"]


def test_skill_test_judge_prompt_compacts_large_outputs() -> None:
    old_payload = "old-output-" * 5000
    recent_payload = "recent-output-" * 5000
    payload = SkillTestService._build_judge_prompt_payload(
        expectation={"expectation": "判断是否已经引导用户完成下一步。"},
        scoped_outputs=[
            {
                "seq_no": 1,
                "occurred_at": "2026-05-13T00:00:01+00:00",
                "event_kind": "terminal.text.output.v1",
                "mime_type": "text/plain",
                "payload_inline": old_payload,
            },
            {
                "seq_no": 2,
                "occurred_at": "2026-05-13T00:00:02+00:00",
                "event_kind": "terminal.text.output.v1",
                "mime_type": "text/plain",
                "payload_inline": recent_payload,
            },
        ],
        final_output="final-output-" * 5000,
        run_status="succeeded",
        cutoff=datetime(2026, 5, 13, tzinfo=timezone.utc),
        policy={
            "transcript_budget_chars": 5000,
            "event_budget_chars": 2000,
            "final_output_budget_chars": 1000,
        },
    )

    prompt_json = json.dumps(payload, ensure_ascii=False)

    assert payload["terminal_output_count_before_cutoff"] == 2
    assert payload["terminal_outputs_before_cutoff"]
    assert payload["input_compaction"]["terminal_output_count"] == 2
    assert payload["input_compaction"]["transcript_budget_chars"] == 5000
    assert payload["input_compaction"]["final_output_truncated"] is True
    assert any(item["payload_truncated"] for item in payload["terminal_outputs_before_cutoff"])
    assert len(prompt_json) < 10000
    assert old_payload not in prompt_json
    assert recent_payload not in prompt_json


def test_skill_test_scenario_rejects_duplicate_open_run() -> None:
    client, _, _ = create_test_client()

    with client:
        created = client.post(
            "/api/v1/skills",
            json={
                "key": "skill-test-scenario-duplicate",
                "name": "Skill Test Scenario Duplicate",
                "description": "Validate active scenario run conflict.",
            },
        ).json()
        publish_response = client.post(
            f"/api/v1/skills/{created['id']}/publish",
            json={"publish_reason": "Scenario duplicate publish"},
        )
        compile_request_id = publish_response.json()["compile_request"]["id"]
        client.post(f"/api/v1/compiler/requests/{compile_request_id}/retry")
        scenario_response = client.post(
            f"/api/v1/skills/{created['id']}/test-scenarios",
            json={
                "name": "等待后再输入",
                "duration_ms": 2000,
                "timeline": {
                    "duration_ms": 2000,
                    "lanes": [
                        {"id": "input.text", "kind": "input"},
                        {"id": "expected.semantic", "kind": "output"},
                    ],
                    "events": [
                        {
                            "id": "late_input",
                            "lane_id": "input.text",
                            "at_ms": 1500,
                            "payload_inline": "稍后输入",
                        }
                    ],
                },
            },
        )
        scenario_id = scenario_response.json()["id"]
        start_response = client.post(f"/api/v1/skills/{created['id']}/test-scenarios/{scenario_id}/runs", json={})
        duplicate_response = client.post(f"/api/v1/skills/{created['id']}/test-scenarios/{scenario_id}/runs", json={})

    assert start_response.status_code == 202
    assert start_response.json()["driver_status"] == "waiting_time"
    assert duplicate_response.status_code == 409
    assert duplicate_response.json()["details"]["scenario_run_id"] == start_response.json()["id"]


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
