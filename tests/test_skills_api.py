from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.app import create_app
from app.compiler.models import ArtifactObject
from app.core.config import Settings
from app.jobs.types import (
    LEGACY_SKILL_TEST_TIMELINE_DRIVER_JOB_TYPE,
    PSKILL_COMPILE_JOB_TYPE,
    PSKILL_TEST_JOB_TYPE,
    RUNTIME_STEP_JOB_TYPE,
)
from app.pskills import materials
from app.pskills import video_analysis
from app.pskills import service as skills_service_module
from app.pskills.models import PSkillMaterial, PSkillMaterialAnalysis
from app.testing.service import SkillTestService
from app.pskills.exceptions import SkillsGatewayError, SkillValidationError
from app.gateway.asr import AsrTranscription
from app.gateway.inference import LlmAttachment, LlmCompletion
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
    files: dict[str, str | bytes] = field(default_factory=dict)
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
            content=str(project.files[file_path]),
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

    def commit_repository_files(
        self,
        *,
        project_id: str,
        branch: str,
        files: dict[str, str],
        binary_files: dict[str, bytes] | None = None,
        commit_message: str,
    ) -> str:
        project = self.projects[project_id]
        assert branch == project.default_branch
        assert commit_message
        for file_path, content in files.items():
            project.files[file_path] = content
            if file_path == "README.md":
                project.readme_content = content
            if file_path == "SKILL.md":
                project.skill_md_content = content
            if file_path == "skill.yaml":
                project.skill_yaml_content = content
        for file_path, content in (binary_files or {}).items():
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

    @staticmethod
    def _request_snapshot(
        *,
        route_key: str,
        system_prompt: str,
        user_prompt: str,
        content_parts: list[dict[str, object]] | None = None,
        attachments: list[dict[str, object]] | None = None,
    ) -> dict[str, object]:
        return {
            "redaction": {"mode": "redacted"},
            "provider": "fake-openai-compatible",
            "method": "POST",
            "url": "https://fake-llm.test/v1/chat/completions",
            "endpoint": "/chat/completions",
            "route_key": route_key,
            "headers": {"Authorization": "Bearer [redacted]", "Content-Type": "application/json"},
            "body": {
                "model": "fake-model",
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": content_parts if content_parts is not None else user_prompt},
                ],
                "temperature": 0.2,
            },
            "attachments": attachments or [],
        }

    def complete(self, *, system_prompt: str, user_prompt: str, route_key: str = "text") -> LlmCompletion:
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "route_key": route_key,
            }
        )
        if "SKILL 编译智能体" in system_prompt:
            content = json.dumps(build_test_formal_v5_artifact(), ensure_ascii=False)
        elif "generate_psop_skill_source_from_materials" in user_prompt:
            try:
                parsed_prompt = json.loads(user_prompt)
                material_id = str(parsed_prompt["material_analysis_results"][0]["source"]["material_id"])
                candidate_assets = [
                    item
                    for item in parsed_prompt.get("candidate_reference_assets", [])
                    if isinstance(item, dict) and item.get("id") and item.get("reference_path")
                ]
                selected_reference_assets = [
                    {
                        "asset_id": str(item["id"]),
                        "reference_path": str(item["reference_path"]),
                        "reason": "测试选择前两个候选帧作为运行时参考。",
                    }
                    for item in candidate_assets[:2]
                ]
                keyframe_paths = [
                    str(item["reference_path"])
                    for item in selected_reference_assets
                    if item.get("reference_path")
                ]
            except (KeyError, IndexError, TypeError, json.JSONDecodeError):
                material_id = "material-1"
                keyframe_paths = []
                selected_reference_assets = []
            reference_lines = "\n".join(f"![关键帧]({path})" for path in keyframe_paths) or "- 原始素材摘要已用于生成。"
            content = json.dumps(
                {
                    "directory_tree": (
                        "README.md\n"
                        "SKILL.md\n"
                        "prompts/system.md\n"
                        "references/README.md\n"
                        "examples/input.md\n"
                        "examples/expected-output.md\n"
                        "tests/checklist.md"
                    ),
                    "files": {
                        "README.md": "# Generated Skill\n\n基于原始素材生成的 Skill 草稿。\n",
                        "SKILL.md": "# Generated Skill\n\n请根据素材帮助用户完成任务，并参考视频关键帧。\n",
                        "prompts/system.md": "你是一个基于素材工作的 PSOP Skill 智能体。\n",
                        "references/README.md": f"# References\n\n{reference_lines}\n",
                        "examples/input.md": "# Input\n\n用户给出现场问题。\n",
                        "examples/expected-output.md": "# Expected Output\n\n给出结构化行动建议。\n",
                        "tests/checklist.md": "# Checklist\n\n- [ ] README 已说明用途\n- [ ] SKILL 已包含执行步骤\n",
                        "skill.yaml": "skill:\n  identity:\n    key: should-be-ignored\n",
                    },
                    "review_notes": ["需要人工复核边界条件。"],
                    "generation_reason": "素材包含创建 Skill 所需的任务说明与示例。",
                    "material_usage": [{"material_id": material_id, "usage": "提炼流程与示例"}],
                    "selected_reference_assets": selected_reference_assets,
                },
                ensure_ascii=False,
            )
        elif "generate_psop_test_scenarios" in user_prompt:
            content = json.dumps(
                {
                    "scenarios": [
                        {
                            "name": "自动生成发布前冒烟场景",
                            "description": "由 pskill.tester 根据 PSkill 和 EG 生成。",
                            "duration_ms": 120000,
                            "timeline": {
                                "schema_version": "psop-skill-test-timeline/v1",
                                "duration_ms": 120000,
                                "events": [
                                    {
                                        "id": "generated_user_request",
                                        "lane_id": "input.text",
                                        "at_ms": 0,
                                        "event_kind": "terminal.text.input.v1",
                                        "mime_type": "text/plain",
                                        "payload_inline": "请按技能完成一次标准现场任务。",
                                    },
                                    {
                                        "id": "expect_safe_guidance",
                                        "lane_id": "expected.semantic",
                                        "at_ms": 60000,
                                        "expectation": "系统应给出安全、清晰、可执行的现场指导。",
                                    },
                                ],
                            },
                            "judge_policy": {"route_key": "text", "confidence_threshold": 0.7},
                        }
                    ],
                    "diagnostics": [],
                },
                ensure_ascii=False,
            )
        elif "黑盒时序测试 Judge" in system_prompt:
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
            request=self._request_snapshot(
                route_key=route_key,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
            ),
        )

    def complete_multimodal(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        attachments: list[LlmAttachment],
        route_key: str = "multimodal",
    ) -> LlmCompletion:
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "route_key": route_key,
                "attachments": ",".join(attachment.filename for attachment in attachments),
            }
        )
        if "final_verify" in system_prompt or "final_verify" in user_prompt:
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
                    "reason": "多模态现场证据满足当前步骤完成标准。",
                    "next_phase": "final_verify",
                    "terminal_message": "已确认多模态证据，继续最终核验。",
                },
                ensure_ascii=False,
            )
        else:
            content = json.dumps(
                {
                    "summary": "视觉或音视频素材已由 LLM Gateway 解析。",
                    "content": {"text": "素材包含可用于创建 Skill 的多模态线索。", "language": ""},
                    "evidence_items": [
                        {
                            "kind": "visual_observation",
                            "content": "素材包含可用于创建 Skill 的多模态线索。",
                            "observations": ["fake multimodal signal"],
                        }
                    ],
                    "signals": [{"kind": "multimodal", "confidence": 0.9}],
                },
                ensure_ascii=False,
            )
        content_parts: list[dict[str, object]] = [{"type": "text", "text": user_prompt}]
        for attachment in attachments:
            if attachment.media_type.startswith("image/"):
                content_parts.append(
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{attachment.media_type};base64,[redacted]",
                        },
                    }
                )
            else:
                content_parts.append(
                    {
                        "type": "input_attachment",
                        "filename": attachment.filename,
                        "media_type": attachment.media_type,
                        "content_base64_chars": len(attachment.content_base64),
                    }
                )
        attachments_metadata = [
            {
                "filename": attachment.filename,
                "media_type": attachment.media_type,
                "content_base64_chars": len(attachment.content_base64),
            }
            for attachment in attachments
        ]
        return LlmCompletion(
            content=content,
            provider="fake-openai-compatible",
            model="fake-model",
            raw_response={"id": "fake-multimodal-response"},
            usage={"input_tokens": 20, "output_tokens": 10, "total_tokens": 30},
            request=self._request_snapshot(
                route_key=route_key,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                content_parts=content_parts,
                attachments=attachments_metadata,
            ),
        )


class FakeAsrGateway:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def transcribe(
        self,
        *,
        filename: str,
        content: bytes,
        media_type: str = "audio/wav",
        language: str | None = None,
        prompt: str | None = None,
    ) -> AsrTranscription:
        self.calls.append(
            {
                "filename": filename,
                "content": content,
                "media_type": media_type,
                "language": language,
                "prompt": prompt,
            }
        )
        return AsrTranscription(
            text="第一步关闭电源。第二步拆下面板。第三步清洁滤网并复位。",
            language="Chinese",
            raw_response={"text": "第一步关闭电源。第二步拆下面板。第三步清洁滤网并复位。"},
        )


class FakeObjectStore:
    def __init__(self) -> None:
        self.uploads: list[dict[str, object]] = []
        self.objects: dict[tuple[str, str], bytes] = {}

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
        self.objects[("test-bucket", object_key)] = content
        return StoredObject(
            bucket="test-bucket",
            object_key=object_key,
            media_type=media_type,
            size_bytes=len(content),
            checksum=f"sha256-{len(content)}",
            metadata=metadata or {},
        )

    def download_bytes(self, *, bucket: str, object_key: str) -> bytes:
        return self.objects[(bucket, object_key)]


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
            "llm_route_key": "text",
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
    fake_asr = FakeAsrGateway()
    fake_object_store = FakeObjectStore()
    client = TestClient(
        create_app(
            create_test_settings(),
            gitlab_gateway=fake_gateway,
            inference_gateway=fake_inference,
            asr_gateway=fake_asr,
            object_store=fake_object_store,
        )
    )
    return client, fake_gateway, fake_inference


def test_parse_generated_skill_draft_handles_outer_fence_and_inner_markdown_fences() -> None:
    content = json.dumps(
        {
            "directory_tree": "README.md\nSKILL.md",
            "files": {
                "README.md": "# README\n",
                "SKILL.md": "# Skill\n",
                "prompts/system.md": "system",
                "references/README.md": "reference",
                "examples/input.md": "```text\n用户输入\n```",
                "examples/expected-output.md": "```text\n助手输出\n```",
                "tests/checklist.md": "- [ ] ok",
            },
            "review_notes": [],
            "generation_reason": "ok",
            "material_usage": [],
            "selected_reference_assets": ["references/video-keyframes/material/000000000.jpg"],
        },
        ensure_ascii=False,
    )

    parsed = materials.parse_generated_skill_draft(f"```json\n{content}\n```")

    assert parsed.files["examples/input.md"].startswith("```text")
    assert parsed.selected_reference_assets == [
        {"reference_path": "references/video-keyframes/material/000000000.jpg", "reason": ""}
    ]


def test_create_skill_initializes_gitlab_and_persists_metadata() -> None:
    client, fake_gateway, _ = create_test_client()

    with client:
        response = client.post(
            "/api/v1/pskills",
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
    assert payload["is_published"] is False
    assert payload["current_draft_version"]["status"] == "draft"
    assert payload["current_draft_version"]["source_commit_sha"].startswith("commit-")
    assert len(fake_gateway.projects) == 1


def test_pskills_and_materials_routes_are_available() -> None:
    client, _, _ = create_test_client()

    with client:
        create_response = client.post(
            "/api/v1/pskills",
            json={
                "key": "pskill-materials",
                "name": "PSkill Materials",
                "description": "Create a PSkill through the renamed API.",
            },
        )
        skill_id = create_response.json()["id"]
        list_response = client.get("/api/v1/pskills")

        upload_response = client.post(
            f"/api/v1/pskills/{skill_id}/materials",
            data={
                "name": "作业指南",
                "description": "阶段 1 materials alias",
                "material_kind": "markdown",
                "source_note": "pskills route",
            },
            files={"file": ("guide.md", b"# Guide\n\nUse lockout before repair.\n", "text/markdown")},
        )
        material_id = upload_response.json()["id"]
        batch_analyze_response = client.post(
            f"/api/v1/pskills/{skill_id}/materials/batch-analyze",
            json={"material_ids": [material_id]},
        )
        materials_response = client.get(f"/api/v1/pskills/{skill_id}/materials")
        detail_response = client.get(f"/api/v1/pskills/{skill_id}/materials/{material_id}")
        content_response = client.get(f"/api/v1/pskills/{skill_id}/materials/{material_id}/content")
        delete_response = client.delete(f"/api/v1/pskills/{skill_id}/materials/{material_id}")

    assert create_response.status_code == 201
    assert any(item["id"] == skill_id for item in list_response.json())
    assert upload_response.status_code == 201
    assert upload_response.json()["source_note"] == "pskills route"
    assert batch_analyze_response.status_code == 200
    assert batch_analyze_response.json()["pskill_definition_id"] == skill_id
    assert batch_analyze_response.json()["requested_count"] == 1
    assert batch_analyze_response.json()["analyzed_count"] == 1
    assert batch_analyze_response.json()["skipped_count"] == 0
    assert batch_analyze_response.json()["analyses"][0]["material_id"] == material_id
    assert batch_analyze_response.json()["analyses"][0]["status"] == "ready"
    assert materials_response.status_code == 200
    assert [item["id"] for item in materials_response.json()] == [material_id]
    assert detail_response.status_code == 200
    assert detail_response.json()["analysis_result"]["content"]["text"].startswith("# Guide")
    assert content_response.status_code == 200
    assert content_response.content.startswith(b"# Guide")
    assert delete_response.status_code == 200
    assert delete_response.json()["deleted"] is True


def test_list_skills_filters_by_published_state() -> None:
    client, _, _ = create_test_client()

    with client:
        draft_skill = client.post(
            "/api/v1/pskills",
            json={
                "key": "draft-only",
                "name": "Draft Only",
                "description": "Keep this skill unpublished.",
            },
        ).json()
        published_skill = client.post(
            "/api/v1/pskills",
            json={
                "key": "published-skill",
                "name": "Published Skill",
                "description": "Publish this skill.",
            },
        ).json()
        publish_response = client.post(
            f"/api/v1/pskills/{published_skill['id']}/publish",
            json={"publish_reason": "Initial publish"},
        )
        compile_request_id = publish_response.json()["compile_request"]["id"]
        client.post(f"/api/v1/compiler/requests/{compile_request_id}/retry")

        all_response = client.get("/api/v1/pskills")
        published_response = client.get("/api/v1/pskills?is_published=true")
        unpublished_response = client.get("/api/v1/pskills?is_published=false")

    assert all_response.status_code == 200
    all_skills = {skill["id"]: skill for skill in all_response.json()}
    assert all_skills[draft_skill["id"]]["is_published"] is False
    assert all_skills[published_skill["id"]]["is_published"] is True

    assert published_response.status_code == 200
    published_ids = {skill["id"] for skill in published_response.json()}
    assert published_skill["id"] in published_ids
    assert draft_skill["id"] not in published_ids

    assert unpublished_response.status_code == 200
    unpublished_ids = {skill["id"] for skill in unpublished_response.json()}
    assert draft_skill["id"] in unpublished_ids
    assert published_skill["id"] not in unpublished_ids


def test_get_and_save_skill_source() -> None:
    client, _, _ = create_test_client()

    with client:
        created = client.post(
            "/api/v1/pskills",
            json={
                "key": "diagnosis-assistant",
                "name": "Diagnosis Assistant",
                "description": "Assist engineers with diagnostics.",
            },
        ).json()

        skill_id = created["id"]
        source_response = client.get(f"/api/v1/pskills/{skill_id}/source")
        assert source_response.status_code == 200
        source_payload = source_response.json()
        assert "skill:" in source_payload["skill_yaml_content"]
        before_detail = client.get(f"/api/v1/pskills/{skill_id}").json()
        before_skill_md = before_detail["current_draft_version"]["manifest_snapshot"]["prompt_material"]["skill_md"]

        save_response = client.put(
            f"/api/v1/pskills/{skill_id}/source",
            json={
                "base_commit_sha": source_payload["head_commit_sha"],
                "readme_content": source_payload["readme_content"] + "\nUpdated from test.\n",
                "skill_md_content": source_payload["skill_md_content"] + "\n## Validation\n\n- test path\n",
                "skill_yaml_content": "skill:\n  identity:\n    key: tampered-by-user\n",
            },
        )
        after_detail = client.get(f"/api/v1/pskills/{skill_id}").json()

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
    assert after_detail["updated_at"] != before_detail["updated_at"]


def test_pskill_draft_generate_invokes_builder_and_apply_patch_updates_source() -> None:
    client, _, _ = create_test_client()

    with client:
        created = client.post(
            "/api/v1/pskills",
            json={
                "key": "builder-draft-api",
                "name": "Builder Draft API",
                "description": "Generate reviewable draft patches.",
            },
        ).json()
        skill_id = created["id"]
        material_response = client.post(
            f"/api/v1/pskills/{skill_id}/materials",
            data={
                "name": "Draft notes",
                "description": "Builder draft source note",
                "material_kind": "markdown",
                "source_note": "operator note",
            },
            files={"file": ("notes.md", b"# Notes\n\nCheck voltage evidence.\n", "text/markdown")},
        )
        material_id = material_response.json()["id"]
        source_response = client.get(f"/api/v1/pskills/{skill_id}/source")
        source_payload = source_response.json()
        generate_response = client.post(
            f"/api/v1/pskills/{skill_id}/draft/generate",
            json={
                "user_description": "Add evidence handling steps for voltage checks.",
                "material_ids": [material_id],
                "base_commit_sha": source_payload["head_commit_sha"],
            },
        )
        generated_payload = generate_response.json()
        agent_run_id = generated_payload["agent_run"]["id"]
        authorizations_response = client.get(f"/api/v1/agent-runs/{agent_run_id}/tool-authorizations")
        tool_calls_response = client.get(f"/api/v1/agent-runs/{agent_run_id}/tool-calls")
        after_generate_source_response = client.get(f"/api/v1/pskills/{skill_id}/source")

        file_changes = generated_payload["patch"]["file_changes"]
        apply_response = client.post(
            f"/api/v1/pskills/{skill_id}/draft/apply-patch",
            json={
                "base_commit_sha": source_payload["head_commit_sha"],
                "files": {item["path"]: item["proposed_content"] for item in file_changes},
                "builder_agent_run_id": agent_run_id,
                "commit_message": "Apply builder draft API patch",
            },
        )
        after_apply_detail_response = client.get(f"/api/v1/pskills/{skill_id}")

    assert material_response.status_code == 201
    assert material_response.json()["status"] == "ready"
    assert source_response.status_code == 200

    assert generate_response.status_code == 201
    assert generated_payload["status"] == "patch_proposed"
    assert generated_payload["agent_run"]["agent_key"] == "pskill.builder"
    assert generated_payload["agent_run"]["status"] == "succeeded"
    assert generated_payload["material_ids"] == [material_id]
    assert generated_payload["base_commit_sha"] == source_payload["head_commit_sha"]

    patch = generated_payload["patch"]
    assert patch["status"] == "patch_proposed"
    assert patch["committed"] is False
    assert patch["requires_human_apply"] is True
    assert patch["file_changes"][0]["path"] == "SKILL.md"
    assert patch["file_changes"][0]["changed"] is True
    assert "+## Builder Draft Proposal" in patch["diff"]
    assert "Draft notes" in patch["file_changes"][0]["proposed_content"]

    assert authorizations_response.status_code == 200
    assert authorizations_response.json() == []
    assert tool_calls_response.status_code == 200
    assert tool_calls_response.json()[0]["tool_name"] == "psop.repository.propose_patch"
    assert tool_calls_response.json()[0]["status"] == "succeeded"

    assert after_generate_source_response.status_code == 200
    assert after_generate_source_response.json()["head_commit_sha"] == source_payload["head_commit_sha"]
    assert after_generate_source_response.json()["skill_md_content"] == source_payload["skill_md_content"]

    assert apply_response.status_code == 200
    applied_payload = apply_response.json()
    assert applied_payload["applied"] is True
    assert applied_payload["changed_files"] == ["SKILL.md"]
    assert applied_payload["committed_commit_sha"] != source_payload["head_commit_sha"]
    assert "## Builder Draft Proposal" in applied_payload["source"]["skill_md_content"]

    after_apply_detail = after_apply_detail_response.json()
    assert after_apply_detail_response.status_code == 200
    assert after_apply_detail["latest_draft_head_sha"] == applied_payload["committed_commit_sha"]
    assert after_apply_detail["current_draft_version"]["builder_agent_run_id"] == agent_run_id
    assert "## Builder Draft Proposal" in after_apply_detail["current_draft_version"]["manifest_snapshot"][
        "prompt_material"
    ]["skill_md"]


def test_pskill_material_upload_list_detail_content_and_delete() -> None:
    client, _, fake_inference = create_test_client()

    with client:
        created = client.post(
            "/api/v1/pskills",
            json={
                "key": "raw-material-skill",
                "name": "Raw Material Skill",
                "description": "Create skills from source materials.",
            },
        ).json()
        skill_id = created["id"]

        upload_response = client.post(
            f"/api/v1/pskills/{skill_id}/materials",
            data={
                "name": "作业指南",
                "description": "现场作业流程素材",
                "material_kind": "markdown",
                "source_note": "operator upload",
            },
            files={"file": ("guide.md", b"# Guide\n\nUse lockout before repair.\n", "text/markdown")},
        )
        image_response = client.post(
            f"/api/v1/pskills/{skill_id}/materials",
            data={"name": "设备照片", "material_kind": "image"},
            files={"file": ("panel.png", b"not-really-a-png", "image/png")},
        )
        jobs_response = client.get("/api/v1/runtime/jobs", params={"job_type": "material_analysis"})
        list_response = client.get(f"/api/v1/pskills/{skill_id}/materials")
        material_id = upload_response.json()["id"]
        detail_response = client.get(f"/api/v1/pskills/{skill_id}/materials/{material_id}")
        content_response = client.get(f"/api/v1/pskills/{skill_id}/materials/{material_id}/content")
        range_response = client.get(
            f"/api/v1/pskills/{skill_id}/materials/{material_id}/content",
            headers={"Range": "bytes=0-6"},
        )
        delete_response = client.delete(f"/api/v1/pskills/{skill_id}/materials/{material_id}")
        after_delete_response = client.get(f"/api/v1/pskills/{skill_id}/materials")
        deleted_detail_response = client.get(f"/api/v1/pskills/{skill_id}/materials/{material_id}")

    assert upload_response.status_code == 201
    upload_payload = upload_response.json()
    assert upload_payload["status"] == "ready"
    assert upload_payload["filename"] == "guide.md"
    assert upload_payload["source_note"] == "operator upload"
    assert "Guide" in upload_payload["analysis_result"]["content"]["text"]

    assert image_response.status_code == 201
    assert image_response.json()["status"] == "ready"
    assert image_response.json()["analysis_result"]["debug"]["processor"] == "llm_multimodal"
    assert any(
        call.get("attachments") == "panel.png" and call.get("route_key") == "multimodal"
        for call in fake_inference.calls
    )
    assert any(job["token_usage"] and job["token_usage"]["total_tokens"] == 30 for job in jobs_response.json())

    assert list_response.status_code == 200
    assert {item["id"] for item in list_response.json()} == {material_id, image_response.json()["id"]}

    assert detail_response.status_code == 200
    assert "lockout" in detail_response.json()["analysis_result"]["content"]["text"]
    assert content_response.status_code == 200
    assert content_response.content == b"# Guide\n\nUse lockout before repair.\n"
    assert content_response.headers["accept-ranges"] == "bytes"
    assert range_response.status_code == 206
    assert range_response.headers["content-range"] == "bytes 0-6/36"
    assert range_response.content == b"# Guide"

    assert delete_response.status_code == 200
    assert delete_response.json() == {"deleted": True, "material_id": material_id}
    assert {item["id"] for item in after_delete_response.json()} == {image_response.json()["id"]}
    assert deleted_detail_response.status_code == 404


def test_pskill_material_upload_rejects_url_only_payload() -> None:
    client, _, _ = create_test_client()

    with client:
        created = client.post(
            "/api/v1/pskills",
            json={
                "key": "url-only-material-skill",
                "name": "URL Only Material Skill",
                "description": "Reject URL raw materials.",
            },
        ).json()
        response = client.post(
            f"/api/v1/pskills/{created['id']}/materials",
            data={
                "source_url": "https://example.test/reference",
                "name": "参考页面",
            },
        )

    assert response.status_code == 422
    assert response.json()["message"] == "请上传素材文件。"


def test_pskill_material_video_uses_dedicated_upload_limit() -> None:
    settings = create_test_settings()
    settings.material_max_upload_bytes = 8
    settings.material_video_max_upload_bytes = 32
    processor = materials.MaterialProcessor(
        settings=settings,
        inference_gateway=FakeInferenceGateway(),
        object_store=FakeObjectStore(),
    )

    processor._validate_upload(filename="guide.mp4", content=b"x" * 16, mime_type="video/mp4")

    with pytest.raises(SkillValidationError) as exc_info:
        processor._validate_upload(filename="guide.txt", content=b"x" * 16, mime_type="text/plain")

    assert exc_info.value.message == "上传素材超过大小限制。"
    assert exc_info.value.details["max_bytes"] == 8


def _fake_video_analysis_result() -> video_analysis.VideoAnalysisResult:
    keyframes = [
        video_analysis.VideoKeyframeAnalysis(
            timestamp_ms=0,
            filename="000000000.jpg",
            content=b"fake-keyframe-0",
            caption="关闭设备电源并确认安全。",
            observations=[{"kind": "safety"}],
            frame_source="timeline_sample",
            metadata={"frame_source": "timeline_sample", "operation_relevance": "high"},
        ),
        video_analysis.VideoKeyframeAnalysis(
            timestamp_ms=30000,
            filename="000030000.jpg",
            content=b"fake-keyframe-1",
            caption="拆下面板并清洁滤网。",
            observations=[{"kind": "operation"}],
            frame_source="scene_change",
            metadata={"frame_source": "scene_change", "operation_relevance": "high"},
        ),
    ]
    asr = AsrTranscription(text="先关闭电源，然后拆下面板并清洁滤网。", language="Chinese")
    return video_analysis.VideoAnalysisResult(
        asr=asr,
        keyframes=keyframes,
        duration_ms=60_000,
    )


def test_pskill_material_pdf_audio_and_video_extraction(monkeypatch) -> None:
    monkeypatch.setattr(materials, "_extract_pdf_text", lambda content: "PDF extracted procedure text.")
    monkeypatch.setattr(
        skills_service_module,
        "analyze_video_material",
        lambda **_: _fake_video_analysis_result(),
    )
    client, _, fake_inference = create_test_client()

    with client:
        created = client.post(
            "/api/v1/pskills",
            json={
                "key": "multimodal-material-skill",
                "name": "Multimodal Material Skill",
                "description": "Create skills from PDFs and media.",
            },
        ).json()
        skill_id = created["id"]
        pdf_response = client.post(
            f"/api/v1/pskills/{skill_id}/materials",
            data={"name": "PDF SOP", "material_kind": "pdf"},
            files={"file": ("sop.pdf", b"%PDF-1.4 fake", "application/pdf")},
        )
        audio_response = client.post(
            f"/api/v1/pskills/{skill_id}/materials",
            data={"name": "Audio Notes", "material_kind": "audio"},
            files={"file": ("notes.wav", b"RIFF fake wav", "audio/wav")},
        )
        video_response = client.post(
            f"/api/v1/pskills/{skill_id}/materials",
            data={"name": "Video Walkthrough", "material_kind": "video"},
            files={"file": ("walkthrough.mp4", b"fake mp4", "video/mp4")},
        )
        video_analysis_response = client.get(
            f"/api/v1/pskills/{skill_id}/materials/{video_response.json()['id']}/analysis"
        )
        list_response = client.get(f"/api/v1/pskills/{skill_id}/materials")

    assert pdf_response.status_code == 201
    assert pdf_response.json()["status"] == "ready"
    assert "PDF extracted procedure text" in pdf_response.json()["analysis_result"]["content"]["text"]

    assert audio_response.status_code == 201
    assert audio_response.json()["status"] == "ready"
    assert audio_response.json()["analysis_result"]["debug"]["processor"] == "llm_multimodal"

    assert video_response.status_code == 201
    assert video_response.json()["status"] == "ready"
    assert video_response.json()["analysis_status"] == "ready"
    assert video_response.json()["derived_asset_count"] == 2
    assert video_response.json()["analysis_result"]["debug"]["processor"] == "video_analysis"
    assert video_analysis_response.status_code == 200
    assert video_analysis_response.json()["analysis_result"]["content"]["text"].startswith("先关闭电源")
    assert len(video_analysis_response.json()["derived_assets"]) == 2
    assert len(list_response.json()) == 3
    assert any(
        call.get("attachments") == "notes.wav" and call.get("route_key") == "multimodal"
        for call in fake_inference.calls
    )


def test_failed_video_raw_material_can_be_reanalyzed(monkeypatch) -> None:
    attempts = {"count": 0}

    def fake_analyze_video_material(**_: object) -> video_analysis.VideoAnalysisResult:
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise SkillsGatewayError(
                "ASR Gateway 返回错误响应。",
                details={"status_code": 413, "body": "audio too large"},
            )
        return _fake_video_analysis_result()

    monkeypatch.setattr(skills_service_module, "analyze_video_material", fake_analyze_video_material)
    client, _, _ = create_test_client()

    with client:
        created = client.post(
            "/api/v1/pskills",
            json={
                "key": "retry-material-analysis-skill",
                "name": "Retry Video Analysis Skill",
                "description": "Retry failed video parsing.",
            },
        ).json()
        skill_id = created["id"]
        upload_response = client.post(
            f"/api/v1/pskills/{skill_id}/materials",
            data={"name": "视频教程", "material_kind": "video"},
            files={"file": ("walkthrough.mp4", b"fake mp4", "video/mp4")},
        )
        material_id = upload_response.json()["id"]
        failed_analysis_response = client.get(
            f"/api/v1/pskills/{skill_id}/materials/{material_id}/analysis"
        )
        retry_response = client.post(f"/api/v1/pskills/{skill_id}/materials/{material_id}/analyze")
        detail_response = client.get(f"/api/v1/pskills/{skill_id}/materials/{material_id}")

    assert upload_response.status_code == 201
    assert upload_response.json()["status"] == "failed"
    assert failed_analysis_response.status_code == 200
    assert failed_analysis_response.json()["status"] == "failed"
    assert failed_analysis_response.json()["error_details"]["status_code"] == 413
    assert failed_analysis_response.json()["error_details"]["body"] == "audio too large"
    assert retry_response.status_code == 200
    assert retry_response.json()["status"] == "ready"
    assert detail_response.status_code == 200
    assert detail_response.json()["status"] == "ready"
    assert detail_response.json()["analysis_status"] == "ready"
    assert attempts["count"] == 2


def test_processing_video_raw_material_cannot_be_reanalyzed(monkeypatch) -> None:
    monkeypatch.setattr(
        skills_service_module,
        "analyze_video_material",
        lambda **_: _fake_video_analysis_result(),
    )
    client, _, _ = create_test_client()

    with client:
        created = client.post(
            "/api/v1/pskills",
            json={
                "key": "processing-material-analysis-skill",
                "name": "Processing Video Analysis Skill",
                "description": "Reject duplicate processing video parsing.",
            },
        ).json()
        skill_id = created["id"]
        upload_response = client.post(
            f"/api/v1/pskills/{skill_id}/materials",
            data={"name": "视频教程", "material_kind": "video"},
            files={"file": ("walkthrough.mp4", b"fake mp4", "video/mp4")},
        )
        material_id = upload_response.json()["id"]
        with client.app.state.db_manager.session() as session:
            material = session.get(PSkillMaterial, material_id)
            analysis = (
                session.query(PSkillMaterialAnalysis)
                .filter(PSkillMaterialAnalysis.material_id == material_id)
                .one()
            )
            material.status = "processing"
            analysis.status = "running"
            session.commit()
        retry_response = client.post(f"/api/v1/pskills/{skill_id}/materials/{material_id}/analyze")

    assert upload_response.status_code == 201
    assert retry_response.status_code == 422
    assert retry_response.json()["message"] == "素材正在分析中，不能重复解析。"
    assert retry_response.json()["details"] == {
        "material_id": material_id,
        "material_status": "processing",
        "analysis_status": "running",
    }


def test_generate_skill_draft_from_materials_commits_standard_files_without_publish_or_compile(monkeypatch) -> None:
    monkeypatch.setattr(
        skills_service_module,
        "analyze_video_material",
        lambda **_: _fake_video_analysis_result(),
    )
    client, fake_gateway, fake_inference = create_test_client()

    with client:
        created = client.post(
            "/api/v1/pskills",
            json={
                "key": "generated-skill",
                "name": "Generated Skill",
                "description": "Generate source from materials.",
            },
        ).json()
        skill_id = created["id"]
        material_response = client.post(
            f"/api/v1/pskills/{skill_id}/materials",
            data={"name": "流程说明"},
            files={"file": ("workflow.md", b"# Workflow\n\nAsk, inspect, then advise.\n", "text/markdown")},
        )
        material_id = material_response.json()["id"]
        video_response = client.post(
            f"/api/v1/pskills/{skill_id}/materials",
            data={"name": "视频教程", "material_kind": "video"},
            files={"file": ("walkthrough.mp4", b"fake mp4", "video/mp4")},
        )
        video_material_id = video_response.json()["id"]

        generate_response = client.post(
            f"/api/v1/pskills/{skill_id}/materials/generate-skill-draft",
            json={
                "user_description": "请基于素材生成一个现场支持 Skill。",
                "base_commit_sha": created["latest_draft_head_sha"],
            },
        )
        generated_payload = generate_response.json()
        agent_run_id = generated_payload["agent_run"]["id"]
        agent_run_response = client.get(f"/api/v1/agent-runs/{agent_run_id}")
        agent_events_response = client.get(f"/api/v1/agent-runs/{agent_run_id}/events")
        skill_activations_response = client.get(f"/api/v1/agent-runs/{agent_run_id}/skill-activations")
        model_calls_response = client.get(f"/api/v1/agent-runs/{agent_run_id}/model-calls")
        detail_response = client.get(f"/api/v1/pskills/{skill_id}")
        source_response = client.get(f"/api/v1/pskills/{skill_id}/source")
        publishes_response = client.get(f"/api/v1/pskills/{skill_id}/publishes")
        versions_response = client.get(f"/api/v1/pskills/{skill_id}/versions")

    assert generate_response.status_code == 200
    payload = generated_payload
    assert payload["status"] == "succeeded"
    assert payload["agent_run"]["agent_key"] == "pskill.builder"
    assert payload["agent_run"]["owner_type"] == "pskill_material_generation"
    assert payload["agent_run"]["owner_id"] == payload["id"]
    assert payload["agent_run"]["status"] == "succeeded"
    assert payload["agent_run"]["output_payload"]["schema"] == "PSkillBuilderResult"
    assert payload["agent_run"]["output_payload"]["committed_commit_sha"] == payload["committed_commit_sha"]
    assert payload["prompt_metadata"]["agent_run_id"] == agent_run_id
    assert payload["material_ids"] == [video_material_id, material_id]
    assert payload["committed_commit_sha"].startswith("commit-")
    assert payload["prompt_metadata"]["reference_files"] == [
        f"references/video-keyframes/{video_material_id}/000000000.jpg",
        f"references/video-keyframes/{video_material_id}/000030000.jpg",
    ]
    assert [item["reference_path"] for item in payload["prompt_metadata"]["selected_reference_assets"]] == [
        f"references/video-keyframes/{video_material_id}/000000000.jpg",
        f"references/video-keyframes/{video_material_id}/000030000.jpg",
    ]
    assert set(payload["generated_files"]) >= {
        "README.md",
        "SKILL.md",
        "prompts/system.md",
        "references/README.md",
        "examples/input.md",
        "examples/expected-output.md",
        "tests/checklist.md",
    }
    assert "skill.yaml" not in payload["generated_files"]
    assert payload["material_usage"][0]["material_id"] == video_material_id
    assert fake_gateway.projects[created["gitlab_project_id"]].files["README.md"].startswith("# Generated Skill")
    assert fake_gateway.projects[created["gitlab_project_id"]].files[
        f"references/video-keyframes/{video_material_id}/000000000.jpg"
    ] == b"fake-keyframe-0"
    assert "should-be-ignored" not in fake_gateway.projects[created["gitlab_project_id"]].files["skill.yaml"]
    assert detail_response.json()["latest_draft_head_sha"] == payload["committed_commit_sha"]
    assert detail_response.json()["current_draft_version"]["builder_agent_run_id"] == agent_run_id
    assert detail_response.json()["updated_at"] != created["updated_at"]
    prompt_material = detail_response.json()["current_draft_version"]["manifest_snapshot"]["prompt_material"]
    assert prompt_material["readme"].startswith("# Generated Skill")
    assert prompt_material["skill_md"].startswith("# Generated Skill")
    assert source_response.json()["head_commit_sha"] == payload["committed_commit_sha"]
    assert publishes_response.json() == []
    assert versions_response.json()[0]["builder_agent_run_id"] == agent_run_id
    assert agent_run_response.status_code == 200
    assert agent_run_response.json()["agent_key"] == "pskill.builder"
    assert agent_run_response.json()["status"] == "succeeded"
    event_types = {item["event_type"] for item in agent_events_response.json()}
    assert {
        "agent.skills.activated",
        "pskill.builder.generation.linked",
        "pskill.builder.generation.started",
        "pskill.builder.model_call.completed",
        "pskill.builder.generation.succeeded",
    }.issubset(event_types)
    assert skill_activations_response.status_code == 200
    assert {item["activation_context"]["package_name"] for item in skill_activations_response.json()} == {
        "pskill-builder",
        "ffmpeg-video-processing",
        "document-ocr-processing",
    }
    assert model_calls_response.status_code == 200
    model_call = model_calls_response.json()[0]
    assert model_call["provider"] == "fake-openai-compatible"
    assert model_call["route_key"] == "text"
    assert model_call["request_payload"]["prompt_payload"]["task"] == "generate_psop_skill_source_from_materials"
    generation_call = next(
        call for call in fake_inference.calls if "generate_psop_skill_source_from_materials" in call["user_prompt"]
    )
    generation_prompt = json.loads(generation_call["user_prompt"])
    assert generation_call["route_key"] == "text"
    assert "psop_skill_form_definition" in generation_prompt
    assert "physical_world_skill_guidance" in generation_prompt
    assert "publishable_document_skill_standard" in generation_prompt
    assert len(generation_prompt["material_analysis_results"]) == 2


def test_generate_skill_draft_from_materials_rejects_stale_head(monkeypatch) -> None:
    monkeypatch.setattr(
        skills_service_module,
        "analyze_video_material",
        lambda **_: _fake_video_analysis_result(),
    )
    client, fake_gateway, _ = create_test_client()

    with client:
        created = client.post(
            "/api/v1/pskills",
            json={
                "key": "stale-generation",
                "name": "Stale Generation",
                "description": "Reject stale source generation.",
            },
        ).json()
        skill_id = created["id"]
        source_payload = client.get(f"/api/v1/pskills/{skill_id}/source").json()
        video_response = client.post(
            f"/api/v1/pskills/{skill_id}/materials",
            data={"name": "素材"},
            files={"file": ("walkthrough.mp4", b"fake mp4", "video/mp4")},
        )
        fake_gateway.commit_skill_source(
            project_id=created["gitlab_project_id"],
            branch=created["default_branch"],
            readme_content=source_payload["readme_content"],
            skill_md_content=source_payload["skill_md_content"],
            skill_yaml_content=source_payload["skill_yaml_content"],
            commit_message="External edit",
        )
        response = client.post(
            f"/api/v1/pskills/{skill_id}/materials/generate-skill-draft",
            json={
                "user_description": "生成草稿。",
                "base_commit_sha": source_payload["head_commit_sha"],
            },
        )

    assert response.status_code == 409
    assert response.json()["code"] == "skill_source_conflict"


def test_generate_skill_draft_from_materials_rejects_material_subset_field() -> None:
    client, _, _ = create_test_client()

    with client:
        response = client.post(
            "/api/v1/pskills/skill-id/materials/generate-skill-draft",
            json={
                "material_ids": ["material-id"],
                "user_description": "生成草稿。",
            },
        )

    assert response.status_code == 422
    assert any(error["loc"][-1] == "material_ids" for error in response.json()["detail"])


def test_generate_skill_draft_from_materials_requires_ready_video() -> None:
    client, _, _ = create_test_client()

    with client:
        created = client.post(
            "/api/v1/pskills",
            json={
                "key": "video-required",
                "name": "Video Required",
                "description": "Reject generation without analyzed video.",
            },
        ).json()
        text_response = client.post(
            f"/api/v1/pskills/{created['id']}/materials",
            data={"name": "文本素材"},
            files={"file": ("notes.txt", b"Build a safe checklist.\n", "text/plain")},
        )
        response = client.post(
            f"/api/v1/pskills/{created['id']}/materials/generate-skill-draft",
            json={
                "user_description": "生成草稿。",
                "base_commit_sha": created["latest_draft_head_sha"],
            },
        )

    assert response.status_code == 422
    assert response.json()["message"] == "生成 Skill 至少需要选择一个已分析完成的视频素材。"


def test_repository_tree_file_and_folder_operations() -> None:
    client, fake_gateway, _ = create_test_client()

    with client:
        created = client.post(
            "/api/v1/pskills",
            json={
                "key": "repo-browser",
                "name": "Repo Browser",
                "description": "Browse skill source files.",
            },
        ).json()
        skill_id = created["id"]

        tree_response = client.get(f"/api/v1/pskills/{skill_id}/repository/tree")
        create_folder_response = client.post(
            f"/api/v1/pskills/{skill_id}/repository/folders",
            json={"path": "examples"},
        )
        create_file_response = client.post(
            f"/api/v1/pskills/{skill_id}/repository/files",
            json={"path": "examples/demo.md", "content": "# Demo\n"},
        )
        nested_tree_response = client.get(
            f"/api/v1/pskills/{skill_id}/repository/tree",
            params={"path": "examples"},
        )
        file_response = client.get(
            f"/api/v1/pskills/{skill_id}/repository/files",
            params={"path": "examples/demo.md"},
        )
        save_response = client.put(
            f"/api/v1/pskills/{skill_id}/repository/files",
            json={
                "path": "examples/demo.md",
                "content": "# Demo\n\nUpdated.\n",
                "base_commit_sha": file_response.json()["head_commit_sha"],
            },
        )
        before_repo_detail = client.get(f"/api/v1/pskills/{skill_id}").json()
        skill_md_response = client.get(
            f"/api/v1/pskills/{skill_id}/repository/files",
            params={"path": "SKILL.md"},
        )
        skill_md_save_response = client.put(
            f"/api/v1/pskills/{skill_id}/repository/files",
            json={
                "path": "SKILL.md",
                "content": skill_md_response.json()["content"] + "\n## Repo Edit\n\n- update core instruction\n",
                "base_commit_sha": skill_md_response.json()["head_commit_sha"],
            },
        )
        after_repo_detail = client.get(f"/api/v1/pskills/{skill_id}").json()
        manifest_response = client.get(
            f"/api/v1/pskills/{skill_id}/repository/files",
            params={"path": "skill.yaml"},
        )
        manifest_save_response = client.put(
            f"/api/v1/pskills/{skill_id}/repository/files",
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
            "/api/v1/pskills",
            json={
                "key": "repair-planner",
                "name": "Repair Planner",
                "description": "Plan repair actions.",
            },
        ).json()
        skill_id = created["id"]
        source_payload = client.get(f"/api/v1/pskills/{skill_id}/source").json()
        fake_gateway.commit_skill_source(
            project_id=created["gitlab_project_id"],
            branch=created["default_branch"],
            readme_content=source_payload["readme_content"],
            skill_md_content=source_payload["skill_md_content"],
            skill_yaml_content=source_payload["skill_yaml_content"],
            commit_message="External change",
        )

        save_response = client.put(
            f"/api/v1/pskills/{skill_id}/source",
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
            "/api/v1/pskills",
            json={
                "key": "field-support",
                "name": "Field Support",
                "description": "Support field operators.",
            },
        ).json()
        skill_id = created["id"]

        initial_versions_response = client.get(f"/api/v1/pskills/{skill_id}/versions")
        publish_response = client.post(
            f"/api/v1/pskills/{skill_id}/publish",
            json={"publish_reason": "Initial MVP publish"},
        )
        publish_payload = publish_response.json()
        compile_request_id = publish_payload["compile_request"]["id"]
        progress_response = client.get(f"/api/v1/compiler/requests/{compile_request_id}/progress")
        compile_response = client.post(f"/api/v1/compiler/requests/{compile_request_id}/retry")
        events_response = client.get(f"/api/v1/compiler/requests/{compile_request_id}/events")
        detail_response = client.get(f"/api/v1/pskills/{skill_id}")
        publishes_response = client.get(f"/api/v1/pskills/{skill_id}/publishes")
        versions_response = client.get(f"/api/v1/pskills/{skill_id}/versions")

    assert initial_versions_response.status_code == 200
    initial_versions_payload = initial_versions_response.json()
    assert len(initial_versions_payload) == 1
    assert initial_versions_payload[0]["id"] == created["current_draft_version"]["id"]
    assert initial_versions_payload[0]["version_no"] == 0
    assert initial_versions_payload[0]["status"] == "draft"

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

    assert versions_response.status_code == 200
    versions_payload = versions_response.json()
    assert [version["status"] for version in versions_payload] == ["published", "draft"]
    assert versions_payload[0]["id"] == publish_payload["published_version"]["id"]
    assert versions_payload[0]["source_commit_sha"] == publish_payload["published_commit_sha"]
    assert versions_payload[1]["id"] == created["current_draft_version"]["id"]


def test_pskill_activity_websocket_streams_publish_compile_snapshot() -> None:
    client, _, _ = create_test_client()

    with client:
        created = client.post(
            "/api/v1/pskills",
            json={
                "key": "pskill-activity-ws",
                "name": "PSkill Activity WS",
                "description": "Validate PSkill activity websocket.",
            },
        ).json()
        skill_id = created["id"]
        publish_response = client.post(
            f"/api/v1/pskills/{skill_id}/publish",
            json={"publish_reason": "Activity websocket publish"},
        )
        compile_request_id = publish_response.json()["compile_request"]["id"]

        with client.websocket_connect(f"/ws/pskills/{skill_id}/activity") as websocket:
            connected = websocket.receive_json()
            pending_snapshot = websocket.receive_json()
            compile_response = client.post(f"/api/v1/compiler/requests/{compile_request_id}/retry")
            terminal_snapshot = websocket.receive_json()

    assert publish_response.status_code == 202
    assert connected["event_type"] == "ws.connected"
    assert connected["pskill_id"] == skill_id

    assert pending_snapshot["event_type"] == "pskill.activity.snapshot"
    pending_payload = pending_snapshot["payload"]
    assert pending_payload["pskill"]["id"] == skill_id
    assert pending_payload["active"] is True
    assert pending_payload["terminal"] is False
    assert pending_payload["publishes"][0]["publish_status"] == "compiling"
    assert pending_payload["compile_requests"][0]["id"] == compile_request_id
    assert pending_payload["compile_requests"][0]["status"] == "pending"
    assert pending_payload["compile_requests"][0]["progress"]["terminal"] is False

    assert compile_response.status_code == 200
    assert terminal_snapshot["event_type"] == "pskill.activity.snapshot"
    terminal_payload = terminal_snapshot["payload"]
    assert terminal_payload["active"] is False
    assert terminal_payload["terminal"] is True
    assert terminal_payload["pskill"]["latest_published_version_id"] == publish_response.json()["published_version"]["id"]
    assert terminal_payload["publishes"][0]["publish_status"] == "published"
    assert terminal_payload["compile_requests"][0]["status"] == "succeeded"
    assert terminal_payload["compile_requests"][0]["artifact_id"]
    assert terminal_payload["compile_requests"][0]["progress"]["terminal"] is True
    assert terminal_payload["compile_requests"][0]["progress"]["terminal_status"] == "succeeded"


def test_publish_gate_runs_after_publish_compile() -> None:
    client, _, _ = create_test_client()

    with client:
        created = client.post(
            "/api/v1/pskills",
            json={
                "key": "publish-gate-demo",
                "name": "Publish Gate Demo",
                "description": "Validate publish gate checks.",
            },
        ).json()
        skill_id = created["id"]

        publish_response = client.post(
            f"/api/v1/pskills/{skill_id}/publish",
            json={"publish_reason": "Gate coverage"},
        )
        publish_payload = publish_response.json()
        compile_request_id = publish_payload["compile_request"]["id"]
        compile_response = client.post(f"/api/v1/compiler/requests/{compile_request_id}/retry")
        compile_payload = compile_response.json()

        gate_response = client.post(f"/api/v1/pskills/{skill_id}/publish-gate", json={})
        testing_gate_response = client.post("/api/v1/testing/publish-gate/run", json={"pskill_id": skill_id})

    assert publish_response.status_code == 202
    assert compile_response.status_code == 200
    assert compile_payload["status"] == "succeeded"
    assert compile_payload["artifact_id"]

    assert gate_response.status_code == 201
    gate_payload = gate_response.json()
    assert gate_payload["pskill_definition_id"] == skill_id
    assert gate_payload["pskill_version_id"] == publish_payload["published_version"]["id"]
    assert gate_payload["status"] == "review_required"
    assert gate_payload["score"] >= 90
    assert gate_payload["result_json"]["decision"] == "require_human_review"
    assert gate_payload["result_json"]["compile_artifact_id"] == compile_payload["artifact_id"]
    assert gate_payload["result_json"]["checks"]["source"]["status"] == "passed"
    assert gate_payload["result_json"]["checks"]["compile"]["status"] == "passed"
    assert gate_payload["result_json"]["checks"]["tests"]["status"] == "review_required"
    assert gate_payload["result_json"]["coverage"]["scenario_count"] == 0
    evidence_refs = gate_payload["result_json"]["evidence_refs"]
    assert evidence_refs[0]["kind"] == "pskill_version"
    assert evidence_refs[0]["id"] == publish_payload["published_version"]["id"]
    assert evidence_refs[1]["kind"] == "compile_artifact"
    assert evidence_refs[1]["id"] == compile_payload["artifact_id"]
    assert evidence_refs[1]["compile_request_id"] == compile_request_id
    assert gate_payload["result_json"]["warnings"] == [
        {
            "check": "tests",
            "code": "no_active_test_scenarios",
            "message": "当前 PSkill 尚未配置 active 测试场景。",
        }
    ]

    assert testing_gate_response.status_code == 201
    assert testing_gate_response.json()["pskill_definition_id"] == skill_id
    assert testing_gate_response.json()["status"] == "review_required"


def test_tester_agent_generates_test_scenarios() -> None:
    client, _, _ = create_test_client()

    with client:
        created = client.post(
            "/api/v1/pskills",
            json={
                "key": "tester-generation-demo",
                "name": "Tester Generation Demo",
                "description": "Validate pskill.tester scenario generation.",
            },
        ).json()
        skill_id = created["id"]
        publish_response = client.post(
            f"/api/v1/pskills/{skill_id}/publish",
            json={"publish_reason": "Generate test scenarios"},
        )
        publish_payload = publish_response.json()
        compile_request_id = publish_payload["compile_request"]["id"]
        compile_response = client.post(f"/api/v1/compiler/requests/{compile_request_id}/retry")
        compile_payload = compile_response.json()

        generate_response = client.post(
            f"/api/v1/testing/pskills/{skill_id}/generate-scenarios",
            json={"compile_artifact_id": compile_payload["artifact_id"], "scenario_count": 1, "focus": "smoke"},
        )
        generated_payload = generate_response.json()
        agent_run_id = generated_payload["agent_run"]["id"]
        scenarios_response = client.get(f"/api/v1/pskills/{skill_id}/test-scenarios")
        agent_events_response = client.get(f"/api/v1/agent-runs/{agent_run_id}/events")
        model_calls_response = client.get(f"/api/v1/agent-runs/{agent_run_id}/model-calls")
        skill_activations_response = client.get(f"/api/v1/agent-runs/{agent_run_id}/skill-activations")

    assert publish_response.status_code == 202
    assert compile_response.status_code == 200
    assert compile_payload["artifact_id"]
    assert generate_response.status_code == 201
    assert generated_payload["agent_run"]["agent_key"] == "pskill.tester"
    assert generated_payload["agent_run"]["owner_type"] == "pskill_test_scenario_generation"
    assert generated_payload["agent_run"]["status"] == "succeeded"
    assert generated_payload["agent_run"]["output_payload"]["decision"] == "generated"
    assert generated_payload["agent_run"]["output_payload"]["compile_artifact_id"] == compile_payload["artifact_id"]
    assert generated_payload["diagnostics"] == []

    scenario = generated_payload["scenarios"][0]
    assert scenario["pskill_definition_id"] == skill_id
    assert scenario["target_compile_artifact_id"] == compile_payload["artifact_id"]
    assert scenario["name"] == "自动生成发布前冒烟场景"
    assert scenario["timeline"]["schema_version"] == "psop-skill-test-timeline/v1"
    assert [event["id"] for event in scenario["timeline"]["events"]] == [
        "generated_user_request",
        "expect_safe_guidance",
    ]
    assert scenarios_response.json()[0]["id"] == scenario["id"]

    event_types = [item["event_type"] for item in agent_events_response.json()]
    assert "testing.scenario_generation.started" in event_types
    assert "agent.skills.activated" in event_types
    assert "testing.scenario_generation.model_call.completed" in event_types
    assert "testing.scenario_generation.completed" in event_types
    assert skill_activations_response.status_code == 200
    assert {item["activation_context"]["package_name"] for item in skill_activations_response.json()} == {
        "pskill-tester",
        "ffmpeg-video-processing",
    }

    model_call = model_calls_response.json()[0]
    assert model_call["provider"] == "fake-openai-compatible"
    assert model_call["route_key"] == "text"
    assert model_call["request_payload"]["agent_prompt"]["definition_key"] == "skill_test.pre_publish"
    assert model_call["request_payload"]["prompt_payload"]["operation"] == "generate_psop_test_scenarios"
    assert model_call["response_payload"]["parsed"]["scenarios"][0]["name"] == "自动生成发布前冒烟场景"


def test_testing_suite_api_creates_and_runs_suite() -> None:
    client, _, _ = create_test_client()

    timeline = {
        "schema_version": "psop-skill-test-timeline/v1",
        "duration_ms": 5000,
        "events": [
            {
                "id": "suite_user_request",
                "lane_id": "input.text",
                "at_ms": 0,
                "event_kind": "terminal.text.input.v1",
                "mime_type": "text/plain",
                "payload_inline": "请完成一次套件测试任务。",
            },
            {
                "id": "suite_expect_completion",
                "lane_id": "expected.semantic",
                "at_ms": 1000,
                "expectation": "系统应确认现场步骤已完成。",
            },
        ],
    }

    with client:
        created = client.post(
            "/api/v1/pskills",
            json={
                "key": "testing-suite-demo",
                "name": "Testing Suite Demo",
                "description": "Validate testing suite API.",
            },
        ).json()
        skill_id = created["id"]
        publish_response = client.post(
            f"/api/v1/pskills/{skill_id}/publish",
            json={"publish_reason": "Suite API"},
        )
        publish_payload = publish_response.json()
        compile_request_id = publish_payload["compile_request"]["id"]
        compile_response = client.post(f"/api/v1/compiler/requests/{compile_request_id}/retry")
        compile_payload = compile_response.json()

        create_suite_response = client.post(
            "/api/v1/testing/suites",
            json={
                "pskill_id": skill_id,
                "pskill_version_id": publish_payload["published_version"]["id"],
                "name": "发布前回归套件",
                "suite_type": "pre_publish",
            },
        )
        suite_payload = create_suite_response.json()
        list_suites_response = client.get(f"/api/v1/testing/suites?pskill_id={skill_id}")
        create_scenario_response = client.post(
            f"/api/v1/testing/suites/{suite_payload['id']}/scenarios",
            json={
                "name": "套件场景",
                "description": "通过 testing/suites API 创建。",
                "duration_ms": 5000,
                "target_compile_artifact_id": compile_payload["artifact_id"],
                "timeline": timeline,
                "judge_policy": {"route_key": "text", "confidence_threshold": 0.7},
            },
        )
        suite_run_response = client.post(f"/api/v1/testing/suites/{suite_payload['id']}/run", json={})
        suite_run_payload = suite_run_response.json()
        test_run_id = suite_run_payload["runs"][0]["id"]
        get_run_response = client.get(f"/api/v1/testing/runs/{test_run_id}")
        run_events_response = client.get(f"/api/v1/testing/runs/{test_run_id}/events")
        publish_gate_response = client.post(f"/api/v1/pskills/{skill_id}/publish-gate", json={})

    assert publish_response.status_code == 202
    assert compile_response.status_code == 200
    assert create_suite_response.status_code == 201
    assert suite_payload["pskill_definition_id"] == skill_id
    assert suite_payload["pskill_version_id"] == publish_payload["published_version"]["id"]
    assert suite_payload["suite_type"] == "pre_publish"
    assert suite_payload["scenario_count"] == 0

    assert list_suites_response.status_code == 200
    assert list_suites_response.json()[0]["id"] == suite_payload["id"]

    assert create_scenario_response.status_code == 201
    scenario_payload = create_scenario_response.json()
    assert scenario_payload["suite_id"] == suite_payload["id"]
    assert scenario_payload["target_compile_artifact_id"] == compile_payload["artifact_id"]

    assert suite_run_response.status_code == 202
    assert suite_run_payload["suite"]["id"] == suite_payload["id"]
    assert suite_run_payload["suite"]["scenario_count"] == 1
    assert suite_run_payload["status"] == "passed"
    assert suite_run_payload["result_summary"]["total"] == 1
    assert suite_run_payload["runs"][0]["suite_id"] == suite_payload["id"]
    assert suite_run_payload["runs"][0]["status"] == "passed"
    assert suite_run_payload["runs"][0]["pskill_version_id"] == publish_payload["published_version"]["id"]

    assert get_run_response.status_code == 200
    assert get_run_response.json()["id"] == test_run_id
    assert get_run_response.json()["status"] == "passed"

    assert run_events_response.status_code == 200
    event_types = [item["event_type"] for item in run_events_response.json()]
    assert "testing.run.linked" in event_types
    assert "testing.run.evaluation_completed" in event_types

    assert publish_gate_response.status_code == 201
    gate_payload = publish_gate_response.json()
    assert gate_payload["status"] == "passed"
    gate_scenario = gate_payload["result_json"]["coverage"]["scenario_results"][0]
    assert gate_scenario["latest_run_id"] == test_run_id
    assert gate_scenario["agent_run_id"] == suite_run_payload["runs"][0]["agent_run_id"]
    assert gate_scenario["run_id"] == suite_run_payload["runs"][0]["run_id"]
    assert gate_scenario["artifact_id"] == compile_payload["artifact_id"]
    evidence_refs = gate_payload["result_json"]["evidence_refs"]
    assert any(
        item["kind"] == "pskill_test_run"
        and item["id"] == test_run_id
        and item["agent_run_id"] == suite_run_payload["runs"][0]["agent_run_id"]
        and item["run_id"] == suite_run_payload["runs"][0]["run_id"]
        for item in evidence_refs
    )
    assert any(
        item["kind"] == "agent_run"
        and item["id"] == suite_run_payload["runs"][0]["agent_run_id"]
        and item["agent_key"] == "pskill.tester"
        for item in evidence_refs
    )
    assert any(
        item["kind"] == "runtime_replay"
        and item["run_id"] == suite_run_payload["runs"][0]["run_id"]
        and item["test_run_id"] == test_run_id
        for item in evidence_refs
    )


def test_manual_compile_request_does_not_publish_draft() -> None:
    client, _, _ = create_test_client()

    with client:
        created = client.post(
            "/api/v1/pskills",
            json={
                "key": "manual-compile",
                "name": "Manual Compile",
                "description": "Compile without publishing.",
            },
        ).json()
        skill_id = created["id"]

        legacy_compile_response = client.post(f"/api/v1/compiler/skills/{skill_id}/compile")
        compile_response = client.post(f"/api/v1/compiler/pskills/{skill_id}/compile")
        compile_payload = compile_response.json()
        compile_request_id = compile_payload["id"]
        compile_agent_run_id = compile_payload["agent_run_id"]
        progress_response = client.get(f"/api/v1/compiler/requests/{compile_request_id}/progress")
        retry_response = client.post(f"/api/v1/compiler/requests/{compile_request_id}/retry")
        compiled_payload = retry_response.json()
        compile_agent_events_response = client.get(f"/api/v1/compiler/requests/{compile_request_id}/agent-events")
        compile_agent_run_response = client.get(f"/api/v1/agent-runs/{compile_agent_run_id}")
        compile_agent_model_calls_response = client.get(f"/api/v1/agent-runs/{compile_agent_run_id}/model-calls")
        artifact_response = client.get(f"/api/v1/compiler/artifacts/{compiled_payload['artifact_id']}")
        list_response = client.get("/api/v1/compiler/requests")
        detail_response = client.get(f"/api/v1/pskills/{skill_id}")

    assert legacy_compile_response.status_code == 404
    assert compile_response.status_code == 202
    assert compile_payload["trigger_type"] == "manual"
    assert compile_payload["status"] == "pending"
    assert compile_agent_run_id
    assert compile_payload["progress"]["current_stage"] == "compile_request_created"
    assert compile_payload["progress"]["terminal"] is False
    assert compile_payload["progress"]["percent"] > 0
    assert progress_response.status_code == 200
    assert progress_response.json()["stages"][-1]["label"] == "完成编译"
    assert retry_response.status_code == 200
    assert compiled_payload["status"] == "succeeded"
    assert compiled_payload["agent_run_id"] == compile_agent_run_id
    assert compiled_payload["artifact_id"]
    assert compiled_payload["progress"]["terminal"] is True
    assert compiled_payload["progress"]["terminal_status"] == "succeeded"
    assert compiled_payload["progress"]["percent"] == 100
    assert compile_agent_run_response.status_code == 200
    assert compile_agent_run_response.json()["agent_key"] == "pskill.compiler"
    assert compile_agent_run_response.json()["status"] == "succeeded"
    assert compile_agent_run_response.json()["output_payload"]["compile_request_id"] == compile_request_id
    assert compile_agent_model_calls_response.status_code == 200
    assert compile_agent_model_calls_response.json()[0]["provider"] == "llm_inference_gateway"
    assert compile_agent_events_response.status_code == 200
    event_types = [item["event_type"] for item in compile_agent_events_response.json()]
    assert "compile.request.linked" in event_types
    assert "compile.request.started" in event_types
    assert "compile.agent.model_call.completed" in event_types
    assert "compile.request.succeeded" in event_types
    assert list_response.json()[0]["agent_run_id"] == compile_agent_run_id
    assert list_response.json()[0]["progress"]["terminal_status"] == "succeeded"
    assert list_response.json()[0]["progress"]["percent"] == 100
    assert artifact_response.status_code == 200
    assert artifact_response.json()["compile_request"]["id"] == compile_request_id
    assert artifact_response.json()["compile_request"]["agent_run_id"] == compile_agent_run_id
    assert artifact_response.json()["compile_request"]["progress"]["percent"] == 100
    assert detail_response.json()["latest_published_version"] is None


def test_publish_skill_records_failed_startup_when_gitlab_fails() -> None:
    client, fake_gateway, _ = create_test_client()

    with client:
        created = client.post(
            "/api/v1/pskills",
            json={
                "key": "gitlab-freeze-failure",
                "name": "GitLab Freeze Failure",
                "description": "Validate publish startup failure record.",
            },
        ).json()
        skill_id = created["id"]
        fake_gateway.fail_get_skill_source = True

        publish_response = client.post(
            f"/api/v1/pskills/{skill_id}/publish",
            json={"publish_reason": "Expect GitLab failure"},
        )
        publishes_response = client.get(f"/api/v1/pskills/{skill_id}/publishes")

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
            "/api/v1/pskills",
            json={
                "key": "issue-one-demo",
                "name": "Issue One Demo",
                "description": "Validate issue #1 vertical slice.",
            },
        ).json()

        publish_response = client.post(
            f"/api/v1/pskills/{created['id']}/publish",
            json={"publish_reason": "Issue #1 acceptance publish"},
        )
        publish_payload = publish_response.json()
        compile_request_id = publish_payload["compile_request"]["id"]
        compile_response = client.post(f"/api/v1/compiler/requests/{compile_request_id}/retry")
        compile_payload = compile_response.json()
        artifact_id = compile_payload["artifact_id"]

        artifact_response = client.get(f"/api/v1/compiler/artifacts/{artifact_id}")
        validate_artifact_response = client.post(f"/api/v1/compiler/artifacts/{artifact_id}/validate")
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
            "/api/v1/runtime/invocations",
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
        run_events_response = client.get(f"/api/v1/runs/{run_id}/events")
        run_event_append_response = client.post(
            f"/api/v1/runs/{run_id}/events",
            json={
                "direction": "input",
                "event_kind": "terminal.text.input.v1",
                "mime_type": "text/plain",
                "payload_inline": "追加现场确认",
                "external_event_id": "issue-one-demo-extra-input",
            },
        )
        run_response = client.get(f"/api/v1/runs/{run_id}")
        trace_response = client.get(f"/api/v1/runs/{run_id}/traces")
        trace_payload = trace_response.json()
        replay_trace_id = next(
            trace["id"]
            for trace in trace_payload
            if trace["event_type"] == "runtime.wait_checkpoint.entered"
        )
        run_events_after_append_response = client.get(f"/api/v1/runs/{run_id}/events")
        replay_response = client.get(f"/api/v1/replay/runs/{run_id}")
        replay_trace_response = client.get(f"/api/v1/replay/traces/{replay_trace_id}")
        missing_replay_trace_response = client.get("/api/v1/replay/traces/not-a-trace")
        jobs_response = client.get("/api/v1/runtime/jobs")
        job_stats_response = client.get("/api/v1/runtime/jobs/stats")

    assert publish_response.status_code == 202
    assert publish_payload["compile_request"]["status"] == "pending"
    assert compile_response.status_code == 200
    assert compile_payload["status"] == "succeeded"
    assert artifact_response.status_code == 200
    assert validate_artifact_response.status_code == 200
    validate_payload = validate_artifact_response.json()
    assert validate_payload["artifact_id"] == artifact_id
    assert validate_payload["compile_request_id"] == compile_request_id
    assert validate_payload["pskill_version_id"] == publish_payload["published_version"]["id"]
    assert validate_payload["valid"] is True
    assert validate_payload["diagnostics"] == []
    assert validate_payload["graph_summary"]["nodes"] == [
        "start",
        "instruct_collect_context",
        "evaluate_collect_context",
        "final_verify",
        "terminal",
    ]
    assert validate_payload["normalized_artifact"]["formal_revision"] == "psop-eg-formal/v5"
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
    assert invocation_payload["compile_artifact_id"] == artifact_id
    assert invocation_payload["compile_request_id"] == compile_request_id
    assert invocation_payload["terminal_session_id"]
    initial_run_payload = initial_run_response.json()
    assert initial_run_payload["status"] == "waiting_input"
    assert initial_run_payload["compile_artifact_id"] == artifact_id
    assert initial_run_payload["compile_request_id"] == compile_request_id
    assert initial_run_payload["current_step"] == "collect_context"
    assert initial_run_payload["checkpoint_id"] == "collect_context_evidence"
    run_payload = run_response.json()
    assert run_payload["status"] == "succeeded"
    assert run_payload["terminal_session_id"] == invocation_payload["terminal_session_id"]
    assert run_payload["compile_artifact_id"] == artifact_id
    assert run_payload["compile_request_id"] == compile_request_id
    assert run_payload["latest_run_event_seq"] == 6
    assert run_payload["latest_terminal_seq"] == 6
    assert run_payload["latest_trace_seq"] == 7
    assert len(run_payload["binding_summary"]) == 2
    assert "测试任务已完成" in run_payload["final_output"]
    assert "final_verify" in fake_inference.calls[-1]["system_prompt"]

    event_types = [event["event_type"] for event in trace_payload]
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
    assert run_events_response.status_code == 200
    assert [item["direction"] for item in run_events_response.json()] == ["input", "output"]
    assert run_event_append_response.status_code == 202
    assert run_event_append_response.json()["seq_no"] == 3
    assert [item["seq_no"] for item in run_events_after_append_response.json()] == [1, 2, 3, 4, 5, 6]

    replay_payload = replay_response.json()
    replay_trace_payload = replay_trace_response.json()
    assert [item["title"] for item in replay_payload["timeline"]][:6] == [
        "终端输入",
        "绑定解析",
        "runtime.start.completed",
        "终端输出",
        "等待现场证据",
        "LLM 输出",
    ]
    assert len(replay_payload["run_events"]) == 6
    assert "terminal_events" not in replay_payload
    assert "trace_events" not in replay_payload
    assert len(replay_payload["bindings"]) == 2
    assert replay_payload["run"]["final_output"] == run_payload["final_output"]
    assert replay_payload["provenance"]["invocation_id"] == invocation_payload["id"]
    assert replay_payload["provenance"]["run_id"] == run_id
    assert replay_payload["provenance"]["pskill_version_id"] == publish_payload["published_version"]["id"]
    assert replay_payload["provenance"]["compile_artifact_id"] == artifact_id
    assert replay_payload["provenance"]["compile_request_id"] == compile_request_id
    assert replay_payload["provenance"]["latest_session_token_snapshot_id"]
    assert replay_payload["provenance"]["latest_session_token_seq"] == run_payload["latest_snapshot_seq"]
    eg_node_path = replay_payload["eg_node_path"]
    assert [item["node_id"] for item in eg_node_path] == [
        "start",
        "instruct_collect_context",
        "instruct_collect_context",
        "evaluate_collect_context",
        "final_verify",
        "terminal",
    ]
    assert eg_node_path[1]["checkpoint_id"] == "collect_context_evidence"
    assert eg_node_path[1]["event_type"] == "runtime.wait_checkpoint.entered"
    assert {item["trace_id"] for item in eg_node_path} <= {item["id"] for item in replay_payload["run_traces"]}
    assert replay_trace_response.status_code == 200
    assert replay_trace_payload["trace"]["id"] == replay_trace_id
    assert replay_trace_payload["trace"]["run_id"] == run_id
    assert replay_trace_payload["run"]["id"] == run_id
    assert replay_trace_payload["timeline_item"]["source_kind"] == "run_trace"
    assert replay_trace_payload["timeline_item"]["source_id"] == replay_trace_id
    assert replay_trace_payload["timeline_item"]["event_type"] == "runtime.wait_checkpoint.entered"
    assert replay_trace_payload["replay"]["run"]["id"] == run_id
    assert replay_trace_payload["replay"]["provenance"]["compile_request_id"] == compile_request_id
    assert replay_trace_payload["replay"]["eg_node_path"][1]["trace_id"] == replay_trace_id
    assert missing_replay_trace_response.status_code == 404

    jobs = jobs_response.json()
    assert {job["job_type"] for job in jobs} >= {PSKILL_COMPILE_JOB_TYPE, RUNTIME_STEP_JOB_TYPE}
    assert all(job["status"] == "succeeded" for job in jobs)
    compile_job = next(job for job in jobs if job["job_type"] == PSKILL_COMPILE_JOB_TYPE)
    runtime_job = next(job for job in jobs if job["job_type"] == RUNTIME_STEP_JOB_TYPE)
    assert compile_job["started_at"]
    assert compile_job["finished_at"]
    assert compile_job["duration_ms"] is not None
    assert compile_job["token_usage"]["total_tokens"] >= 15
    assert runtime_job["started_at"]
    assert runtime_job["finished_at"]
    assert runtime_job["progress"]["percent"] == 100
    assert runtime_job["token_usage"]["total_tokens"] >= 45
    job_stats = job_stats_response.json()
    assert job_stats["succeeded"] >= 2
    assert job_stats["token_usage"]["total_tokens"] >= 60


def test_runtime_run_can_be_cancelled_through_runs_api() -> None:
    client, _, _ = create_test_client()

    with client:
        created = client.post(
            "/api/v1/pskills",
            json={
                "key": "runtime-cancel-demo",
                "name": "Runtime Cancel Demo",
                "description": "Validate cancelling a waiting runtime run.",
            },
        ).json()

        publish_response = client.post(
            f"/api/v1/pskills/{created['id']}/publish",
            json={"publish_reason": "Runtime cancel publish"},
        )
        compile_request_id = publish_response.json()["compile_request"]["id"]
        compile_response = client.post(f"/api/v1/compiler/requests/{compile_request_id}/retry")

        invocation_response = client.post(
            "/api/v1/runtime/invocations",
            json={
                "skill_key": "runtime-cancel-demo",
                "input_envelope": {"user_input": "启动后等待现场证据"},
                "gateway_type": "web",
            },
        )
        invocation_payload = invocation_response.json()
        run_id = invocation_payload["run_id"]
        initial_run_response = client.get(f"/api/v1/runs/{run_id}")
        cancel_response = client.post(f"/api/v1/runs/{run_id}/cancel", json={"reason": "用户取消本次运行"})
        second_cancel_response = client.post(f"/api/v1/runs/{run_id}/cancel", json={"reason": "不应覆盖原始原因"})
        run_response = client.get(f"/api/v1/runs/{run_id}")
        invocation_detail_response = client.get(f"/api/v1/gateway/invocations/{invocation_payload['id']}")
        terminal_session_response = client.get(f"/api/v1/terminal/sessions/{run_id}")
        snapshots_response = client.get(f"/api/v1/runs/{run_id}/snapshots")
        traces_response = client.get(f"/api/v1/runs/{run_id}/traces")
        replay_response = client.get(f"/api/v1/replay/runs/{run_id}")

    assert publish_response.status_code == 202
    assert compile_response.status_code == 200
    assert invocation_response.status_code == 201
    initial_run_payload = initial_run_response.json()
    assert initial_run_payload["status"] == "waiting_input"

    assert cancel_response.status_code == 200
    cancelled = cancel_response.json()
    assert cancelled["status"] == "cancelled"
    assert cancelled["runtime_phase"] == "cancelled"
    assert cancelled["exit_reason"] == "用户取消本次运行"
    assert cancelled["ended_at"]
    assert cancelled["latest_snapshot_seq"] == initial_run_payload["latest_snapshot_seq"] + 1
    assert cancelled["latest_trace_seq"] == initial_run_payload["latest_trace_seq"] + 1

    assert second_cancel_response.status_code == 200
    assert second_cancel_response.json()["exit_reason"] == "用户取消本次运行"
    assert run_response.json()["status"] == "cancelled"
    assert invocation_detail_response.json()["status"] == "cancelled"

    terminal_session = terminal_session_response.json()["terminal_session"]
    assert terminal_session["status"] == "closed"
    assert terminal_session["closed_at"]

    snapshots = snapshots_response.json()
    assert snapshots[-1]["token_payload"]["status"] == "cancelled"
    assert snapshots[-1]["token_payload"]["phase"] == "cancelled"
    assert snapshots[-1]["token_payload"]["control"]["cancelled"]["reason"] == "用户取消本次运行"
    assert snapshots[-1]["selection_summary"] == {"selected": None, "reason": "cancelled"}

    traces = traces_response.json()
    assert traces[-1]["event_type"] == "runtime.cancelled"
    assert traces[-1]["payload"] == {"reason": "用户取消本次运行"}
    assert replay_response.json()["run"]["status"] == "cancelled"


def test_skill_debug_invocation_uses_runtime_without_skill_test_case() -> None:
    client, _, _ = create_test_client()

    with client:
        created = client.post(
            "/api/v1/pskills",
            json={
                "key": "skill-debug-terminal",
                "name": "Skill Debug Terminal",
                "description": "Validate direct skill debug terminal flow.",
            },
        ).json()
        publish_response = client.post(
            f"/api/v1/pskills/{created['id']}/publish",
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
            f"/api/v1/terminal/sessions/{run_id}/events",
            data={
                "event": json.dumps(
                    {
                        "direction": "input",
                        "text": "现场证据已确认",
                        "external_event_id": "terminal-debug-upload-000001",
                    },
                    ensure_ascii=False,
                )
            },
            files=[("files", ("debug-photo.png", b"debug-image", "image/png"))],
        )
        uploaded_event = upload_response.json()["event"]
        uploaded_image_part = next(part for part in uploaded_event["parts"] if part["kind"] == "image")
        uploaded_content_response = client.get(
            f"/api/v1/terminal/sessions/{run_id}/events/{uploaded_event['id']}/parts/{uploaded_image_part['part_id']}/content"
        )
        uploaded_content_range_response = client.get(
            f"/api/v1/terminal/sessions/{run_id}/events/{uploaded_event['id']}/parts/{uploaded_image_part['part_id']}/content",
            headers={"Range": "bytes=0-4"},
        )
        final_run_response = client.get(f"/api/v1/runs/{run_id}")
        terminal_events_response = client.get(f"/api/v1/terminal/sessions/{run_id}/events")
        replay_response = client.get(f"/api/v1/replay/runs/{run_id}")
        old_cases_response = client.get(f"/api/v1/pskills/{created['id']}/test-cases", params={"mode": "debug"})
        test_jobs_response = client.get(
            "/api/v1/runtime/jobs",
            params={"job_type": LEGACY_SKILL_TEST_TIMELINE_DRIVER_JOB_TYPE},
        )

    assert invocation_response.status_code == 201
    assert invocation["run_id"]
    assert invocation["gateway_type"] == "terminal"
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
    assert uploaded_event["event_kind"] == "terminal.multimodal.input.v1"
    assert uploaded_event["mime_type"] == "multipart/mixed"
    assert [part["kind"] for part in uploaded_event["parts"]] == ["text", "image"]
    assert uploaded_event["parts"][0]["text"] == "现场证据已确认"
    assert uploaded_image_part["metadata"]["filename"] == "debug-photo.png"
    assert "object_key" not in uploaded_image_part["metadata"]
    assert uploaded_content_response.status_code == 200
    assert uploaded_content_response.headers["content-type"] == "image/png"
    assert uploaded_content_response.content == b"debug-image"
    assert uploaded_content_range_response.status_code == 206
    assert uploaded_content_range_response.content == b"debug"
    assert final_run_response.json()["status"] == "succeeded"
    assert any(event["event_kind"] == "terminal.multimodal.input.v1" for event in terminal_events_response.json())
    assert replay_response.status_code == 200
    assert replay_response.json()["run"]["id"] == run_id
    assert len(replay_response.json()["run_events"]) >= 3
    assert "terminal_events" not in replay_response.json()
    assert "trace_events" not in replay_response.json()
    assert old_cases_response.status_code == 404
    assert test_jobs_response.status_code == 200
    assert test_jobs_response.json() == []


def test_terminal_events_accept_multipart_multimodal_parts_and_feed_llm() -> None:
    client, _, fake_inference = create_test_client()

    with client:
        created = client.post(
            "/api/v1/pskills",
            json={
                "key": "terminal-multipart-event",
                "name": "Terminal Multipart Event",
                "description": "Validate one terminal event can carry ordered multimodal parts.",
            },
        ).json()
        publish_response = client.post(
            f"/api/v1/pskills/{created['id']}/publish",
            json={"publish_reason": "Multipart terminal publish"},
        )
        compile_request_id = publish_response.json()["compile_request"]["id"]
        client.post(f"/api/v1/compiler/requests/{compile_request_id}/retry")
        invocation_response = client.post(
            "/api/v1/gateway/invocations",
            json={
                "skill_key": "terminal-multipart-event",
                "gateway_type": "terminal",
                "terminal_context": {"terminal_kind": "web", "operator_mode": "debug"},
                "input_envelope": {"user_input": "启动多模态验证"},
            },
        )
        run_id = invocation_response.json()["run_id"]
        event_payload = {
            "direction": "input",
            "text": "请结合现场图像、视频和音频判断故障。",
        }
        append_response = client.post(
            f"/api/v1/terminal/sessions/{run_id}/events",
            data={"event": json.dumps(event_payload, ensure_ascii=False)},
            files=[
                ("files", ("fault.png", b"image-bytes", "image/png")),
                ("files", ("clip.mp4", b"video-bytes", "video/mp4")),
                ("files", ("note.wav", b"audio-bytes", "audio/wav")),
            ],
        )
        appended_event = append_response.json()["event"]
        image_part = next(part for part in appended_event["parts"] if part["kind"] == "image")
        image_part_content_response = client.get(
            f"/api/v1/terminal/sessions/{run_id}/events/{appended_event['id']}/parts/{image_part['part_id']}/content"
        )
        final_run_response = client.get(f"/api/v1/runs/{run_id}")
        snapshots_response = client.get(f"/api/v1/runs/{run_id}/snapshots")
        terminal_events_response = client.get(f"/api/v1/terminal/sessions/{run_id}/events")
        event_parts_response = client.get(f"/api/v1/runs/{run_id}/event-parts")
        replay_response = client.get(f"/api/v1/replay/runs/{run_id}")
        with client.app.state.db_manager.session() as session:
            artifact_objects = [
                session.get(ArtifactObject, part["artifact_object_id"])
                for part in appended_event["parts"]
                if part.get("artifact_object_id")
            ]
            artifact_object_keys = [item.object_key for item in artifact_objects if item]

    assert append_response.status_code == 202
    assert appended_event["event_kind"] == "terminal.multimodal.input.v1"
    assert appended_event["mime_type"] == "multipart/mixed"
    assert [part["part_id"] for part in appended_event["parts"]] == ["text_1", "image_1", "video_1", "audio_1"]
    assert [part["order_index"] for part in appended_event["parts"]] == [1, 2, 3, 4]
    assert appended_event["parts"][0]["text"] == "请结合现场图像、视频和音频判断故障。"
    assert all(not part.get("caption") for part in appended_event["parts"])
    media_parts = [part for part in appended_event["parts"] if part["kind"] != "text"]
    assert [part["kind"] for part in media_parts] == ["image", "video", "audio"]
    assert all(part["artifact_object_id"] for part in media_parts)
    assert all("object_key" not in part["metadata"] for part in media_parts)
    assert artifact_object_keys
    assert all(key.startswith(f"run-event-parts/{run_id}/") for key in artifact_object_keys)
    assert all("terminal-event-parts" not in key for key in artifact_object_keys)

    assert image_part_content_response.status_code == 200
    assert image_part_content_response.headers["content-type"] == "image/png"
    assert image_part_content_response.content == b"image-bytes"
    assert final_run_response.json()["status"] == "succeeded"

    final_token = snapshots_response.json()[-1]["token_payload"]
    latest_evidence = final_token["control"]["latest_evidence"]
    assert latest_evidence["id"] == appended_event["id"]
    assert [part["part_id"] for part in latest_evidence["parts"]] == ["text_1", "image_1", "video_1", "audio_1"]
    assert "请结合现场图像" in latest_evidence["input_bundle"]["text"]
    assert "fault.png" in latest_evidence["input_bundle"]["text"]
    assert "object_key" not in latest_evidence["input_bundle"]["text"]
    assert "terminal-event-parts" not in latest_evidence["input_bundle"]["text"]
    assert "请结合现场图像" in final_token["input_envelope"]["user_input"]

    multimodal_calls = [call for call in fake_inference.calls if call.get("attachments")]
    assert multimodal_calls
    assert any(call["attachments"] == "fault.png,clip.mp4,note.wav" for call in multimodal_calls)

    terminal_events = terminal_events_response.json()
    persisted_event = next(event for event in terminal_events if event["id"] == appended_event["id"])
    assert [part["kind"] for part in persisted_event["parts"]] == ["text", "image", "video", "audio"]
    assert event_parts_response.status_code == 200
    event_parts = [part for part in event_parts_response.json() if part["run_event_id"] == appended_event["id"]]
    assert [part["part_id"] for part in event_parts] == ["text_1", "image_1", "video_1", "audio_1"]
    replay_event = next(event for event in replay_response.json()["run_events"] if event["id"] == appended_event["id"])
    assert [part["part_id"] for part in replay_event["parts"]] == ["text_1", "image_1", "video_1", "audio_1"]


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
            "/api/v1/pskills",
            json={
                "key": "terminal-upload-object-store-failure",
                "name": "Terminal Upload Object Store Failure",
                "description": "Validate upload failure is surfaced as JSON.",
            },
        ).json()
        publish_response = client.post(
            f"/api/v1/pskills/{created['id']}/publish",
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
            f"/api/v1/terminal/sessions/{run_id}/events",
            data={"event": json.dumps({"direction": "input", "text": "图片证据"}, ensure_ascii=False)},
            files=[("files", ("fault.jpg", b"image-bytes", "image/jpeg"))],
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
            "/api/v1/pskills",
            json={
                "key": "ws-terminal-demo",
                "name": "WS Terminal Demo",
                "description": "Validate terminal websocket broadcast.",
            },
        ).json()
        publish_response = client.post(
            f"/api/v1/pskills/{created['id']}/publish",
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
            previous_run = client.get(f"/api/v1/runs/{run_id}").json()
            previous_trace_seq = previous_run["latest_trace_seq"]
            previous_snapshot_seq = previous_run["latest_snapshot_seq"]
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
            terminal_events_response = client.get(f"/api/v1/terminal/sessions/{run_id}/events")
            appended_events = [
                event
                for event in terminal_events_response.json()
                if event["seq_no"] >= append_response.json()["seq_no"]
            ]
            run_traces_response = client.get(f"/api/v1/runs/{run_id}/traces")
            appended_traces = [
                trace
                for trace in run_traces_response.json()
                if trace["seq_no"] > previous_trace_seq
            ]
            snapshots_response = client.get(f"/api/v1/runs/{run_id}/snapshots")
            appended_snapshots = [
                snapshot
                for snapshot in snapshots_response.json()
                if snapshot["seq_no"] > previous_snapshot_seq
            ]
            messages = [
                websocket.receive_json()
                for _ in range(len(appended_events) + len(appended_traces) + len(appended_snapshots) + 1)
            ]

    assert invocation_response.status_code == 201
    assert connected["event_type"] == "ws.connected"
    assert append_response.status_code == 202
    assert terminal_events_response.status_code == 200
    assert run_traces_response.status_code == 200
    assert snapshots_response.status_code == 200
    terminal_messages = [message for message in messages if message["event_type"] == "run.event.appended"]
    trace_messages = [message for message in messages if message["event_type"] == "run.trace.appended"]
    snapshot_messages = [
        message for message in messages if message["event_type"] == "session_token.snapshot.appended"
    ]
    run_updated_messages = [message for message in messages if message["event_type"] == "run.updated"]
    assert len(terminal_messages) == len(appended_events)
    assert len(trace_messages) == len(appended_traces)
    assert len(snapshot_messages) == len(appended_snapshots)
    assert len(run_updated_messages) == 1
    assert [message["seq_no"] for message in terminal_messages] == [event["seq_no"] for event in appended_events]
    assert [message["seq_no"] for message in trace_messages] == [trace["seq_no"] for trace in appended_traces]
    assert [message["seq_no"] for message in snapshot_messages] == [
        snapshot["seq_no"] for snapshot in appended_snapshots
    ]
    assert terminal_messages[0]["payload"]["payload_inline"] == "WS 输入"
    assert terminal_messages[0]["seq_no"] == append_response.json()["seq_no"]
    assert [message["payload"]["direction"] for message in terminal_messages] == ["input", "output", "output", "output"]
    assert any("测试任务已完成" in str(message["payload"]["payload_inline"]) for message in terminal_messages)
    assert "runtime.final.completed" in {message["payload"]["event_type"] for message in trace_messages}
    assert snapshot_messages[-1]["payload"]["snapshot_hash"]
    assert run_updated_messages[0]["payload"]["status"] == "succeeded"
    assert "测试任务已完成" in run_updated_messages[0]["payload"]["final_output"]


def test_run_websocket_broadcasts_binding_updates() -> None:
    client, _, _ = create_test_client()

    with client:
        created = client.post(
            "/api/v1/pskills",
            json={
                "key": "ws-binding-demo",
                "name": "WS Binding Demo",
                "description": "Validate binding websocket broadcast.",
            },
        ).json()
        publish_response = client.post(
            f"/api/v1/pskills/{created['id']}/publish",
            json={"publish_reason": "WS binding publish"},
        )
        compile_request_id = publish_response.json()["compile_request"]["id"]
        client.post(f"/api/v1/compiler/requests/{compile_request_id}/retry")
        invocation_response = client.post(
            "/api/v1/gateway/invocations",
            json={
                "skill_key": "ws-binding-demo",
                "input_envelope": {"user_input": "启动 binding WS 验证"},
                "gateway_type": "terminal",
                "terminal_context": {"terminal_kind": "web"},
            },
        )
        run_id = invocation_response.json()["run_id"]

        with client.websocket_connect(f"/ws/runs/{run_id}") as websocket:
            connected = websocket.receive_json()
            previous_trace_seq = client.get(f"/api/v1/runs/{run_id}").json()["latest_trace_seq"]
            resolve_response = client.post(
                f"/api/v1/runs/{run_id}/bindings/resolve",
                json={
                    "bindings": [
                        {
                            "requirement_key": "terminal.input",
                            "target_kind": "web_terminal",
                            "target_ref": "custom-terminal-target",
                            "channel": "input",
                        }
                    ]
                },
            )
            messages = [websocket.receive_json() for _ in range(3)]
            run_traces_response = client.get(f"/api/v1/runs/{run_id}/traces")
            appended_traces = [
                trace
                for trace in run_traces_response.json()
                if trace["seq_no"] > previous_trace_seq
            ]

    assert invocation_response.status_code == 201
    assert connected["event_type"] == "ws.connected"
    assert resolve_response.status_code == 200
    assert run_traces_response.status_code == 200
    event_types = [message["event_type"] for message in messages]
    assert event_types == ["run.trace.appended", "binding.updated", "run.updated"]
    assert [trace["event_type"] for trace in appended_traces] == ["binding.updated"]
    assert messages[0]["payload"]["event_type"] == "binding.updated"
    assert messages[1]["payload"]["bindings"]
    assert messages[1]["payload"]["bindings"][0]["target_ref"] == "custom-terminal-target"
    assert messages[2]["payload"]["binding_summary"][0]["target_ref"] == "custom-terminal-target"


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
            "/api/v1/pskills",
            json={
                "key": "skill-test-scenario",
                "name": "Skill Test Scenario",
                "description": "Validate black-box timeline scenario flow.",
            },
        ).json()
        publish_response = client.post(
            f"/api/v1/pskills/{created['id']}/publish",
            json={"publish_reason": "Scenario test publish"},
        )
        compile_request_id = publish_response.json()["compile_request"]["id"]
        client.post(f"/api/v1/compiler/requests/{compile_request_id}/retry")

        scenario_response = client.post(
            f"/api/v1/pskills/{created['id']}/test-scenarios",
            json={
                "name": "雨伞维修时序场景",
                "description": "时间轴驱动输入，时间点以前判断输出。",
                "duration_ms": 5000,
                "timeline": timeline,
                "judge_policy": {"route_key": "text", "confidence_threshold": 0.7},
            },
        )
        scenario = scenario_response.json()
        upload_response = client.post(
            f"/api/v1/pskills/{created['id']}/test-scenarios/{scenario['id']}/assets",
            data={"name": "伞骨图片", "description": "测试图片", "lane_id": "input.image"},
            files={"file": ("umbrella.png", b"fake-image", "image/png")},
        )
        uploaded_asset = upload_response.json()
        patched_timeline = copy.deepcopy(scenario["timeline"])
        patched_timeline["events"].append(
            {
                "id": "fault_photo",
                "lane_id": "input.image",
                "at_ms": 0,
                "event_kind": "terminal.image.input.v1",
                "mime_type": "image/*",
                "asset_id": uploaded_asset["id"],
                "payload_inline": "伞骨近照",
            }
        )
        patch_response = client.patch(
            f"/api/v1/pskills/{created['id']}/test-scenarios/{scenario['id']}",
            json={"timeline": patched_timeline},
        )
        scenario = patch_response.json()
        assets_response = client.get(f"/api/v1/pskills/{created['id']}/test-scenarios/{scenario['id']}/assets")
        asset_content_response = client.get(
            f"/api/v1/pskills/{created['id']}/test-scenarios/{scenario['id']}/assets/{uploaded_asset['id']}/content"
        )
        old_case_response = client.get(f"/api/v1/pskills/{created['id']}/test-cases")
        old_runs_response = client.get("/api/v1/skill-test-runs/not-found")

        start_response = client.post(f"/api/v1/pskills/{created['id']}/test-scenarios/{scenario['id']}/runs", json={})
        scenario_run = start_response.json()
        terminal_events_response = client.get(f"/api/v1/terminal/sessions/{scenario_run['run_id']}/events")
        jobs_response = client.get("/api/v1/runtime/jobs")
        evaluate_response = client.post(f"/api/v1/skill-test-scenario-runs/{scenario_run['id']}/evaluate")
        review_response = client.get(f"/api/v1/skill-test-scenario-runs/{scenario_run['id']}/review")
        list_response = client.get(f"/api/v1/pskills/{created['id']}/test-scenarios")
        runs_response = client.get(f"/api/v1/pskills/{created['id']}/test-scenarios/{scenario['id']}/runs")
        tester_agent_run_response = client.get(f"/api/v1/agent-runs/{scenario_run['agent_run_id']}")
        tester_agent_events_response = client.get(f"/api/v1/agent-runs/{scenario_run['agent_run_id']}/events")
        tester_agent_model_calls_response = client.get(f"/api/v1/agent-runs/{scenario_run['agent_run_id']}/model-calls")

        review = review_response.json()
        cursor = review["cursor_anchors"][-1]
        fork_response = client.post(
            f"/api/v1/skill-test-scenario-runs/{scenario_run['id']}/fork-scenario",
            json={"cursor": cursor, "name": "从切面继续的场景"},
        )
        forked = fork_response.json()
        fork_assets_response = client.get(f"/api/v1/pskills/{created['id']}/test-scenarios/{forked['id']}/assets")
        fork_asset_content_response = client.get(
            f"/api/v1/pskills/{created['id']}/test-scenarios/{forked['id']}/assets/{fork_assets_response.json()[0]['id']}/content"
        )
        fork_debug_response = client.post(
            f"/api/v1/skill-test-scenario-runs/{scenario_run['id']}/fork-debug",
            json={"cursor": cursor},
        )

    assert scenario_response.status_code == 201
    assert scenario["suite_id"]
    lane_ids = [lane["id"] for lane in scenario["timeline"]["lanes"]]
    assert lane_ids[:2] == ["sensor.gps", "sensor.pose3d"]
    assert scenario["timeline"]["lanes"][-1] == {"id": "expected.semantic", "kind": "output", "label": "文本"}
    assert scenario["timeline"]["schema_version"] == "psop-skill-test-timeline/v1"
    assert upload_response.status_code == 201
    assert patch_response.status_code == 200
    assert upload_response.json()["mime_type"] == "image/png"
    assert assets_response.json()[0]["id"] == upload_response.json()["id"]
    assert asset_content_response.status_code == 200
    assert asset_content_response.content == b"fake-image"
    assert asset_content_response.headers["content-type"] == "image/png"
    assert "umbrella.png" in asset_content_response.headers["content-disposition"]
    assert old_case_response.status_code == 404
    assert old_runs_response.status_code == 404

    assert start_response.status_code == 202
    assert scenario_run["driver_status"] == "completed"
    assert scenario_run["suite_id"] == scenario["suite_id"]
    assert scenario_run["pskill_version_id"]
    assert scenario_run["artifact_id"]
    assert scenario_run["agent_run_id"]
    assert scenario_run["driver_cursor"] == 2
    assert scenario_run["result_summary"]["total"] == 1
    assert scenario_run["result_summary"]["passed"] == 1
    assert scenario_run["status"] == "passed"
    assert sorted(event["event_id"] for event in scenario_run["driver_events"]) == ["fault_photo", "initial_fault_context"]
    assert all(event["run_event_id"] == event["terminal_event_id"] for event in scenario_run["driver_events"])

    terminal_events = terminal_events_response.json()
    scripted_inputs = [event for event in terminal_events if event["direction"] == "input"]
    text_inputs = [event for event in scripted_inputs if event["event_kind"] == "terminal.text.input.v1"]
    image_inputs = [event for event in scripted_inputs if event["event_kind"] == "terminal.image.input.v1"]
    assert [event["payload_inline"] for event in text_inputs] == ["请检查这把伞如何修复"]
    assert text_inputs[0]["external_event_id"] == (
        f"skill-test-scenario-run:{scenario_run['id']}:timeline:initial_fault_context"
    )
    assert image_inputs[0]["payload_inline"]["asset_id"] == upload_response.json()["id"]
    assert image_inputs[0]["payload_inline"]["name"] == "伞骨图片"
    assert image_inputs[0]["payload_inline"]["description"] == "伞骨近照"
    assert any(event["direction"] == "output" and "测试任务已完成" in str(event["payload_inline"]) for event in terminal_events)
    assert any(job["job_type"] == PSKILL_TEST_JOB_TYPE and job["status"] == "succeeded" for job in jobs_response.json())
    assert any(
        job["job_type"] == PSKILL_TEST_JOB_TYPE
        and job["token_usage"]
        and job["token_usage"]["total_tokens"] >= 15
        for job in jobs_response.json()
    )
    assert any(call["route_key"] == "text" and "黑盒时序测试 Judge" in call["system_prompt"] for call in fake_inference.calls)

    assert evaluate_response.status_code == 200
    assert evaluate_response.json()["status"] == "passed"
    assert review_response.status_code == 200
    assert review["scenario"]["id"] == scenario["id"]
    assert review["scenario_run"]["id"] == scenario_run["id"]
    assert review["scenario_run"]["agent_run_id"] == scenario_run["agent_run_id"]
    assert review["expectation_evaluations"][0]["expectation_id"] == "expect_completion"
    assert review["expectation_evaluations"][0]["status"] == "passed"
    assert review["expectation_evaluations"][0]["evidence_refs"] == [{"kind": "run_event", "seq_no": 4}]
    judge_raw_response = review["expectation_evaluations"][0]["raw_response"]
    assert judge_raw_response["request"]["route_key"] == "text"
    assert judge_raw_response["request"]["prompt_payload"]["expectation"] == "系统应确认现场步骤已完成。"
    assert judge_raw_response["request"]["prompt_payload"]["run_status"] == "succeeded"
    assert isinstance(judge_raw_response["request"]["prompt_payload"]["run_events_before_cutoff"], list)
    assert "terminal_outputs_before_cutoff" not in judge_raw_response["request"]["prompt_payload"]
    assert "terminal_output_count_before_cutoff" not in judge_raw_response["request"]["prompt_payload"]
    assert judge_raw_response["request"]["user_prompt"] == json.dumps(
        judge_raw_response["request"]["prompt_payload"],
        ensure_ascii=False,
        sort_keys=True,
    )
    assert judge_raw_response["parsed"]["status"] == "passed"
    assert judge_raw_response["content"]
    assert review["replay_timeline"]
    assert review["stage_outputs"][0]["judge_result"]["evidence_refs"] == [{"kind": "run_event", "seq_no": 4}]
    assert list_response.json()[0]["latest_run"]["id"] == scenario_run["id"]
    assert list_response.json()[0]["latest_run"]["agent_run_id"] == scenario_run["agent_run_id"]
    assert runs_response.json()[0]["id"] == scenario_run["id"]
    assert tester_agent_run_response.status_code == 200
    assert tester_agent_run_response.json()["agent_key"] == "pskill.tester"
    assert tester_agent_run_response.json()["status"] == "succeeded"
    assert tester_agent_run_response.json()["output_payload"]["decision"] == "pass"
    tester_event_types = [item["event_type"] for item in tester_agent_events_response.json()]
    assert "testing.run.linked" in tester_event_types
    assert "testing.run.evaluation_started" in tester_event_types
    assert "testing.agent.model_call.completed" in tester_event_types
    assert "testing.run.evaluation_completed" in tester_event_types
    assert tester_agent_model_calls_response.json()[0]["provider"] == "fake-openai-compatible"
    assert tester_agent_model_calls_response.json()[0]["request_payload"]["expectation_id"] == "expect_completion"

    assert fork_response.status_code == 201
    assert forked["fork_seed"]["source_scenario_run_id"] == scenario_run["id"]
    assert forked["fork_seed"]["terminal_seq"] == cursor["terminal_seq"]
    forked_image_event = next(event for event in forked["timeline"]["events"] if event["id"] == "fork_fault_photo")
    forked_assets = fork_assets_response.json()
    assert fork_assets_response.status_code == 200
    assert forked_image_event["asset_id"] != upload_response.json()["id"]
    assert forked_assets[0]["id"] == forked_image_event["asset_id"]
    assert forked_assets[0]["name"] == "伞骨图片"
    assert forked_assets[0]["filename"] == "umbrella.png"
    assert forked_assets[0]["artifact_object_id"] == upload_response.json()["artifact_object_id"]
    assert fork_asset_content_response.status_code == 200
    assert fork_asset_content_response.content == b"fake-image"
    assert fork_debug_response.status_code == 201
    assert fork_debug_response.json()["terminal_context"]["operator_mode"] == "debug"
    assert fork_debug_response.json()["terminal_context"]["debug_context"]["kind"] == "skill_debug"


def test_skill_test_scenario_timeline_parts_append_single_terminal_event() -> None:
    client, _, fake_inference = create_test_client()

    with client:
        created = client.post(
            "/api/v1/pskills",
            json={
                "key": "skill-test-multimodal-parts",
                "name": "Skill Test Multimodal Parts",
                "description": "Validate timeline parts are sent as one terminal event.",
            },
        ).json()
        publish_response = client.post(
            f"/api/v1/pskills/{created['id']}/publish",
            json={"publish_reason": "Scenario multimodal publish"},
        )
        compile_request_id = publish_response.json()["compile_request"]["id"]
        client.post(f"/api/v1/compiler/requests/{compile_request_id}/retry")
        scenario_response = client.post(
            f"/api/v1/pskills/{created['id']}/test-scenarios",
            json={"name": "多模态现场包", "duration_ms": 5000, "timeline": {"events": []}},
        )
        scenario = scenario_response.json()
        image_upload = client.post(
            f"/api/v1/pskills/{created['id']}/test-scenarios/{scenario['id']}/assets",
            data={"name": "现场照片", "description": "电控柜照片", "lane_id": "input.image"},
            files={"file": ("panel.png", b"panel-image", "image/png")},
        ).json()
        video_upload = client.post(
            f"/api/v1/pskills/{created['id']}/test-scenarios/{scenario['id']}/assets",
            data={"name": "启动视频", "description": "设备启动过程", "lane_id": "input.video"},
            files={"file": ("startup.mp4", b"startup-video", "video/mp4")},
        ).json()
        patched_timeline = copy.deepcopy(scenario["timeline"])
        patched_timeline["events"] = [
            {
                "id": "site_bundle",
                "lane_id": "input.text",
                "at_ms": 0,
                "parts": [
                    {
                        "part_id": "text_1",
                        "kind": "text",
                        "mime_type": "text/plain",
                        "text": "现场电控柜启动后抖动，请结合素材判断。",
                    },
                    {
                        "part_id": "image_1",
                        "kind": "image",
                        "asset_id": image_upload["id"],
                    },
                    {
                        "part_id": "video_1",
                        "kind": "video",
                        "asset_id": video_upload["id"],
                    },
                ],
            },
            {
                "id": "expect_completion",
                "lane_id": "expected.semantic",
                "at_ms": 5000,
                "expectation": "系统应完成多模态现场包评估。",
            },
        ]
        patch_response = client.patch(
            f"/api/v1/pskills/{created['id']}/test-scenarios/{scenario['id']}",
            json={"timeline": patched_timeline},
        )
        scenario = patch_response.json()
        start_response = client.post(f"/api/v1/pskills/{created['id']}/test-scenarios/{scenario['id']}/runs", json={})
        scenario_run = start_response.json()
        terminal_events_response = client.get(f"/api/v1/terminal/sessions/{scenario_run['run_id']}/events")
        review_response = client.get(f"/api/v1/skill-test-scenario-runs/{scenario_run['id']}/review")
        cursor = review_response.json()["cursor_anchors"][-1]
        fork_response = client.post(
            f"/api/v1/skill-test-scenario-runs/{scenario_run['id']}/fork-scenario",
            json={"cursor": cursor, "name": "从多模态包继续"},
        )

    assert patch_response.status_code == 200
    patched_event = next(event for event in scenario["timeline"]["events"] if event["id"] == "site_bundle")
    assert patched_event["event_kind"] == "terminal.multimodal.input.v1"
    assert patched_event["mime_type"] == "multipart/mixed"
    assert [part["part_id"] for part in patched_event["parts"]] == ["text_1", "image_1", "video_1"]

    assert start_response.status_code == 202
    assert scenario_run["driver_status"] == "completed"
    assert scenario_run["driver_events"] == [
        {
            **scenario_run["driver_events"][0],
            "event_id": "site_bundle",
            "lane_id": "input.text",
            "at_ms": 0,
        }
    ]
    terminal_inputs = [event for event in terminal_events_response.json() if event["direction"] == "input"]
    bundled_inputs = [event for event in terminal_inputs if event["external_event_id"].endswith(":site_bundle")]
    assert len(bundled_inputs) == 1
    bundled_event = bundled_inputs[0]
    assert bundled_event["event_kind"] == "terminal.multimodal.input.v1"
    assert bundled_event["mime_type"] == "multipart/mixed"
    assert [part["kind"] for part in bundled_event["parts"]] == ["text", "image", "video"]
    assert [part["metadata"].get("filename") for part in bundled_event["parts"][1:]] == ["panel.png", "startup.mp4"]
    assert any(call.get("attachments") == "panel.png,startup.mp4" for call in fake_inference.calls)

    assert review_response.status_code == 200
    review_terminal_event = next(
        event for event in review_response.json()["replay"]["run_events"] if event["id"] == bundled_event["id"]
    )
    assert [part["part_id"] for part in review_terminal_event["parts"]] == ["text_1", "image_1", "video_1"]

    assert fork_response.status_code == 201
    forked_parts = next(event for event in fork_response.json()["timeline"]["events"] if event["id"] == "fork_site_bundle")["parts"]
    assert forked_parts[1]["asset_id"] != image_upload["id"]
    assert forked_parts[2]["asset_id"] != video_upload["id"]


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
            "/api/v1/pskills",
            json={
                "key": "skill-test-scenario-fork-selected-time",
                "name": "Skill Test Scenario Fork Selected Time",
                "description": "Validate forked timeline respects the selected review playhead.",
            },
        ).json()
        publish_response = client.post(
            f"/api/v1/pskills/{created['id']}/publish",
            json={"publish_reason": "Scenario fork selected time publish"},
        )
        compile_request_id = publish_response.json()["compile_request"]["id"]
        client.post(f"/api/v1/compiler/requests/{compile_request_id}/retry")
        scenario_response = client.post(
            f"/api/v1/pskills/{created['id']}/test-scenarios",
            json={
                "name": "按选中时间 Fork 的场景",
                "duration_ms": 10000,
                "timeline": timeline,
            },
        )
        scenario = scenario_response.json()
        start_response = client.post(f"/api/v1/pskills/{created['id']}/test-scenarios/{scenario['id']}/runs", json={})
        scenario_run = start_response.json()
        fork_response = client.post(
            f"/api/v1/skill-test-scenario-runs/{scenario_run['id']}/fork-scenario",
            json={"cursor": {"time_ms": 4000, "terminal_seq": 7, "snapshot_seq": 3}, "name": "从 4s 继续"},
        )

    assert scenario_response.status_code == 201
    lane_ids = [lane["id"] for lane in scenario["timeline"]["lanes"]]
    assert lane_ids[:2] == ["sensor.gps", "sensor.pose3d"]
    assert scenario["timeline"]["lanes"][-1]["label"] == "文本"
    assert start_response.status_code == 202
    assert fork_response.status_code == 201

    forked = fork_response.json()
    assert forked["duration_ms"] == 10000
    assert forked["timeline"]["duration_ms"] == 10000
    assert forked["fork_seed"]["time_ms"] == 4000
    assert forked["fork_seed"]["terminal_seq"] == 7
    assert [(event["id"], event["at_ms"]) for event in forked["timeline"]["events"]] == [
        ("fork_early_input", 1000),
        ("fork_middle_input", 3000),
    ]
    assert [event["payload_inline"] for event in forked["timeline"]["events"] if event["lane_id"] == "input.text"] == ["早期输入", "中段输入"]


def test_skill_test_scenario_run_can_be_cancelled() -> None:
    client, _, _ = create_test_client()

    timeline = {
        "schema_version": "psop-skill-test-timeline/v1",
        "duration_ms": 60000,
        "lanes": [
            {"id": "input.text", "kind": "input", "label": "文本"},
            {"id": "expected.semantic", "kind": "output", "label": "文本"},
        ],
        "events": [
            {
                "id": "delayed_input",
                "lane_id": "input.text",
                "at_ms": 60000,
                "payload_inline": "一分钟后才发送的输入",
            },
            {
                "id": "expect_delayed",
                "lane_id": "expected.semantic",
                "at_ms": 60000,
                "expectation": "系统应处理延迟输入。",
            },
        ],
    }

    with client:
        created = client.post(
            "/api/v1/pskills",
            json={
                "key": "skill-test-scenario-cancel",
                "name": "Skill Test Scenario Cancel",
                "description": "Validate cancelling a running skill test scenario.",
            },
        ).json()
        publish_response = client.post(
            f"/api/v1/pskills/{created['id']}/publish",
            json={"publish_reason": "Scenario cancel publish"},
        )
        compile_request_id = publish_response.json()["compile_request"]["id"]
        client.post(f"/api/v1/compiler/requests/{compile_request_id}/retry")
        scenario_response = client.post(
            f"/api/v1/pskills/{created['id']}/test-scenarios",
            json={"name": "可终止运行场景", "duration_ms": 60000, "timeline": timeline},
        )
        scenario = scenario_response.json()
        start_response = client.post(f"/api/v1/pskills/{created['id']}/test-scenarios/{scenario['id']}/runs", json={})
        scenario_run = start_response.json()
        cancel_response = client.post(
            f"/api/v1/skill-test-scenario-runs/{scenario_run['id']}/cancel",
            json={"reason": "用户终止测试"},
        )
        runtime_run_response = client.get(f"/api/v1/runs/{scenario_run['run_id']}")
        terminal_session_response = client.get(f"/api/v1/terminal/sessions/{scenario_run['run_id']}")
        jobs_response = client.get(
            "/api/v1/runtime/jobs",
            params={"job_type": LEGACY_SKILL_TEST_TIMELINE_DRIVER_JOB_TYPE},
        )
        review_response = client.get(f"/api/v1/skill-test-scenario-runs/{scenario_run['id']}/review")
        second_cancel_response = client.post(f"/api/v1/skill-test-scenario-runs/{scenario_run['id']}/cancel", json={})

    assert scenario_response.status_code == 201
    assert start_response.status_code == 202
    assert scenario_run["driver_status"] == "waiting_time"
    assert cancel_response.status_code == 200
    cancelled = cancel_response.json()
    assert cancelled["status"] == "cancelled"
    assert cancelled["driver_status"] == "cancelled"
    assert cancelled["ended_at"]
    assert cancelled["result_summary"]["status"] == "cancelled"
    assert cancelled["result_summary"]["reason"] == "用户终止测试"
    assert runtime_run_response.json()["status"] == "cancelled"
    assert runtime_run_response.json()["exit_reason"] == "用户终止测试"
    assert terminal_session_response.json()["terminal_session"]["status"] == "closed"
    driver_jobs = [job for job in jobs_response.json() if job["payload"].get("scenario_run_id") == scenario_run["id"]]
    assert driver_jobs[0]["status"] == "cancelled"
    assert review_response.json()["scenario_run"]["status"] == "cancelled"
    assert second_cancel_response.status_code == 200
    assert second_cancel_response.json()["status"] == "cancelled"


def test_skill_test_run_activity_websocket_streams_review_snapshot() -> None:
    client, _, _ = create_test_client()

    timeline = {
        "schema_version": "psop-skill-test-timeline/v1",
        "duration_ms": 60000,
        "lanes": [
            {"id": "input.text", "kind": "input", "label": "文本"},
            {"id": "expected.semantic", "kind": "output", "label": "文本"},
        ],
        "events": [
            {
                "id": "delayed_input",
                "lane_id": "input.text",
                "at_ms": 60000,
                "payload_inline": "一分钟后发送。",
            },
            {
                "id": "expect_delayed",
                "lane_id": "expected.semantic",
                "at_ms": 60000,
                "expectation": "系统应处理延迟输入。",
            },
        ],
    }

    with client:
        created = client.post(
            "/api/v1/pskills",
            json={
                "key": "skill-test-run-activity-ws",
                "name": "Skill Test Run Activity WS",
                "description": "Validate test run activity websocket.",
            },
        ).json()
        publish_response = client.post(
            f"/api/v1/pskills/{created['id']}/publish",
            json={"publish_reason": "Test run activity websocket publish"},
        )
        compile_request_id = publish_response.json()["compile_request"]["id"]
        client.post(f"/api/v1/compiler/requests/{compile_request_id}/retry")
        scenario = client.post(
            f"/api/v1/pskills/{created['id']}/test-scenarios",
            json={"name": "activity ws scenario", "duration_ms": 60000, "timeline": timeline},
        ).json()
        start_response = client.post(f"/api/v1/pskills/{created['id']}/test-scenarios/{scenario['id']}/runs", json={})
        scenario_run = start_response.json()

        with client.websocket_connect(f"/ws/test-runs/{scenario_run['id']}") as websocket:
            connected = websocket.receive_json()
            initial_snapshot = websocket.receive_json()
            cancel_response = client.post(
                f"/api/v1/skill-test-scenario-runs/{scenario_run['id']}/cancel",
                json={"reason": "活动流测试终止"},
            )
            cancelled_snapshot = websocket.receive_json()

    assert start_response.status_code == 202
    assert scenario_run["status"] == "waiting_input"
    assert scenario_run["driver_status"] == "waiting_time"
    assert connected["event_type"] == "ws.connected"
    assert connected["test_run_id"] == scenario_run["id"]

    assert initial_snapshot["event_type"] == "test_run.activity.snapshot"
    initial_payload = initial_snapshot["payload"]
    assert initial_payload["test_run"]["id"] == scenario_run["id"]
    assert initial_payload["active"] is True
    assert initial_payload["terminal"] is False
    assert initial_payload["review"]["scenario_run"]["driver_status"] == "waiting_time"
    assert [event["event_type"] for event in initial_payload["agent_events"]] == [
        "agent.run.created",
        "agent.skills.activated",
        "testing.run.linked",
    ]

    assert cancel_response.status_code == 200
    cancelled_payload = cancelled_snapshot["payload"]
    assert cancelled_payload["test_run"]["status"] == "cancelled"
    assert cancelled_payload["review"]["scenario_run"]["result_summary"]["reason"] == "活动流测试终止"
    assert cancelled_payload["terminal"] is True
    assert cancelled_payload["active"] is False


def test_skill_test_scenario_sensor_timeline_review_stage_outputs_and_fork() -> None:
    client, _, _ = create_test_client()

    timeline = {
        "schema_version": "psop-skill-test-timeline/v1",
        "duration_ms": 5000,
        "events": [
            {
                "id": "gps_start",
                "lane_id": "sensor.gps",
                "at_ms": 0,
                "payload_inline": {"latitude": "31.2304", "longitude": "121.4737", "accuracy_m": "3.5"},
            },
            {
                "id": "pose_start",
                "lane_id": "sensor.pose3d",
                "at_ms": 0,
                "payload_inline": {"x": "1.1", "y": "2.2", "z": "3.3", "yaw": "90"},
            },
            {
                "id": "operator_context",
                "lane_id": "input.text",
                "at_ms": 0,
                "payload_inline": "现场已到达目标设备。",
            },
            {
                "id": "stage_confirm_arrival",
                "lane_id": "expected.semantic",
                "at_ms": 5000,
                "expectation": "系统应基于定位和现场输入确认到达目标设备。",
            },
        ],
    }

    with client:
        created = client.post(
            "/api/v1/pskills",
            json={
                "key": "skill-test-sensors",
                "name": "Skill Test Sensors",
                "description": "Validate sensor lanes in skill test timelines.",
            },
        ).json()
        publish_response = client.post(
            f"/api/v1/pskills/{created['id']}/publish",
            json={"publish_reason": "Sensor scenario publish"},
        )
        compile_request_id = publish_response.json()["compile_request"]["id"]
        client.post(f"/api/v1/compiler/requests/{compile_request_id}/retry")

        scenario_response = client.post(
            f"/api/v1/pskills/{created['id']}/test-scenarios",
            json={"name": "传感器输入阶段场景", "duration_ms": 5000, "timeline": timeline},
        )
        scenario = scenario_response.json()
        patched_timeline = copy.deepcopy(scenario["timeline"])
        next(event for event in patched_timeline["events"] if event["lane_id"] == "sensor.gps")["payload_inline"]["accuracy_m"] = 2.5
        patch_response = client.patch(
            f"/api/v1/pskills/{created['id']}/test-scenarios/{scenario['id']}",
            json={"timeline": patched_timeline},
        )
        reloaded_response = client.get(f"/api/v1/pskills/{created['id']}/test-scenarios/{scenario['id']}")

        start_response = client.post(f"/api/v1/pskills/{created['id']}/test-scenarios/{scenario['id']}/runs", json={})
        scenario_run = start_response.json()
        terminal_events_response = client.get(f"/api/v1/terminal/sessions/{scenario_run['run_id']}/events")
        review_response = client.get(f"/api/v1/skill-test-scenario-runs/{scenario_run['id']}/review")
        stage_output = review_response.json()["stage_outputs"][0]
        fork_response = client.post(
            f"/api/v1/skill-test-scenario-runs/{scenario_run['id']}/fork-scenario",
            json={"cursor": stage_output["cursor"], "name": "从到达确认阶段继续"},
        )

    assert scenario_response.status_code == 201
    lane_ids = [lane["id"] for lane in scenario["timeline"]["lanes"]]
    assert lane_ids[:2] == ["sensor.gps", "sensor.pose3d"]
    assert scenario["timeline"]["lanes"][-1]["label"] == "文本"
    gps_event = next(event for event in scenario["timeline"]["events"] if event["lane_id"] == "sensor.gps")
    pose_event = next(event for event in scenario["timeline"]["events"] if event["lane_id"] == "sensor.pose3d")
    assert gps_event["event_kind"] == "sensor.gps.reading.v1"
    assert gps_event["mime_type"] == "application/json"
    assert gps_event["payload_inline"]["latitude"] == 31.2304
    assert pose_event["event_kind"] == "sensor.pose3d.reading.v1"

    assert patch_response.status_code == 200
    assert reloaded_response.json()["timeline"]["events"][0]["payload_inline"]["accuracy_m"] == 2.5

    assert start_response.status_code == 202
    terminal_events = terminal_events_response.json()
    sensor_events = [event for event in terminal_events if event["event_kind"].startswith("sensor.")]
    assert [event["event_kind"] for event in sensor_events] == ["sensor.gps.reading.v1", "sensor.pose3d.reading.v1"]
    assert sensor_events[0]["payload_inline"]["accuracy_m"] == 2.5
    assert sensor_events[1]["payload_inline"]["yaw"] == 90.0

    assert review_response.status_code == 200
    assert stage_output["stage_id"] == "stage_confirm_arrival"
    assert stage_output["time_ms"] == 5000
    assert stage_output["expectation"] == "系统应基于定位和现场输入确认到达目标设备。"
    assert stage_output["actual_outputs"]
    assert stage_output["actual_outputs"][0]["run_event_id"]
    assert stage_output["actual_outputs"][0]["run_event_id"] == stage_output["actual_outputs"][0]["terminal_event_id"]
    assert stage_output["judge_result"]["status"] == "passed"
    assert stage_output["human_review"] == {"status": "pending", "reviewer": None, "reason": "", "updated_at": None}
    assert stage_output["cursor"]["time_ms"] == 5000
    assert stage_output["cursor"]["terminal_seq"] >= 3

    assert fork_response.status_code == 201
    assert fork_response.json()["fork_seed"]["time_ms"] == 5000
    assert fork_response.json()["fork_seed"]["terminal_seq"] == stage_output["cursor"]["terminal_seq"]


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

    assert payload["run_event_count_before_cutoff"] == 2
    assert payload["run_events_before_cutoff"]
    assert "terminal_outputs_before_cutoff" not in payload
    assert "terminal_output_count_before_cutoff" not in payload
    assert payload["input_compaction"]["run_event_count"] == 2
    assert "terminal_output_count" not in payload["input_compaction"]
    assert payload["input_compaction"]["transcript_budget_chars"] == 5000
    assert payload["input_compaction"]["final_output_truncated"] is True
    assert any(item["payload_truncated"] for item in payload["run_events_before_cutoff"])
    assert len(prompt_json) < 10000
    assert old_payload not in prompt_json
    assert recent_payload not in prompt_json


def test_skill_test_judge_evidence_refs_use_runtime_names() -> None:
    assert SkillTestService._normalize_judge_evidence_refs(
        [
            {"kind": "terminal_event", "seq_no": 1},
            {"kind": "trace_event", "id": "trace-1"},
            {"kind": "artifact", "id": "artifact-1"},
            "invalid",
        ]
    ) == [
        {"kind": "run_event", "seq_no": 1},
        {"kind": "run_trace", "id": "trace-1"},
        {"kind": "artifact", "id": "artifact-1"},
    ]


def test_skill_test_scenario_rejects_duplicate_open_run() -> None:
    client, _, _ = create_test_client()

    with client:
        created = client.post(
            "/api/v1/pskills",
            json={
                "key": "skill-test-scenario-duplicate",
                "name": "Skill Test Scenario Duplicate",
                "description": "Validate active scenario run conflict.",
            },
        ).json()
        publish_response = client.post(
            f"/api/v1/pskills/{created['id']}/publish",
            json={"publish_reason": "Scenario duplicate publish"},
        )
        compile_request_id = publish_response.json()["compile_request"]["id"]
        client.post(f"/api/v1/compiler/requests/{compile_request_id}/retry")
        scenario_response = client.post(
            f"/api/v1/pskills/{created['id']}/test-scenarios",
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
        start_response = client.post(f"/api/v1/pskills/{created['id']}/test-scenarios/{scenario_id}/runs", json={})
        duplicate_response = client.post(f"/api/v1/pskills/{created['id']}/test-scenarios/{scenario_id}/runs", json={})

    assert start_response.status_code == 202
    assert start_response.json()["driver_status"] == "waiting_time"
    assert duplicate_response.status_code == 409
    assert duplicate_response.json()["details"]["scenario_run_id"] == start_response.json()["id"]


def test_delete_skill_requires_name_confirmation_and_archives_gitlab_project() -> None:
    client, fake_gateway, _ = create_test_client()

    with client:
        created = client.post(
            "/api/v1/pskills",
            json={
                "key": "delete-me",
                "name": "Delete Me",
                "description": "Archive this project.",
            },
        ).json()
        skill_id = created["id"]

        mismatch_response = client.request(
            "DELETE",
            f"/api/v1/pskills/{skill_id}",
            json={"confirmation_name": "Wrong Name"},
        )
        delete_response = client.request(
            "DELETE",
            f"/api/v1/pskills/{skill_id}",
            json={"confirmation_name": "Delete Me"},
        )
        list_response = client.get("/api/v1/pskills")
        archived_response = client.get("/api/v1/pskills?status=archived")

    assert mismatch_response.status_code == 422
    assert mismatch_response.json()["code"] == "skill_validation_error"

    assert delete_response.status_code == 200
    delete_payload = delete_response.json()
    assert delete_payload["status"] == "archived"
    assert fake_gateway.projects[created["gitlab_project_id"]].archived is True

    assert all(skill["id"] != skill_id for skill in list_response.json())
    assert any(skill["id"] == skill_id for skill in archived_response.json())
