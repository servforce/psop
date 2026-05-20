from __future__ import annotations

from fastapi.testclient import TestClient

from app.agents.registry import PromptRegistry, content_hash
from app.app import create_app
from app.domain.agent_prompts.models import AgentPromptVersion
from app.domain.agent_prompts.repository import AgentPromptRepository
from app.domain.agent_prompts.service import AgentPromptService
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
            assert "default.compile_agent" in bindings
            assert "skill_test.semantic_judge" in bindings
            assert "runtime.llm_node_fallback" in bindings

            binding = bindings["default.compile_agent"]
            version = session.get(AgentPromptVersion, binding.active_version_id)
            assert version is not None
            files = dict(version.files)
            files["system.md"] = "DB managed compile prompt"
            version.files = files
            version.content_hash = content_hash(files)
            session.commit()

            pack = PromptRegistry().load_agent_for_usage(
                "default.compile_agent",
                fallback_ref="skill_compilation/formal_v5_compile/v1",
                session=session,
            )

            assert pack.source == "db"
            assert pack.system_prompt == "DB managed compile prompt"
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

        detail_response = client.get(f"/api/v1/agent-prompts/{judge_prompt['id']}")
        assert detail_response.status_code == 200
        detail = detail_response.json()
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

        bindings_response = client.get("/api/v1/agent-prompt-bindings")
        assert bindings_response.status_code == 200
        binding = next(item for item in bindings_response.json() if item["usage_key"] == "skill_test.semantic_judge")
        assert binding["active_version_id"] == draft["id"]

