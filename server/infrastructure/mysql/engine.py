"""Async MySQL engine, session factory and readiness validation."""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from .config import DatabaseConfig
from .pool_metrics import MeasuredAsyncAdaptedQueuePool


def create_engine(config: DatabaseConfig) -> AsyncEngine:
    return create_async_engine(
        config.url,
        poolclass=MeasuredAsyncAdaptedQueuePool,
        pool_pre_ping=True,
        pool_size=config.pool_size,
        max_overflow=config.max_overflow,
        pool_timeout=config.pool_timeout_s,
        pool_recycle=config.pool_recycle_s,
        connect_args={
            "connect_timeout": config.connect_timeout_s,
            "init_command": f"SET SESSION max_execution_time = {config.statement_timeout_s * 1000}",
        },
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


async def verify_database_ready(engine: AsyncEngine) -> None:
    """Verify connectivity; schema changes remain exclusively Alembic-owned."""
    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))


SessionFactory = Callable[[], AsyncSession]
