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
        await asyncio.to_thread(fcntl.flock, descriptor, fcntl.LOCK_EX)
        try:
            yield
        finally:
            await asyncio.to_thread(fcntl.flock, descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)
