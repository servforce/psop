from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent_harness.definitions import DEFAULT_COMPILE_AGENT_REF, PROMPT_USAGE_AGENT_KEYS
from app.pskills.exceptions import SkillsConfigurationError


AGENTS_ROOT = Path(__file__).resolve().parent
DEFAULT_DOMAIN_PACK_ID = "generic"
DEFAULT_DOMAIN_PACK_VERSION = "v1"


@dataclass(frozen=True, slots=True)
class AgentPromptPack:
    key: str
    agent_id: str
    agent_key: str
    version: str
    scenario: str
    route_key: str
    description: str
    root_path: Path
    spec: dict[str, Any]
    files: dict[str, str]
    system_prompt: str
    prompt_hash: str
    definition_key: str | None = None
    version_id: str | None = None
    version_label: str | None = None
    source: str = "repo"

    def metadata(self) -> dict[str, Any]:
        metadata = {
            "agent_id": self.agent_id,
            "agent_key": self.agent_key,
            "prompt_ref": self.key,
            "version": self.version,
            "scenario": self.scenario,
            "route_key": self.route_key,
            "prompt_hash": self.prompt_hash,
        }
        if self.definition_key:
            metadata["definition_key"] = self.definition_key
        if self.version_id:
            metadata["version_id"] = self.version_id
        if self.version_label:
            metadata["version_label"] = self.version_label
        metadata["source"] = self.source
        return metadata


@dataclass(frozen=True, slots=True)
class DomainPack:
    pack_id: str
    version: str
    title: str
    root_path: Path
    spec: dict[str, Any]
    files: dict[str, str]
    content_hash: str

    @property
    def key(self) -> str:
        return f"{self.pack_id}/{self.version}"

    @property
    def guidance(self) -> str:
        parts: list[str] = []
        for relative_path, content in sorted(self.files.items()):
            if relative_path == "pack.yaml":
                continue
            parts.append(f"## {relative_path}\n\n{content.strip()}")
        return "\n\n".join(parts).strip()

    def metadata(self) -> dict[str, Any]:
        return {
            "domain_pack_id": self.pack_id,
            "domain_pack_key": self.key,
            "version": self.version,
            "title": self.title,
            "domain_pack_hash": self.content_hash,
        }


@dataclass(frozen=True, slots=True)
class DomainPackResolution:
    requested_ref: str | None
    pack: DomainPack
    used_default: bool
    fallback_reason: str = ""


class PromptRegistry:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or AGENTS_ROOT

    def load_default_compile_agent(self) -> AgentPromptPack:
        return self.load_agent(DEFAULT_COMPILE_AGENT_REF)

    def load_default_compile_agent_for_session(self, session: Session | None = None) -> AgentPromptPack:
        return self.load_agent_for_usage(
            "pskill.compile.formal_v5",
            fallback_ref=DEFAULT_COMPILE_AGENT_REF,
            session=session,
        )

    def load_agent_for_usage(
        self,
        usage_key: str,
        *,
        fallback_ref: str,
        session: Session | None = None,
    ) -> AgentPromptPack:
        if session is not None:
            db_pack = self._load_agent_for_usage_from_db(session, usage_key)
            if db_pack:
                return db_pack
        return self.load_agent(fallback_ref, usage_key=usage_key)

    def load_agent(self, ref: str, *, usage_key: str | None = None) -> AgentPromptPack:
        root_path = self.root / ref
        files = _read_asset_files(root_path)
        return _build_agent_prompt_pack(
            key=ref,
            root_path=root_path,
            files=files,
            usage_key=usage_key,
            source="repo",
        )

    def _load_agent_for_usage_from_db(self, session: Session, usage_key: str) -> AgentPromptPack | None:
        from app.agent_prompts.models import AgentPromptBinding, AgentPromptDefinition, AgentPromptVersion

        binding = session.scalar(select(AgentPromptBinding).where(AgentPromptBinding.usage_key == usage_key))
        if not binding or not binding.active_version_id:
            return None
        version = session.get(AgentPromptVersion, binding.active_version_id)
        definition = session.get(AgentPromptDefinition, binding.definition_id)
        if not version or not definition or version.status != "published":
            return None
        files = {str(key): str(value) for key, value in dict(version.files or {}).items()}
        return _build_agent_prompt_pack(
            key=f"{definition.key}/{version.version_label}",
            root_path=self.root,
            files=files,
            definition_key=definition.key,
            version_id=version.id,
            version_label=version.version_label,
            usage_key=usage_key,
            source="db",
        )


def _build_agent_prompt_pack(
    *,
    key: str,
    root_path: Path,
    files: dict[str, str],
    definition_key: str | None = None,
    version_id: str | None = None,
    version_label: str | None = None,
    usage_key: str | None = None,
    source: str = "repo",
) -> AgentPromptPack:
    spec = _load_yaml_asset(files, root_path, "agent.yaml")
    system_prompt = _required_text(files, root_path, "system.md").strip()

    agent_id = _required_string(spec, "agent_id", root_path / "agent.yaml")
    agent_key = _agent_key_from_prompt_spec(spec, usage_key=usage_key, fallback=agent_id)
    version = str(spec.get("version") or version_label or root_path.name)
    scenario = str(spec.get("scenario") or key.split("/", 1)[0])
    route_key = str(spec.get("route_key") or "text")
    description = str(spec.get("description") or "")

    return AgentPromptPack(
        key=key,
        agent_id=agent_id,
        agent_key=agent_key,
        version=version,
        scenario=scenario,
        route_key=route_key,
        description=description,
        root_path=root_path,
        spec=spec,
        files=files,
        system_prompt=system_prompt,
        prompt_hash=content_hash(files),
        definition_key=definition_key,
        version_id=version_id,
        version_label=version_label,
        source=source,
    )


def _agent_key_from_prompt_spec(spec: dict[str, Any], *, usage_key: str | None, fallback: str) -> str:
    explicit = str(spec.get("agent_key") or "").strip()
    if explicit:
        return explicit
    mapped = PROMPT_USAGE_AGENT_KEYS.get(str(usage_key or "").strip())
    return mapped or fallback


class DomainPackRegistry:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or AGENTS_ROOT / "domain_packs"

    def load_default(self) -> DomainPack:
        return self.load_domain_pack(DEFAULT_DOMAIN_PACK_ID, DEFAULT_DOMAIN_PACK_VERSION)

    def resolve(self, ref: str | None) -> DomainPackResolution:
        if not ref:
            return DomainPackResolution(requested_ref=None, pack=self.load_default(), used_default=False)

        pack_id, version = _parse_domain_pack_ref(ref)
        try:
            pack = self.load_domain_pack(pack_id, version)
            return DomainPackResolution(requested_ref=ref, pack=pack, used_default=False)
        except SkillsConfigurationError as exc:
            fallback = self.load_default()
            return DomainPackResolution(
                requested_ref=ref,
                pack=fallback,
                used_default=True,
                fallback_reason=exc.message,
            )

    def load_domain_pack(self, pack_id: str, version: str = DEFAULT_DOMAIN_PACK_VERSION) -> DomainPack:
        root_path = self.root / pack_id / version
        files = _read_asset_files(root_path)
        spec = _load_yaml_asset(files, root_path, "pack.yaml")

        configured_id = str(spec.get("pack_id") or pack_id)
        configured_version = str(spec.get("version") or version)
        title = str(spec.get("title") or configured_id)

        return DomainPack(
            pack_id=configured_id,
            version=configured_version,
            title=title,
            root_path=root_path,
            spec=spec,
            files=files,
            content_hash=content_hash(files),
        )


def content_hash(files: dict[str, str]) -> str:
    digest = hashlib.sha256()
    for relative_path, content in sorted(files.items()):
        digest.update(relative_path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(content.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def _read_asset_files(root_path: Path) -> dict[str, str]:
    if not root_path.exists() or not root_path.is_dir():
        raise SkillsConfigurationError(
            "Agent prompt asset directory does not exist.",
            details={"path": str(root_path)},
        )

    files: dict[str, str] = {}
    for path in sorted(root_path.rglob("*")):
        if not path.is_file() or path.name.startswith("."):
            continue
        relative_path = path.relative_to(root_path).as_posix()
        files[relative_path] = path.read_text(encoding="utf-8")
    if not files:
        raise SkillsConfigurationError(
            "Agent prompt asset directory is empty.",
            details={"path": str(root_path)},
        )
    return files


def _load_yaml_asset(files: dict[str, str], root_path: Path, relative_path: str) -> dict[str, Any]:
    content = _required_text(files, root_path, relative_path)
    try:
        parsed = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        raise SkillsConfigurationError(
            "Agent prompt asset YAML cannot be parsed.",
            details={"path": str(root_path / relative_path), "error": str(exc)},
        ) from exc
    if not isinstance(parsed, dict):
        raise SkillsConfigurationError(
            "Agent prompt asset YAML must be an object.",
            details={"path": str(root_path / relative_path)},
        )
    return parsed


def _required_text(files: dict[str, str], root_path: Path, relative_path: str) -> str:
    content = files.get(relative_path)
    if content is None:
        raise SkillsConfigurationError(
            "Agent prompt asset file is missing.",
            details={"path": str(root_path / relative_path)},
        )
    return content


def _required_string(spec: dict[str, Any], field_name: str, path: Path) -> str:
    value = spec.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise SkillsConfigurationError(
            f"Agent prompt asset `{field_name}` must be a non-empty string.",
            details={"path": str(path), "field": field_name},
        )
    return value


def _parse_domain_pack_ref(ref: str) -> tuple[str, str]:
    normalized = ref.strip().strip("/")
    if not normalized:
        return DEFAULT_DOMAIN_PACK_ID, DEFAULT_DOMAIN_PACK_VERSION
    parts = normalized.split("/")
    if len(parts) == 1:
        return parts[0], DEFAULT_DOMAIN_PACK_VERSION
    return parts[0], parts[1]
