from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import quote_plus

from pydantic import Field, model_validator
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
    object_store_connect_timeout_seconds: float = Field(default=3.0, gt=0)
    object_store_read_timeout_seconds: float = Field(default=30.0, gt=0)
    object_store_total_max_attempts: int = Field(default=2, gt=0)
    object_store_max_pool_connections: int = Field(default=16, gt=0)
    object_store_auto_create_bucket: bool = True
    test_data_max_upload_bytes: int = 25 * 1024 * 1024
    terminal_event_max_upload_files: int = Field(default=4, gt=0)
    terminal_event_max_file_bytes: int = Field(default=25 * 1024 * 1024, gt=0)
    terminal_event_max_total_file_bytes: int = Field(default=25 * 1024 * 1024, gt=0)
    terminal_event_max_request_bytes: int = Field(default=27 * 1024 * 1024, gt=0)
    terminal_object_store_io_workers: int = Field(default=8, gt=0)
    raw_material_max_upload_bytes: int = 50 * 1024 * 1024
    raw_material_video_max_upload_bytes: int = 2 * 1024 * 1024 * 1024
    raw_material_extract_text_max_chars: int = 80_000
    raw_material_url_timeout_seconds: float = 20.0
    video_max_analyzed_frames: int = 120
    otel_enabled: bool = True
    otel_traces_enabled: bool = True
    otel_logs_enabled: bool = True
    otel_metrics_enabled: bool = True
    otel_console_exporter: bool = False
    otel_exporter_otlp_endpoint: str = "http://127.0.0.1:4318"
    otel_exporter_otlp_protocol: str = "http/protobuf"
    otel_service_name: str = "psop-backend"
    llm_provider: str = "openai-compatible"
    llm_api_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str | None = None
    llm_text_model: str = "qwen3.7-plus"
    llm_text_enable_thinking: bool = True
    llm_text_thinking_budget: int | None = 8192
    llm_multimodal_model: str = "qwen3.6-plus"
    llm_multimodal_enable_thinking: bool = True
    llm_multimodal_thinking_budget: int | None = 8192
    llm_timeout_seconds: float = 600.0
    standard_lightrag_base_url: str = "http://10.0.0.20:9621"
    standard_lightrag_api_key: str = "servforce"
    standard_lightrag_timeout_seconds: float = 20.0
    standard_lightrag_max_results: int = 8
    asr_api_base_url: str = "http://10.0.0.20:12302"
    asr_language: str | None = "zh"
    asr_timeout_seconds: float = 600.0
    asr_temperature: float | None = 0.0
    runtime_worker_enabled: bool = True
    runtime_worker_embedded_enabled: bool = False
    runtime_worker_runtime_concurrency: int = Field(default=2, ge=0)
    runtime_worker_build_test_concurrency: int = Field(default=1, ge=0)
    runtime_worker_material_concurrency: int = Field(default=1, ge=0)
    runtime_job_poll_interval_seconds: float = Field(default=0.5, gt=0)
    runtime_job_lease_seconds: int = Field(default=60, gt=0)
    runtime_job_heartbeat_seconds: int = Field(default=20, gt=0)
    runtime_job_recovery_scan_seconds: int = Field(default=10, gt=0)
    runtime_worker_shutdown_grace_seconds: int = Field(default=30, gt=0)
    runtime_job_max_attempts: int = Field(default=3, gt=0)
    runtime_step_timeout_seconds: int = Field(default=120, gt=0)
    runtime_event_transport: Literal["auto", "inprocess", "postgres_notify"] = "auto"
    runtime_event_channel: str = "psop_runtime_events"
    agent_harness_profile: str = "dev_open"
    agent_harness_sandbox_provider: str = "local"
    agent_harness_sandbox_root: str = ".psop/agent-runs"
    agent_harness_workspace_root: str = ".psop/agent-runs"
    agent_harness_mcp_enabled: bool = False

    @model_validator(mode="after")
    def validate_terminal_media_limits(self) -> "Settings":
        if self.terminal_event_max_file_bytes > self.terminal_event_max_total_file_bytes:
            raise ValueError("终端事件单文件上限不能大于文件总量上限")
        if self.terminal_event_max_total_file_bytes >= self.terminal_event_max_request_bytes:
            raise ValueError("终端事件请求体上限必须大于文件总量上限，以容纳 multipart 开销")
        if self.object_store_max_pool_connections < self.terminal_object_store_io_workers:
            raise ValueError("对象存储连接池不能小于对象存储 I/O worker 数")
        return self

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
