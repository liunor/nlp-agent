from __future__ import annotations

import asyncio
from datetime import UTC, datetime


def test_sandbox_lease_demand_counts_online_waiters_once_at_highest_role() -> None:
    from server.sandbox.metrics import summarize_sandbox_lease_demand

    demand = summarize_sandbox_lease_demand(
        lease_rows=(
            (frozenset({"developer", "student"}), False),
            (frozenset({"teacher"}), False),
            (frozenset({"student"}), True),
            (frozenset({"guest"}), False),
        )
    )

    assert demand.online_count == 4
    assert demand.unassigned_count == 3
    assert demand.role_demand == {"developer": 1, "teacher": 1, "guest": 1}


def test_aggregate_sandbox_capacity_samples_keeps_latest_real_point_per_bucket() -> None:
    from server.sandbox.metrics import aggregate_sandbox_capacity_samples

    samples = [
        {"timestamp": 100, "ready": 1},
        {"timestamp": 239, "ready": 2},
        {"timestamp": 300, "ready": 3},
        {"timestamp": 359, "ready": 4},
        {"timestamp": 420, "ready": 5},
        {"timestamp": 500, "ready": 6},
    ]

    result = aggregate_sandbox_capacity_samples(
        samples,
        now=500,
        window_seconds=300,
        bucket_seconds=60,
        max_points=60,
    )

    assert [(row["timestamp"], row["ready"]) for row in result] == [
        (239.0, 2),
        (359.0, 4),
        (420.0, 5),
        (500.0, 6),
    ]


def test_aggregate_sandbox_capacity_samples_rejects_invalid_window() -> None:
    import pytest

    from server.sandbox.metrics import aggregate_sandbox_capacity_samples

    with pytest.raises(ValueError):
        aggregate_sandbox_capacity_samples([], window_seconds=0)
    with pytest.raises(ValueError):
        aggregate_sandbox_capacity_samples([], bucket_seconds=0)


def test_collect_sandbox_lease_demand_resolves_active_roles() -> None:
    from server.sandbox.metrics import collect_sandbox_lease_demand

    class Result:
        def __init__(self, rows):
            self._rows = rows

        def all(self):
            return self._rows

    class Session:
        calls = 0

        async def execute(self, _query):
            self.calls += 1
            if self.calls == 1:
                return Result([("developer-user", None), ("student-user", "runtime-1")])
            return Result([("developer-user", "developer"), ("student-user", "student")])

    demand = asyncio.run(
        collect_sandbox_lease_demand(
            Session(),
            now=datetime(2026, 1, 1, tzinfo=UTC),
        )
    )

    assert demand.online_count == 2
    assert demand.unassigned_count == 1
    assert demand.role_demand == {"developer": 1}
    assert demand.active_session_count == 2


def test_collect_sandbox_lease_demand_counts_distinct_users_and_sessions() -> None:
    from server.sandbox.metrics import collect_sandbox_lease_demand

    class Result:
        def __init__(self, rows):
            self._rows = rows

        def all(self):
            return self._rows

    class Session:
        calls = 0

        async def execute(self, _query):
            self.calls += 1
            if self.calls == 1:
                return Result([
                    ("developer-user", None),
                    ("developer-user", "runtime-1"),
                    ("student-user", None),
                ])
            return Result([
                ("developer-user", "developer"),
                ("student-user", "student"),
            ])

    demand = asyncio.run(
        collect_sandbox_lease_demand(
            Session(),
            now=datetime(2026, 1, 1, tzinfo=UTC),
        )
    )

    assert demand.online_count == 2
    assert demand.active_session_count == 3
    assert demand.unassigned_count == 1
    assert demand.role_demand == {"student": 1}


def test_arrival_rate_uses_new_sandbox_leases_not_code_execution_count() -> None:
    from server.sandbox.metrics import sandbox_arrival_rate_per_min

    assert sandbox_arrival_rate_per_min(new_lease_count=15, window_seconds=300) == 3.0
    assert sandbox_arrival_rate_per_min(new_lease_count=0, window_seconds=300) == 0.0


def test_capacity_sample_contains_online_waiters_and_role_demand() -> None:
    from server.sandbox.metrics import record_sandbox_capacity_sample

    class ScalarResult:
        def __init__(self, value):
            self.value = value

        def all(self):
            return self.value

    class Session:
        async def scalars(self, _query):
            return ScalarResult([])

        async def scalar(self, _query):
            return 0

        async def execute(self, query):
            if "nlp_sandbox_leases" in str(query):
                return ScalarResult([("developer-user", None), ("guest-user", "runtime-1")])
            return ScalarResult([("developer-user", "developer"), ("guest-user", "guest")])

    class Factory:
        def __call__(self):
            class Context:
                async def __aenter__(self):
                    return Session()

                async def __aexit__(self, *_args):
                    return False

            return Context()

    class Store:
        def __init__(self):
            self.sample = None

        async def record(self, sample):
            self.sample = sample

    store = Store()
    asyncio.run(record_sandbox_capacity_sample(Factory(), store=store))

    assert store.sample["online_count"] == 2
    assert store.sample["unassigned_count"] == 1
    assert store.sample["role_demand"] == {"developer": 1}


def test_redis_metrics_store_keeps_bounded_history() -> None:
    from server.sandbox.metrics import RedisSandboxMetricsStore

    class FakeRedis:
        def __init__(self):
            self.rows: list[tuple[float, str]] = []

        async def zadd(self, _key, values):
            self.rows.extend((score, member) for member, score in values.items())

        async def zremrangebyscore(self, _key, _minimum, maximum):
            self.rows[:] = [(score, member) for score, member in self.rows if score > maximum]

        async def expire(self, _key, _seconds):
            return True

        async def zrange(self, _key, start, end):
            values = [member for _, member in sorted(self.rows)]
            return values[start: None if end == -1 else end + 1]

    async def exercise():
        store = RedisSandboxMetricsStore(FakeRedis())
        await store.record({"timestamp": 1.0, "ready": 1})
        history = await store.record({"timestamp": 2.0, "ready": 2})
        latest = await store.latest()
        return history, latest

    history, latest = asyncio.run(exercise())
    assert [item["ready"] for item in history] == [1, 2]
    assert latest == {"timestamp": 2.0, "ready": 2}


def test_redis_metrics_store_can_read_history_without_recording_a_sample() -> None:
    from server.sandbox.metrics import RedisSandboxMetricsStore

    class ReadOnlyFakeRedis:
        async def zrange(self, _key, start, end):
            assert (start, end) == (-60, -1)
            return ['{"timestamp":1.0,"ready":1}']

    history = asyncio.run(RedisSandboxMetricsStore(ReadOnlyFakeRedis()).recent())

    assert history == [{"timestamp": 1.0, "ready": 1}]
