"""Transactional task dispatch seam for MySQL-authoritative Turn delivery."""

from __future__ import annotations

from typing import Any

from gateway.dispatch import TurnTask


class OutboxTurnDispatcher:
    """Persist a Turn task in MySQL; the relay is the only Redis publisher."""

    def __init__(
        self,
        reliability: Any,
        transport: Any,
    ) -> None:
        self._reliability = reliability
        self._transport = transport

    async def submit(self, task: TurnTask) -> None:
        """Track a task already persisted atomically with its Turn.

        MySQLGatewayRepository.create_turn owns the transaction that inserts both
        rows.  Keeping the dispatcher write-free prevents a second transaction
        from producing an orphaned outbox message.
        """
        # Submission is durable but not execution. Live Worker heartbeats and
        # MySQL Turn status provide the execution gauges.

    async def healthy_worker_count(self) -> int | None:
        return await self._transport.healthy_worker_count()

    async def worker_heartbeats(self) -> list[dict[str, Any]] | None:
        return await self._transport.worker_heartbeats()

    async def cancel(self, turn_id: str) -> None:
        await self._transport.cancel(turn_id)

    async def inject(self, turn_id: str, content: str) -> None:
        await self._transport.inject(turn_id, content)

    async def close(self, *, force: bool = False, grace_s: float = 0) -> None:
        await self._transport.close(force=force, grace_s=grace_s)

    def active_count(self) -> int:
        return 0

    @property
    def client(self) -> Any:
        """Expose the transport Redis client to lifecycle listeners."""
        return getattr(self._transport, "client", None)

    @property
    def config(self) -> Any:
        """Expose transport configuration to shared Redis listeners."""
        return getattr(self._transport, "config", None)
