from fastapi.testclient import TestClient

from app.app import create_app
from app.core.config import Settings


def create_test_settings() -> Settings:
    return Settings(
        app_name="PSOP Backend Scaffold",
        database_url="sqlite+pysqlite:///:memory:",
        database_check_on_startup=False,
        database_auto_create_schema=False,
    )


def test_healthz() -> None:
    with TestClient(create_app(create_test_settings())) as client:
        response = client.get("/healthz")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["service"] == "PSOP Backend Scaffold"
    assert payload["mode"] == "scaffold"


def test_service_info() -> None:
    with TestClient(create_app(create_test_settings())) as client:
        response = client.get("/")

    assert response.status_code == 200
    payload = response.json()
    assert payload["name"] == "PSOP Backend Scaffold"
    assert payload["mode"] == "scaffold"
    assert "backend" in payload["modules"]


def test_api_health() -> None:
    with TestClient(create_app(create_test_settings())) as client:
        response = client.get("/api/v1/system/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_api_service_info() -> None:
    with TestClient(create_app(create_test_settings())) as client:
        response = client.get("/api/v1/system")

    assert response.status_code == 200
    assert response.json()["api_prefix"] == "/api/v1"


def test_runs_status_filter_exposes_openapi_enum_and_rejects_unknown_status() -> None:
    settings = create_test_settings().model_copy(update={"database_auto_create_schema": True})
    app = create_app(settings)
    openapi = app.openapi()
    parameters = openapi["paths"]["/api/v1/runs"]["get"]["parameters"]
    status_parameter = next(parameter for parameter in parameters if parameter["name"] == "status")
    enum_schema = next(
        schema
        for schema in status_parameter["schema"].get("anyOf", [status_parameter["schema"]])
        if "enum" in schema
    )

    assert enum_schema["enum"] == [
        "queued",
        "waiting_runtime",
        "running",
        "waiting_input",
        "succeeded",
        "failed",
        "aborted",
        "cancelled",
    ]

    with TestClient(app) as client:
        valid_response = client.get("/api/v1/runs", params={"status": "waiting_input"})
        invalid_response = client.get("/api/v1/runs", params={"status": "unknown"})

    assert valid_response.status_code == 200
    assert valid_response.json() == []
    assert invalid_response.status_code == 422


def test_inference_models_api_lists_two_configured_capabilities() -> None:
    settings = create_test_settings()
    with TestClient(create_app(settings)) as client:
        response = client.get("/api/v1/gateway/inference/models")

    assert response.status_code == 200
    payload = response.json()
    assert [item["route_key"] for item in payload] == ["text", "multimodal"]
    assert payload[0]["model"] == settings.llm_text_model
    assert payload[0]["supports_text"] is True
    assert payload[0]["supports_attachments"] is False
    assert payload[0]["thinking_enabled"] is settings.llm_text_enable_thinking
    assert payload[0]["thinking_budget"] == settings.llm_text_thinking_budget
    assert payload[1]["model"] == settings.llm_multimodal_model
    assert payload[1]["supports_text"] is True
    assert payload[1]["supports_attachments"] is True
    assert payload[1]["thinking_enabled"] is settings.llm_multimodal_enable_thinking
    assert payload[1]["thinking_budget"] == settings.llm_multimodal_thinking_budget
    assert all("api_key" not in item for item in payload)


def test_settings_build_database_url() -> None:
    settings = Settings(
        database_url=None,
        database_host="db.example.local",
        database_port=5433,
        database_name="psop_test",
        database_user="tester",
        database_password="secret",
    )

    assert (
        settings.sqlalchemy_database_url
        == "postgresql+psycopg://tester:secret@db.example.local:5433/psop_test"
    )
