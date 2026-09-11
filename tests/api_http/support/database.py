"""Small MySQL probes for isolated HTTP-test setup and invariants."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import Engine, create_engine, text


@dataclass
class MySqlProbe:
    database_url: str
    _engine: Engine | None = None

    @property
    def engine(self) -> Engine:
        if self._engine is None:
            self._engine = create_engine(
                self.database_url.replace("mysql+aiomysql://", "mysql+pymysql://"),
                pool_pre_ping=True,
            )
        return self._engine

    def scalar(self, statement: str, **parameters: Any) -> Any:
        with self.engine.connect() as connection:
            return connection.execute(text(statement), parameters).scalar()

    def execute(self, statement: str, **parameters: Any) -> None:
        """Apply deterministic test state changes in the isolated database.

        This is intentionally limited to the managed test database.  The
        environment fixture refuses caller-provided database URLs before a
        probe can be created.
        """
        with self.engine.begin() as connection:
            connection.execute(text(statement), parameters)

    def close(self) -> None:
        if self._engine is not None:
            self._engine.dispose()
            self._engine = None
