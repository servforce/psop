from __future__ import annotations

from sqlalchemy import select

from app.jobs.types import SKILL_SYNC_JOB_TYPE
from app.jobs.worker import RuntimeJobWorker
from app.skills.models import SkillBinding, SkillPackage, SkillVersion
from tests.test_skills_api import create_test_client


BUILDER_ALLOWED_TOOLS = [
    "psop.pskills.get",
    "psop.materials.list",
    "psop.materials.read_analysis",
    "psop.repository.read_file",
    "psop.repository.propose_patch",
    "psop.pskill_manifest.parse",
    "psop.pskill_manifest.render",
    "psop.memory.search",
    "psop.memory.write_candidate",
]


def test_skill_packages_sync_and_detail_use_skills_namespace() -> None:
    client, _, _ = create_test_client()

    with client:
        sync_response = client.post("/api/v1/skills/sync")
        list_response = client.get("/api/v1/skills")
        psop_response = client.get("/api/v1/skills", params={"scope": "psop"})
        detail_response = client.get("/api/v1/skills/pskill-builder")
        runner_detail_response = client.get("/api/v1/skills/pskill-runner-field-assistant")
        versions_response = client.get("/api/v1/skills/pskill-builder/versions")
        version_id = versions_response.json()[0]["id"]
        validate_response = client.post(f"/api/v1/skills/pskill-builder/versions/{version_id}/validate")
        activate_response = client.post(f"/api/v1/skills/pskill-builder/versions/{version_id}/activate")
        pskills_list_response = client.get("/api/v1/pskills")
        with client.app.state.db_manager.session() as session:
            skill_bindings = list(session.scalars(select(SkillBinding)).all())

    assert sync_response.status_code == 200
    assert sync_response.json()["scanned_count"] == 9
    assert sync_response.json()["package_count"] == 9

    package_names = {item["name"] for item in list_response.json()}
    assert list_response.status_code == 200
    assert {
        "pskill-builder",
        "pskill-compiler-formal-v5",
        "pskill-tester",
        "pskill-runner-field-assistant",
        "pskill-runner-evidence-evaluator",
        "pskill-run-evaluator",
        "psop-governance-manager",
        "ffmpeg-video-processing",
        "document-ocr-processing",
    } <= package_names

    assert psop_response.status_code == 200
    assert {item["scope"] for item in psop_response.json()} == {"psop"}

    detail = detail_response.json()
    assert detail_response.status_code == 200
    assert detail["name"] == "pskill-builder"
    assert detail["scope"] == "psop"
    assert [item["key"] for item in detail["used_by_agents"]] == ["pskill.builder"]
    assert detail["used_by_agents"][0]["usage_key"] == "pskill-builder"
    assert detail["used_by_agents"][0]["skill_binding_id"]
    assert detail["active_version"]["allowed_tools"] == BUILDER_ALLOWED_TOOLS
    assert detail["active_version"]["resource_count"] >= 1
    assert detail["resources"][0]["resource_kind"] == "skill"

    runner_detail = runner_detail_response.json()
    assert runner_detail_response.status_code == 200
    assert runner_detail["active_version"]["allowed_tools"] == ["psop.runtime.read"]
    assert [item["key"] for item in runner_detail["used_by_agents"]] == ["pskill.runner"]
    assert runner_detail["used_by_agents"][0]["usage_key"] == "pskill-runner-field-assistant"

    assert len(skill_bindings) == 11
    assert {(item.agent_key, item.usage_key) for item in skill_bindings} >= {
        ("pskill.builder", "pskill-builder"),
        ("pskill.builder", "ffmpeg-video-processing"),
        ("pskill.builder", "document-ocr-processing"),
        ("pskill.tester", "ffmpeg-video-processing"),
        ("pskill.runner", "pskill-runner-field-assistant"),
        ("pskill.runner", "pskill-runner-evidence-evaluator"),
        ("pskill.runner", "ffmpeg-video-processing"),
    }

    assert versions_response.status_code == 200
    assert versions_response.json()[0]["content_hash"] == detail["active_content_hash"]
    assert validate_response.status_code == 200
    assert validate_response.json()["validation_status"] == "warning"
    assert validate_response.json()["validation_diagnostics"]
    assert activate_response.status_code == 200
    assert activate_response.json()["active_version_id"] == version_id
    assert pskills_list_response.status_code == 200
    assert pskills_list_response.json() == []


def test_skill_bindings_refresh_when_agent_version_activates() -> None:
    client, _, _ = create_test_client()

    with client:
        sync_response = client.post("/api/v1/skills/sync")
        initial_builder_response = client.get("/api/v1/skills/pskill-builder")
        before_agent_response = client.get("/api/v1/agents/pskill.builder")
        spec = {
            **before_agent_response.json()["active_version"]["spec_json"],
            "allowed_skill_names": ["ffmpeg-video-processing"],
        }
        draft_response = client.post(
            "/api/v1/agents/pskill.builder/versions",
            json={"version_label": "builder-ffmpeg-only", "spec_json": spec},
        )
        draft = next(item for item in draft_response.json()["versions"] if item["version_label"] == "builder-ffmpeg-only")
        publish_response = client.post(f"/api/v1/agents/pskill.builder/versions/{draft['id']}/publish")
        activate_response = client.post(f"/api/v1/agents/pskill.builder/versions/{draft['id']}/activate")
        builder_response = client.get("/api/v1/skills/pskill-builder")
        ffmpeg_response = client.get("/api/v1/skills/ffmpeg-video-processing")
        with client.app.state.db_manager.session() as session:
            builder_bindings = list(
                session.scalars(
                    select(SkillBinding)
                    .join(SkillPackage, SkillPackage.id == SkillBinding.package_id)
                    .where(SkillPackage.name == "pskill-builder")
                ).all()
            )
            ffmpeg_bindings = list(
                session.scalars(
                    select(SkillBinding)
                    .join(SkillPackage, SkillPackage.id == SkillBinding.package_id)
                    .where(SkillPackage.name == "ffmpeg-video-processing")
                ).all()
            )

    assert sync_response.status_code == 200
    assert initial_builder_response.status_code == 200
    assert [item["key"] for item in initial_builder_response.json()["used_by_agents"]] == ["pskill.builder"]
    assert draft_response.status_code == 201
    assert publish_response.status_code == 200
    assert activate_response.status_code == 200

    assert builder_response.status_code == 200
    assert builder_response.json()["used_by_agents"] == []
    assert builder_bindings == []

    assert ffmpeg_response.status_code == 200
    assert {item["key"] for item in ffmpeg_response.json()["used_by_agents"]} == {
        "pskill.builder",
        "pskill.tester",
        "pskill.runner",
    }
    assert {item.agent_key for item in ffmpeg_bindings} == {"pskill.builder", "pskill.tester", "pskill.runner"}


def test_skill_packages_api_queues_sync_job_for_worker_processing() -> None:
    client, _, _ = create_test_client()

    with client:
        payload = {"idempotency_key": "skill-sync-api-1"}
        queue_response = client.post("/api/v1/skills/sync/queue", json=payload)
        duplicate_response = client.post("/api/v1/skills/sync/queue", json=payload)
        job_id = queue_response.json()["id"]

        worker = RuntimeJobWorker(
            settings=client.app.state.settings,
            database_manager=client.app.state.db_manager,
            gitlab_gateway=client.app.state.gitlab_gateway,
            inference_gateway=client.app.state.inference_gateway,
            asr_gateway=client.app.state.asr_gateway,
            object_store=client.app.state.object_store,
        )
        processed = worker.run_once()
        job_response = client.get(
            "/api/v1/runtime/jobs",
            params={"job_type": SKILL_SYNC_JOB_TYPE, "q": job_id},
        )

    assert queue_response.status_code == 202
    assert queue_response.json()["job_type"] == SKILL_SYNC_JOB_TYPE
    assert queue_response.json()["status"] == "pending"
    assert queue_response.json()["payload"]["operation"] == "skill_sync"
    assert duplicate_response.status_code == 202
    assert duplicate_response.json()["id"] == job_id

    assert processed is True
    job = job_response.json()[0]
    assert job["status"] == "succeeded"
    assert job["metrics"]["scanned_count"] == 9
    assert job["metrics"]["package_count"] == 9
    assert job["metrics"]["version_count"] == 9
    assert job["progress"]["percent"] == 100
    assert "packages=9" in job["progress"]["detail"]


def test_skill_package_version_create_validate_and_activate_lifecycle() -> None:
    client, _, _ = create_test_client()

    payload = {
        "version_label": "builder-candidate",
        "manifest_json": {
            "name": "pskill-builder",
            "description": "Candidate builder package",
            "allowed-tools": BUILDER_ALLOWED_TOOLS,
        },
        "body_object_key": "uploads/pskill-builder/builder-candidate/SKILL.md",
        "resource_index": [
            {
                "path": "SKILL.md",
                "kind": "skill",
                "content_hash": "skill-md-hash",
                "size_bytes": 128,
            },
            {
                "path": "references/README.md",
                "kind": "references",
                "content_hash": "reference-hash",
                "size_bytes": 64,
            },
        ],
    }

    with client:
        create_response = client.post("/api/v1/skills/pskill-builder/versions", json=payload)
        detail = create_response.json()
        created = next(item for item in detail["versions"] if item["version_label"] == "builder-candidate")
        validate_response = client.post(f"/api/v1/skills/pskill-builder/versions/{created['id']}/validate")
        activate_response = client.post(f"/api/v1/skills/pskill-builder/versions/{created['id']}/activate")
        duplicate_response = client.post("/api/v1/skills/pskill-builder/versions", json=payload)

    assert create_response.status_code == 201
    assert created["status"] == "candidate"
    assert created["validation_status"] == "valid"
    assert created["allowed_tools"] == BUILDER_ALLOWED_TOOLS
    assert created["resource_count"] == 2
    assert validate_response.status_code == 200
    assert validate_response.json()["validation_status"] == "valid"
    assert activate_response.status_code == 200
    assert activate_response.json()["active_version_id"] == created["id"]
    assert activate_response.json()["active_version"]["version_label"] == "builder-candidate"
    assert duplicate_response.status_code == 409


def test_skill_package_version_create_rejects_expanded_allowed_tools() -> None:
    client, _, _ = create_test_client()
    expanded_tools = [*BUILDER_ALLOWED_TOOLS, "psop.repository.commit_patch"]
    payload = {
        "version_label": "expanded-tools-candidate",
        "manifest_json": {
            "name": "pskill-builder",
            "description": "Candidate that attempts to expand tool permissions.",
            "allowed-tools": expanded_tools,
        },
        "body_object_key": "uploads/pskill-builder/expanded-tools-candidate/SKILL.md",
        "resource_index": [
            {
                "path": "SKILL.md",
                "kind": "skill",
                "content_hash": "expanded-skill-md-hash",
                "size_bytes": 128,
            },
            {
                "path": "references/README.md",
                "kind": "references",
                "content_hash": "expanded-reference-hash",
                "size_bytes": 64,
            },
        ],
    }

    with client:
        response = client.post("/api/v1/skills/pskill-builder/versions", json=payload)

    assert response.status_code == 422
    assert response.json()["code"] == "skill_validation_error"
    assert response.json()["details"]["expanded_tools"] == ["psop.repository.commit_patch"]


def test_skill_package_activation_rejects_directly_persisted_expanded_allowed_tools() -> None:
    client, _, _ = create_test_client()
    expanded_tools = [*BUILDER_ALLOWED_TOOLS, "psop.repository.commit_patch"]

    with client:
        client.post("/api/v1/skills/sync")
        before_response = client.get("/api/v1/skills/pskill-builder")
        active_version_id = before_response.json()["active_version_id"]

        with client.app.state.db_manager.session() as session:
            package = session.scalar(select(SkillPackage).where(SkillPackage.name == "pskill-builder"))
            assert package is not None
            candidate = SkillVersion(
                package_id=package.id,
                version_label="direct-expanded-tools-candidate",
                status="candidate",
                content_hash="direct-expanded-tools-candidate-hash",
                manifest_json={
                    "name": "pskill-builder",
                    "description": "Directly persisted candidate that attempts to expand tools.",
                    "allowed-tools": expanded_tools,
                },
                body_object_key="uploads/pskill-builder/direct-expanded-tools-candidate/SKILL.md",
                resource_index=[
                    {
                        "path": "SKILL.md",
                        "kind": "skill",
                        "content_hash": "direct-expanded-skill-md-hash",
                        "size_bytes": 128,
                    },
                    {
                        "path": "references/README.md",
                        "kind": "references",
                        "content_hash": "direct-expanded-reference-hash",
                        "size_bytes": 64,
                    },
                ],
                allowed_tools=expanded_tools,
                validation_status="pending",
                validation_diagnostics=[],
            )
            session.add(candidate)
            session.flush()
            candidate_id = candidate.id
            session.commit()

        validate_response = client.post(f"/api/v1/skills/pskill-builder/versions/{candidate_id}/validate")
        activate_response = client.post(f"/api/v1/skills/pskill-builder/versions/{candidate_id}/activate")
        after_response = client.get("/api/v1/skills/pskill-builder")

    diagnostics = validate_response.json()["validation_diagnostics"]
    assert validate_response.status_code == 200
    assert validate_response.json()["validation_status"] == "invalid"
    assert [item["code"] for item in diagnostics] == ["allowed_tools_expand_package_scope"]
    assert diagnostics[0]["expanded_tools"] == ["psop.repository.commit_patch"]

    assert activate_response.status_code == 422
    assert activate_response.json()["details"]["version_id"] == candidate_id
    assert activate_response.json()["details"]["diagnostics"][0]["code"] == "allowed_tools_expand_package_scope"
    assert after_response.json()["active_version_id"] == active_version_id
