from __future__ import annotations

import traceback

import yaml

from app.agent_harness.events import AgentEventWriter
from app.agent_harness.runners.deepagents_runner import DeepAgentsRunner
from app.agent_harness.schemas import AgentDefinition, AgentInvocation, AgentResult
from app.agent_harness.skills.loader import SkillLoader
from app.agent_harness.tools.builtin import register_builtin_tools
from app.agent_harness.tools.registry import ToolRegistry
from app.agent_harness.workspace.manager import WorkspaceManager
from app.core.config import Settings
from app.gateway.inference import LlmInferenceGateway


class AgentHarnessService:
    def __init__(self, *, settings: Settings, inference_gateway: LlmInferenceGateway) -> None:
        self.settings = settings
        self.inference_gateway = inference_gateway
        self.workspace_manager = WorkspaceManager(settings)
        self.demo_root = settings.backend_root / "app" / "agent_harness" / "demo"

    def invoke(self, invocation: AgentInvocation) -> AgentResult:
        if not self.settings.agent_harness_enabled:
            raise RuntimeError("Agent Harness 当前未启用。")
        definition = self._load_definition(invocation.agent_key)
        workspace = self.workspace_manager.create(
            agent_run_id=invocation.workspace_id,
            input_payload=invocation.model_dump(mode="json"),
        )
        event_writer = AgentEventWriter(workspace.events_path)
        event_writer.record(
            "agent.run.started",
            {
                "agent_key": definition.agent_key,
                "agent_version": definition.version,
                "runner": definition.runner,
                "profile": self.settings.agent_harness_profile,
            },
        )
        try:
            skills = [
                SkillLoader(self.demo_root / "skills").load(skill_name, event_writer)
                for skill_name in definition.skills
            ]
            registry = ToolRegistry()
            register_builtin_tools(registry)
            result = DeepAgentsRunner(self.inference_gateway).invoke(
                invocation=invocation,
                definition=definition,
                system_prompt=self._read_demo_file("system.md"),
                memory_text=self._read_demo_file("AGENTS.md"),
                skills=skills,
                tool_registry=registry,
                workspace=workspace,
                event_writer=event_writer,
            )
            event_writer.record("agent.run.completed", {"status": result.status})
            result.events = event_writer.events
            workspace.write_output(result.model_dump(mode="json"))
            return result
        except Exception as exc:
            event_writer.record(
                "agent.run.failed",
                {"error_type": exc.__class__.__name__, "error": str(exc), "traceback": traceback.format_exc()},
            )
            result = AgentResult(
                agent_run_id=workspace.agent_run_id,
                agent_key=definition.agent_key,
                status="failed",
                final_output="",
                error_message=str(exc),
                events=event_writer.events,
                workspace_path=str(workspace.workspace_path),
            )
            workspace.write_output(result.model_dump(mode="json"))
            return result

    def _load_definition(self, agent_key: str) -> AgentDefinition:
        if agent_key != "demo.psop_harness_agent":
            raise FileNotFoundError(f"未找到 AgentDefinition：{agent_key}")
        payload = yaml.safe_load((self.demo_root / "agent.yaml").read_text(encoding="utf-8")) or {}
        if not isinstance(payload, dict):
            raise ValueError("agent.yaml 顶层必须是对象。")
        return AgentDefinition.model_validate(payload)

    def _read_demo_file(self, filename: str) -> str:
        return (self.demo_root / filename).read_text(encoding="utf-8")


def build_agent_harness_service(settings: Settings, inference_gateway: LlmInferenceGateway) -> AgentHarnessService:
    return AgentHarnessService(settings=settings, inference_gateway=inference_gateway)
