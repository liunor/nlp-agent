"""Production lifecycle for releasing abandoned quota Reservations."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from utils.logger import get_logger


logger = get_logger("nlp_agent.quota.reaper")


class QuotaReservationReaper:
    """Run the database-backed Reservation expiry sweep for a process."""

    def __init__(
        self,
        quota_service: Any,
        *,
        interval_seconds: float = 30.0,
        operations_service: Any | None = None,
        operations_interval_seconds: float = 3_600.0,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        if operations_interval_seconds <= 0:
            raise ValueError("operations_interval_seconds must be positive")
        self._quota_service = quota_service
        self._interval_seconds = interval_seconds
        self._operations_service = operations_service
        self._operations_interval_seconds = operations_interval_seconds
        self._task: asyncio.Task[None] | None = None

    @property
    def task(self) -> asyncio.Task[None] | None:
        return self._task

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(
                self._run(),
                name="quota-reservation-reaper",
            )

    async def stop(self) -> None:
        task = self._task
        if task is None:
            return
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        self._task = None

    async def _run(self) -> None:
        last_operations_at: datetime | None = None
        while True:
            try:
                now = datetime.now(timezone.utc)
                await asyncio.to_thread(self._quota_service.expire_reservations)
                expire_grants = getattr(self._quota_service, "expire_grants", None)
                if expire_grants is not None:
                    await asyncio.to_thread(
                        expire_grants,
                        now=now,
                    )
                if self._operations_service is not None and (
                    last_operations_at is None
                    or (now - last_operations_at).total_seconds()
                    >= self._operations_interval_seconds
                ):
                    await asyncio.to_thread(
                        self._operations_service.run_maintenance,
                        now=now,
                    )
                    last_operations_at = now
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("quota reservation expiry pass failed")
            await asyncio.sleep(self._interval_seconds)
