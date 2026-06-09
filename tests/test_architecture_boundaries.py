from __future__ import annotations

import ast
from pathlib import Path

from app.app import create_app
from app.agent_harness.definitions import (
    AGENT_PROMPT_FALLBACKS,
    DEFAULT_AGENT_SKILLS,
    PROMPT_USAGE_AGENT_KEYS,
)
from app.agents.service import DEFAULT_AGENT_SPECS
from tests.test_skills_api import create_test_settings


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_runtime_domain_does_not_import_agent_memory_domain() -> None:
    runtime_files = sorted((PROJECT_ROOT / "backend" / "app" / "runtime").glob("*.py"))
    assert runtime_files

    violations: list[str] = []
    for path in runtime_files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module == "app.memory" or module.startswith("app.memory."):
                    violations.append(f"{path.relative_to(PROJECT_ROOT)} imports {module}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "app.memory" or alias.name.startswith("app.memory."):
                        violations.append(f"{path.relative_to(PROJECT_ROOT)} imports {alias.name}")

    assert violations == []


def test_backend_does_not_restore_domain_package_layer() -> None:
    assert not (PROJECT_ROOT / "backend" / "app" / "domain").exists()


def test_agent_harness_definitions_keep_builtin_agent_modules() -> None:
    definitions_dir = PROJECT_ROOT / "backend" / "app" / "agent_harness" / "definitions"

    expected_files = {
        "builtin_agents.py",
        "pskill_builder.py",
        "pskill_compiler.py",
        "pskill_tester.py",
        "pskill_runner.py",
        "pskill_evaluator.py",
        "psop_governance.py",
    }
    sandbox_dir = PROJECT_ROOT / "backend" / "app" / "agent_harness" / "sandbox"
    expected_sandbox_files = {"sandbox.py", "sandbox_workspace.py", "restricted_workspace.py", "docker_sandbox.py"}
    events_dir = PROJECT_ROOT / "backend" / "app" / "agent_harness" / "events"
    expected_events_files = {"agent_event_emitter.py", "event_types.py", "event_redaction.py"}

    assert expected_files <= {path.name for path in definitions_dir.glob("*.py")}
    assert expected_sandbox_files <= {path.name for path in sandbox_dir.glob("*.py")}
    assert expected_events_files <= {path.name for path in events_dir.glob("*.py")}


def test_api_routes_use_pskill_and_materials_naming() -> None:
    app = create_app(create_test_settings())
    route_paths = {getattr(route, "path", "") for route in app.routes}

    forbidden_fragments = {
        "/raw-materials",
        "/terminal-events",
        "/trace-events",
        "/agent-skills",
        "/api/v1/compiler/skills/",
    }
    violations = sorted(
        path
        for path in route_paths
        if any(fragment in path for fragment in forbidden_fragments)
    )

    assert violations == []
    assert "/api/v1/compiler/pskills/{pskill_id}/compile" in route_paths
    assert "/api/v1/pskills/{skill_id}/materials" in route_paths
    assert "/api/v1/runtime/invocations" in route_paths
    assert "/api/v1/runtime/invocations/{invocation_id}" in route_paths
    assert "/api/v1/runs/{run_id}/events" in route_paths
    assert "/api/v1/runs/{run_id}/terminal-session" in route_paths
    assert "/api/v1/runs/{run_id}/traces" in route_paths
    assert "/api/v1/memory/{memory_id}" in route_paths
    assert "/api/v1/evaluations" in route_paths
    assert "/api/v1/skills" in route_paths


def test_runtime_openapi_hides_legacy_terminal_compat_routes() -> None:
    app = create_app(create_test_settings())
    openapi_paths = set(app.openapi()["paths"])

    assert "/api/v1/runs/{run_id}/terminal-session" in openapi_paths
    assert "/api/v1/runs/{run_id}/events" in openapi_paths
    assert "/api/v1/runs/{run_id}/events/{event_id}/parts/{part_id}/content" in openapi_paths
    assert not any("/terminal/sessions" in path for path in openapi_paths)


def test_runtime_invocation_openapi_marks_gateway_entrypoints_deprecated() -> None:
    app = create_app(create_test_settings())
    paths = app.openapi()["paths"]

    assert paths["/api/v1/runtime/invocations"]["post"].get("deprecated") is not True
    assert paths["/api/v1/runtime/invocations"]["get"].get("deprecated") is not True
    assert paths["/api/v1/runtime/invocations/{invocation_id}"]["get"].get("deprecated") is not True
    assert paths["/api/v1/gateway/invocations"]["post"]["deprecated"] is True
    assert paths["/api/v1/gateway/invocations"]["get"]["deprecated"] is True
    assert paths["/api/v1/gateway/invocations/{invocation_id}"]["get"]["deprecated"] is True


def test_default_agents_keep_closed_loop_keys_and_runner_boundary() -> None:
    specs = {spec["key"]: spec for spec in DEFAULT_AGENT_SPECS}

    assert list(specs) == [
        "pskill.builder",
        "pskill.compiler",
        "pskill.tester",
        "pskill.runner",
        "pskill.evaluator",
        "psop.governance",
    ]
    assert specs["pskill.runner"]["output_schema"]["name"] == "RuntimeAgentObservation"
    assert specs["pskill.runner"]["allowed_tools"] == ["psop.runtime.read"]
    assert specs["pskill.runner"]["allowed_skill_names"] == [
        "pskill-runner-field-assistant",
        "pskill-runner-evidence-evaluator",
        "ffmpeg-video-processing",
    ]
    assert specs["pskill.tester"]["allowed_skill_names"] == ["pskill-tester", "ffmpeg-video-processing"]
    assert specs["psop.governance"]["output_schema"]["name"] == "GovernanceProposalResult"
    assert set(specs) == set(DEFAULT_AGENT_SKILLS) == set(AGENT_PROMPT_FALLBACKS)
    assert {key: spec["allowed_skill_names"] for key, spec in specs.items()} == DEFAULT_AGENT_SKILLS
    for agent_key, (usage_key, _prompt_ref) in AGENT_PROMPT_FALLBACKS.items():
        assert PROMPT_USAGE_AGENT_KEYS[usage_key] == agent_key
    assert PROMPT_USAGE_AGENT_KEYS["default.skill_creation_agent"] == "pskill.builder"
    assert PROMPT_USAGE_AGENT_KEYS["runtime.llm_node_fallback"] == "pskill.runner"


def test_server_design_keeps_pskill_api_paths_distinct_from_skill_packages() -> None:
    design = (PROJECT_ROOT / "docs" / "PSOP服务端详细设计v1.md").read_text(encoding="utf-8")

    forbidden_fragments = {
        "  domain/",
        "/api/v1/skills/{skill_id}",
        "/api/v1/compiler/skills/",
        "`skill_compile_request`",
        "skill_compile_request_id",
        "RAW_MATERIAL_",
        "/raw-materials",
        "/agent-skills",
        "raw materials",
        "Trace Event",
        "Terminal Event / Part",
    }
    violations = sorted(fragment for fragment in forbidden_fragments if fragment in design)

    assert violations == []
    assert "`PSkills -> GitLab source -> Publish -> Compile -> EG Compile Artifact`" in design
    assert "### 9.2 PSkills / Materials" in design
    assert "`GET` | `/api/v1/pskills` | PSkill 列表" in design
    assert "`GET` | `/api/v1/skills` | Skill 包列表" in design
    assert "`GET` | `/api/v1/memory/{memory_id}` | Memory 详情" in design
    assert "`GET` | `/api/v1/evaluations` | Run evaluation report 列表" in design
    assert "RunEvent / RunEventPart" in design
    assert "RunTrace" in design
    assert "`POST` | `/api/v1/runs/{run_id}/cancel`" in design
    assert "`GET` | `/api/v1/runs/{run_id}/terminal-session` | terminal session 摘要" in design
    assert "`POST` | `/api/v1/runtime/invocations`" in design
    assert "`GET/POST` | `/api/v1/gateway/invocations*` | deprecated 兼容入口" in design
    assert "`GET` | `/api/v1/runs` | run 列表；支持 `status`、`pskill_id`" in design
    assert "无 `/api/v1/runs/{run_id}/cancel`" not in design


def test_static_runtime_uses_runtime_invocation_api_for_new_code() -> None:
    runtime_js = (PROJECT_ROOT / "static" / "js" / "app" / "runtime.js").read_text(encoding="utf-8")

    assert "/runtime/invocations" in runtime_js
    assert "/gateway/invocations" not in runtime_js


def test_overview_design_uses_current_closed_loop_job_and_runtime_names() -> None:
    design = (PROJECT_ROOT / "docs" / "PSOP概要设计v1.md").read_text(encoding="utf-8")

    forbidden_fragments = {
        "raw_material_analysis",
        "skill_raw_material_generation",
        "job_type=compile",
        "job_type=runtime)",
        "domain/*",
        "- Skill 总对象",
        "用户定义的是 `Skills`",
        "terminal input 同步",
        "terminal transcript",
        "recoverable terminal turn failure",
        "`/api/v1/runs/{run_id}/cancel` 路由；服务层已有",
    }
    violations = sorted(fragment for fragment in forbidden_fragments if fragment in design)

    assert violations == []
    assert "当前代码中必须区分四层对象" in design
    assert "用户定义的是 `PSkills`" in design
    assert "`POST /api/v1/runtime/invocations`" in design
    assert "/api/v1/gateway/invocations` 仅作为兼容入口保留" in design
    assert "Agent 使用的是 `Skills` 能力包" in design
    assert "backend/app/* domains" in design
    assert "`material_analysis`" in design
    assert "`pskill_build`" in design
    assert "`pskill_compile`" in design
    assert "`runtime_step`" in design
    assert "`run_evaluation`" in design
    assert "`governance_proposal`" in design
    assert "Claim 前会恢复过期 lease" in design
    assert "进入 `dead_letter`" in design
    assert "`run_event`、`run_event_part`、`run_trace`" in design
    assert "`pskill.evaluator` 基于 run facts" in design
    assert "`psop.governance` 转成治理提案" in design
    assert "`Observability`" in design


def test_frontend_design_uses_pskill_materials_and_run_trace_paths() -> None:
    design = (PROJECT_ROOT / "docs" / "PSOP前端详细设计v1.md").read_text(encoding="utf-8")

    forbidden_fragments = {
        "/api/v1/skills/{skill_id}",
        "/raw-materials",
        "raw materials",
        "/terminal-events",
        "/trace-events",
        "/terminal/sessions",
    }
    violations = sorted(fragment for fragment in forbidden_fragments if fragment in design)

    assert violations == []
    assert "`GET /api/v1/pskills`" in design
    assert "`POST /api/v1/runs/{run_id}/cancel`" in design
    assert "`/api/v1/pskills/{skill_id}/materials*`" in design
    assert "`/traces`" in design
    assert "独立 Observability 工作台" not in design
    assert "Platform Observability 工作台" in design
    assert "`/admin/platform/observability`" in design
    assert "`/admin/governance/proposals`" in design
    assert "`/admin/platform/tool-authorizations`" in design
    assert "`/ws/tool-authorizations`" in design
    assert "`/api/v1/observability/*`" in design
    assert "- `/api/v1/runs/{run_id}/cancel`" not in design


def test_terminal_access_guide_uses_run_event_api_paths() -> None:
    guide = (PROJECT_ROOT / "docs" / "PSOP终端接入说明v1.md").read_text(encoding="utf-8")

    forbidden_fragments = {
        "/terminal/sessions",
        "terminal.event.appended",
        "TerminalEvent",
        "terminal_event",
        "latest_terminal_seq",
    }
    violations = sorted(fragment for fragment in forbidden_fragments if fragment in guide)

    assert violations == []
    assert "POST /api/v1/runtime/invocations" in guide
    assert "GET /api/v1/runs/{run_id}/terminal-session" in guide
    assert "GET /api/v1/runs/{run_id}/events" in guide
    assert "POST /api/v1/runs/{run_id}/events" in guide
    assert "GET /api/v1/runs/{run_id}/events/{event_id}/parts/{part_id}/content" in guide
    assert "run.event.appended" in guide
