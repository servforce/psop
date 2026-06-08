from __future__ import annotations

from tests.test_skills_api import create_test_client


def test_skill_packages_sync_and_detail_use_skills_namespace() -> None:
    client, _, _ = create_test_client()

    with client:
        sync_response = client.post("/api/v1/skills/sync")
        list_response = client.get("/api/v1/skills")
        psop_response = client.get("/api/v1/skills", params={"scope": "psop"})
        detail_response = client.get("/api/v1/skills/pskill-builder")
        versions_response = client.get("/api/v1/skills/pskill-builder/versions")
        version_id = versions_response.json()[0]["id"]
        validate_response = client.post(f"/api/v1/skills/pskill-builder/versions/{version_id}/validate")
        activate_response = client.post(f"/api/v1/skills/pskill-builder/versions/{version_id}/activate")
        pskills_list_response = client.get("/api/v1/pskills")

    assert sync_response.status_code == 200
    assert sync_response.json()["scanned_count"] == 8
    assert sync_response.json()["package_count"] == 8

    package_names = {item["name"] for item in list_response.json()}
    assert list_response.status_code == 200
    assert {
        "pskill-builder",
        "pskill-compiler-formal-v5",
        "pskill-tester",
        "pskill-runner-field-assistant",
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
    assert detail["active_version"]["allowed_tools"] == ["psop.pskills.read", "psop.materials.read"]
    assert detail["active_version"]["resource_count"] >= 1
    assert detail["resources"][0]["resource_kind"] == "skill"

    assert versions_response.status_code == 200
    assert versions_response.json()[0]["content_hash"] == detail["active_content_hash"]
    assert validate_response.status_code == 200
    assert validate_response.json()["validation_status"] == "warning"
    assert validate_response.json()["validation_diagnostics"]
    assert activate_response.status_code == 200
    assert activate_response.json()["active_version_id"] == version_id
    assert pskills_list_response.status_code == 200
    assert pskills_list_response.json() == []


def test_skill_package_version_create_validate_and_activate_lifecycle() -> None:
    client, _, _ = create_test_client()

    payload = {
        "version_label": "builder-candidate",
        "manifest_json": {
            "name": "pskill-builder",
            "description": "Candidate builder package",
            "allowed-tools": ["psop.pskills.read", "psop.materials.read"],
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
    assert created["allowed_tools"] == ["psop.pskills.read", "psop.materials.read"]
    assert created["resource_count"] == 2
    assert validate_response.status_code == 200
    assert validate_response.json()["validation_status"] == "valid"
    assert activate_response.status_code == 200
    assert activate_response.json()["active_version_id"] == created["id"]
    assert activate_response.json()["active_version"]["version_label"] == "builder-candidate"
    assert duplicate_response.status_code == 409
