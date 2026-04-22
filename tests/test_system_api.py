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


def test_settings_build_database_url() -> None:
    settings = Settings(
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
