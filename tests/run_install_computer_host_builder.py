#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.agent_harness.service import AgentHarnessService
from app.core.config import Settings
from app.domain.skills.schemas import GenerateSkillDraftRequest
from app.domain.skills.service import SkillsService
from app.gateway.asr import HttpAsrGateway
from app.gateway.gitlab import HttpGitLabSkillSourceGateway
from app.gateway.inference import OpenAICompatibleInferenceGateway
from app.infra.database import DatabaseManager
from app.infra.object_store import ObjectStoreService


DEFAULT_SKILL_ID = "01738427-60f9-4171-871b-ab84b40ac2db"
DEFAULT_USER_DESCRIPTION = "帮我构建一个安装电脑主机的技能。"
REQUIRED_SKILLS = {"psop-builder-core", "psop-builder-evidence-mapping", "psop-builder-quality-review"}
REQUIRED_TOOLS = {
    "psop.builder.read_current_source",
    "psop.builder.list_materials",
    "psop.builder.read_material_analysis",
    "psop.builder.list_reference_assets",
    "psop.standard.search",
    "psop.builder.submit_candidate",
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a real PSOP Builder generation test for 安装电脑主机.")
    parser.add_argument("--skill-id", default=DEFAULT_SKILL_ID, help="PSOP Skill ID。")
    parser.add_argument("--user-description", default=DEFAULT_USER_DESCRIPTION, help="本次构建输入。")
    parser.add_argument("--no-base-commit", action="store_true", help="不传 base_commit_sha，跳过 source head 预检查。")
    parser.add_argument("--full-output", action="store_true", help="打印完整 generation response JSON。")
    args = parser.parse_args()

    _load_orm_models()
    settings = Settings(runtime_worker_enabled=False)
    db_manager = DatabaseManager(settings.sqlalchemy_database_url)
    object_store = ObjectStoreService.from_settings(settings)
    service = SkillsService(
        settings=settings,
        gitlab_gateway=HttpGitLabSkillSourceGateway.from_settings(settings),
        inference_gateway=OpenAICompatibleInferenceGateway.from_settings(settings),
        asr_gateway=HttpAsrGateway.from_settings(settings),
        object_store=object_store,
        agent_harness_service=AgentHarnessService(settings=settings),
    )

    with db_manager.session() as session:
        detail = service.get_skill_detail(session, args.skill_id)
        materials = service.list_raw_materials(session, skill_id=args.skill_id)
        _print_preflight(detail, materials, settings)

        base_commit_sha = None if args.no_base_commit else detail.latest_draft_head_sha
        response = service.generate_skill_draft_from_raw_materials(
            session,
            skill_id=args.skill_id,
            payload=GenerateSkillDraftRequest(
                user_description=args.user_description,
                base_commit_sha=base_commit_sha,
            ),
        )

    payload = response.model_dump(mode="json")
    print(json.dumps(payload if args.full_output else _generation_summary(payload), ensure_ascii=False, indent=2))
    return _validate_result(payload)


def _print_preflight(detail: Any, materials: list[Any], settings: Settings) -> None:
    print(
        json.dumps(
            {
                "preflight": {
                    "skill_id": detail.id,
                    "skill_name": detail.name,
                    "latest_draft_head_sha": detail.latest_draft_head_sha,
                    "material_count": len(materials),
                    "materials": [
                        {
                            "id": item.id,
                            "name": item.name,
                            "material_kind": item.material_kind,
                            "status": item.status,
                            "analysis_status": item.analysis_status,
                            "derived_asset_count": item.derived_asset_count,
                        }
                        for item in materials
                    ],
                    "database_url": _redact_database_url(settings.sqlalchemy_database_url),
                    "standard_lightrag_base_url": settings.standard_lightrag_base_url,
                    "agent_harness_sandbox_root": str((settings.repo_root / settings.agent_harness_sandbox_root).resolve())
                    if not Path(settings.agent_harness_sandbox_root).is_absolute()
                    else settings.agent_harness_sandbox_root,
                }
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def _generation_summary(payload: dict[str, Any]) -> dict[str, Any]:
    metadata = payload.get("prompt_metadata") if isinstance(payload.get("prompt_metadata"), dict) else {}
    generated_files = payload.get("generated_files") if isinstance(payload.get("generated_files"), dict) else {}
    return {
        "generation_id": payload.get("id"),
        "job_id": payload.get("job_id"),
        "status": payload.get("status"),
        "error_message": payload.get("error_message"),
        "committed_commit_sha": payload.get("committed_commit_sha"),
        "agent": {
            "agent_key": metadata.get("agent_key"),
            "agent_run_id": metadata.get("agent_run_id"),
            "sandbox_path": metadata.get("sandbox_path"),
            "events_path": metadata.get("events_path"),
            "builder_artifact_path": metadata.get("builder_artifact_path"),
            "builder_files_path": metadata.get("builder_files_path"),
            "embedded_reference_image_count": metadata.get("embedded_reference_image_count"),
            "standard_search_summary": metadata.get("standard_search_summary"),
        },
        "selected_reference_assets": metadata.get("selected_reference_assets"),
        "generated_file_paths": sorted(generated_files.keys()),
        "generation_reason": payload.get("generation_reason"),
        "review_notes": payload.get("review_notes"),
        "material_usage": payload.get("material_usage"),
    }


def _validate_result(payload: dict[str, Any]) -> int:
    if payload.get("status") != "succeeded":
        print(f"PSOP Builder 测试失败：{payload.get('error_message') or 'generation status is not succeeded'}", file=sys.stderr)
        return 1

    metadata = payload.get("prompt_metadata") if isinstance(payload.get("prompt_metadata"), dict) else {}
    sandbox_path = str(metadata.get("sandbox_path") or "")
    events_path = Path(str(metadata.get("events_path") or ""))
    generated_files = payload.get("generated_files") if isinstance(payload.get("generated_files"), dict) else {}
    skill_md = str(generated_files.get("SKILL.md") or "")

    checks = [
        ("agent_key", metadata.get("agent_key") == "psop.builder"),
        ("agent_run_id", bool(metadata.get("agent_run_id"))),
        ("builder_artifact_path", metadata.get("builder_artifact_path") == "sandbox://outputs/builder-result.json"),
        ("builder_files_path", metadata.get("builder_files_path") == "sandbox://outputs/skill-draft"),
        ("committed_commit_sha", bool(payload.get("committed_commit_sha"))),
        ("SKILL.md", bool(skill_md.strip())),
    ]
    failed = [name for name, ok in checks if not ok]
    if failed:
        print(f"PSOP Builder 测试失败，缺少关键输出：{failed}", file=sys.stderr)
        return 1

    if sandbox_path:
        builder_result_path = Path(sandbox_path) / "outputs" / "builder-result.json"
        files_root = Path(sandbox_path) / "outputs" / "skill-draft"
        if not builder_result_path.exists() or not files_root.is_dir():
            print("PSOP Builder 测试失败：sandbox outputs 缺少 builder-result.json 或 skill-draft 目录。", file=sys.stderr)
            return 1

    if events_path.exists():
        events = _read_events(events_path)
        loaded_skills = {
            str(event.get("payload", {}).get("skill_name") or "")
            for event in events
            if event.get("event_type") == "agent.skill.loaded"
        }
        completed_tools = {
            str(event.get("payload", {}).get("tool_name") or "")
            for event in events
            if event.get("event_type") == "agent.tool.completed"
        }
        event_types = {str(event.get("event_type") or "") for event in events}
        missing_skills = sorted(REQUIRED_SKILLS - loaded_skills)
        missing_tools = sorted(REQUIRED_TOOLS - completed_tools)
        if "agent.memory.read" not in event_types or missing_skills or missing_tools:
            print(
                json.dumps(
                    {
                        "message": "PSOP Builder 测试失败：agent 事件不完整。",
                        "memory_read": "agent.memory.read" in event_types,
                        "missing_skills": missing_skills,
                        "missing_tools": missing_tools,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                file=sys.stderr,
            )
            return 1
    else:
        print(f"PSOP Builder 测试失败：events.jsonl 不存在：{events_path}", file=sys.stderr)
        return 1

    if int(metadata.get("embedded_reference_image_count") or 0) > 0:
        if "data:image/" not in skill_md:
            print("PSOP Builder 测试失败：metadata 显示已内嵌图片，但 SKILL.md 未包含 data:image。", file=sys.stderr)
            return 1
        if "## 嵌入参考图片" in skill_md:
            print("PSOP Builder 测试失败：参考图片不应集中追加到文档底部。", file=sys.stderr)
            return 1

    print("PSOP Builder 智能体测试通过。")
    return 0


def _read_events(path: Path) -> list[dict[str, Any]]:
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        events.append(json.loads(line))
    return events


def _load_orm_models() -> None:
    from app.agent_harness.persistence import models as agent_harness_models  # noqa: F401
    from app.domain.agent_prompts import models as agent_prompt_models  # noqa: F401
    from app.domain.compiler import models as compiler_models  # noqa: F401
    from app.domain.jobs import models as job_models  # noqa: F401
    from app.domain.runtime import models as runtime_models  # noqa: F401
    from app.domain.skill_tests import models as skill_test_models  # noqa: F401
    from app.domain.skills import models as skill_models  # noqa: F401


def _redact_database_url(value: str) -> str:
    if "@" not in value or "://" not in value:
        return value
    scheme, rest = value.split("://", 1)
    if "@" not in rest:
        return value
    return f"{scheme}://[redacted]@{rest.split('@', 1)[1]}"


if __name__ == "__main__":
    raise SystemExit(main())
