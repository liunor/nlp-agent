import asyncio

import pytest

from server.quota.reaper import QuotaReservationReaper
from server.worker.runtime import create_worker_quota_reaper


@pytest.mark.asyncio
async def test_reaper_runs_expiry_and_stops_cleanly():
    class Service:
        def __init__(self) -> None:
            self.calls = 0

        def expire_reservations(self) -> int:
            self.calls += 1
            return 1

    service = Service()
    reaper = QuotaReservationReaper(service, interval_seconds=0.01)
    reaper.start()
    await asyncio.sleep(0.06)
    await reaper.stop()

    assert service.calls >= 1
    assert reaper.task is None


@pytest.mark.asyncio
async def test_reaper_runs_phase4_rollup_and_alert_maintenance():
    class Service:
        def expire_reservations(self) -> int:
            return 0

    class Operations:
        def __init__(self) -> None:
            self.calls = 0

        def run_maintenance(self, **_: object) -> dict[str, int]:
            self.calls += 1
            return {"rollup_rows": 1, "alerts_created": 0}

    operations = Operations()
    reaper = QuotaReservationReaper(
        Service(),
        interval_seconds=0.01,
        operations_service=operations,
        operations_interval_seconds=0.01,
    )
    reaper.start()
    await asyncio.sleep(0.06)
    await reaper.stop()

    assert operations.calls >= 1


def test_worker_reaper_factory_injects_phase4_operations_service():
    class QuotaService:
        engine = object()

        def expire_reservations(self) -> int:
            return 0

    class Repository:
        quota_service = QuotaService()

    reaper = create_worker_quota_reaper(
        Repository(),
        {"quota_reap_interval_s": 5, "quota_operations_interval_s": 60},
    )

    assert reaper is not None
    assert reaper._operations_service is not None
    assert reaper._interval_seconds == 5
    assert reaper._operations_interval_seconds == 60
