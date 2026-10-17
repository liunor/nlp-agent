"""Queue pools that expose connection-acquisition latency and timeouts."""

from __future__ import annotations

from threading import Lock
from time import perf_counter
from typing import Any

from sqlalchemy import exc
from sqlalchemy.pool import AsyncAdaptedQueuePool, QueuePool


_LATENCY_BUCKETS_MS = (1, 5, 10, 25, 50, 100, 250, 500, 1000, 5000)


class _CheckoutMetrics:
    def __init__(self) -> None:
        self._lock = Lock()
        self._count = 0
        self._timeouts = 0
        self._pending = 0
        self._sum_ms = 0.0
        self._max_ms = 0.0
        self._bucket_counts = {bucket: 0 for bucket in _LATENCY_BUCKETS_MS}

    def begin(self) -> None:
        with self._lock:
            self._pending += 1

    def record(self, elapsed_ms: float, *, timed_out: bool) -> None:
        with self._lock:
            self._pending = max(0, self._pending - 1)
            self._count += 1
            self._sum_ms += elapsed_ms
            self._max_ms = max(self._max_ms, elapsed_ms)
            if timed_out:
                self._timeouts += 1
            for bucket in _LATENCY_BUCKETS_MS:
                if elapsed_ms <= bucket:
                    self._bucket_counts[bucket] += 1

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "count": self._count,
                "pending": self._pending,
                "sum_ms": round(self._sum_ms, 3),
                "max_ms": round(self._max_ms, 3),
                "timeouts": self._timeouts,
                "buckets_le_ms": {
                    str(bucket): self._bucket_counts[bucket]
                    for bucket in _LATENCY_BUCKETS_MS
                },
                "buckets_gt_ms": {
                    str(_LATENCY_BUCKETS_MS[-1]): max(
                        0,
                        self._count
                        - self._bucket_counts[_LATENCY_BUCKETS_MS[-1]],
                    )
                },
            }


class _MeasuredCheckoutPool:
    """Measure connection acquisition, including queue waits/timeouts.

    SQLAlchemy's public ``Pool.connect`` boundary covers both queue waiting
    and first-connection creation, without instrumenting each DB call site.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._checkout_observations = _CheckoutMetrics()
        super().__init__(*args, **kwargs)

    def connect(self):
        self._checkout_observations.begin()
        started = perf_counter()
        timed_out = False
        try:
            return super().connect()
        except exc.TimeoutError:
            timed_out = True
            raise
        finally:
            self._checkout_observations.record(
                (perf_counter() - started) * 1000,
                timed_out=timed_out,
            )

    def checkout_metrics(self) -> dict[str, Any]:
        return self._checkout_observations.snapshot()


class MeasuredQueuePool(_MeasuredCheckoutPool, QueuePool):
    """Synchronous SQLAlchemy queue pool with checkout latency metrics."""


class MeasuredAsyncAdaptedQueuePool(_MeasuredCheckoutPool, AsyncAdaptedQueuePool):
    """Async SQLAlchemy queue pool with checkout latency metrics."""
