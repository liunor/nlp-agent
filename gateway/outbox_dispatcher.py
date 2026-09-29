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
        self._active: set[str] = set()

    async def submit(self, task: TurnTask) -> None:
        """Track a task already persisted atomically with its Turn.

        MySQLGatewayRepository.create_turn owns the transaction that inserts both
        rows.  Keeping the dispatcher write-free prevents a second transaction
        from producing an orphaned outbox message.
        """
        self._active.add(task.turn_id)

    async def cancel(self, turn_id: str) -> None:
        await self._transport.cancel(turn_id)
        self._active.discard(turn_id)

    async def inject(self, turn_id: str, content: str) -> None:
        await self._transport.inject(turn_id, content)

    async def close(self, *, force: bool = False, grace_s: float = 0) -> None:
        await self._transport.close(force=force, grace_s=grace_s)
        self._active.clear()

    def active_count(self) -> int:
        return len(self._active)

    @property
    def client(self) -> Any:
        """Expose the transport Redis client to lifecycle listeners."""
        return getattr(self._transport, "client", None)

    @property
    def config(self) -> Any:
        """Expose transport configuration to shared Redis listeners."""
        return getattr(self._transport, "config", None)
