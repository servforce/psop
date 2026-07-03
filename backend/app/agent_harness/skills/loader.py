from __future__ import annotations

import hashlib
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any

import yaml

from app.agent_harness.events import AgentEventWriter
from app.agent_harness.skills.spec import AgentSkill


class SkillLoader:
    def __init__(self, skills_root: Path) -> None:
        self.skills_root = skills_root

    def load_metadata(self, skill_name: str) -> AgentSkill:
        metadata, _, skill_path = self._read(skill_name)
        return _skill_from_parts(metadata=metadata, body="", skill_path=skill_path, skill_name=skill_name)

    def load(self, skill_name: str, event_writer: AgentEventWriter | None = None) -> AgentSkill:
        metadata, body, skill_path = self._read(skill_name)
        skill = _skill_from_parts(metadata=metadata, body=body.strip(), skill_path=skill_path, skill_name=skill_name)
        if event_writer:
            event_writer.record(
                "agent.skill.loaded",
                {"skill_name": skill.name, "description": skill.description, "allowed_tools": skill.allowed_tools},
            )
        return skill

    def load_resource(
        self,
        skill_name: str,
        resource_path: str,
        event_writer: AgentEventWriter | None = None,
        *,
        max_chars: int = 60_000,
    ) -> dict[str, Any]:
        skill_dir = (self.skills_root / skill_name).resolve()
        if not skill_dir.exists():
            raise FileNotFoundError(f"Agent Skill 不存在：{skill_name}")
        relative_path = _normalize_resource_path(resource_path)
        resolved_path = (skill_dir / relative_path).resolve()
        try:
            resolved_path.relative_to(skill_dir)
        except ValueError as exc:
            raise ValueError("resource_path 必须位于声明的 Skill 目录内。") from exc
        if not resolved_path.is_file():
            raise FileNotFoundError(f"Agent Skill resource 不存在：{resource_path}")
        if resolved_path.suffix.lower() != ".md":
            raise ValueError("Agent Skill resource 当前仅允许读取 Markdown 文件。")
        max_chars = _normalize_max_chars(max_chars)
        content = resolved_path.read_text(encoding="utf-8")
        returned_content = content[:max_chars]
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        normalized_path = PurePosixPath(*relative_path.parts).as_posix()
        truncated = len(returned_content) < len(content)
        if event_writer:
            event_writer.record(
                "agent.skill.resource.loaded",
                {
                    "skill_name": skill_name,
                    "resource_path": normalized_path,
                    "path": str(resolved_path),
                    "content_hash": content_hash,
                    "char_count": len(content),
                    "returned_chars": len(returned_content),
                    "truncated": truncated,
                },
            )
        return {
            "skill_name": skill_name,
            "resource_path": normalized_path,
            "content": returned_content,
            "path": str(resolved_path),
            "content_hash": content_hash,
            "char_count": len(content),
            "returned_chars": len(returned_content),
            "truncated": truncated,
        }

    def _read(self, skill_name: str) -> tuple[dict[str, Any], str, Path]:
        skill_path = self.skills_root / skill_name / "SKILL.md"
        if not skill_path.exists():
            raise FileNotFoundError(f"Agent Skill 不存在：{skill_name}")
        metadata, body = _parse_skill_md(skill_path.read_text(encoding="utf-8"))
        return metadata, body, skill_path


def _parse_skill_md(content: str) -> tuple[dict[str, Any], str]:
    if not content.startswith("---\n"):
        raise ValueError("Agent Skill 必须包含 YAML frontmatter。")
    parts = content.split("---", 2)
    if len(parts) < 3:
        raise ValueError("Agent Skill frontmatter 格式无效。")
    _, raw_metadata, body = parts
    metadata = yaml.safe_load(raw_metadata) or {}
    if not isinstance(metadata, dict):
        raise ValueError("Agent Skill frontmatter 必须是 YAML 对象。")
    return metadata, body


def _skill_from_parts(*, metadata: dict[str, Any], body: str, skill_path: Path, skill_name: str) -> AgentSkill:
    name = str(metadata.get("name") or "").strip()
    if name != skill_name:
        raise ValueError(f"Agent Skill name 必须匹配目录名：{name!r} != {skill_name!r}")
    description = str(metadata.get("description") or "").strip()
    if not description:
        raise ValueError(f"{skill_path} 必须声明 description。")
    return AgentSkill(
        name=name,
        description=description,
        allowed_tools=_parse_allowed_tools(metadata, skill_path),
        instruction=body,
        path=str(skill_path),
    )


def _parse_allowed_tools(metadata: dict[str, Any], skill_path: Path) -> list[str]:
    if "allowed-tools" not in metadata:
        raise ValueError(f"{skill_path} 必须声明 allowed-tools。")
    raw = metadata["allowed-tools"]
    if not isinstance(raw, list):
        raise ValueError(f"{skill_path} 的 allowed-tools 必须是字符串列表。")
    allowed_tools: list[str] = []
    for item in raw:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{skill_path} 的 allowed-tools 只能包含非空字符串。")
        allowed_tools.append(item.strip())
    return allowed_tools


def _normalize_resource_path(resource_path: str) -> Path:
    if not isinstance(resource_path, str) or not resource_path.strip():
        raise ValueError("resource_path 必须是非空字符串。")
    value = resource_path.strip()
    if "\0" in value or "\\" in value:
        raise ValueError("resource_path 包含非法字符。")
    parsed = PurePosixPath(value)
    if parsed.is_absolute() or not parsed.parts:
        raise ValueError("resource_path 必须是相对路径。")
    if any(part in {"", ".", ".."} for part in parsed.parts):
        raise ValueError("resource_path 不允许包含 . 或 ..。")
    return Path(*parsed.parts)


def _normalize_max_chars(max_chars: int) -> int:
    if not isinstance(max_chars, int) or max_chars <= 0:
        raise ValueError("max_chars 必须是正整数。")
    return min(max_chars, 120_000)
