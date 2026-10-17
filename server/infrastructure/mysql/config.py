"""Typed MySQL configuration without constructing database connections."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DatabaseConfig:
    url: str
    pool_size: int = 10
    max_overflow: int = 20
    pool_timeout_s: int = 5
    pool_recycle_s: int = 1800
    connect_timeout_s: int = 5
    statement_timeout_s: int = 30

    def __post_init__(self) -> None:
        if not self.url.startswith("mysql+aiomysql://"):
            raise ValueError("database URL must use mysql+aiomysql://")
        if self.pool_size < 1:
            raise ValueError("pool_size must be at least 1")
        if self.max_overflow < 0:
            raise ValueError("max_overflow must not be negative")
        if self.pool_timeout_s < 1:
            raise ValueError("pool_timeout_s must be at least 1 second")
        if self.pool_recycle_s < 1:
            raise ValueError("pool_recycle_s must be at least 1")
        if self.connect_timeout_s < 1 or self.statement_timeout_s < 1:
            raise ValueError("database timeouts must be at least 1 second")

    @classmethod
    def from_runtime(cls, runtime: dict[str, object], *, synchronous: bool = False) -> "DatabaseConfig":
        return cls(
            url=str(runtime.get("url", "")).strip(),
            pool_size=int(runtime.get("sync_pool_size", 5) if synchronous else runtime.get("pool_size", 10)),
            max_overflow=int(runtime.get("sync_max_overflow", 5) if synchronous else runtime.get("max_overflow", 20)),
            pool_timeout_s=int(runtime.get("pool_timeout_s", 5)),
            pool_recycle_s=int(runtime.get("pool_recycle_s", 1800)),
            connect_timeout_s=int(runtime.get("connect_timeout_s", 5)),
            statement_timeout_s=int(runtime.get("statement_timeout_s", 30)),
        )
