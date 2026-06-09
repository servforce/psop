from __future__ import annotations

from fastapi.testclient import TestClient

from app.agents.registry import PromptRegistry, content_hash
from app.app import create_app
from app.agent_prompts.models import AgentPromptVersion
from app.agent_prompts.repository import AgentPromptRepository
from app.agent_prompts.service import AgentPromptService
from app.infra.database import DatabaseManager
from tests.test_skills_api import FakeGitLabGateway, FakeInferenceGateway, FakeObjectStore, create_test_settings


def test_agent_prompt_seed_creates_default_bindings_and_db_registry_priority() -> None:
    settings = create_test_settings()
    database_manager = DatabaseManager(settings.sqlalchemy_database_url)
    database_manager.create_schema()
    service = AgentPromptService()
    repository = AgentPromptRepository()

    try:
        with database_manager.session() as session:
            assert service.ensure_seed_data(session) is True
            session.commit()

            bindings = {item.usage_key: item for item in repository.list_bindings(session)}
            assert "pskill.build.default" in bindings
            assert "pskill.compile.formal_v5" in bindings
            assert "pskill.test.pre_publish" in bindings
            assert "pskill.run.node" in bindings
            assert "pskill.evaluate.run" in bindings
            assert "psop.governance.proposal" in bindings
            assert "default.compile_agent" in bindings
            assert "default.skill_creation_agent" in bindings
            assert "skill_test.semantic_judge" in bindings
            assert "runtime.llm_node_fallback" in bindings

            binding = bindings["pskill.compile.formal_v5"]
            version = session.get(AgentPromptVersion, binding.active_version_id)
            assert version is not None
            files = dict(version.files)
            files["system.md"] = "DB managed compile prompt"
            version.files = files
            version.content_hash = content_hash(files)
            session.commit()

            pack = PromptRegistry().load_agent_for_usage(
                "pskill.compile.formal_v5",
                fallback_ref="skill_compilation/formal_v5_compile/v1",
                session=session,
            )

            assert pack.source == "db"
            assert pack.system_prompt == "DB managed compile prompt"
            assert pack.metadata()["agent_key"] == "pskill.compiler"
            assert pack.metadata()["prompt_ref"] == "skill_compilation.formal_v5_compile/v1"
            assert pack.metadata()["definition_key"] == "skill_compilation.formal_v5_compile"
    finally:
        database_manager.dispose()


def test_agent_prompt_api_validates_publishes_and_activates_versions() -> None:
    client = TestClient(
        create_app(
            create_test_settings(),
            gitlab_gateway=FakeGitLabGateway(),
            inference_gateway=FakeInferenceGateway(),
            object_store=FakeObjectStore(),
        )
    )
    with client:
        list_response = client.get("/api/v1/agent-prompts")
        assert list_response.status_code == 200
        prompts = list_response.json()
        judge_prompt = next(item for item in prompts if item["key"] == "skill_test.semantic_judge")
        assert judge_prompt["agent_key"] == "pskill.tester"

        detail_response = client.get(f"/api/v1/agent-prompts/{judge_prompt['id']}")
        assert detail_response.status_code == 200
        detail = detail_response.json()
        assert detail["agent_key"] == "pskill.tester"
        parent_version = detail["selected_version"]

        draft_response = client.post(
            f"/api/v1/agent-prompts/{judge_prompt['id']}/versions",
            json={"parent_version_id": parent_version["id"]},
        )
        assert draft_response.status_code == 201
        draft = draft_response.json()["selected_version"]
        files = dict(draft["files"])
        files["system.md"] = ""

        save_response = client.put(
            f"/api/v1/agent-prompts/{judge_prompt['id']}/versions/{draft['id']}/files",
            json={"files": files},
        )
        assert save_response.status_code == 200

        invalid_response = client.post(
            f"/api/v1/agent-prompts/{judge_prompt['id']}/versions/{draft['id']}/validate"
        )
        assert invalid_response.status_code == 200
        assert invalid_response.json()["valid"] is False

        files["system.md"] = "Judge prompt from API draft"
        save_response = client.put(
            f"/api/v1/agent-prompts/{judge_prompt['id']}/versions/{draft['id']}/files",
            json={"files": files},
        )
        assert save_response.status_code == 200

        publish_response = client.post(
            f"/api/v1/agent-prompts/{judge_prompt['id']}/versions/{draft['id']}/publish"
        )
        assert publish_response.status_code == 200
        assert publish_response.json()["status"] == "published"
        assert publish_response.json()["files"]["agent.yaml"]

        edit_published_response = client.put(
            f"/api/v1/agent-prompts/{judge_prompt['id']}/versions/{draft['id']}/files",
            json={"files": files},
        )
        assert edit_published_response.status_code == 409

        activate_response = client.post(
            f"/api/v1/agent-prompts/{judge_prompt['id']}/versions/{draft['id']}/activate",
            json={"usage_key": "skill_test.semantic_judge"},
        )
        assert activate_response.status_code == 200
        assert activate_response.json()["active_version_id"] == draft["id"]
        assert activate_response.json()["agent_key"] == "pskill.tester"

        bindings_response = client.get("/api/v1/agent-prompt-bindings")
        assert bindings_response.status_code == 200
        binding = next(item for item in bindings_response.json() if item["usage_key"] == "skill_test.semantic_judge")
        assert binding["active_version_id"] == draft["id"]


def test_agent_prompt_api_create_persists_agent_key_in_generated_pack() -> None:
    client = TestClient(
        create_app(
            create_test_settings(),
            gitlab_gateway=FakeGitLabGateway(),
            inference_gateway=FakeInferenceGateway(),
            object_store=FakeObjectStore(),
        )
    )
    with client:
        create_response = client.post(
            "/api/v1/agent-prompts",
            json={
                "key": "custom.runner.prompt",
                "agent_id": "custom.runner.prompt",
                "agent_key": "pskill.runner",
                "scenario": "runtime_execution",
                "name": "Custom Runner Prompt",
                "description": "Custom runner prompt",
                "route_key": "text",
            },
        )
        assert create_response.status_code == 201
        created = create_response.json()
        assert created["agent_key"] == "pskill.runner"
        assert created["active_version_id"] is None

        version = created["selected_version"]
        files = dict(version["files"])
        assert "agent_key: pskill.runner" in files["agent.yaml"]

        files["system.md"] = "Custom runner prompt body"
        save_response = client.put(
            f"/api/v1/agent-prompts/{created['id']}/versions/{version['id']}/files",
            json={"files": files},
        )
        assert save_response.status_code == 200

        validate_response = client.post(
            f"/api/v1/agent-prompts/{created['id']}/versions/{version['id']}/validate"
        )
        assert validate_response.status_code == 200
        assert validate_response.json()["valid"] is True
        assert validate_response.json()["metadata"]["agent_key"] == "pskill.runner"

        publish_response = client.post(
            f"/api/v1/agent-prompts/{created['id']}/versions/{version['id']}/publish"
        )
        assert publish_response.status_code == 200
        assert publish_response.json()["status"] == "published"

        activate_response = client.post(
            f"/api/v1/agent-prompts/{created['id']}/versions/{version['id']}/activate",
            json={"usage_key": "custom.runner.prompt"},
        )
        assert activate_response.status_code == 200
        assert activate_response.json()["active_version_id"] == version["id"]
        assert activate_response.json()["agent_key"] == "pskill.runner"
