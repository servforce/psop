from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy.orm import Session

from app.pskills.exceptions import SkillNotFoundError, SkillValidationError
from app.pskills.models import now_utc
from app.skills.models import SkillPackage, SkillResource, SkillVersion
from app.skills.repository import SkillPackageRepository
from app.skills.schemas import (
    SkillPackageDetailResponse,
    SkillPackageSummaryResponse,
    SkillPackageSyncResponse,
    SkillActivationResponse,
    SkillResourceResponse,
    SkillVersionResponse,
)


SKILLS_ROOT = Path(__file__).resolve().parents[3] / "skills"


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
        skills_root: Path | None = None,
    ) -> None:
        self.repository = repository or SkillPackageRepository()
        self.skills_root = skills_root or SKILLS_ROOT

    def sync_packages(self, session: Session) -> SkillPackageSyncResponse:
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
        if changed:
            session.commit()
        packages = self.list_packages(session)
        version_count = sum(item.version_count for item in packages)
        return SkillPackageSyncResponse(
            changed=changed,
            scanned_count=len(scanned),
            package_count=len(packages),
            version_count=version_count,
            packages=packages,
        )

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

    def validate_version(self, session: Session, package_name: str, version_id: str) -> SkillVersionResponse:
        package, version = self._get_package_version(session, package_name, version_id)
        diagnostics = self._validate_version_diagnostics(package, version)
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
        diagnostics = self._validate_version_diagnostics(package, version)
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

    @staticmethod
    def _validate_version_diagnostics(package: SkillPackage, version: SkillVersion) -> list[dict[str, Any]]:
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
        if not any(str(path).startswith("references/") for path in resource_paths):
            diagnostics.append({
                "severity": "warning",
                "code": "missing_references",
                "message": "建议为 Skill package 提供 references/ 资料。",
            })
        return diagnostics

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
            version_count=len(versions),
            created_at=package.created_at,
            updated_at=package.updated_at,
        )

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
    return [str(tool) for tool in value]


def _hash_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _resource_kind(path: str) -> str:
    if path == "SKILL.md":
        return "skill"
    first, _, _ = path.partition("/")
    if first in {"references", "scripts", "assets", "examples", "tests", "prompts"}:
        return first
    return "file"
