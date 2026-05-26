from __future__ import annotations

from typing import Any

import yaml
from sqlalchemy.orm import Session

from app.agents.registry import DEFAULT_COMPILE_AGENT_REF, AgentPromptPack, PromptRegistry, content_hash
from app.domain.agent_prompts.models import AgentPromptBinding, AgentPromptDefinition, AgentPromptVersion
from app.domain.agent_prompts.repository import AgentPromptRepository
from app.domain.agent_prompts.schemas import (
    AgentPromptActivateRequest,
    AgentPromptBindingResponse,
    AgentPromptBindingUpdateRequest,
    AgentPromptCreateRequest,
    AgentPromptDefinitionDetailResponse,
    AgentPromptDefinitionSummaryResponse,
    AgentPromptValidationResponse,
    AgentPromptVersionCreateRequest,
    AgentPromptVersionDetailResponse,
    AgentPromptVersionFilesUpdateRequest,
    AgentPromptVersionSummaryResponse,
)
from app.domain.skills.exceptions import SkillConflictError, SkillNotFoundError, SkillValidationError
from app.domain.skills.models import now_utc


DEFAULT_AGENT_PROMPT_SEEDS = [
    {
        "definition_key": "skill_compilation.formal_v5_compile",
        "ref": DEFAULT_COMPILE_AGENT_REF,
        "name": "SKILL 编译智能体",
        "usage_keys": ["default.compile_agent"],
    },
    {
        "definition_key": "skill_creation.conversational_draft",
        "ref": "skill_creation/conversational_draft/v1",
        "name": "Skill 构建智能体",
        "usage_keys": ["default.skill_creation_agent"],
    },
    {
        "definition_key": "skill_test.semantic_judge",
        "ref": "skill_test/semantic_judge/v1",
        "name": "测试语义 Judge",
        "usage_keys": ["skill_test.semantic_judge"],
    },
    {
        "definition_key": "runtime_execution.llm_node_fallback",
        "ref": "runtime_execution/llm_node_fallback/v1",
        "name": "Runtime LLM 节点兜底提示词",
        "usage_keys": ["runtime.llm_node_fallback"],
    },
]


class AgentPromptService:
    def __init__(
        self,
        *,
        repository: AgentPromptRepository | None = None,
        prompt_registry: PromptRegistry | None = None,
    ) -> None:
        self.repository = repository or AgentPromptRepository()
        self.prompt_registry = prompt_registry or PromptRegistry()

    def ensure_seed_data(self, session: Session) -> bool:
        changed = False
        for seed in DEFAULT_AGENT_PROMPT_SEEDS:
            try:
                pack = self.prompt_registry.load_agent(str(seed["ref"]))
            except Exception:
                continue
            definition = self.repository.get_definition_by_key(session, str(seed["definition_key"]))
            if not definition:
                definition = AgentPromptDefinition(
                    key=str(seed["definition_key"]),
                    agent_id=pack.agent_id,
                    scenario=pack.scenario,
                    name=str(seed["name"]),
                    description=pack.description,
                    status="active",
                )
                session.add(definition)
                session.flush()
                changed = True
            if definition.agent_id != pack.agent_id:
                definition.agent_id = pack.agent_id
                changed = True
            if definition.scenario != pack.scenario:
                definition.scenario = pack.scenario
                changed = True
            if not definition.description and pack.description:
                definition.description = pack.description
                changed = True

            version = self.repository.get_version_by_hash(
                session,
                definition_id=definition.id,
                content_hash=pack.prompt_hash,
            )
            if not version:
                version_no = self.repository.next_version_no(session, definition.id)
                version = AgentPromptVersion(
                    definition_id=definition.id,
                    version_no=version_no,
                    version_label=self._seed_version_label(pack.version, version_no),
                    status="published",
                    route_key=pack.route_key,
                    files=pack.files,
                    content_hash=pack.prompt_hash,
                    published_at=now_utc(),
                )
                session.add(version)
                session.flush()
                changed = True
            expected_version_label = self._seed_version_label(pack.version, version.version_no)
            if version.version_label != expected_version_label:
                version.version_label = expected_version_label
                changed = True
            if not definition.active_version_id:
                definition.active_version_id = version.id
                changed = True

            for usage_key in seed["usage_keys"]:
                binding = self.repository.get_binding(session, str(usage_key))
                if not binding:
                    session.add(
                        AgentPromptBinding(
                            usage_key=str(usage_key),
                            definition_id=definition.id,
                            active_version_id=version.id,
                        )
                    )
                    changed = True
        if changed:
            session.flush()
        return changed

    def resolve_prompt_pack(
        self,
        session: Session | None,
        *,
        usage_key: str,
        fallback_ref: str,
    ) -> AgentPromptPack:
        if session is not None:
            self.ensure_seed_data(session)
            return self.prompt_registry.load_agent_for_usage(
                usage_key,
                fallback_ref=fallback_ref,
                session=session,
            )
        return self.prompt_registry.load_agent(fallback_ref)

    def list_definitions(self, session: Session) -> list[AgentPromptDefinitionSummaryResponse]:
        if self.ensure_seed_data(session):
            session.commit()
        return [self._build_definition_summary(session, item) for item in self.repository.list_definitions(session)]

    def create_definition(
        self,
        session: Session,
        payload: AgentPromptCreateRequest,
    ) -> AgentPromptDefinitionDetailResponse:
        if self.repository.get_definition_by_key(session, payload.key):
            raise SkillConflictError("Agent Prompt Pack key 已存在。", details={"key": payload.key})
        files = self._normalize_files(payload.files)
        if "agent.yaml" not in files:
            files["agent.yaml"] = yaml.safe_dump(
                {
                    "agent_id": payload.agent_id,
                    "version": "v1",
                    "scenario": payload.scenario,
                    "route_key": payload.route_key or "default",
                    "description": payload.description,
                },
                allow_unicode=True,
                sort_keys=False,
            )
        if "system.md" not in files:
            files["system.md"] = ""

        definition = AgentPromptDefinition(
            key=payload.key,
            agent_id=payload.agent_id,
            scenario=payload.scenario,
            name=payload.name,
            description=payload.description,
            status="active",
        )
        session.add(definition)
        session.flush()
        version = AgentPromptVersion(
            definition_id=definition.id,
            version_no=1,
            version_label="v1",
            status="draft",
            route_key=payload.route_key or "default",
            files=files,
            content_hash=content_hash(files),
        )
        session.add(version)
        session.flush()
        session.commit()
        return self.get_definition(session, definition.id, selected_version_id=version.id)

    def get_definition(
        self,
        session: Session,
        definition_id: str,
        *,
        selected_version_id: str | None = None,
    ) -> AgentPromptDefinitionDetailResponse:
        if self.ensure_seed_data(session):
            session.commit()
        definition = self._get_definition(session, definition_id)
        versions = self.repository.list_versions(session, definition.id)
        selected = None
        if selected_version_id:
            selected = self._get_version_for_definition(session, definition, selected_version_id)
        elif versions:
            selected = versions[0]
        summary = self._build_definition_summary(session, definition)
        return AgentPromptDefinitionDetailResponse(
            **summary.model_dump(),
            versions=[self._build_version_summary(item) for item in versions],
            selected_version=self._build_version_detail(selected) if selected else None,
        )

    def create_version(
        self,
        session: Session,
        definition_id: str,
        payload: AgentPromptVersionCreateRequest,
    ) -> AgentPromptDefinitionDetailResponse:
        definition = self._get_definition(session, definition_id)
        parent = None
        if payload.parent_version_id:
            parent = self._get_version_for_definition(session, definition, payload.parent_version_id)
        else:
            parent = self.repository.latest_version(session, definition.id)
        files = self._normalize_files(payload.files) if payload.files is not None else dict(parent.files if parent else {})
        version_no = self.repository.next_version_no(session, definition.id)
        version = AgentPromptVersion(
            definition_id=definition.id,
            version_no=version_no,
            version_label=payload.version_label or f"v{version_no}",
            status="draft",
            route_key=self._route_key_from_files(files, default=parent.route_key if parent else "default"),
            files=files,
            content_hash=content_hash(files),
            parent_version_id=parent.id if parent else None,
        )
        session.add(version)
        session.commit()
        return self.get_definition(session, definition.id, selected_version_id=version.id)

    def update_version_files(
        self,
        session: Session,
        definition_id: str,
        version_id: str,
        payload: AgentPromptVersionFilesUpdateRequest,
    ) -> AgentPromptVersionDetailResponse:
        definition = self._get_definition(session, definition_id)
        version = self._get_version_for_definition(session, definition, version_id)
        if version.status != "draft":
            raise SkillConflictError("已发布的 Prompt Pack 版本不可编辑，请创建新 draft。", details={"version_id": version_id})
        files = self._normalize_files(payload.files)
        version.files = files
        version.route_key = self._route_key_from_files(files, default=version.route_key)
        version.content_hash = content_hash(files)
        session.commit()
        return self._build_version_detail(version)

    def validate_version(self, session: Session, definition_id: str, version_id: str) -> AgentPromptValidationResponse:
        definition = self._get_definition(session, definition_id)
        version = self._get_version_for_definition(session, definition, version_id)
        return self._validate_files(version.files)

    def publish_version(
        self,
        session: Session,
        definition_id: str,
        version_id: str,
    ) -> AgentPromptVersionDetailResponse:
        definition = self._get_definition(session, definition_id)
        version = self._get_version_for_definition(session, definition, version_id)
        validation = self._validate_files(version.files)
        if not validation.valid:
            raise SkillValidationError("Agent Prompt Pack 校验失败。", details={"errors": validation.errors})
        if version.status == "archived":
            raise SkillConflictError("已归档版本不可发布。", details={"version_id": version.id})
        metadata = validation.metadata
        version.status = "published"
        version.route_key = str(metadata.get("route_key") or version.route_key or "default")
        version.content_hash = content_hash(version.files)
        version.published_at = version.published_at or now_utc()
        definition.agent_id = str(metadata["agent_id"])
        definition.scenario = str(metadata["scenario"])
        session.commit()
        return self._build_version_detail(version)

    def activate_version(
        self,
        session: Session,
        definition_id: str,
        version_id: str,
        payload: AgentPromptActivateRequest,
    ) -> AgentPromptDefinitionDetailResponse:
        definition = self._get_definition(session, definition_id)
        version = self._get_version_for_definition(session, definition, version_id)
        if version.status != "published":
            raise SkillValidationError("只有 published 版本可以启用。", details={"version_id": version.id})
        usage_keys = [payload.usage_key] if payload.usage_key else [item.usage_key for item in self.repository.list_bindings_for_definition(session, definition.id)]
        if not usage_keys:
            usage_keys = self._default_usage_keys_for_definition(definition.key)
        for usage_key in usage_keys:
            binding = self.repository.get_binding(session, str(usage_key))
            if binding and binding.definition_id != definition.id:
                raise SkillConflictError(
                    "该 usage_key 已绑定到其他 Prompt Pack。",
                    details={"usage_key": usage_key, "definition_id": binding.definition_id},
                )
            if not binding:
                binding = AgentPromptBinding(usage_key=str(usage_key), definition_id=definition.id)
                session.add(binding)
            binding.active_version_id = version.id
        definition.active_version_id = version.id
        session.commit()
        return self.get_definition(session, definition.id, selected_version_id=version.id)

    def list_bindings(self, session: Session) -> list[AgentPromptBindingResponse]:
        if self.ensure_seed_data(session):
            session.commit()
        return [self._build_binding_response(session, item) for item in self.repository.list_bindings(session)]

    def update_binding(
        self,
        session: Session,
        usage_key: str,
        payload: AgentPromptBindingUpdateRequest,
    ) -> AgentPromptBindingResponse:
        definition = self._get_definition(session, payload.definition_id)
        version = self._get_version_for_definition(session, definition, payload.active_version_id)
        if version.status != "published":
            raise SkillValidationError("绑定只能指向 published 版本。", details={"version_id": version.id})
        binding = self.repository.get_binding(session, usage_key)
        if not binding:
            binding = AgentPromptBinding(usage_key=usage_key, definition_id=definition.id)
            session.add(binding)
        binding.definition_id = definition.id
        binding.active_version_id = version.id
        definition.active_version_id = version.id
        session.commit()
        return self._build_binding_response(session, binding)

    def _get_definition(self, session: Session, definition_id: str) -> AgentPromptDefinition:
        definition = self.repository.get_definition(session, definition_id)
        if not definition:
            raise SkillNotFoundError("未找到 Agent Prompt Pack。", details={"definition_id": definition_id})
        return definition

    def _get_version_for_definition(
        self,
        session: Session,
        definition: AgentPromptDefinition,
        version_id: str,
    ) -> AgentPromptVersion:
        version = self.repository.get_version(session, version_id)
        if not version or version.definition_id != definition.id:
            raise SkillNotFoundError("未找到 Agent Prompt Pack 版本。", details={"version_id": version_id})
        return version

    @staticmethod
    def _seed_version_label(asset_version: str, version_no: int) -> str:
        if version_no <= 1 and asset_version:
            return asset_version
        return f"v{version_no}"

    @staticmethod
    def _normalize_files(files: dict[str, str]) -> dict[str, str]:
        normalized: dict[str, str] = {}
        for path, content in files.items():
            clean_path = str(path).strip().strip("/")
            if not clean_path or clean_path.startswith(".") or ".." in clean_path.split("/"):
                raise SkillValidationError("Prompt Pack 文件路径无效。", details={"path": path})
            normalized[clean_path] = str(content)
        return normalized

    @staticmethod
    def _route_key_from_files(files: dict[str, str], *, default: str = "default") -> str:
        try:
            spec = yaml.safe_load(files.get("agent.yaml") or "") or {}
        except yaml.YAMLError:
            return default
        return str(spec.get("route_key") or default or "default") if isinstance(spec, dict) else default

    def _validate_files(self, files: dict[str, str]) -> AgentPromptValidationResponse:
        errors: list[dict[str, Any]] = []
        metadata: dict[str, Any] = {}
        raw_spec = files.get("agent.yaml")
        if raw_spec is None:
            errors.append({"path": "agent.yaml", "message": "缺少 agent.yaml。"})
            spec = {}
        else:
            try:
                spec = yaml.safe_load(raw_spec) or {}
            except yaml.YAMLError as exc:
                errors.append({"path": "agent.yaml", "message": f"YAML 无法解析：{exc}"})
                spec = {}
            if not isinstance(spec, dict):
                errors.append({"path": "agent.yaml", "message": "agent.yaml 顶层必须是对象。"})
                spec = {}

        system_prompt = str(files.get("system.md") or "").strip()
        if not system_prompt:
            errors.append({"path": "system.md", "message": "system.md 不能为空。"})
        for field in ("agent_id", "scenario", "route_key"):
            value = spec.get(field)
            if not isinstance(value, str) or not value.strip():
                errors.append({"path": "agent.yaml", "field": field, "message": f"{field} 不能为空。"})
        if not errors:
            metadata = {
                "agent_id": str(spec["agent_id"]),
                "scenario": str(spec["scenario"]),
                "version": str(spec.get("version") or ""),
                "route_key": str(spec["route_key"]),
                "description": str(spec.get("description") or ""),
                "content_hash": content_hash(files),
            }
        return AgentPromptValidationResponse(valid=not errors, errors=errors, metadata=metadata)

    def _build_definition_summary(
        self,
        session: Session,
        definition: AgentPromptDefinition,
    ) -> AgentPromptDefinitionSummaryResponse:
        versions = self.repository.list_versions(session, definition.id)
        active_version = session.get(AgentPromptVersion, definition.active_version_id) if definition.active_version_id else None
        return AgentPromptDefinitionSummaryResponse(
            id=definition.id,
            key=definition.key,
            agent_id=definition.agent_id,
            scenario=definition.scenario,
            name=definition.name,
            description=definition.description,
            status=definition.status,
            active_version_id=active_version.id if active_version else None,
            active_version_label=active_version.version_label if active_version else None,
            active_content_hash=active_version.content_hash if active_version else None,
            version_count=len(versions),
            bindings=[self._build_binding_response(session, item) for item in self.repository.list_bindings_for_definition(session, definition.id)],
            created_at=definition.created_at,
            updated_at=definition.updated_at,
        )

    def _build_binding_response(self, session: Session, binding: AgentPromptBinding) -> AgentPromptBindingResponse:
        definition = session.get(AgentPromptDefinition, binding.definition_id)
        active_version = session.get(AgentPromptVersion, binding.active_version_id) if binding.active_version_id else None
        return AgentPromptBindingResponse(
            id=binding.id,
            usage_key=binding.usage_key,
            definition_id=binding.definition_id,
            definition_key=definition.key if definition else "",
            active_version_id=active_version.id if active_version else None,
            active_version_label=active_version.version_label if active_version else None,
            active_content_hash=active_version.content_hash if active_version else None,
            created_at=binding.created_at,
            updated_at=binding.updated_at,
        )

    @staticmethod
    def _build_version_summary(version: AgentPromptVersion) -> AgentPromptVersionSummaryResponse:
        return AgentPromptVersionSummaryResponse(
            id=version.id,
            definition_id=version.definition_id,
            version_no=version.version_no,
            version_label=version.version_label,
            status=version.status,
            route_key=version.route_key,
            content_hash=version.content_hash,
            parent_version_id=version.parent_version_id,
            published_at=version.published_at,
            created_at=version.created_at,
            updated_at=version.updated_at,
        )

    def _build_version_detail(self, version: AgentPromptVersion) -> AgentPromptVersionDetailResponse:
        return AgentPromptVersionDetailResponse(
            **self._build_version_summary(version).model_dump(),
            files={str(key): str(value) for key, value in dict(version.files or {}).items()},
        )

    @staticmethod
    def _default_usage_keys_for_definition(definition_key: str) -> list[str]:
        for seed in DEFAULT_AGENT_PROMPT_SEEDS:
            if seed["definition_key"] == definition_key:
                return [str(item) for item in seed["usage_keys"]]
        return [definition_key]
