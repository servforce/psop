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
    cors_allow_origins: list[str] = [
        "http://127.0.0.1:4173",
        "http://localhost:4173",
    ]

    database_url: str | None = None
    database_host: str = "127.0.0.1"
    database_port: int = 5432
    database_name: str = "psop"
    database_user: str = "postgres"
    database_password: str = "postgres"
    database_check_on_startup: bool = False
    database_auto_create_schema: bool = False

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
