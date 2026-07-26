from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.agent_harness.persistence.models import AgentEventRecord, AgentRunRecord
from app.app import create_app
from app.core.config import Settings
from app.domain.jobs.models import RuntimeJob
from app.domain.skills.models import SkillDefinition, SkillRawMaterialGeneration


SKILL_ID = "00000000-0000-0000-0000-000000000010"
GENERATION_ID = "00000000-0000-0000-0000-000000000020"
JOB_ID = "00000000-0000-0000-0000-000000000030"


def _settings() -> Settings:
    return Settings(
        database_url="sqlite+pysqlite:///:memory:",
        database_check_on_startup=False,
        database_auto_create_schema=True,
        runtime_worker_enabled=False,
        standard_lightrag_base_url="",
        standard_lightrag_api_key="",
    )


def _insert_builder_generation(client: TestClient, *, status: str = "pending", with_run: bool = False) -> None:
    manager = client.app.state.db_manager
    with manager.session() as session:
        session.add(
            SkillDefinition(
                id=SKILL_ID,
                key="install-computer-host",
                name="安装电脑主机",
                gitlab_project_id="project-install-computer-host",
                repository_url="https://gitlab.example.local/skills/install-computer-host",
            )
        )
        session.add(
            SkillRawMaterialGeneration(
                id=GENERATION_ID,
                skill_definition_id=SKILL_ID,
                material_ids=["material-1"],
                user_description="帮我构建一个安装电脑主机的技能。",
                status=status,
                prompt_metadata={
                    "agent_key": "psop.builder",
                    "agent_run_id": GENERATION_ID,
                    "job_id": JOB_ID,
                    "reference_files": ["references/frame-001.png"],
                    "standard_search_summary": {"status": "ok", "result_count": 1},
                },
                generated_files={"README.md": "# 安装电脑主机", "SKILL.md": "# 安装电脑主机"},
                generation_reason="基于素材生成安装电脑主机流程。",
                review_notes=["已按步骤嵌入参考图片。"],
                committed_commit_sha="commit-0001",
            )
        )
        session.add(
            RuntimeJob(
                id=JOB_ID,
                job_type="skill_raw_material_generation",
                status=status,
                payload={
                    "generation_id": GENERATION_ID,
                    "current_stage": "succeeded" if status == "succeeded" else "calling_model",
                    "progress": {
                        "percent": 100 if status == "succeeded" else 25,
                        "current_stage": status,
                        "label": "生成完成" if status == "succeeded" else "构建智能体生成中",
                    },
                    "progress_stages": [
                        {"key": "queued", "label": "等待生成", "status": "succeeded"},
                        {"key": "calling_model", "label": "构建智能体生成中", "status": status},
                        {"key": "succeeded", "label": "生成完成", "status": "succeeded" if status == "succeeded" else "pending"},
                    ],
                },
                dedupe_key=f"skill-raw-material-generation:{GENERATION_ID}",
                metrics={"input_tokens": 11, "output_tokens": 22, "total_tokens": 33, "llm_calls": 1},
            )
        )
        if with_run:
            session.add(
                AgentRunRecord(
                    id=GENERATION_ID,
                    agent_key="psop.builder",
                    agent_version="v1",
                    status=status,
                    related_skill_definition_id=SKILL_ID,
                    related_generation_id=GENERATION_ID,
                    related_job_id=JOB_ID,
                    input_summary={"context_keys": ["material_analysis_results"]},
                    sandbox_path="/tmp/agent-run",
                    model_info={"agent_key": "psop.builder"},
                )
            )
            session.add(
                AgentEventRecord(
                    agent_run_id=GENERATION_ID,
                    seq_no=1,
                    event_type="agent.tool.completed",
                    payload={
                        "tool_name": "psop.builder.read_material_analysis",
                        "args": {"base64": "data:image/png;base64,SHOULD_NOT_LEAK"},
                        "result_status": "ok",
                        "result_type": "json",
                    },
                    occurred_at=datetime.now(timezone.utc),
                )
            )
            session.add(
                AgentEventRecord(
                    agent_run_id=GENERATION_ID,
                    seq_no=5,
                    event_type="agent.model.started",
                    payload={"model": "scripted-builder", "model_call_index": 1},
                    occurred_at=datetime.now(timezone.utc),
                )
            )
            for seq_no in (3, 4):
                session.add(
                    AgentEventRecord(
                        agent_run_id=GENERATION_ID,
                        seq_no=seq_no,
                        event_type="agent.tool.started",
                        payload={"tool_name": "psop.builder.submit_candidate"},
                        occurred_at=datetime.now(timezone.utc),
                    )
                )
            session.add(
                AgentEventRecord(
                    agent_run_id=GENERATION_ID,
                    seq_no=2,
                    event_type="agent.token.usage",
                    payload={"total": {"input_tokens": 11, "output_tokens": 22, "total_tokens": 33}},
                    occurred_at=datetime.now(timezone.utc),
                )
            )
        session.commit()


def test_latest_agent_run_returns_queued_generation_before_run_record_exists() -> None:
    with TestClient(create_app(_settings())) as client:
        _insert_builder_generation(client, status="pending", with_run=False)

        response = client.get(
            "/api/v1/agents/runs/latest",
            params={"agent_key": "psop.builder", "related_skill_definition_id": SKILL_ID},
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["agent_run_id"] == GENERATION_ID
        assert payload["status"] == "pending"
        assert payload["user_description"] == "帮我构建一个安装电脑主机的技能。"
        assert payload["related_job_id"] == JOB_ID
        assert payload["progress"]["label"] == "构建智能体生成中"


def test_agent_run_timeline_maps_events_and_hides_raw_tool_payload() -> None:
    with TestClient(create_app(_settings())) as client:
        _insert_builder_generation(client, status="succeeded", with_run=True)

        response = client.get(f"/api/v1/agents/runs/{GENERATION_ID}/timeline")

        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "succeeded"
        assert payload["related_runtime_run_id"] == ""
        assert payload["final"]["reference_files"] == ["references/frame-001.png"]
        assert payload["final"]["committed_commit_sha"] == "commit-0001"
        assert payload["token_usage"]["total_tokens"] == 33
        assert payload["model_call_count"] == 1
        assert payload["candidate_submission_attempts"] == 2
        assert payload["candidate_correction_attempts"] == 1
        assert any(step["title"] == "读取素材解析" for step in payload["steps"])
        serialized = response.text
        assert "SHOULD_NOT_LEAK" not in serialized
        assert "material_analysis_results" not in serialized


def test_agent_run_events_stream_sends_snapshot_and_final() -> None:
    with TestClient(create_app(_settings())) as client:
        _insert_builder_generation(client, status="succeeded", with_run=True)

        with client.stream("GET", f"/api/v1/agents/runs/{GENERATION_ID}/events") as response:
            assert response.status_code == 200
            body = "\n".join(response.iter_lines())

        assert "event: snapshot" in body
        assert "event: final" in body
        assert GENERATION_ID in body
