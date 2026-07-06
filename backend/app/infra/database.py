from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool


class Base(DeclarativeBase):
    """Declarative base for the PSOP backend models."""


def _build_engine(database_url: str) -> Engine:
    engine_kwargs: dict[str, object] = {"future": True}
    connect_args: dict[str, object] = {}

    if database_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
        if ":memory:" in database_url:
            engine_kwargs["poolclass"] = StaticPool
    else:
        engine_kwargs["pool_pre_ping"] = True

    return create_engine(database_url, connect_args=connect_args, **engine_kwargs)


class DatabaseManager:
    """Owns the SQLAlchemy engine and session factory."""

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url
        self.engine = _build_engine(database_url)
        self.session_factory = sessionmaker(
            bind=self.engine,
            autoflush=False,
            autocommit=False,
            expire_on_commit=False,
        )

    def check_connection(self) -> None:
        with self.engine.connect() as connection:
            connection.execute(text("SELECT 1"))

    def create_schema(self) -> None:
        from app.domain.agent_prompts import models as agent_prompt_models  # noqa: F401
        from app.domain.skills import models  # noqa: F401
        from app.domain.compiler import models as compiler_models  # noqa: F401
        from app.domain.jobs import models as job_models  # noqa: F401
        from app.domain.runtime import models as runtime_models  # noqa: F401
        from app.domain.skill_tests import models as skill_test_models  # noqa: F401
        from app.agent_harness.persistence import models as agent_harness_models  # noqa: F401

        Base.metadata.create_all(self.engine)
        self._reconcile_schema()

    def _reconcile_schema(self) -> None:
        inspector = inspect(self.engine)
        if "agent_run" not in inspector.get_table_names():
            return

        agent_run_columns = {column["name"] for column in inspector.get_columns("agent_run")}
        statements: list[str] = []
        if "related_runtime_run_id" not in agent_run_columns:
            statements.append(
                "ALTER TABLE agent_run "
                "ADD COLUMN related_runtime_run_id VARCHAR(36) NOT NULL DEFAULT ''"
            )

        agent_run_indexes = {index["name"] for index in inspector.get_indexes("agent_run")}
        if "idx_agent_run_related_runtime_run" not in agent_run_indexes:
            statements.append(
                "CREATE INDEX IF NOT EXISTS idx_agent_run_related_runtime_run "
                "ON agent_run (related_runtime_run_id)"
            )

        if not statements:
            return

        with self.engine.begin() as connection:
            for statement in statements:
                connection.execute(text(statement))

    @contextmanager
    def session(self) -> Generator[Session, None, None]:
        session = self.session_factory()
        try:
            yield session
        finally:
            session.close()

    def dispose(self) -> None:
        self.engine.dispose()
