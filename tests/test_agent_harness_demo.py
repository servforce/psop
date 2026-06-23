from __future__ import annotations

from app.agent_harness.schemas import AgentInvocation
from app.agent_harness.service import AgentHarnessService
from app.core.config import Settings
from tests.test_skills_api import FakeInferenceGateway


def test_agent_harness_demo_runs_with_scripted_model(tmp_path) -> None:
    settings = Settings(
        database_url="sqlite+pysqlite:///:memory:",
        runtime_worker_enabled=False,
        agent_harness_workspace_root=str(tmp_path / "agent-runs"),
    )
    service = AgentHarnessService(settings=settings, inference_gateway=FakeInferenceGateway())

    result = service.invoke(
        AgentInvocation(
            agent_key="demo.psop_harness_agent",
            input={"text": "进入泵房前检查 PPE，确认阀门关闭，记录压力表读数。"},
            use_mock_model=True,
        )
    )

    event_types = [event.event_type for event in result.events]
    assert result.status == "succeeded"
    assert "已完成检查清单生成" in result.final_output
    assert "agent.skill.loaded" in event_types
    assert event_types.count("agent.tool.completed") >= 4
    assert "agent.memory.write" in event_types
    assert result.workspace_path is not None
    workspace_path = tmp_path / "agent-runs" / result.agent_run_id
    assert (workspace_path / "output.json").exists()
    assert (workspace_path / "events.jsonl").exists()
    assert (workspace_path / "memory.json").exists()
    assert (workspace_path / "workspace" / "result.md").exists()
