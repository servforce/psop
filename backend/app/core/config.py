from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from urllib.parse import quote_plus

from pydantic_settings import BaseSettings, SettingsConfigDict


_REPO_ROOT = Path(__file__).resolve().parents[3]
_BACKEND_ROOT = _REPO_ROOT / "backend"


class Settings(BaseSettings):
    """Generic backend scaffold settings for the PSOP workspace."""

    model_config = SettingsConfigDict(
        env_file=(
            _BACKEND_ROOT / ".env",
            _REPO_ROOT / ".env",
        ),
        env_prefix="PSOP_",
        extra="ignore",
    )

    app_name: str = "PSOP Backend Scaffold"
    app_version: str = "0.1.0"
    environment: str = "development"
    debug: bool = True
    api_prefix: str = "/api/v1"
    log_level: str = "INFO"
    log_format: str = "plain"
    cors_allow_origins: list[str] = ["*"]

    database_url: str | None = None
    database_host: str = "127.0.0.1"
    database_port: int = 5432
    database_name: str = "psop"
    database_user: str = "postgres"
    database_password: str = "postgres"
    database_check_on_startup: bool = False
    database_auto_create_schema: bool = False
    gitlab_api_base_url: str = "https://gitlab.com/api/v4"
    gitlab_token: str | None = None
    gitlab_skills_group_path: str = "skills"
    gitlab_default_branch: str = "main"
    gitlab_timeout_seconds: float = 15.0
    object_store_endpoint: str = "http://127.0.0.1:9000"
    object_store_access_key: str = "minioadmin"
    object_store_secret_key: str = "minioadmin"
    object_store_bucket: str = "psop-artifacts"
    object_store_region: str = "us-east-1"
    object_store_secure: bool = False
    test_data_max_upload_bytes: int = 25 * 1024 * 1024
    raw_material_max_upload_bytes: int = 50 * 1024 * 1024
    raw_material_extract_text_max_chars: int = 80_000
    raw_material_url_timeout_seconds: float = 20.0
    otel_enabled: bool = True
    otel_traces_enabled: bool = True
    otel_logs_enabled: bool = True
    otel_console_exporter: bool = False
    otel_exporter_otlp_endpoint: str = "http://127.0.0.1:4318"
    otel_exporter_otlp_protocol: str = "http/protobuf"
    otel_service_name: str = "psop-backend"
    llm_provider: str = "openai-compatible"
    llm_api_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str | None = None
    llm_default_model: str = "gpt-4.1-mini"
    llm_timeout_seconds: float = 600.0
    runtime_worker_enabled: bool = True
    runtime_job_lease_seconds: int = 60
    runtime_job_max_attempts: int = 3
    runtime_step_timeout_seconds: int = 120

    @property
    def repo_root(self) -> Path:
        return _REPO_ROOT

    @property
    def backend_root(self) -> Path:
        return _BACKEND_ROOT

    @property
    def server_root(self) -> Path:
        return self.backend_root

    @property
    def static_root(self) -> Path:
        return self.repo_root / "static"

    @property
    def sqlalchemy_database_url(self) -> str:
        if self.database_url:
            return self.database_url

        user = quote_plus(self.database_user)
        password = quote_plus(self.database_password)
        return (
            f"postgresql+psycopg://{user}:{password}"
            f"@{self.database_host}:{self.database_port}/{self.database_name}"
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
