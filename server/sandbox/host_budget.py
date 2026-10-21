"""Cross-Compose host budget coordination for Sandbox Manager instances."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import os
from typing import AsyncIterator

try:  # pragma: no cover - Windows development has no fcntl implementation.
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]


@asynccontextmanager
async def host_budget_lock(path: str) -> AsyncIterator[None]:
    """Hold a host-shared advisory lock while reserving Docker capacity.

    Production Compose mounts the same host directory into test and production
    Manager containers.  An empty path deliberately disables the lock for
    local single-process development; when configured on POSIX, an inability
    to create or lock the file fails closed instead of allowing an unsafe
    refill race.
    """

    lock_path = path.strip()
    if not lock_path or fcntl is None:
        yield
        return

    parent = os.path.dirname(lock_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o660)
    try:
        # A blocking worker can acquire the lock after its awaiter was
        # cancelled and closed this descriptor. Poll without a worker so
        # cancellation never leaves a future, unowned lock acquisition.
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                await asyncio.sleep(0.05)
        try:
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)
