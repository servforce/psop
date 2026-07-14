from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
import time

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import QueuePool, StaticPool

from app.core.observability import record_metric_histogram


class Base(DeclarativeBase):
    """Declarative base for the PSOP backend models."""


class _ObservedQueuePool(QueuePool):
    def _do_get(self):
        started_at = time.perf_counter()
        try:
            return super()._do_get()
        finally:
            record_metric_histogram(
                "psop.database.checkout_wait",
                max(0.0, time.perf_counter() - started_at),
                unit="s",
                description="Time waiting to check out a database connection",
            )


def _build_engine(database_url: str) -> Engine:
    engine_kwargs: dict[str, object] = {"future": True}
    connect_args: dict[str, object] = {}

    if database_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
        if ":memory:" in database_url:
            engine_kwargs["poolclass"] = StaticPool
    else:
        engine_kwargs["pool_pre_ping"] = True
        engine_kwargs["poolclass"] = _ObservedQueuePool

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
        table_names = set(inspector.get_table_names())
        statements: list[str] = []
        if "agent_run" in table_names:
            agent_run_columns = {column["name"] for column in inspector.get_columns("agent_run")}
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

        for table_name, index_name in (
            ("session_token_snapshot", "uk_session_token_snapshot_run_seq"),
            ("trace_event", "uk_trace_event_run_seq"),
        ):
            if table_name not in table_names:
                continue
            existing_names = {
                item["name"]
                for item in [
                    *inspector.get_indexes(table_name),
                    *inspector.get_unique_constraints(table_name),
                ]
                if item.get("name")
            }
            if index_name not in existing_names:
                statements.append(
                    f"CREATE UNIQUE INDEX IF NOT EXISTS {index_name} "
                    f"ON {table_name} (run_id, seq_no)"
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
