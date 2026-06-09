from __future__ import annotations

from typing import Any

from app.pskills.exceptions import SkillValidationError


VALID_MEMORY_TYPES = {"short_term", "semantic", "episodic", "procedural", "artifact"}
VALID_MEMORY_STATUSES = {"pending_review", "active", "rejected", "archived"}

FORMAL_FACT_SOURCE_KINDS = {
    "git_source",
    "eg_compile_artifact",
    "session_token_snapshot",
    "run_event",
    "run_trace",
}

MEMORY_BOUNDARY_METADATA = {
    "used_as_runtime_state": False,
    "authoritative_source": False,
}

FORMAL_SOURCE_REPLACEMENT_FLAGS = {
    "used_as_runtime_state",
    "replaces_formal_source",
    "replaces_runtime_state",
    "authoritative_source",
}

SOURCE_REF_LOCATOR_KEYS = ("id", "ref_id", "run_id", "seq_no", "path", "object_key")


def normalize_memory_type(memory_type: str) -> str:
    normalized = str(memory_type or "").strip()
    if normalized not in VALID_MEMORY_TYPES:
        raise SkillValidationError("memory_type 无效。", details={"memory_type": memory_type})
    return normalized


def normalize_memory_status(status: str) -> str:
    normalized = str(status or "").strip()
    if normalized not in VALID_MEMORY_STATUSES:
        raise SkillValidationError("memory status 无效。", details={"status": status})
    return normalized


def normalize_source_refs(source_refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized_refs: list[dict[str, Any]] = []
    for index, item in enumerate(source_refs):
        if not isinstance(item, dict):
            raise SkillValidationError("memory source_refs 条目必须是对象。", details={"index": index})
        kind = str(item.get("kind") or "").strip()
        locator_key = next((key for key in SOURCE_REF_LOCATOR_KEYS if str(item.get(key) or "").strip()), "")
        locator = str(item.get(locator_key) or "").strip() if locator_key else ""
        if not kind or not locator:
            raise SkillValidationError(
                "memory source_refs 必须包含 kind 和可回放定位符。",
                details={"index": index, "source_ref": item},
            )
        normalized = dict(item)
        normalized["kind"] = kind
        if "id" in item or "ref_id" in item:
            normalized["id"] = locator
        normalized_refs.append(normalized)
    return normalized_refs


def memory_boundary_metadata(source_refs: list[dict[str, Any]], metadata: dict[str, Any]) -> dict[str, Any]:
    assert_memory_does_not_replace_formal_source(metadata)
    formal_source_refs = []
    for item in source_refs:
        if str(item.get("kind") or "") not in FORMAL_FACT_SOURCE_KINDS:
            continue
        formal_ref = {"kind": str(item.get("kind") or "")}
        for key in SOURCE_REF_LOCATOR_KEYS:
            value = str(item.get(key) or "").strip()
            if value:
                formal_ref[key if key != "ref_id" else "id"] = value
                break
        formal_source_refs.append(formal_ref)
    normalized_metadata = {
        **dict(metadata),
        **MEMORY_BOUNDARY_METADATA,
    }
    if formal_source_refs:
        normalized_metadata["formal_source_refs"] = formal_source_refs
    return normalized_metadata


def assert_memory_does_not_replace_formal_source(metadata: dict[str, Any]) -> None:
    replacement_flags = formal_source_replacement_flags(metadata)
    if replacement_flags:
        raise SkillValidationError(
            "Memory 不能替代 Git source、EG artifact、SessionTokenSnapshot、run_event 或 run_trace。",
            details={"replacement_flags": replacement_flags},
        )


def formal_source_replacement_flags(metadata: dict[str, Any]) -> list[str]:
    replacement_flags = sorted(
        key
        for key in FORMAL_SOURCE_REPLACEMENT_FLAGS
        if bool(metadata.get(key)) is True
    )
    authoritative_kind = str(metadata.get("authoritative_source_kind") or "").strip()
    if authoritative_kind in FORMAL_FACT_SOURCE_KINDS:
        replacement_flags.append("authoritative_source_kind")
    return replacement_flags
