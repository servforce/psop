from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.models import AgentDefinition, AgentVersion
from app.jobs.models import RuntimeJob
from app.jobs.repository import JobRepository
from app.jobs.types import SKILL_SYNC_JOB_TYPE
from app.pskills.exceptions import SkillConflictError, SkillNotFoundError, SkillValidationError
from app.pskills.models import generate_uuid, now_utc
from app.skills.models import SkillActivation, SkillBinding, SkillPackage, SkillResource, SkillVersion
from app.skills.repository import SkillPackageRepository
from app.skills.schemas import (
    CreateSkillVersionRequest,
    QueueSkillSyncRequest,
    SkillPackageAgentUsageResponse,
    SkillPackageDetailResponse,
    SkillPackageSummaryResponse,
    SkillPackageSyncResponse,
    SkillActivationResponse,
    SkillResourceResponse,
    SkillVersionResponse,
)


SKILLS_ROOT = Path(__file__).resolve().parents[3] / "skills"
DEFAULT_SKILL_CONTEXT_MAX_CHARS = 4000
DEFAULT_SKILL_CONTEXT_REFERENCE_LIMIT = 3


@dataclass(frozen=True)
class ScannedSkillPackage:
    name: str
    scope: str
    source_path: Path
    manifest: dict[str, Any]
    body: str
    files: dict[str, bytes]
    content_hash: str
    resource_index: list[dict[str, Any]]


class SkillPackageService:
    def __init__(
        self,
        *,
        repository: SkillPackageRepository | None = None,
        job_repository: JobRepository | None = None,
        skills_root: Path | None = None,
    ) -> None:
        self.repository = repository or SkillPackageRepository()
        self.job_repository = job_repository or JobRepository()
        self.skills_root = skills_root or SKILLS_ROOT

    def enqueue_skill_sync_job(self, session: Session, payload: QueueSkillSyncRequest | None = None) -> str:
        request = payload or QueueSkillSyncRequest()
        idempotency_key = _normalize_optional(request.idempotency_key) or generate_uuid()
        dedupe_key = f"job:skill-sync:{idempotency_key}"
        existing = self.job_repository.get_runtime_job_by_dedupe_key(session, dedupe_key)
        if existing:
            return existing.id
        job = RuntimeJob(
            job_type=SKILL_SYNC_JOB_TYPE,
            status="pending",
            payload={"operation": "skill_sync", "idempotency_key": idempotency_key},
            dedupe_key=dedupe_key,
        )
        session.add(job)
        session.commit()
        return job.id

    def process_skill_sync_job(self, session: Session, job_id: str) -> SkillPackageSyncResponse:
        job = self.job_repository.get_runtime_job(session, job_id)
        if not job:
            raise SkillNotFoundError("未找到 Skill package 同步任务。", details={"job_id": job_id})
        if job.job_type != SKILL_SYNC_JOB_TYPE:
            raise SkillValidationError("当前 worker 仅支持 Skill package 同步任务。", details={"job_type": job.job_type})

        result = self.sync_packages(session)
        result_payload = result.model_dump(mode="json")
        metrics = dict(job.metrics or {})
        metrics.update(
            {
                "scanned_count": result.scanned_count,
                "package_count": result.package_count,
                "version_count": result.version_count,
                "changed": result.changed,
            }
        )
        job.payload = {
            **(job.payload or {}),
            "operation": "skill_sync",
            "sync_result": result_payload,
        }
        job.metrics = metrics
        job.status = "succeeded"
        job.last_error = ""
        job.lease_until = None
        session.commit()
        return result

    def sync_packages(self, session: Session, *, commit: bool = True) -> SkillPackageSyncResponse:
        scanned = self._scan_packages()
        changed = False
        for item in scanned:
            package = self.repository.get_package_by_name(session, item.name)
            description = str(item.manifest.get("description") or "")
            if not package:
                package = SkillPackage(
                    name=item.name,
                    scope=item.scope,
                    description=description,
                    source_uri=str(item.source_path.relative_to(self.skills_root.parent)),
                    status="active",
                )
                session.add(package)
                session.flush()
                changed = True
            if package.scope != item.scope:
                package.scope = item.scope
                changed = True
            if package.description != description:
                package.description = description
                changed = True
            source_uri = str(item.source_path.relative_to(self.skills_root.parent))
            if package.source_uri != source_uri:
                package.source_uri = source_uri
                changed = True
            if package.status != "active":
                package.status = "active"
                changed = True

            version = self.repository.get_version_by_hash(
                session,
                package_id=package.id,
                content_hash=item.content_hash,
            )
            if not version:
                version = SkillVersion(
                    package_id=package.id,
                    version_label=f"sync-{item.content_hash[:12]}",
                    status="active",
                    content_hash=item.content_hash,
                    manifest_json=item.manifest,
                    body_object_key=str(item.source_path.relative_to(self.skills_root.parent) / "SKILL.md"),
                    resource_index=item.resource_index,
                    allowed_tools=_allowed_tools(item.manifest),
                    validation_status="valid",
                    validation_diagnostics=[],
                    activated_at=now_utc(),
                )
                session.add(version)
                session.flush()
                for resource in item.resource_index:
                    session.add(
                        SkillResource(
                            version_id=version.id,
                            resource_path=str(resource["path"]),
                            resource_kind=str(resource["kind"]),
                            content_hash=str(resource["content_hash"]),
                            size_bytes=int(resource["size_bytes"]),
                        )
                    )
                changed = True
            active_version = self.repository.get_version(session, package.active_version_id)
            if not active_version:
                package.active_version_id = version.id
                changed = True
        bindings_changed = self.sync_agent_skill_bindings(session, sync_packages=False, commit=False)
        changed = changed or bindings_changed
        if changed and commit:
            session.commit()
        elif changed:
            session.flush()
        packages = self.list_packages(session)
        version_count = sum(item.version_count for item in packages)
        return SkillPackageSyncResponse(
            changed=changed,
            scanned_count=len(scanned),
            package_count=len(packages),
            version_count=version_count,
            packages=packages,
        )

    def sync_agent_skill_bindings(
        self,
        session: Session,
        *,
        sync_packages: bool = False,
        commit: bool = True,
    ) -> bool:
        if sync_packages:
            self.sync_packages(session, commit=False)
        specs = self._agent_skill_specs(session)
        package_by_name = {package.name: package for package in self.repository.list_packages(session, status="active")}
        desired: dict[tuple[str, str], tuple[str, str | None]] = {}
        managed_agent_keys = {str(spec["key"]) for spec in specs}
        for spec in specs:
            agent_key = str(spec["key"])
            for package_name in _normalize_string_list(spec.get("allowed_skill_names") or []):
                package = package_by_name.get(package_name)
                if not package or not package.active_version_id:
                    continue
                desired[(agent_key, package.id)] = (package.name, package.active_version_id)

        changed = False
        existing = [
            binding
            for binding in self.repository.list_bindings(session)
            if binding.agent_key in managed_agent_keys
        ]
        existing_by_key = {(binding.agent_key, binding.package_id): binding for binding in existing}
        for key, (usage_key, active_version_id) in desired.items():
            binding = existing_by_key.get(key)
            if not binding:
                session.add(
                    SkillBinding(
                        agent_key=key[0],
                        usage_key=usage_key,
                        package_id=key[1],
                        active_version_id=active_version_id,
                    )
                )
                changed = True
                continue
            if binding.usage_key != usage_key:
                binding.usage_key = usage_key
                changed = True
            if binding.active_version_id != active_version_id:
                binding.active_version_id = active_version_id
                changed = True

        for key, binding in existing_by_key.items():
            if key not in desired:
                session.delete(binding)
                changed = True
        if changed and commit:
            session.commit()
        elif changed:
            session.flush()
        return changed

    def list_packages(
        self,
        session: Session,
        *,
        scope: str | None = None,
        status: str | None = None,
    ) -> list[SkillPackageSummaryResponse]:
        return [
            self._build_package_summary(session, item)
            for item in self.repository.list_packages(session, scope=scope, status=status)
        ]

    def get_package(self, session: Session, package_name: str) -> SkillPackageDetailResponse:
        package = self.repository.get_package_by_name(session, package_name)
        if not package:
            raise SkillNotFoundError("未找到 Skill package。", details={"package_name": package_name})
        versions = self.repository.list_versions(session, package.id)
        active_version = self.repository.get_version(session, package.active_version_id)
        active_resources = self.repository.list_resources(session, active_version.id) if active_version else []
        return SkillPackageDetailResponse(
            **self._build_package_summary(session, package).model_dump(),
            versions=[self._build_version_response(session, version) for version in versions],
            active_version=self._build_version_response(session, active_version) if active_version else None,
            resources=[self._build_resource_response(resource) for resource in active_resources],
        )

    def list_versions(self, session: Session, package_name: str) -> list[SkillVersionResponse]:
        package = self.repository.get_package_by_name(session, package_name)
        if not package:
            raise SkillNotFoundError("未找到 Skill package。", details={"package_name": package_name})
        return [self._build_version_response(session, item) for item in self.repository.list_versions(session, package.id)]

    def create_version(
        self,
        session: Session,
        package_name: str,
        payload: CreateSkillVersionRequest,
    ) -> SkillPackageDetailResponse:
        package = self.repository.get_package_by_name(session, package_name)
        if not package:
            raise SkillNotFoundError("未找到 Skill package。", details={"package_name": package_name})

        parent = self._resolve_parent_version(session, package=package, parent_version_id=payload.parent_version_id)
        if payload.manifest_json is None and not parent:
            raise SkillValidationError(
                "创建 SkillVersion 必须提供 manifest_json。",
                details={"package_name": package_name},
            )

        manifest = _json_clone(payload.manifest_json if payload.manifest_json is not None else parent.manifest_json)
        if not isinstance(manifest, dict):
            raise SkillValidationError("manifest_json 必须是对象。", details={"package_name": package_name})
        resource_index = self._normalize_resource_index(
            payload.resource_index if payload.resource_index is not None else (parent.resource_index if parent else [])
        )
        allowed_tools = self._resolve_allowed_tools(
            manifest=manifest,
            payload_allowed_tools=payload.allowed_tools,
            parent=parent,
        )
        self._raise_if_allowed_tools_expands(
            package=package,
            allowed_tools=allowed_tools,
            baseline_version=parent,
        )
        version_label = (payload.version_label or "").strip() or self._next_candidate_version_label(session, package)
        body_object_key = (payload.body_object_key or "").strip()
        if not body_object_key:
            body_object_key = parent.body_object_key if parent else f"skills/{package.name}/versions/{version_label}/SKILL.md"
        content_hash = (payload.content_hash or "").strip() or self._hash_version_payload(
            manifest_json=manifest,
            body_object_key=body_object_key,
            resource_index=resource_index,
            allowed_tools=allowed_tools,
        )
        if self.repository.get_version_by_hash(session, package_id=package.id, content_hash=content_hash):
            raise SkillConflictError(
                "相同 content_hash 的 SkillVersion 已存在。",
                details={"package_name": package.name, "content_hash": content_hash},
            )

        version = SkillVersion(
            package_id=package.id,
            version_label=version_label,
            status="candidate",
            content_hash=content_hash,
            manifest_json=manifest,
            body_object_key=body_object_key,
            resource_index=resource_index,
            allowed_tools=allowed_tools,
            validation_status="pending",
            validation_diagnostics=[],
        )
        version.validation_diagnostics = self._validate_version_diagnostics(
            package,
            version,
            allowed_tools_baseline=parent,
        )
        version.validation_status = self._validation_status_from_diagnostics(version.validation_diagnostics)
        session.add(version)
        session.flush()
        for resource in resource_index:
            session.add(
                SkillResource(
                    version_id=version.id,
                    resource_path=str(resource["path"]),
                    resource_kind=str(resource["kind"]),
                    content_hash=str(resource["content_hash"]),
                    size_bytes=int(resource["size_bytes"]),
                )
            )
        package.updated_at = now_utc()
        session.commit()
        return self.get_package(session, package_name)

    def validate_version(self, session: Session, package_name: str, version_id: str) -> SkillVersionResponse:
        package, version = self._get_package_version(session, package_name, version_id)
        diagnostics = self._validate_version_diagnostics(
            package,
            version,
            allowed_tools_baseline=self._active_allowed_tools_baseline(session, package=package, version=version),
        )
        version.validation_diagnostics = diagnostics
        version.validation_status = self._validation_status_from_diagnostics(diagnostics)
        version.updated_at = now_utc()
        session.commit()
        return self._build_version_response(session, version)

    def activate_version(self, session: Session, package_name: str, version_id: str) -> SkillPackageDetailResponse:
        package, version = self._get_package_version(session, package_name, version_id)
        try:
            self._activate_version_model(session, package=package, version=version)
        except SkillValidationError:
            session.commit()
            raise
        session.commit()
        return self.get_package(session, package_name)

    def activate_version_from_tool(
        self,
        session: Session,
        *,
        package_name: str,
        version_id: str,
        commit: bool = True,
    ) -> dict[str, Any]:
        package, version = self._get_package_version(session, package_name, version_id)
        result = self._activate_version_model(session, package=package, version=version)
        if commit:
            session.commit()
        return result

    def _activate_version_model(
        self,
        session: Session,
        *,
        package: SkillPackage,
        version: SkillVersion,
    ) -> dict[str, Any]:
        diagnostics = self._validate_version_diagnostics(
            package,
            version,
            allowed_tools_baseline=self._active_allowed_tools_baseline(session, package=package, version=version),
        )
        if any(item["severity"] == "error" for item in diagnostics):
            version.validation_status = "invalid"
            version.validation_diagnostics = diagnostics
            version.updated_at = now_utc()
            raise SkillValidationError(
                "Skill package version 校验失败，不能激活。",
                details={"package_name": package.name, "version_id": version.id, "diagnostics": diagnostics},
            )
        previous_version_id = package.active_version_id
        version.validation_status = self._validation_status_from_diagnostics(diagnostics)
        version.validation_diagnostics = diagnostics
        version.status = "active"
        version.activated_at = now_utc()
        package.active_version_id = version.id
        package.updated_at = now_utc()
        session.flush()
        return {
            "package_name": package.name,
            "version_id": version.id,
            "version_label": version.version_label,
            "previous_version_id": previous_version_id,
        }

    def list_activations(self, session: Session, agent_run_id: str) -> list[SkillActivationResponse]:
        return [
            SkillActivationResponse(
                id=item.id,
                agent_run_id=item.agent_run_id,
                package_id=item.package_id,
                version_id=item.version_id,
                activation_context=item.activation_context,
                created_at=item.created_at,
            )
            for item in self.repository.list_activations(session, agent_run_id)
        ]

    def hydrate_agent_run_skill_context(
        self,
        session: Session,
        *,
        agent_run_id: str,
        max_chars: int = DEFAULT_SKILL_CONTEXT_MAX_CHARS,
        reference_limit: int = DEFAULT_SKILL_CONTEXT_REFERENCE_LIMIT,
    ) -> list[dict[str, Any]]:
        context: list[dict[str, Any]] = []
        resolved_max_chars = max(400, min(12000, int(max_chars or DEFAULT_SKILL_CONTEXT_MAX_CHARS)))
        resolved_reference_limit = max(0, min(10, int(reference_limit or DEFAULT_SKILL_CONTEXT_REFERENCE_LIMIT)))
        for activation in self.repository.list_activations(session, agent_run_id):
            package = self.repository.get_package(session, activation.package_id)
            version = self.repository.get_version(session, activation.version_id)
            if not package or not version:
                continue
            resources = version.resource_index if isinstance(version.resource_index, list) else []
            references: list[dict[str, Any]] = []
            for resource in resources:
                if len(references) >= resolved_reference_limit or not isinstance(resource, dict):
                    continue
                resource_path = str(resource.get("path") or "").strip()
                if not resource_path.startswith("references/"):
                    continue
                content = self._read_local_package_file(package, resource_path, max_chars=resolved_max_chars)
                if content:
                    references.append({"path": resource_path, "content": content})
            context.append(
                {
                    "package_name": package.name,
                    "scope": package.scope,
                    "version_id": version.id,
                    "version_label": version.version_label,
                    "content_hash": version.content_hash,
                    "allowed_tools": [str(tool) for tool in version.allowed_tools],
                    "skill_md": self._read_local_package_file(package, "SKILL.md", max_chars=resolved_max_chars),
                    "references": references,
                }
            )
        return context

    def activate_agent_run_skills(
        self,
        session: Session,
        *,
        agent_run_id: str,
        agent_key: str,
        selected_names: list[Any],
        sync: bool = True,
    ) -> tuple[set[str], list[str]]:
        if sync:
            self.sync_agent_skill_bindings(session, sync_packages=True, commit=False)
        active_tools, active_skill_names = self.active_skill_allowed_tools_for_agent(
            session,
            agent_key=agent_key,
            sync=False,
        )
        if not active_skill_names:
            active_tools, active_skill_names = self._active_tools_from_skill_names(
                session,
                _normalize_string_list(selected_names),
            )
        for package_name in active_skill_names:
            package = self.repository.get_package_by_name(session, package_name)
            if not package or not package.active_version_id:
                continue
            version = self.repository.get_version(session, package.active_version_id)
            if not version:
                continue
            activation = self.repository.get_activation(
                session,
                agent_run_id=agent_run_id,
                version_id=version.id,
            )
            if activation:
                continue
            session.add(
                SkillActivation(
                    agent_run_id=agent_run_id,
                    package_id=package.id,
                    version_id=version.id,
                    activation_context={
                        "agent_key": agent_key,
                        "package_name": package.name,
                        "content_hash": version.content_hash,
                    },
                )
            )
        session.flush()
        return active_tools, active_skill_names

    def _read_local_package_file(self, package: SkillPackage, relative_path: str, *, max_chars: int) -> str:
        source_uri = str(package.source_uri or "").strip()
        if not source_uri.startswith("skills/"):
            return ""
        try:
            base_path = (self.skills_root.parent / source_uri).resolve()
            root_path = self.skills_root.parent.resolve()
            base_path.relative_to(root_path)
            file_path = (base_path / relative_path).resolve()
            file_path.relative_to(base_path)
        except (OSError, ValueError):
            return ""
        if not file_path.is_file():
            return ""
        try:
            content = file_path.read_text(encoding="utf-8")
        except OSError:
            return ""
        if len(content) <= max_chars:
            return content
        return f"{content[:max_chars]}\n...[truncated]"

    def active_skill_allowed_tools_for_agent(
        self,
        session: Session,
        *,
        agent_key: str,
        sync: bool = True,
    ) -> tuple[set[str], list[str]]:
        if sync:
            self.sync_agent_skill_bindings(session, sync_packages=True, commit=False)
        allowed_tools: set[str] = set()
        active_skill_names: list[str] = []
        for binding in self.repository.list_bindings(session, agent_key=agent_key):
            package = self.repository.get_package(session, binding.package_id)
            version = self.repository.get_version(session, binding.active_version_id)
            if not package or not version:
                continue
            active_skill_names.append(package.name)
            allowed_tools.update(str(tool) for tool in version.allowed_tools)
        return allowed_tools, active_skill_names

    def _active_tools_from_skill_names(self, session: Session, skill_names: list[str]) -> tuple[set[str], list[str]]:
        allowed_tools: set[str] = set()
        active_skill_names: list[str] = []
        for package_name in skill_names:
            package = self.repository.get_package_by_name(session, package_name)
            if not package or not package.active_version_id:
                continue
            version = self.repository.get_version(session, package.active_version_id)
            if not version:
                continue
            active_skill_names.append(package.name)
            allowed_tools.update(str(tool) for tool in version.allowed_tools)
        return allowed_tools, active_skill_names

    def _get_package_version(self, session: Session, package_name: str, version_id: str) -> tuple[SkillPackage, SkillVersion]:
        package = self.repository.get_package_by_name(session, package_name)
        if not package:
            raise SkillNotFoundError("未找到 Skill package。", details={"package_name": package_name})
        version = self.repository.get_version(session, version_id)
        if not version or version.package_id != package.id:
            raise SkillNotFoundError(
                "未找到 Skill package version。",
                details={"package_name": package_name, "version_id": version_id},
            )
        return package, version

    def _resolve_parent_version(
        self,
        session: Session,
        *,
        package: SkillPackage,
        parent_version_id: str | None,
    ) -> SkillVersion | None:
        if parent_version_id:
            version = self.repository.get_version(session, parent_version_id)
            if not version or version.package_id != package.id:
                raise SkillNotFoundError(
                    "未找到父级 Skill package version。",
                    details={"package_name": package.name, "parent_version_id": parent_version_id},
                )
            return version
        active_version = self.repository.get_version(session, package.active_version_id)
        if active_version:
            return active_version
        versions = self.repository.list_versions(session, package.id)
        return versions[0] if versions else None

    def _next_candidate_version_label(self, session: Session, package: SkillPackage) -> str:
        return f"candidate-{len(self.repository.list_versions(session, package.id)) + 1}"

    def _resolve_allowed_tools(
        self,
        *,
        manifest: dict[str, Any],
        payload_allowed_tools: list[str] | None,
        parent: SkillVersion | None,
    ) -> list[str]:
        if payload_allowed_tools is not None:
            return _normalize_string_list(payload_allowed_tools)
        manifest_tools = _allowed_tools(manifest)
        if manifest_tools or "allowed-tools" in manifest or "allowed_tools" in manifest:
            return manifest_tools
        if parent:
            return _normalize_string_list(parent.allowed_tools)
        return []

    def _active_allowed_tools_baseline(
        self,
        session: Session,
        *,
        package: SkillPackage,
        version: SkillVersion,
    ) -> SkillVersion | None:
        if not package.active_version_id or package.active_version_id == version.id:
            return None
        return self.repository.get_version(session, package.active_version_id)

    @staticmethod
    def _raise_if_allowed_tools_expands(
        *,
        package: SkillPackage,
        allowed_tools: list[str],
        baseline_version: SkillVersion | None,
    ) -> None:
        diagnostic = SkillPackageService._allowed_tools_expansion_diagnostic(
            package=package,
            allowed_tools=allowed_tools,
            baseline_version=baseline_version,
        )
        if not diagnostic:
            return
        raise SkillValidationError(
            "Skill package allowed-tools 只能收窄，不能扩大权限。",
            details={
                "package_name": package.name,
                "baseline_version_id": diagnostic["baseline_version_id"],
                "expanded_tools": diagnostic["expanded_tools"],
            },
        )

    def _normalize_resource_index(self, resource_index: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not isinstance(resource_index, list):
            raise SkillValidationError("resource_index 必须是数组。")
        normalized: list[dict[str, Any]] = []
        for index, item in enumerate(resource_index):
            if not isinstance(item, dict):
                raise SkillValidationError("resource_index 条目必须是对象。", details={"index": index})
            path = str(item.get("path") or item.get("resource_path") or "").strip()
            if not path:
                raise SkillValidationError("resource_index 条目缺少 path。", details={"index": index})
            self._validate_resource_path(path, index=index)
            kind = str(item.get("kind") or item.get("resource_kind") or _resource_kind(path)).strip() or "file"
            content_hash = str(item.get("content_hash") or "").strip()
            if not content_hash:
                content_hash = _hash_bytes(json.dumps(item, sort_keys=True, ensure_ascii=False).encode("utf-8"))
            try:
                size_bytes = int(item.get("size_bytes") or 0)
            except (TypeError, ValueError) as exc:
                raise SkillValidationError("resource_index.size_bytes 必须是整数。", details={"index": index}) from exc
            if size_bytes < 0:
                raise SkillValidationError("resource_index.size_bytes 不能小于 0。", details={"index": index})
            normalized.append(
                {
                    "path": path,
                    "kind": kind,
                    "content_hash": content_hash,
                    "size_bytes": size_bytes,
                }
            )
        return normalized

    @staticmethod
    def _validate_resource_path(path: str, *, index: int) -> None:
        parts = Path(path).parts
        if path.startswith("/") or "\\" in path or not parts or any(part in {"", ".", ".."} for part in parts):
            raise SkillValidationError("resource_index.path 必须是安全相对路径。", details={"index": index, "path": path})

    @staticmethod
    def _hash_version_payload(
        *,
        manifest_json: dict[str, Any],
        body_object_key: str,
        resource_index: list[dict[str, Any]],
        allowed_tools: list[str],
    ) -> str:
        payload = {
            "manifest_json": manifest_json,
            "body_object_key": body_object_key,
            "resource_index": resource_index,
            "allowed_tools": allowed_tools,
        }
        return _hash_bytes(json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))

    @staticmethod
    def _validate_version_diagnostics(
        package: SkillPackage,
        version: SkillVersion,
        *,
        allowed_tools_baseline: SkillVersion | None = None,
    ) -> list[dict[str, Any]]:
        diagnostics: list[dict[str, Any]] = []
        manifest = version.manifest_json if isinstance(version.manifest_json, dict) else {}
        resource_index = version.resource_index if isinstance(version.resource_index, list) else []
        resource_paths = {str(item.get("path", "")) for item in resource_index if isinstance(item, dict)}

        if not manifest.get("name"):
            diagnostics.append({"severity": "error", "code": "missing_name", "message": "SKILL.md frontmatter 缺少 name。"})
        elif str(manifest.get("name")) != package.name:
            diagnostics.append({
                "severity": "error",
                "code": "name_mismatch",
                "message": "SKILL.md frontmatter name 必须与 package name 一致。",
                "expected": package.name,
                "actual": str(manifest.get("name")),
            })
        if not manifest.get("description"):
            diagnostics.append({
                "severity": "warning",
                "code": "missing_description",
                "message": "SKILL.md frontmatter 建议提供 description。",
            })
        if "SKILL.md" not in resource_paths:
            diagnostics.append({"severity": "error", "code": "missing_skill_md", "message": "Skill package 必须包含 SKILL.md。"})
        if not version.content_hash:
            diagnostics.append({"severity": "error", "code": "missing_content_hash", "message": "SkillVersion 缺少 content_hash。"})
        if not isinstance(version.allowed_tools, list):
            diagnostics.append({"severity": "error", "code": "invalid_allowed_tools", "message": "allowed_tools 必须是列表。"})
        if not version.allowed_tools:
            diagnostics.append({
                "severity": "warning",
                "code": "empty_allowed_tools",
                "message": "Skill package 未声明 allowed-tools。",
            })
        expansion = SkillPackageService._allowed_tools_expansion_diagnostic(
            package=package,
            allowed_tools=version.allowed_tools if isinstance(version.allowed_tools, list) else [],
            baseline_version=allowed_tools_baseline,
        )
        if expansion:
            diagnostics.append(expansion)
        if not any(str(path).startswith("references/") for path in resource_paths):
            diagnostics.append({
                "severity": "warning",
                "code": "missing_references",
                "message": "建议为 Skill package 提供 references/ 资料。",
            })
        return diagnostics

    @staticmethod
    def _allowed_tools_expansion_diagnostic(
        *,
        package: SkillPackage,
        allowed_tools: list[str],
        baseline_version: SkillVersion | None,
    ) -> dict[str, Any] | None:
        if not baseline_version:
            return None
        baseline_tools = set(
            _normalize_string_list(
                baseline_version.allowed_tools if isinstance(baseline_version.allowed_tools, list) else []
            )
        )
        expanded_tools = sorted(set(_normalize_string_list(allowed_tools)) - baseline_tools)
        if not expanded_tools:
            return None
        return {
            "severity": "error",
            "code": "allowed_tools_expand_package_scope",
            "message": "Skill package allowed-tools 只能继承或收窄当前/父版本工具集合。",
            "package_name": package.name,
            "baseline_version_id": baseline_version.id,
            "expanded_tools": expanded_tools,
        }

    @staticmethod
    def _validation_status_from_diagnostics(diagnostics: list[dict[str, Any]]) -> str:
        if any(item["severity"] == "error" for item in diagnostics):
            return "invalid"
        if any(item["severity"] == "warning" for item in diagnostics):
            return "warning"
        return "valid"

    def _scan_packages(self) -> list[ScannedSkillPackage]:
        result: list[ScannedSkillPackage] = []
        for scope in ("psop", "public"):
            scope_root = self.skills_root / scope
            if not scope_root.exists():
                continue
            for package_dir in sorted(path for path in scope_root.iterdir() if path.is_dir()):
                skill_file = package_dir / "SKILL.md"
                if not skill_file.exists():
                    continue
                result.append(self._scan_package(scope=scope, package_dir=package_dir, skill_file=skill_file))
        return result

    def _scan_package(self, *, scope: str, package_dir: Path, skill_file: Path) -> ScannedSkillPackage:
        skill_text = skill_file.read_text(encoding="utf-8")
        manifest, body = _parse_skill_markdown(skill_text)
        name = str(manifest.get("name") or package_dir.name)
        files: dict[str, bytes] = {}
        for path in sorted(item for item in package_dir.rglob("*") if item.is_file()):
            relative_path = path.relative_to(package_dir).as_posix()
            files[relative_path] = path.read_bytes()
        resource_index = [
            {
                "path": path,
                "kind": _resource_kind(path),
                "content_hash": _hash_bytes(content),
                "size_bytes": len(content),
            }
            for path, content in files.items()
        ]
        return ScannedSkillPackage(
            name=name,
            scope=scope,
            source_path=package_dir,
            manifest=manifest,
            body=body,
            files=files,
            content_hash=_hash_files(files),
            resource_index=resource_index,
        )

    def _build_package_summary(self, session: Session, package: SkillPackage) -> SkillPackageSummaryResponse:
        versions = self.repository.list_versions(session, package.id)
        active_version = self.repository.get_version(session, package.active_version_id)
        return SkillPackageSummaryResponse(
            id=package.id,
            name=package.name,
            scope=package.scope,
            description=package.description,
            source_uri=package.source_uri,
            status=package.status,
            active_version_id=package.active_version_id,
            active_version_label=active_version.version_label if active_version else None,
            active_content_hash=active_version.content_hash if active_version else None,
            used_by_agents=self._skill_package_agent_usages(session, package),
            version_count=len(versions),
            created_at=package.created_at,
            updated_at=package.updated_at,
        )

    def _skill_package_agent_usages(self, session: Session, package: SkillPackage) -> list[SkillPackageAgentUsageResponse]:
        usages: list[SkillPackageAgentUsageResponse] = []
        specs = {str(spec["key"]): spec for spec in self._agent_skill_specs(session)}
        for binding in self.repository.list_bindings(session, package_id=package.id):
            spec = specs.get(binding.agent_key)
            usages.append(
                SkillPackageAgentUsageResponse(
                    key=binding.agent_key,
                    name=str(spec.get("name") if spec else binding.agent_key),
                    role=str(spec.get("role") if spec else ""),
                    skill_binding_id=binding.id,
                    usage_key=binding.usage_key,
                    active_version_id=str(spec.get("active_version_id") or "") or None,
                    active_version_label=str(spec.get("active_version_label") or "") or None,
                )
            )
        if usages:
            return sorted(usages, key=lambda item: item.key)
        for spec in specs.values():
            allowed_skill_names = spec.get("allowed_skill_names") if isinstance(spec, dict) else []
            if not isinstance(allowed_skill_names, list) or package.name not in allowed_skill_names:
                continue
            usages.append(
                SkillPackageAgentUsageResponse(
                    key=str(spec["key"]),
                    name=str(spec["name"]),
                    role=str(spec["role"]),
                    active_version_id=str(spec.get("active_version_id") or "") or None,
                    active_version_label=str(spec.get("active_version_label") or "") or None,
                )
            )
        return sorted(usages, key=lambda item: item.key)

    @staticmethod
    def _agent_skill_specs(session: Session) -> list[dict[str, Any]]:
        specs: list[dict[str, Any]] = []
        existing_keys: set[str] = set()
        definitions = session.scalars(
            select(AgentDefinition)
            .where(AgentDefinition.status != "archived")
            .order_by(AgentDefinition.key.asc())
        ).all()
        for definition in definitions:
            existing_keys.add(definition.key)
            version = session.get(AgentVersion, definition.active_version_id)
            if not version:
                continue
            spec = dict(version.spec_json or {})
            specs.append(
                {
                    "key": definition.key,
                    "name": definition.name,
                    "role": definition.role,
                    "allowed_skill_names": spec.get("allowed_skill_names") if isinstance(spec, dict) else [],
                    "active_version_id": version.id,
                    "active_version_label": version.version_label,
                }
            )
        from app.agents.service import DEFAULT_AGENT_SPECS

        for seed in DEFAULT_AGENT_SPECS:
            agent_key = str(seed["key"])
            if agent_key in existing_keys:
                continue
            specs.append(
                {
                    "key": agent_key,
                    "name": str(seed["name"]),
                    "role": str(seed["role"]),
                    "allowed_skill_names": seed.get("allowed_skill_names") if isinstance(seed, dict) else [],
                    "active_version_id": None,
                    "active_version_label": "seed",
                }
            )
        return specs

    def _build_version_response(self, session: Session, version: SkillVersion) -> SkillVersionResponse:
        resources = self.repository.list_resources(session, version.id)
        return SkillVersionResponse(
            id=version.id,
            package_id=version.package_id,
            version_label=version.version_label,
            status=version.status,
            content_hash=version.content_hash,
            manifest_json=version.manifest_json,
            body_object_key=version.body_object_key,
            resource_index=version.resource_index,
            allowed_tools=[str(tool) for tool in version.allowed_tools],
            validation_status=version.validation_status,
            validation_diagnostics=[dict(item) for item in version.validation_diagnostics],
            activated_at=version.activated_at,
            resource_count=len(resources),
            created_at=version.created_at,
            updated_at=version.updated_at,
        )

    @staticmethod
    def _build_resource_response(resource: SkillResource) -> SkillResourceResponse:
        return SkillResourceResponse(
            id=resource.id,
            version_id=resource.version_id,
            resource_path=resource.resource_path,
            resource_kind=resource.resource_kind,
            content_hash=resource.content_hash,
            size_bytes=resource.size_bytes,
            created_at=resource.created_at,
        )


def _parse_skill_markdown(content: str) -> tuple[dict[str, Any], str]:
    if not content.startswith("---\n"):
        return {}, content
    _, rest = content.split("---\n", 1)
    manifest_text, separator, body = rest.partition("\n---\n")
    if not separator:
        return {}, content
    manifest = yaml.safe_load(manifest_text) or {}
    if not isinstance(manifest, dict):
        manifest = {}
    return manifest, body.lstrip()


def _hash_files(files: dict[str, bytes]) -> str:
    digest = hashlib.sha256()
    for path, content in sorted(files.items()):
        digest.update(path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(content)
        digest.update(b"\0")
    return digest.hexdigest()


def _allowed_tools(manifest: dict[str, Any]) -> list[str]:
    value = manifest.get("allowed-tools", manifest.get("allowed_tools", []))
    if not isinstance(value, list):
        return []
    return _normalize_string_list(value)


def _hash_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _normalize_string_list(value: list[Any]) -> list[str]:
    return [str(item).strip() for item in value if str(item).strip()]


def _normalize_optional(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _json_clone(value: Any) -> Any:
    return json.loads(json.dumps(value))


def _resource_kind(path: str) -> str:
    if path == "SKILL.md":
        return "skill"
    first, _, _ = path.partition("/")
    if first in {"references", "scripts", "assets", "examples", "tests", "prompts"}:
        return first
    return "file"
