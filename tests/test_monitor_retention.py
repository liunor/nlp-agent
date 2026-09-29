import asyncio

import pytest

from server.monitor.retention import run_monitor_retention, run_retention_once


class RetentionRepository:
    def __init__(self):
        self.calls: list[tuple[int, int]] = []

    def prune(self, trace_days: int, event_days: int):
        self.calls.append((trace_days, event_days))
        return {"traces": 2, "spans": 4, "events": 3, "daily_metrics": 0}


@pytest.mark.asyncio
async def test_retention_once_prunes_using_the_configured_windows():
    repository = RetentionRepository()

    result = await run_retention_once(repository, trace_days=45, event_days=14)

    assert repository.calls == [(45, 14)]
    assert result["events"] == 3


@pytest.mark.asyncio
async def test_monthly_retention_waits_before_first_cleanup_and_repeats(monkeypatch):
    repository = RetentionRepository()
    sleeps: list[float] = []

    async def fake_sleep(delay: float):
        sleeps.append(delay)
        if len(sleeps) == 2:
            raise asyncio.CancelledError

    monkeypatch.setattr("server.monitor.retention.asyncio.sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        await run_monitor_retention(
            repository,
            trace_days=31,
            event_days=31,
            initial_delay_s=7,
            interval_s=30,
        )

    assert sleeps == [7, 30]
    assert repository.calls == [(31, 31)]


@pytest.mark.asyncio
async def test_monthly_retention_can_prune_audit_logs_with_a_longer_policy(monkeypatch):
    repository = RetentionRepository()
    sleeps: list[float] = []
    audit_calls: list[int] = []

    async def fake_sleep(delay: float):
        sleeps.append(delay)
        raise asyncio.CancelledError

    async def cleanup_audit():
        audit_calls.append(180)
        return {"audit_logs": 4}

    monkeypatch.setattr("server.monitor.retention.asyncio.sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        await run_monitor_retention(
            repository,
            trace_days=30,
            event_days=30,
            initial_delay_s=0,
            interval_s=30,
            audit_cleanup=cleanup_audit,
        )

    assert audit_calls == [180]
    assert repository.calls == [(30, 30)]
    assert sleeps == [30]
