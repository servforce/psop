#!/usr/bin/env python3
"""Minimal skill compiler for PSOP v1 contract.

Compiles one skill source directory into:
  - build/manifest.json
  - build/skill-package.json
  - build/execution-graph.json
  - build/build-report.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

REQUIRED_FILES = ("SKILL.md", "skill.yaml")
REQUIRED_DIRS = ("prompts", "references", "examples", "tests")
SKILL_REQUIRED_FIELDS = ("name", "code", "version", "purpose", "inputs", "outputs", "workflow_steps")
STEP_REQUIRED_FIELDS = ("id", "title", "objective")
STEP_TYPE_ENUM = {"collect_input", "task"}
STEP_STATUS_ENUM = {"succeeded", "failed", "waiting_input", "timed_out"}
TRANSITION_ON_STATUS_ENUM = {"succeeded", "failed", "waiting_input", "timed_out"}
VALUE_TYPE_ENUM = {"string", "number", "boolean", "object", "array"}
FAILURE_STATUS_ENUM = {"failed", "timed_out"}
SCHEMA_VERSION = "psop-skill-build/v1"
GRAPH_VERSION = "v1"
RUN_SERVER_MIN_VERSION = "0.1.0"


@dataclass
class BuildContext:
    skill_dir: Path
    build_dir: Path
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    unconsumed_extensions: list[str] = field(default_factory=list)

    def add_error(self, message: str) -> None:
        self.errors.append(message)

    def add_warning(self, message: str) -> None:
        self.warnings.append(message)

    def add_note(self, message: str) -> None:
        self.notes.append(message)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compile a skill directory to PSOP runtime bundle (v1).")
    parser.add_argument("--skill-dir", required=True, help="Skill directory path, e.g. skills/skill-creator")
    parser.add_argument(
        "--build-version",
        default="",
        help="Optional build version override. Default: <skill_version>+build.<timestamp>",
    )
    return parser.parse_args()


def canonical_json_bytes(data: Any) -> bytes:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_skill_yaml(skill_yaml_path: Path, ctx: BuildContext) -> dict[str, Any]:
    try:
        raw = yaml.safe_load(skill_yaml_path.read_text(encoding="utf-8"))
    except Exception as exc:  # pragma: no cover - defensive
        ctx.add_error(f"`skill.yaml` 无法解析: {exc}")
        return {}

    if not isinstance(raw, dict) or "skill" not in raw or not isinstance(raw["skill"], dict):
        ctx.add_error("`skill.yaml` 顶层必须包含对象字段 `skill`。")
        return {}

    skill = raw["skill"]
    for key in SKILL_REQUIRED_FIELDS:
        if key not in skill:
            ctx.add_error(f"`skill.yaml` 缺少关键字段: skill.{key}")

    workflow_steps = skill.get("workflow_steps")
    if isinstance(workflow_steps, list):
        if not workflow_steps:
            ctx.add_error("`skill.yaml` 的 skill.workflow_steps 不能为空。")
        for index, step in enumerate(workflow_steps, start=1):
            if not isinstance(step, dict):
                ctx.add_error(f"workflow_steps[{index}] 必须是对象。")
                continue
            for key in STEP_REQUIRED_FIELDS:
                if key not in step:
                    ctx.add_error(f"workflow_steps[{index}] 缺少字段: {key}")
    else:
        ctx.add_error("`skill.yaml` 的 skill.workflow_steps 必须是数组。")

    return skill


def validate_directory(skill_dir: Path, ctx: BuildContext) -> None:
    if not skill_dir.exists():
        ctx.add_error(f"技能目录不存在: {skill_dir}")
        return
    if not skill_dir.is_dir():
        ctx.add_error(f"技能路径不是目录: {skill_dir}")
        return

    for file_name in REQUIRED_FILES:
        file_path = skill_dir / file_name
        if not file_path.is_file():
            ctx.add_error(f"缺少必需文件: {file_name}")

    for dir_name in REQUIRED_DIRS:
        dir_path = skill_dir / dir_name
        if not dir_path.is_dir():
            ctx.add_error(f"缺少必需目录: {dir_name}/")

    standard_names = set(REQUIRED_FILES) | set(REQUIRED_DIRS) | {"build"}
    extensions = sorted(p.name for p in skill_dir.iterdir() if p.name not in standard_names)
    ctx.unconsumed_extensions = extensions


def to_key(raw_name: str, index: int) -> str:
    normalized = raw_name.strip().lower().replace("-", "_").replace(" ", "_")
    normalized = re.sub(r"[^a-z0-9_]+", "", normalized)
    normalized = re.sub(r"_+", "_", normalized).strip("_")
    if not normalized:
        return f"required_field_{index}"
    return normalized


def build_required_inputs(required_fields: list[Any], ctx: BuildContext) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for index, raw in enumerate(required_fields, start=1):
        raw_name = str(raw)
        key = to_key(raw_name, index)
        entry = {
            "key": key,
            "source_path": f"scene.{key}",
            "value_type": "string",
            "required": True,
            "rules": {"min_length": 1},
            "missing_message": f"缺少必需输入：{raw_name}",
        }
        if not any(entry["source_path"].startswith(prefix) for prefix in ("scene.", "context.")):
            ctx.add_error(f"required_inputs.source_path 非法: {entry['source_path']}")
        if entry["value_type"] not in VALUE_TYPE_ENUM:
            ctx.add_error(f"required_inputs.value_type 非法: {entry['value_type']}")
        result.append(entry)
    return result


def build_steps(skill: dict[str, Any], ctx: BuildContext) -> list[dict[str, Any]]:
    workflow_steps = skill.get("workflow_steps", [])
    required_fields = skill.get("inputs", {}).get("required", [])
    required_inputs = build_required_inputs(required_fields if isinstance(required_fields, list) else [], ctx)
    steps: list[dict[str, Any]] = []

    for index, wf_step in enumerate(workflow_steps):
        step_id = str(wf_step.get("id", f"step_{index+1}"))
        title = str(wf_step.get("title", step_id))
        objective = str(wf_step.get("objective", "")).strip()

        if index == 0:
            step_type = "collect_input"
            step_required_inputs = required_inputs
            timeout_sec = 300
            completion_condition = {
                "mode": "input_ready",
                "success_status": "succeeded",
                "failure_status": "failed",
            }
            executor = {
                "kind": "collect_input",
                "collect_mode": "merge_scene_input",
            }
            instruction = ""
        else:
            step_type = "task"
            step_required_inputs = []
            timeout_sec = 600
            completion_condition = {
                "mode": "executor_success",
                "success_status": "succeeded",
                "failure_status": "timed_out",
            }
            instruction = f"{title}：{objective}".strip("：")
            executor = {
                "kind": "llm",
                "instruction": instruction,
                "model_profile": "default",
                "temperature": 0.2,
                "max_tokens": 2000,
            }

        if step_type not in STEP_TYPE_ENUM:
            ctx.add_error(f"step.type 非法: {step_id} -> {step_type}")

        if completion_condition.get("success_status") != "succeeded":
            ctx.add_error(f"completion_condition.success_status 非法: {step_id}")
        if completion_condition.get("failure_status") not in FAILURE_STATUS_ENUM:
            ctx.add_error(f"completion_condition.failure_status 非法: {step_id}")
        if completion_condition.get("mode") not in {"input_ready", "executor_success"}:
            ctx.add_error(f"completion_condition.mode 非法: {step_id}")

        if step_type == "collect_input":
            if executor.get("kind") != "collect_input" or executor.get("collect_mode") != "merge_scene_input":
                ctx.add_error(f"executor 结构非法: {step_id}")
        elif step_type == "task":
            required_executor_fields = ("kind", "instruction", "model_profile", "temperature", "max_tokens")
            missing = [field for field in required_executor_fields if field not in executor]
            if missing:
                ctx.add_error(f"executor 缺少字段: {step_id} -> {', '.join(missing)}")
            if executor.get("kind") != "llm":
                ctx.add_error(f"executor.kind 非法: {step_id} -> {executor.get('kind')}")
            if not instruction:
                ctx.add_warning(f"step 目标说明为空，将使用默认 task 指令: {step_id}")
                executor["instruction"] = f"执行步骤：{title}"

        step = {
            "id": step_id,
            "title": title,
            "type": step_type,
            "required_inputs": step_required_inputs,
            "output_schema": {"type": "object"},
            "executor": executor,
            "timeout_sec": timeout_sec,
            "completion_condition": completion_condition,
        }
        steps.append(step)

    return steps


def build_transitions(steps: list[dict[str, Any]], ctx: BuildContext) -> list[dict[str, Any]]:
    transitions: list[dict[str, Any]] = []
    for index, step in enumerate(steps):
        step_id = step["id"]
        step_type = step["type"]
        if step_type == "collect_input":
            waiting_transition = {
                "from_step_id": step_id,
                "on_status": "waiting_input",
                "to_step_id": step_id,
                "priority": 1,
            }
            transitions.append(waiting_transition)

        if index < len(steps) - 1:
            next_step_id = steps[index + 1]["id"]
            success_transition = {
                "from_step_id": step_id,
                "on_status": "succeeded",
                "to_step_id": next_step_id,
                "priority": 10,
            }
            transitions.append(success_transition)

    for transition in transitions:
        if transition["on_status"] not in TRANSITION_ON_STATUS_ENUM:
            ctx.add_error(f"transition.on_status 非法: {transition}")
        if "priority" not in transition:
            ctx.add_error(f"transition 缺少 priority: {transition}")

    return transitions


def build_execution_graph(skill: dict[str, Any], ctx: BuildContext) -> dict[str, Any]:
    steps = build_steps(skill, ctx)
    transitions = build_transitions(steps, ctx)
    entry_step_id = steps[0]["id"] if steps else ""
    if not entry_step_id:
        ctx.add_error("无法确定 entry_step_id（workflow_steps 为空）。")

    return {
        "graph_version": GRAPH_VERSION,
        "entry_step_id": entry_step_id,
        "steps": steps,
        "transitions": transitions,
    }


def build_manifest(skill: dict[str, Any], execution_graph: dict[str, Any], build_version_override: str) -> dict[str, Any]:
    skill_version = str(skill.get("version", "0.1.0"))
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    build_version = build_version_override or f"{skill_version}+build.{timestamp}"
    generated_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    graph_hash = hashlib.sha256(canonical_json_bytes(execution_graph)).hexdigest()

    return {
        "skill_code": str(skill.get("code", "")),
        "skill_version": skill_version,
        "build_version": build_version,
        "schema_version": SCHEMA_VERSION,
        "entry_step_id": execution_graph.get("entry_step_id", ""),
        "generated_at": generated_at,
        "graph_hash_algo": "sha256",
        "graph_hash": graph_hash,
        "compat": {
            "run_server_min_version": RUN_SERVER_MIN_VERSION,
        },
    }


def build_skill_package(skill: dict[str, Any], references_index: list[str]) -> dict[str, Any]:
    return {
        "name": skill.get("name", ""),
        "purpose": skill.get("purpose", ""),
        "inputs_contract": skill.get("inputs", {}),
        "outputs_contract": skill.get("outputs", {}),
        "global_constraints": skill.get("constraints", []),
        "references_index": references_index,
    }


def collect_references_index(skill_dir: Path) -> list[str]:
    references_dir = skill_dir / "references"
    if not references_dir.exists():
        return []
    return sorted(str(path.relative_to(skill_dir)).replace("\\", "/") for path in references_dir.rglob("*") if path.is_file())


def clear_runtime_outputs(build_dir: Path) -> None:
    for file_name in ("manifest.json", "skill-package.json", "execution-graph.json"):
        target = build_dir / file_name
        if target.exists():
            target.unlink()


def write_build_report(ctx: BuildContext, status: str) -> None:
    report = {
        "status": status,
        "errors": ctx.errors,
        "warnings": ctx.warnings,
        "notes": ctx.notes,
        "unconsumed_extensions": ctx.unconsumed_extensions,
    }
    write_json(ctx.build_dir / "build-report.json", report)


def main() -> int:
    args = parse_args()
    skill_dir = Path(args.skill_dir).resolve()
    build_dir = skill_dir / "build"
    build_dir.mkdir(parents=True, exist_ok=True)
    ctx = BuildContext(skill_dir=skill_dir, build_dir=build_dir)

    validate_directory(skill_dir, ctx)
    if ctx.errors:
        clear_runtime_outputs(build_dir)
        write_build_report(ctx, status="failed")
        for error in ctx.errors:
            print(f"[ERROR] {error}", file=sys.stderr)
        print(f"[FAILED] 编译失败，报告已输出: {build_dir / 'build-report.json'}", file=sys.stderr)
        return 1

    skill_yaml_path = skill_dir / "skill.yaml"
    skill = load_skill_yaml(skill_yaml_path, ctx)
    if ctx.errors:
        clear_runtime_outputs(build_dir)
        write_build_report(ctx, status="failed")
        for error in ctx.errors:
            print(f"[ERROR] {error}", file=sys.stderr)
        print(f"[FAILED] 编译失败，报告已输出: {build_dir / 'build-report.json'}", file=sys.stderr)
        return 1

    dir_name = skill_dir.name
    code = str(skill.get("code", ""))
    if code and code != dir_name:
        ctx.add_warning(f"目录名与 skill.code 不一致: directory={dir_name}, code={code}")

    execution_graph = build_execution_graph(skill, ctx)
    manifest = build_manifest(skill, execution_graph, args.build_version)
    references_index = collect_references_index(skill_dir)
    skill_package = build_skill_package(skill, references_index)

    if manifest.get("graph_hash_algo") != "sha256":
        ctx.add_error("manifest.graph_hash_algo 必须为 sha256。")
    if manifest.get("entry_step_id") != execution_graph.get("entry_step_id"):
        ctx.add_error("manifest.entry_step_id 与 execution-graph.entry_step_id 不一致。")

    if ctx.errors:
        clear_runtime_outputs(build_dir)
        write_build_report(ctx, status="failed")
        for error in ctx.errors:
            print(f"[ERROR] {error}", file=sys.stderr)
        print(f"[FAILED] 编译失败，报告已输出: {build_dir / 'build-report.json'}", file=sys.stderr)
        return 1

    write_json(build_dir / "manifest.json", manifest)
    write_json(build_dir / "skill-package.json", skill_package)
    write_json(build_dir / "execution-graph.json", execution_graph)
    ctx.add_note("运行产物契约校验通过。")
    ctx.add_note("产物可用于 Run Server 最小执行链路验证。")
    write_build_report(ctx, status="succeeded")

    print(f"[OK] 编译完成: {skill_dir}")
    print(f"[OK] 输出目录: {build_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
