from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import create_engine, text
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
        from app.agents import models as agent_models  # noqa: F401
        from app.agent_prompts import models as agent_prompt_models  # noqa: F401
        from app.pskills import models  # noqa: F401
        from app.compiler import models as compiler_models  # noqa: F401
        from app.evaluations import models as evaluation_models  # noqa: F401
        from app.governance import models as governance_models  # noqa: F401
        from app.memory import models as memory_models  # noqa: F401
        from app.jobs import models as job_models  # noqa: F401
        from app.runtime import models as runtime_models  # noqa: F401
        from app.testing import models as skill_test_models  # noqa: F401
        from app.skills import models as skill_package_models  # noqa: F401
        from app.tools import models as tool_models  # noqa: F401

        Base.metadata.create_all(self.engine)

    @contextmanager
    def session(self) -> Generator[Session, None, None]:
        session = self.session_factory()
        try:
            yield session
        finally:
            session.close()

    def dispose(self) -> None:
        self.engine.dispose()
