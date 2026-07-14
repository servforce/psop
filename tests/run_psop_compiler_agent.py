#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.agent_harness.agents.psop.compiler.schemas import validate_compiler_candidate
from app.agent_harness.models.scripted_compiler_chat_model import ScriptedCompilerChatModel
from app.agent_harness.schemas import AgentInvocation
from app.agent_harness.service import AgentHarnessService
from app.core.config import Settings
from app.domain.compiler.formal_v5 import validate_and_normalize_artifact


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the PSOP compiler agent.")
    parser.add_argument("--fixture", required=True, help="包含 AgentInvocation input/context 的 JSON fixture。")
    parser.add_argument("--scripted", action="store_true", help="使用 deterministic scripted model 运行。")
    parser.add_argument("--full-output", action="store_true", help="打印完整 AgentResult JSON。")
    args = parser.parse_args()

    payload = _read_fixture(Path(args.fixture))
    invocation = AgentInvocation(
        agent_key=str(payload.get("agent_key") or "psop.compiler"),
        input=dict(payload.get("input") or {}),
        context=dict(payload.get("context") or {}),
    )
    service = AgentHarnessService(
        settings=Settings(standard_lightrag_base_url="", standard_lightrag_api_key=""),
        chat_model_factory=(lambda _definition: ScriptedCompilerChatModel()) if args.scripted else None,
    )
    result = service.invoke(invocation)
    if args.full_output:
        print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))
    else:
        print(json.dumps(_result_summary(result), ensure_ascii=False, indent=2))
    if result.status != "succeeded":
        return 1
    candidate_path = _compiler_result_path(result.sandbox_path)
    if candidate_path is None or not candidate_path.exists():
        print("未找到 compiler-result.json。", file=sys.stderr)
        return 1
    eg_path = _eg_artifact_path(result.sandbox_path)
    if eg_path is None or not eg_path.exists():
        print("未找到 eg.compile.artifact.json。", file=sys.stderr)
        return 1
    candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    validate_compiler_candidate(candidate)
    eg_artifact = json.loads(eg_path.read_text(encoding="utf-8"))
    validation = validate_and_normalize_artifact(eg_artifact)
    if validation.has_errors or validation.artifact is None:
        print(json.dumps([item.as_dict() for item in validation.diagnostics], ensure_ascii=False, indent=2), file=sys.stderr)
        return 1
    required_artifacts = {"eg_compile_candidate", "eg_compile_artifact_candidate"}
    artifact_types = {artifact.artifact_type for artifact in result.artifacts}
    missing_artifacts = sorted(required_artifacts - artifact_types)
    if missing_artifacts:
        print(f"缺少必要 artifact：{missing_artifacts}", file=sys.stderr)
        return 1
    required_skills = {"psop-compiler"}
    loaded_skills = {
        str(event.payload.get("skill_name") or "")
        for event in result.events
        if event.event_type == "agent.skill.loaded"
    }
    if not required_skills.issubset(loaded_skills):
        print(f"未加载全部 compiler skills：{sorted(loaded_skills)}", file=sys.stderr)
        return 1
    required_resources = {
        "core/SKILL.md",
        "contract/SKILL.md",
        "mapping/SKILL.md",
        "review/SKILL.md",
    }
    loaded_resources = {
        str(event.payload.get("resource_path") or "")
        for event in result.events
        if event.event_type == "agent.skill.resource.loaded"
        and str(event.payload.get("skill_name") or "") == "psop-compiler"
    }
    missing_resources = sorted(required_resources - loaded_resources)
    if missing_resources:
        print(f"未加载全部 compiler skill resources：{missing_resources}", file=sys.stderr)
        return 1
    required_tools = {
        "psop.compiler.read_skill_source",
        "psop.compiler.read_manifest_snapshot",
        "psop.compiler.read_allowed_runtime",
        "psop.compiler.read_domain_pack",
        "psop.compiler.build_formal_v5_scaffold",
        "psop.compiler.validate_formal_v5",
        "psop.compiler.submit_candidate",
    }
    completed_tools = {
        str(event.payload.get("tool_name") or "")
        for event in result.events
        if event.event_type == "agent.tool.completed"
    }
    missing_tools = sorted(required_tools - completed_tools)
    if missing_tools:
        print(f"缺少必要 tool completed 事件：{missing_tools}", file=sys.stderr)
        return 1
    return 0


def _read_fixture(path: Path) -> dict:
    resolved = path if path.is_absolute() else REPO_ROOT / path
    return json.loads(resolved.read_text(encoding="utf-8"))


def _compiler_result_path(sandbox_path: str | None) -> Path | None:
    if not sandbox_path:
        return None
    return Path(sandbox_path) / "outputs" / "compiler-result.json"


def _eg_artifact_path(sandbox_path: str | None) -> Path | None:
    if not sandbox_path:
        return None
    return Path(sandbox_path) / "outputs" / "eg.compile.artifact.json"


def _result_summary(result) -> dict:
    return {
        "status": result.status,
        "agent_key": result.agent_key,
        "agent_run_id": result.agent_run_id,
        "sandbox_path": result.sandbox_path,
        "error_message": result.error_message,
        "artifacts": [artifact.model_dump(mode="json") for artifact in result.artifacts],
        "event_count": len(result.events),
    }


if __name__ == "__main__":
    raise SystemExit(main())
