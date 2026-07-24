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

from app.agent_harness.agents.psop.runner.schemas import validate_runner_observation
from app.agent_harness.models.scripted_runner_chat_model import ScriptedRunnerChatModel
from app.agent_harness.schemas import AgentInvocation
from app.agent_harness.service import AgentHarnessService
from app.core.config import Settings


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the PSOP runner agent.")
    parser.add_argument("--fixture", required=True, help="包含 AgentInvocation input/context 的 JSON fixture。")
    parser.add_argument("--scripted", action="store_true", help="使用 deterministic scripted model 运行。")
    parser.add_argument("--full-output", action="store_true", help="打印完整 AgentResult JSON。")
    args = parser.parse_args()

    payload = _read_fixture(Path(args.fixture))
    invocation = AgentInvocation(
        agent_key=str(payload.get("agent_key") or "psop.runner"),
        input=dict(payload.get("input") or {}),
        context=dict(payload.get("context") or {}),
    )
    service = AgentHarnessService(
        settings=Settings(),
        chat_model_factory=(lambda _definition: ScriptedRunnerChatModel()) if args.scripted else None,
    )
    result = service.invoke(invocation)
    if args.full_output:
        print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))
    else:
        print(json.dumps(_result_summary(result), ensure_ascii=False, indent=2))
    if result.status != "succeeded":
        return 1
    observation_path = _runner_observation_path(result.sandbox_path)
    if observation_path is None or not observation_path.exists():
        print("未找到 runner-observation.json。", file=sys.stderr)
        return 1
    observation = json.loads(observation_path.read_text(encoding="utf-8"))
    validate_runner_observation(
        observation,
        invocation_input=invocation.input,
        invocation_context=invocation.context,
    )
    required_artifacts = {"runner_observation"}
    artifact_types = {artifact.artifact_type for artifact in result.artifacts}
    missing_artifacts = sorted(required_artifacts - artifact_types)
    if missing_artifacts:
        print(f"缺少必要 artifact：{missing_artifacts}", file=sys.stderr)
        return 1
    required_skills = {"psop-runner"}
    loaded_skills = {
        str(event.payload.get("skill_name") or "")
        for event in result.events
        if event.event_type == "agent.skill.loaded"
    }
    if not required_skills.issubset(loaded_skills):
        print(f"未加载全部 runner skills：{sorted(loaded_skills)}", file=sys.stderr)
        return 1
    required_resources = {
        "core/SKILL.md",
        "terminal-guidance/SKILL.md",
        "evidence-evaluation/SKILL.md",
    }
    loaded_resources = {
        str(event.payload.get("resource_path") or "")
        for event in result.events
        if event.event_type == "agent.skill.resource.loaded"
    }
    if not required_resources.issubset(loaded_resources):
        print(f"未加载全部 runner skill resources：{sorted(loaded_resources)}", file=sys.stderr)
        return 1
    required_tools = {
        "psop.runner.read_prompt_view",
        "psop.runner.read_runtime_contract",
        "psop.runner.read_current_checkpoint",
        "psop.runner.list_terminal_events",
        "psop.runner.read_latest_evidence",
        "psop.runner.submit_observation",
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


def _runner_observation_path(sandbox_path: str | None) -> Path | None:
    if not sandbox_path:
        return None
    return Path(sandbox_path) / "outputs" / "runner-observation.json"


def _result_summary(result) -> dict:
    return {
        "agent_run_id": result.agent_run_id,
        "agent_key": result.agent_key,
        "status": result.status,
        "sandbox_path": result.sandbox_path,
        "artifact_types": [artifact.artifact_type for artifact in result.artifacts],
        "event_count": len(result.events),
        "error_message": result.error_message,
    }


if __name__ == "__main__":
    raise SystemExit(main())
