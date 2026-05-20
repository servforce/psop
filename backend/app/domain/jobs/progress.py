from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


PUBLISH_PROGRESS_STAGES: tuple[dict[str, str], ...] = (
    {"key": "source_frozen", "label": "冻结源码"},
    {"key": "compile_request_created", "label": "创建编译任务"},
    {"key": "source_loaded", "label": "读取冻结源码"},
    {"key": "manifest_checked", "label": "校验 manifest"},
    {"key": "agent_compiling", "label": "智能体编译 EG"},
    {"key": "artifact_validating", "label": "校验 EG artifact"},
    {"key": "artifact_emitting", "label": "写入编译产物"},
    {"key": "publish_finalizing", "label": "完成发布"},
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_publish_progress_payload(
    *,
    compile_request_id: str,
    publish_record_id: str | None,
    skill_definition_id: str,
    skill_version_id: str,
    published_commit_sha: str,
) -> dict[str, Any]:
    now = utc_now_iso()
    stages = [
        {
            "key": item["key"],
            "label": item["label"],
            "status": "pending",
            "message": "",
            "started_at": None,
            "finished_at": None,
        }
        for item in PUBLISH_PROGRESS_STAGES
    ]
    payload: dict[str, Any] = {
        "compile_request_id": compile_request_id,
        "publish_record_id": publish_record_id,
        "skill_definition_id": skill_definition_id,
        "skill_version_id": skill_version_id,
        "published_commit_sha": published_commit_sha,
        "current_stage": "source_frozen",
        "terminal": False,
        "terminal_status": None,
        "error_message": "",
        "progress_stages": stages,
        "updated_at": now,
    }
    payload = mark_publish_stage(
        payload,
        "source_frozen",
        "succeeded",
        f"已冻结 commit {published_commit_sha[:12]}。",
    )
    return mark_publish_stage(payload, "compile_request_created", "succeeded", "编译任务已创建。")


def ensure_publish_progress_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    payload = dict(payload or {})
    existing = {
        stage.get("key"): dict(stage)
        for stage in payload.get("progress_stages", [])
        if isinstance(stage, dict) and stage.get("key")
    }
    payload["progress_stages"] = [
        {
            "key": item["key"],
            "label": existing.get(item["key"], {}).get("label", item["label"]),
            "status": existing.get(item["key"], {}).get("status", "pending"),
            "message": existing.get(item["key"], {}).get("message", ""),
            "started_at": existing.get(item["key"], {}).get("started_at"),
            "finished_at": existing.get(item["key"], {}).get("finished_at"),
        }
        for item in PUBLISH_PROGRESS_STAGES
    ]
    payload.setdefault("current_stage", "source_frozen")
    payload.setdefault("terminal", False)
    payload.setdefault("terminal_status", None)
    payload.setdefault("error_message", "")
    payload.setdefault("updated_at", utc_now_iso())
    return payload


def mark_publish_stage(
    payload: dict[str, Any] | None,
    stage_key: str,
    status: str,
    message: str = "",
    *,
    terminal_status: str | None = None,
    error_message: str = "",
) -> dict[str, Any]:
    payload = ensure_publish_progress_payload(payload)
    now = utc_now_iso()
    updated_stages: list[dict[str, Any]] = []
    for stage in payload["progress_stages"]:
        updated = dict(stage)
        if updated["key"] == stage_key:
            updated["status"] = status
            updated["message"] = message
            if status in {"running", "succeeded", "failed"} and not updated.get("started_at"):
                updated["started_at"] = now
            if status in {"succeeded", "failed"}:
                updated["finished_at"] = now
        updated_stages.append(updated)

    payload["progress_stages"] = updated_stages
    payload["current_stage"] = stage_key
    payload["updated_at"] = now
    if terminal_status:
        payload["terminal"] = True
        payload["terminal_status"] = terminal_status
    if error_message:
        payload["error_message"] = error_message
    return payload
