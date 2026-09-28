"""Deterministic Phase 4 capacity and preload planning primitives.

The manager owns Docker I/O; this module only turns observed arrival/latency
signals and a declared compatibility matrix into bounded, auditable decisions.
That makes scale-up behaviour testable on every platform, including Windows
where gVisor itself is unavailable.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
import json
from math import ceil, isfinite
from pathlib import Path
from typing import Iterable


SANDBOX_ROLE_PRIORITY: dict[str, int] = {
    "developer": 0,
    "teacher": 1,
    "student": 2,
    "guest": 3,
}


def _role_priority(role_codes: Iterable[str]) -> int:
    ranks = [SANDBOX_ROLE_PRIORITY.get(str(role).strip().lower(), SANDBOX_ROLE_PRIORITY["guest"]) for role in role_codes]
    return min(ranks, default=SANDBOX_ROLE_PRIORITY["guest"])


def should_defer_claim(
    *,
    current_role_codes: Iterable[str],
    waiting_role_codes: Iterable[Iterable[str]],
) -> bool:
    """Return whether a lower-priority claim should yield to a waiting one."""
    current_priority = _role_priority(current_role_codes)
    return any(
        _role_priority(waiting_roles) < current_priority
        for waiting_roles in waiting_role_codes
    )


def refill_count(
    *,
    target: int,
    ready_count: int,
    creating_count: int,
    total_count: int | None = None,
    total_max: int | None = None,
) -> int:
    """Return the number of new runtimes allowed by both pool and host caps.

    ``target`` describes desired *ready* capacity while ``total_max`` protects
    the host from unbounded assigned/creating/ready growth.  Keeping this
    arithmetic pure gives the Manager one auditable capacity gate.
    """
    if min(target, ready_count, creating_count) < 0:
        raise ValueError("capacity counts must be non-negative")
    if (total_count is None) != (total_max is None):
        raise ValueError("total_count and total_max must be provided together")
    desired = max(0, target - ready_count - creating_count)
    if total_count is None or total_max is None:
        return desired
    if total_count < 0 or total_max < 0:
        raise ValueError("capacity totals must be non-negative")
    return min(desired, max(0, total_max - total_count))


def host_capacity_allows_create(
    *,
    total_count: int,
    total_max: int,
    available_memory_mb: float,
    memory_reserve_mb: int,
    runtime_memory_mb: int,
    disk_free_gb: float,
    disk_reserve_gb: int,
) -> bool:
    """Check the host safety gate before starting one more Runtime."""
    if min(total_count, total_max, memory_reserve_mb, runtime_memory_mb, disk_reserve_gb) < 0:
        raise ValueError("resource limits must be non-negative")
    return bool(
        total_count < total_max
        and available_memory_mb >= memory_reserve_mb + runtime_memory_mb
        and disk_free_gb >= disk_reserve_gb
    )


def _percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


@dataclass(frozen=True, slots=True)
class AdaptivePoolPolicy:
    ready_min: int = 1
    ready_max: int = 5
    burst_buffer: int = 1
    cooldown: timedelta = timedelta(seconds=30)

    def __post_init__(self) -> None:
        if self.ready_min < 0 or self.ready_max < self.ready_min:
            raise ValueError("ready_min/max must form a non-negative range")
        if self.burst_buffer < 0:
            raise ValueError("burst_buffer must be non-negative")
        if self.cooldown < timedelta(0):
            raise ValueError("cooldown must be non-negative")

    def target_for(
        self,
        *,
        arrival_rate_per_min: float,
        refill_p95_s: float,
        unassigned_lease_count: int = 0,
    ) -> int:
        """Estimate ready slots from refill latency and waiting online leases."""
        if arrival_rate_per_min < 0 or refill_p95_s < 0 or unassigned_lease_count < 0:
            raise ValueError("arrival rate, refill latency, and unassigned leases must be non-negative")
        forecast_required = ceil(arrival_rate_per_min * refill_p95_s / 60.0) + self.burst_buffer
        online_required = unassigned_lease_count + self.burst_buffer
        required = max(forecast_required, online_required)
        return min(self.ready_max, max(self.ready_min, required))

    def target_for_samples(
        self,
        samples: Iterable[dict[str, object]],
        *,
        fallback_arrival_rate_per_min: float = 0.0,
        fallback_refill_p95_s: float = 0.0,
    ) -> int:
        """Choose a bounded target from a recent, noisy demand window.

        The Manager samples capacity independently of dashboard traffic.  A
        single latest sample is too easy to under-read just after a burst, so
        use the rolling P95 of arrival/refill signals and the maximum number
        of currently waiting leases.  Invalid values are ignored and an empty
        window safely falls back to the policy minimum.
        """

        def values(name: str) -> list[float]:
            result: list[float] = []
            for sample in samples_list:
                raw = sample.get(name)
                if raw is None:
                    continue
                try:
                    value = float(raw or 0)
                except (TypeError, ValueError):
                    continue
                if isfinite(value):
                    result.append(max(0.0, value))
            return result

        samples_list = [sample for sample in samples if isinstance(sample, dict)]
        if not samples_list:
            return self.ready_min
        arrival_values = values("arrival_rate_per_min")
        refill_values = values("refill_p95_s")
        unassigned_values = values("unassigned_count")
        return self.target_for(
            arrival_rate_per_min=(
                _percentile(arrival_values, 0.95)
                if arrival_values
                else max(0.0, fallback_arrival_rate_per_min)
            ),
            refill_p95_s=(
                _percentile(refill_values, 0.95)
                if refill_values
                else max(0.0, fallback_refill_p95_s)
            ),
            unassigned_lease_count=int(max(unassigned_values, default=0)),
        )

    def target_before_class(self, *, expected_sessions: int, sessions_per_runtime: int = 1) -> int:
        """Reserve capacity for a scheduled class without exceeding hard bounds."""
        if expected_sessions < 0 or sessions_per_runtime < 1:
            raise ValueError("scheduled sessions must be non-negative")
        required = ceil(expected_sessions / sessions_per_runtime) + self.burst_buffer
        return min(self.ready_max, max(self.ready_min, required))

    def should_scale(
        self,
        *,
        current_target: int,
        desired_target: int,
        last_scaled_at: datetime | None,
        now: datetime | None = None,
    ) -> bool:
        """Apply a cooldown only to changes; a target already in range is stable."""
        if current_target == desired_target:
            return False
        if last_scaled_at is None:
            return True
        current = now or datetime.now(UTC)
        stamp = last_scaled_at.replace(tzinfo=UTC) if last_scaled_at.tzinfo is None else last_scaled_at
        return current - stamp >= self.cooldown


@dataclass(frozen=True, slots=True)
class PreloadCompatibility:
    profile_id: str
    image_digest: str
    python_version: str
    runtime_version: str
    modules: tuple[str, ...]
    status: str = "unknown"
    notes: str = ""

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def check_preload_compatibility(
    profile: PreloadCompatibility,
    *,
    python_version: str,
    runtime_version: str,
    available_modules: Iterable[str],
) -> PreloadCompatibility:
    """Return a matrix row marked compatible only when every requirement matches."""
    missing = sorted(set(profile.modules).difference(available_modules))
    reasons: list[str] = []
    if profile.python_version != python_version:
        reasons.append(f"python {python_version} != {profile.python_version}")
    if profile.runtime_version != runtime_version:
        reasons.append(f"runtime {runtime_version} != {profile.runtime_version}")
    if missing:
        reasons.append("missing modules: " + ", ".join(missing))
    return PreloadCompatibility(
        **{
            **profile.as_dict(),
            "status": "compatible" if not reasons else "incompatible",
            "notes": "; ".join(reasons),
        }
    )


def load_preload_matrix(path: Path) -> dict[str, object]:
    """Load the operator-maintained compatibility matrix without executing it."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {"profiles": {}, "source": str(path), "available": False}
    if not isinstance(payload, dict):
        return {"profiles": {}, "source": str(path), "available": False}
    profiles = payload.get("profiles", {})
    return {
        "profiles": profiles if isinstance(profiles, dict) else {},
        "source": str(path),
        "available": True,
    }
