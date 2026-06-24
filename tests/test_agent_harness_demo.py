from __future__ import annotations

from app.agent_harness.schemas import AgentInvocation
from app.agent_harness.models.scripted_chat_model import ScriptedToolCallingChatModel
from app.agent_harness.service import AgentHarnessService
from app.core.config import Settings


def test_agent_harness_demo_runs_through_langchain_agent_with_scripted_model(tmp_path) -> None:
    settings = Settings(
        database_url="sqlite+pysqlite:///:memory:",
        runtime_worker_enabled=False,
        agent_harness_sandbox_root=str(tmp_path / "agent-runs"),
    )
    service = AgentHarnessService(
        settings=settings,
        chat_model_factory=lambda _definition: ScriptedToolCallingChatModel(),
    )

    result = service.invoke(
        AgentInvocation(
            agent_key="demo.psop_harness_agent",
            input={"text": "进入泵房前检查 PPE，确认阀门关闭，记录压力表读数。"},
        )
    )

    event_types = [event.event_type for event in result.events]
    assert result.status == "succeeded"
    assert "已完成检查清单生成" in result.final_output
    assert "agent.skill.loaded" in event_types
    assert "agent.model.started" in event_types
    assert "agent.token.usage" in event_types
    assert event_types.count("agent.tool.completed") >= 5
    assert any(
        event.event_type == "agent.tool.completed" and event.payload["tool_name"] == "load_skill"
        for event in result.events
    )
    assert "agent.memory.write" in event_types
    assert result.sandbox_path is not None
    workspace_path = tmp_path / "agent-runs" / result.agent_run_id
    assert (workspace_path / "output.json").exists()
    assert (workspace_path / "events.jsonl").exists()
    assert (workspace_path / "memory.json").exists()
    assert (workspace_path / "workspace" / "result.md").exists()
