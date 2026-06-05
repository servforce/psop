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
    assert pskills_list_response.status_code == 200
    assert pskills_list_response.json() == []
