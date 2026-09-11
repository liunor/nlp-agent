"""Read-only Redis assertions for the managed real HTTP environment."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from redis import Redis


@dataclass
class RedisProbe:
    port: int
    database: int
    task_stream: str
    task_group: str
    _client: Redis | None = None

    @property
    def client(self) -> Redis:
        if self._client is None:
            self._client = Redis(
                host="127.0.0.1",
                port=self.port,
                db=self.database,
                decode_responses=True,
            )
        return self._client

    def stream_length(self) -> int:
        return int(self.client.xlen(self.task_stream))

    def consumer_group(self) -> dict[str, Any]:
        for group in self.client.xinfo_groups(self.task_stream):
            if str(group.get("name")) == self.task_group:
                return group
        raise AssertionError(f"Redis group {self.task_group!r} does not exist")

    def pending_count(self) -> int:
        return int(self.consumer_group().get("pending", 0))

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None
