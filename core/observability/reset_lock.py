"""Cross-process fence shared by runtime writers and the monitor reset."""

from __future__ import annotations

from contextlib import contextmanager
from functools import wraps
from typing import Any, Callable, Iterator, TypeVar

from sqlalchemy import text


RUNTIME_RESET_LOCK_NAME = "nlp_agent_monitor_runtime_reset"
RUNTIME_WRITE_LOCK_TIMEOUT_S = 10
F = TypeVar("F", bound=Callable[..., Any])


@contextmanager
def runtime_reset_lock(
    engine: Any, *, enabled: bool = True, timeout_s: int = 10
) -> Iterator[None]:
    """Hold a MySQL named lock for one reset transaction boundary.

    The lock is session-scoped and therefore works across Gateway and Monitor
    processes. A normal writer waits for a bounded interval while reset owns
    the fence; a reset likewise waits for an active writer to finish.
    """
    if not enabled:
        yield
        return
    with engine.connect() as connection:
        acquired = int(
            connection.scalar(
                text("SELECT GET_LOCK(:lock_name, :timeout_s)"),
                {"lock_name": RUNTIME_RESET_LOCK_NAME, "timeout_s": max(0, timeout_s)},
            )
            or 0
        )
        if acquired != 1:
            raise RuntimeError("runtime reset lock could not be acquired")
        # GET_LOCK is a SELECT, so SQLAlchemy opens an implicit transaction
        # even though the named lock itself is session-scoped.  Close that
        # transaction before the caller performs the destructive work; the
        # lock remains held by this connection until RELEASE_LOCK below.
        connection.commit()
        try:
            yield
        finally:
            connection.execute(
                text("SELECT RELEASE_LOCK(:lock_name)"),
                {"lock_name": RUNTIME_RESET_LOCK_NAME},
            )
            connection.commit()


@contextmanager
def runtime_write_transaction(
    engine: Any,
    *,
    enabled: bool = True,
    timeout_s: int = RUNTIME_WRITE_LOCK_TIMEOUT_S,
) -> Iterator[Any]:
    """Run one mutation and its reset fence on the same DB connection.

    Writers wait briefly instead of failing on ordinary write/write contention
    or while the monitor performs a bounded reset. This keeps the lock as a
    backpressure boundary rather than turning a transient collision into a
    partially persisted Turn.
    """
    if not enabled:
        with engine.begin() as connection:
            yield connection
        return
    with engine.connect() as connection:
        acquired = int(
            connection.scalar(
                text("SELECT GET_LOCK(:lock_name, :timeout_s)"),
                {"lock_name": RUNTIME_RESET_LOCK_NAME, "timeout_s": max(0, timeout_s)},
            )
            or 0
        )
        # SQLAlchemy starts an implicit transaction for the GET_LOCK SELECT;
        # commit it before opening the mutation transaction. GET_LOCK itself
        # is session-scoped and survives that commit.
        connection.commit()
        if acquired != 1:
            raise RuntimeError("runtime reset is in progress; retry the operation")
        try:
            with connection.begin():
                yield connection
        finally:
            connection.execute(
                text("SELECT RELEASE_LOCK(:lock_name)"),
                {"lock_name": RUNTIME_RESET_LOCK_NAME},
            )
            connection.commit()


def runtime_write_guard(function: F) -> F:
    """Fence a repository mutation without changing test doubles' contract."""
    @wraps(function)
    def guarded(self, *args: Any, **kwargs: Any):
        if not getattr(self, "_runtime_lock_enabled", False):
            return function(self, *args, **kwargs)
        with runtime_reset_lock(
            self._engine,
            enabled=True,
            timeout_s=RUNTIME_WRITE_LOCK_TIMEOUT_S,
        ):
            return function(self, *args, **kwargs)

    return guarded  # type: ignore[return-value]
