"""Per-execution ownership carried through asyncio tasks and asyncio.to_thread."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from collections.abc import Iterator


class LostTurnClaimError(RuntimeError):
    pass


@dataclass
class TurnWriteFence:
    turn_id: str
    generation: int
    worker_id: str
    revoked: bool = False

    def revoke(self) -> None:
        self.revoked = True

    def require_valid(self, turn_id: str | None = None) -> None:
        if self.revoked or (turn_id is not None and turn_id != self.turn_id):
            raise LostTurnClaimError(self.turn_id)


_CURRENT_FENCE: ContextVar[TurnWriteFence | None] = ContextVar("turn_write_fence", default=None)


def current_turn_write_fence() -> TurnWriteFence | None:
    return _CURRENT_FENCE.get()


@contextmanager
def bind_turn_write_fence(fence: TurnWriteFence) -> Iterator[None]:
    token = _CURRENT_FENCE.set(fence)
    try:
        yield
    finally:
        _CURRENT_FENCE.reset(token)
