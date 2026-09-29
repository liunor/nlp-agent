from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest


def test_adaptive_pool_target_is_bounded_and_uses_refill_p95() -> None:
    from server.sandbox.optimization import AdaptivePoolPolicy

    policy = AdaptivePoolPolicy(ready_min=1, ready_max=5, burst_buffer=1)
    assert policy.target_for(arrival_rate_per_min=30, refill_p95_s=4) == 3
    assert policy.target_for(arrival_rate_per_min=10_000, refill_p95_s=60) == 5


def test_adaptive_pool_target_covers_unassigned_online_leases() -> None:
    from server.sandbox.optimization import AdaptivePoolPolicy

    policy = AdaptivePoolPolicy(ready_min=1, ready_max=3, burst_buffer=1)
    assert policy.target_for(
        arrival_rate_per_min=0,
        refill_p95_s=4,
        unassigned_lease_count=2,
    ) == 3
    assert policy.target_for(
        arrival_rate_per_min=0,
        refill_p95_s=4,
        unassigned_lease_count=50,
    ) == 3


def test_adaptive_pool_target_uses_a_recent_demand_window() -> None:
    from server.sandbox.optimization import AdaptivePoolPolicy

    policy = AdaptivePoolPolicy(ready_min=1, ready_max=5, burst_buffer=1)
    samples = [
        {"arrival_rate_per_min": 12, "refill_p95_s": 10, "unassigned_count": 1},
        {"arrival_rate_per_min": 0, "refill_p95_s": 1, "unassigned_count": 0},
        {"arrival_rate_per_min": 0, "refill_p95_s": 1, "unassigned_count": 0},
    ]

    assert policy.target_for(
        arrival_rate_per_min=0,
        refill_p95_s=1,
        unassigned_lease_count=0,
    ) == 1
    assert policy.target_for_samples(samples) == 3
    assert policy.target_for_samples(
        [{"unassigned_count": 0}],
        fallback_arrival_rate_per_min=30,
        fallback_refill_p95_s=4,
    ) == 3


def test_sandbox_claim_priority_is_developer_teacher_student_guest() -> None:
    from server.sandbox.optimization import should_defer_claim

    assert should_defer_claim(
        current_role_codes={"student"},
        waiting_role_codes=(frozenset({"teacher"}),),
    ) is True
    assert should_defer_claim(
        current_role_codes={"guest"},
        waiting_role_codes=(frozenset({"student"}), frozenset({"teacher"})),
    ) is True
    assert should_defer_claim(
        current_role_codes={"developer"},
        waiting_role_codes=(frozenset({"teacher"}), frozenset({"guest"})),
    ) is False


def test_refill_count_respects_the_global_runtime_cap() -> None:
    from server.sandbox.optimization import refill_count

    assert refill_count(target=3, ready_count=1, creating_count=0, total_count=1, total_max=4) == 2
    assert refill_count(target=3, ready_count=1, creating_count=0, total_count=3, total_max=4) == 1
    assert refill_count(target=3, ready_count=1, creating_count=0, total_count=4, total_max=4) == 0


def test_host_resource_guard_blocks_creation_below_memory_or_disk_reserve() -> None:
    from server.sandbox.optimization import host_capacity_allows_create

    assert host_capacity_allows_create(
        total_count=1,
        total_max=4,
        available_memory_mb=4096,
        memory_reserve_mb=3072,
        runtime_memory_mb=768,
        disk_free_gb=20,
        disk_reserve_gb=15,
    ) is True
    assert host_capacity_allows_create(
        total_count=1,
        total_max=4,
        available_memory_mb=3700,
        memory_reserve_mb=3072,
        runtime_memory_mb=768,
        disk_free_gb=20,
        disk_reserve_gb=15,
    ) is False
    assert host_capacity_allows_create(
        total_count=1,
        total_max=4,
        available_memory_mb=4096,
        memory_reserve_mb=3072,
        runtime_memory_mb=768,
        disk_free_gb=14.9,
        disk_reserve_gb=15,
    ) is False


def test_class_forecast_and_cooldown_are_deterministic() -> None:
    from server.sandbox.optimization import AdaptivePoolPolicy

    policy = AdaptivePoolPolicy(ready_min=1, ready_max=10, burst_buffer=2, cooldown=timedelta(seconds=30))
    assert policy.target_before_class(expected_sessions=8, sessions_per_runtime=2) == 6
    stamp = datetime(2026, 1, 1, tzinfo=UTC)
    assert not policy.should_scale(current_target=1, desired_target=3, last_scaled_at=stamp, now=stamp + timedelta(seconds=1))
    assert policy.should_scale(current_target=1, desired_target=3, last_scaled_at=stamp, now=stamp + timedelta(seconds=30))


def test_preload_matrix_marks_missing_dependency_incompatible() -> None:
    from server.sandbox.optimization import PreloadCompatibility, check_preload_compatibility

    row = check_preload_compatibility(
        PreloadCompatibility("python-base", "python@sha256:x", "3.12", "nova-1", ("numpy", "pandas")),
        python_version="3.12", runtime_version="nova-1", available_modules=("numpy",),
    )
    assert row.status == "incompatible"
    assert "pandas" in row.notes


def test_fault_injection_is_opt_in_and_named() -> None:
    from server.sandbox.faults import SandboxFaultInjector, SandboxInjectedFault

    SandboxFaultInjector.from_env("").fail_if_configured("docker.create")
    with pytest.raises(SandboxInjectedFault):
        SandboxFaultInjector.from_env("docker.create").fail_if_configured("docker.create")


def test_preload_matrix_is_operator_visible() -> None:
    from pathlib import Path

    from server.sandbox.optimization import load_preload_matrix

    matrix = load_preload_matrix(Path("configs/sandbox_preload_matrix.json"))
    assert matrix["available"] is True
    assert "python-base" in matrix["profiles"]


def test_startup_benchmark_reports_stage_percentiles_and_updates_matrix(tmp_path) -> None:
    import json

    from scripts.benchmark_sandbox_startup import percentile, update_preload_matrix
    from server.sandbox.optimization import PreloadCompatibility

    assert percentile([10.0, 20.0, 30.0], 0.50) == 20.0
    assert percentile([10.0, 20.0, 30.0], 0.95) == 29.0
    path = tmp_path / "matrix.json"
    path.write_text('{"version": 1, "profiles": {}}', encoding="utf-8")
    update_preload_matrix(
        path,
        PreloadCompatibility(
            "python-base", "image@sha256:x", "3.11", "nova-runtime", ("numpy",), status="compatible"
        ),
        {
            "iterations": [{"stages": {"create_ms": 10.0}}],
            "image_cached": True,
            "stage_percentiles_ms": {"create_ms": {"p50": 10.0, "p95": 10.0}},
        },
    )
    row = json.loads(path.read_text(encoding="utf-8"))["profiles"]["python-base"]
    assert row["status"] == "compatible"
    assert row["benchmark"]["iterations"] == 1
    assert row["benchmark"]["stage_percentiles_ms"]["create_ms"]["p95"] == 10.0


@pytest.mark.asyncio
async def test_adaptive_state_store_persists_target_and_cooldown() -> None:
    from server.sandbox.metrics import RedisSandboxAdaptiveStateStore

    class FakeRedis:
        def __init__(self) -> None:
            self.values: dict[str, object] = {}

        async def hgetall(self, _key: str) -> dict[str, object]:
            return self.values

        async def hset(self, _key: str, *, mapping: dict[str, object]) -> None:
            self.values.update(mapping)

    store = RedisSandboxAdaptiveStateStore(FakeRedis())
    assert await store.load() == (None, None)
    await store.save(target=4, scaled_at=12.5)
    assert await store.load() == (4, 12.5)
