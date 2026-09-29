from __future__ import annotations

RUNTIME_STATES = ("creating", "ready_unbound", "claiming", "assigned", "draining", "failed")


def summarize_runtime_states(rows: list[tuple[str, int]]) -> dict[str, int]:
    counts = {state: 0 for state in RUNTIME_STATES}
    for state, count in rows:
        if state in counts:
            counts[state] = count
    return counts


def capacity_snapshot(states: dict[str, int], *, target: int) -> dict[str, int]:
    ready = states.get("ready_unbound", 0)
    creating = states.get("creating", 0)
    target = max(0, target)
    return {"ready": ready, "creating": creating, "target": target, "deficit": max(0, target - ready - creating)}


def capacity_alerts(
    *,
    deficit: int,
    failed_runtime_count: int,
    unassigned_count: int,
) -> list[dict[str, str]]:
    """Build bounded operator alerts from capacity facts, including waiters."""
    alerts: list[dict[str, str]] = []
    if deficit > 0:
        alerts.append({"code": "pool_deficit", "severity": "warning", "message": "预热池容量低于目标。"})
    if unassigned_count > 0:
        alerts.append(
            {
                "code": "lease_waiting",
                "severity": "warning",
                "message": f"{unassigned_count} 个在线租约正在等待沙箱运行时。",
            }
        )
    if failed_runtime_count > 0:
        alerts.append({"code": "runtime_failed", "severity": "critical", "message": "存在需要重新协调的沙箱运行时。"})
    return alerts


def percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int((len(ordered) - 1) * quantile)))
    return round(ordered[index], 2)


def summarize_execution_latency(durations_ms: list[float]) -> dict[str, float | None | int]:
    return {
        "sample_count": len(durations_ms),
        "p50_ms": percentile(durations_ms, 0.50),
        "p95_ms": percentile(durations_ms, 0.95),
        "p99_ms": percentile(durations_ms, 0.99),
    }
