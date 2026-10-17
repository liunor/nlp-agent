"""Lifecycle-owned MySQL resources for one application process."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from .config import DatabaseConfig
from .engine import create_engine, create_session_factory, verify_database_ready
from .uow import UnitOfWorkFactory


class MySQLRuntime:
    """Own the async engine, session factory and readiness contract for one process."""

    def __init__(self, config: DatabaseConfig) -> None:
        self.config = config
        self._engine: AsyncEngine = create_engine(config)
        self.session_factory: async_sessionmaker[AsyncSession] = create_session_factory(
            self._engine
        )
        self.uow = UnitOfWorkFactory(self.session_factory)

    @classmethod
    def from_runtime(cls, runtime: dict[str, Any]) -> "MySQLRuntime":
        return cls(DatabaseConfig.from_runtime(runtime))

    async def start(self) -> None:
        await verify_database_ready(self._engine)

    async def close(self) -> None:
        await self._engine.dispose()

    def pool_metrics(self) -> dict[str, object]:
        """Expose the current async SQLAlchemy pool gauges without DB labels."""
        pool = self._engine.sync_engine.pool
        return {
            "size": int(pool.size()),
            "checked_out": int(pool.checkedout()),
            "overflow": int(pool.overflow()),
            "checked_in": int(pool.checkedin()),
            "timeout_s": int(getattr(pool, "_timeout", 0)),
            "checkout_latency": pool.checkout_metrics(),
        }
