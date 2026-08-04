"""Database connection and session management for SQLAlchemy."""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Generator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from configs.settings import settings


def generate_uuid7() -> str:
    """Generate a UUID7 identifier for primary keys."""
    try:
        from uuid7 import uuid7
        return str(uuid7())
    except ImportError:
        return str(uuid.uuid4())


def utc_now() -> datetime:
    """Return the current UTC timestamp with timezone info."""
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy models."""

    pass


class DatabaseManager:
    """Manages database connections and sessions."""

    def __init__(self, database_url: str | None = None, **engine_kwargs: Any) -> None:
        self.database_url = database_url or self._get_database_url()
        self._engine: Engine | None = None
        self._session_factory: sessionmaker[Session] | None = None
        self._engine_kwargs = engine_kwargs

    @staticmethod
    def _get_database_url() -> str:
        """Build the database URL from settings."""
        db_config = settings.database_runtime
        db_type = db_config.get("type", "sqlite")

        if db_type == "mysql":
            host = db_config.get("host", "localhost")
            port = db_config.get("port", 3306)
            name = db_config.get("name", "nlp_agent")
            user = db_config.get("user", "root")
            password = db_config.get("password", "")
            return f"mysql+pymysql://{user}:{password}@{host}:{port}/{name}?charset=utf8mb4"
        else:
            from pathlib import Path
            project = Path(__file__).resolve().parent.parent
            db_path = db_config.get("path", ".data/nlp_agent.sqlite3")
            path = Path(db_path)
            if not path.is_absolute():
                path = project / path
            return f"sqlite:///{path}"

    @property
    def engine(self) -> Engine:
        if self._engine is None:
            connect_args = {}
            if self.database_url.startswith("sqlite"):
                connect_args["check_same_thread"] = False

            self._engine = create_engine(
                self.database_url,
                pool_pre_ping=True,
                connect_args=connect_args,
                **self._engine_kwargs,
            )

            if self.database_url.startswith("sqlite"):
                @event.listens_for(self._engine, "connect")
                def set_sqlite_pragma(dbapi_connection, connection_record):
                    cursor = dbapi_connection.cursor()
                    cursor.execute("PRAGMA journal_mode=WAL")
                    cursor.execute("PRAGMA foreign_keys=ON")
                    cursor.close()

        return self._engine

    @property
    def session_factory(self) -> sessionmaker[Session]:
        if self._session_factory is None:
            self._session_factory = sessionmaker(
                bind=self.engine,
                expire_on_commit=False,
                autoflush=False,
            )
        return self._session_factory

    @contextmanager
    def session(self) -> Generator[Session, None, None]:
        """Provide a transactional session scope."""
        session = self.session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def create_tables(self) -> None:
        """Create all tables defined in the Base metadata."""
        Base.metadata.create_all(bind=self.engine)

    def drop_tables(self) -> None:
        """Drop all tables (use with caution)."""
        Base.metadata.drop_all(bind=self.engine)

    def dispose(self) -> None:
        """Dispose of the engine and its connection pool."""
        if self._engine is not None:
            self._engine.dispose()
            self._engine = None
            self._session_factory = None


database_manager = DatabaseManager()


def get_session() -> Generator[Session, None, None]:
    """FastAPI dependency for obtaining a database session."""
    with database_manager.session() as session:
        yield session


async def get_async_session():
    """Async generator wrapper for get_session (for FastAPI Depends)."""
    for session in get_session():
        yield session
