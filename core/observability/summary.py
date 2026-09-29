"""Low-cardinality operational summaries built from persisted telemetry rows."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from math import ceil
from typing import Any, Iterable

from core.observability.traces import trace_chain_identity


LEGACY_TOKEN_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cached_tokens",
    "cache_miss_tokens",
    "reasoning_tokens",
    "total_tokens",
)

_FAILURE_STATUSES = {"error", "timeout", "cancelled", "denied"}
_ERROR_STATUSES = {"error", "timeout"}
# Summary endpoints are dashboards, not export endpoints. Keep every response
# bounded even when an installation has many providers, models, components, or
# event names. Dedicated paginated endpoints remain the source for full detail.
MAX_SUMMARY_DIMENSIONS = 100


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return _utc(value)
    if not value:
        return None
    try:
        return _utc(datetime.fromisoformat(str(value).replace("Z", "+00:00")))
    except (TypeError, ValueError):
        return None


def _in_period(row: dict[str, Any], since: datetime, *, event: bool = False) -> bool:
    keys = ("timestamp",) if event else ("completed_at", "started_at")
    timestamp = next((_parse_datetime(row.get(key)) for key in keys if row.get(key)), None)
    return timestamp is not None and timestamp >= since


def _usage_value(row: dict[str, Any], field: str) -> int:
    value = row.get(field)
    if value is None:
        value = (row.get("usage") or {}).get(field, 0)
    return int(value or 0)


def _ttft_value(row: dict[str, Any]) -> int | None:
    """Read the canonical span TTFT, with compatibility for legacy payloads."""
    value = row.get("ttft_ms")
    if value is None and isinstance(row.get("attributes"), dict):
        value = row["attributes"].get("ttft_ms")
    if value is None:
        return None
    try:
        value = int(value)
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


def _percentile(values: Iterable[int], percentile: float) -> int | None:
    ordered = sorted(values)
    if not ordered:
        return None
    # Use nearest-rank semantics for operational tail metrics. Interpolating
    # between two samples can make a rare slow request disappear from P90/P95/
    # P99, which is the opposite of what an incident dashboard needs.
    rank = max(1, ceil(len(ordered) * percentile))
    return ordered[min(len(ordered) - 1, rank - 1)]


def _dimension_counts(values: Iterable[Any]) -> list[dict[str, Any]]:
    counts = Counter(str(value or "unknown") for value in values)
    return [
        {"value": value, "requests": count}
        for value, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ][:MAX_SUMMARY_DIMENSIONS]


def _token_totals(rows: Iterable[dict[str, Any]]) -> dict[str, int]:
    return {
        field: sum(_usage_value(row, field) for row in rows)
        for field in LEGACY_TOKEN_FIELDS
    }


def _aggregate_rows(rows: list[dict[str, Any]], *, key_name: str, keys: tuple[str, ...]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, ...], dict[str, Any]] = {}
    for row in rows:
        key = tuple(str(row.get(item) or "unknown") for item in keys)
        item = groups.setdefault(
            key,
            {
                key_name: key[0] if len(key) == 1 else None,
                **{item: value for item, value in zip(keys, key)},
                "requests": 0,
                "successes": 0,
                "errors": 0,
                "failed_requests": 0,
                "retries": 0,
                "duration_sum_ms": 0,
                "total_tokens": 0,
            },
        )
        status = str(row.get("status") or "unknown")
        item["requests"] += 1
        item["successes"] += int(status == "ok")
        item["errors"] += int(status in _ERROR_STATUSES)
        item["failed_requests"] += int(status in _FAILURE_STATUSES)
        item["retries"] += int(int(row.get("attempt") or 1) > 1)
        item["duration_sum_ms"] += int(row.get("duration_ms") or 0)
        item["total_tokens"] += _usage_value(row, "total_tokens")

    result = []
    for item in groups.values():
        item["error_rate"] = item["errors"] / item["requests"] if item["requests"] else 0.0
        item["failure_rate"] = item["failed_requests"] / item["requests"] if item["requests"] else 0.0
        item["avg_duration_ms"] = round(item["duration_sum_ms"] / item["requests"]) if item["requests"] else 0
        item.pop("duration_sum_ms", None)
        result.append(item)
    return sorted(result, key=lambda item: (-item["requests"], tuple(str(item.get(key, "")) for key in keys)))


def _user_summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[str(row.get("user_id") or "unknown")].append(row)

    result = []
    for user_id, user_rows in groups.items():
        errors = sum(str(row.get("status") or "") in _ERROR_STATUSES for row in user_rows)
        requests = len(user_rows)
        timestamps = [
            _parse_datetime(row.get("completed_at") or row.get("started_at"))
            for row in user_rows
        ]
        last_seen = max((value for value in timestamps if value is not None), default=None)
        result.append(
            {
                "user_id": user_id,
                "requests": requests,
                "successes": sum(row.get("status") == "ok" for row in user_rows),
                "errors": errors,
                "error_rate": errors / requests if requests else 0.0,
                "total_tokens": sum(_usage_value(row, "total_tokens") for row in user_rows),
                "avg_duration_ms": round(
                    sum(int(row.get("duration_ms") or 0) for row in user_rows) / requests
                )
                if requests
                else 0,
                "workspaces": sorted({str(row.get("workspace_id") or "unknown") for row in user_rows}),
                "last_seen": last_seen.isoformat() if last_seen else None,
            }
        )
    return sorted(result, key=lambda item: (-item["requests"], item["user_id"]))


def _model_rows(spans: list[dict[str, Any]]) -> list[dict[str, Any]]:
    models: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for span in spans:
        if str(span.get("kind") or "") != "model":
            continue
        attributes = span.get("attributes") or {}
        provider = str(
            attributes.get("provider")
            or attributes.get("provider_name")
            or "unknown"
        )
        provider_model = str(
            attributes.get("provider_model")
            or attributes.get("model")
            or attributes.get("model_name")
            or "unknown"
        )
        profile = str(attributes.get("model_profile") or attributes.get("preset") or "unknown")
        models[(provider, provider_model, profile)].append(span)

    result = []
    for (provider, provider_model, profile), rows in models.items():
        errors = sum(str(row.get("status") or "") in _ERROR_STATUSES for row in rows)
        result.append(
            {
                "provider": provider,
                "provider_model": provider_model,
                "model_profile": profile,
                "requests": len(rows),
                "successes": sum(row.get("status") == "ok" for row in rows),
                "errors": errors,
                "error_rate": errors / len(rows) if rows else 0.0,
                "retries": sum(int(row.get("attempt") or 1) > 1 for row in rows),
                "total_tokens": sum(_usage_value(row, "total_tokens") for row in rows),
                "avg_duration_ms": round(
                    sum(int(row.get("duration_ms") or 0) for row in rows) / len(rows)
                )
                if rows
                else 0,
            }
        )
    return sorted(result, key=lambda item: (-item["requests"], item["provider_model"]))[:MAX_SUMMARY_DIMENSIONS]


def _error_groups(spans: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for span in spans:
        if str(span.get("status") or "") not in _FAILURE_STATUSES:
            continue
        key = (
            str(span.get("error_kind") or "unknown"),
            str(span.get("kind") or "unknown"),
            str(span.get("name") or "unknown"),
        )
        groups[key].append(span)

    result = []
    for (error_kind, kind, name), rows in groups.items():
        timestamps = [
            _parse_datetime(row.get("completed_at") or row.get("started_at"))
            for row in rows
        ]
        last_seen = max((value for value in timestamps if value is not None), default=None)
        result.append(
            {
                "error_kind": error_kind,
                "kind": kind,
                "name": name,
                "count": len(rows),
                "last_seen": last_seen.isoformat() if last_seen else None,
                "sample_trace_id": str(rows[0].get("trace_id") or ""),
            }
        )
    return sorted(
        result,
        key=lambda item: (item["count"], item["last_seen"] or ""),
        reverse=True,
    )[:MAX_SUMMARY_DIMENSIONS]


def _period_rows(rows: Iterable[dict[str, Any]], since: datetime) -> list[dict[str, Any]]:
    return [
        row
        for row in rows
        if (row.get("completed_at") or row.get("started_at"))
        and _in_period(row, since)
    ]


def _span_context_rows(
    traces: list[dict[str, Any]], spans: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Attach safe trace dimensions to spans without returning trace payloads."""
    traces_by_id = {str(row.get("trace_id")): row for row in traces if row.get("trace_id")}
    output: list[dict[str, Any]] = []
    for span in spans:
        trace = traces_by_id.get(str(span.get("trace_id") or ""), {})
        attributes = span.get("attributes") or {}
        if not isinstance(attributes, dict):
            attributes = {}
        trace_attributes = trace.get("attributes") or {}
        if not isinstance(trace_attributes, dict):
            trace_attributes = {}
        identity = trace_chain_identity(trace) if trace else {}
        provider = attributes.get("provider") or attributes.get("tool_provider")
        provider_model = (
            attributes.get("provider_model")
            or attributes.get("model")
            or attributes.get("model_name")
        )
        profile = attributes.get("model_profile") or attributes.get("preset")
        output.append(
            {
                **span,
                "_user_id": str(trace.get("user_id") or "unknown"),
                "_workspace_id": str(trace.get("workspace_id") or "unknown"),
                "_trace_id": str(span.get("trace_id") or ""),
                "_provider": str(provider) if provider else "unknown",
                "_provider_model": str(provider_model) if provider_model else "unknown",
                "_model_profile": str(profile) if profile else "unknown",
                "_chain_name": str(identity.get("chain_name") or trace_attributes.get("chain_name") or "unknown"),
                "_entrypoint": str(identity.get("entrypoint") or trace_attributes.get("entrypoint") or "unknown"),
            }
        )
    return output


def _health_dimensions(
    rows: Iterable[dict[str, Any]],
    *,
    keys: tuple[str, ...],
    dimension: str,
) -> list[dict[str, Any]]:
    groups: dict[tuple[str, ...], dict[str, Any]] = {}
    for row in rows:
        values = tuple(str(row.get(key) or "unknown") for key in keys)
        group = groups.setdefault(
            values,
            {
                "requests": 0,
                "successes": 0,
                "errors": 0,
                "failed_requests": 0,
                "retries": 0,
                "total_tokens": 0,
                "_durations": [],
                "_ttfts": [],
                "_users": set(),
                "_workspaces": set(),
                "_error_kinds": set(),
                "_first_seen": None,
                "_last_seen": None,
            },
        )
        status = str(row.get("status") or "unknown").lower()
        timestamp = _parse_datetime(row.get("completed_at") or row.get("started_at"))
        group["requests"] += 1
        group["successes"] += int(status == "ok")
        group["errors"] += int(status in _FAILURE_STATUSES or bool(row.get("error_kind")))
        group["failed_requests"] += int(status in _FAILURE_STATUSES)
        group["retries"] += int(int(row.get("attempt") or 1) > 1)
        group["total_tokens"] += _usage_value(row, "total_tokens")
        if row.get("duration_ms") is not None:
            group["_durations"].append(int(row.get("duration_ms") or 0))
        ttft_ms = _ttft_value(row)
        if ttft_ms is not None:
            group["_ttfts"].append(ttft_ms)
        group["_users"].add(str(row.get("_user_id") or row.get("user_id") or "unknown"))
        group["_workspaces"].add(
            str(row.get("_workspace_id") or row.get("workspace_id") or "unknown")
        )
        if status in _FAILURE_STATUSES or row.get("error_kind"):
            group["_error_kinds"].add(str(row.get("error_kind") or status))
        if timestamp is not None:
            if group["_first_seen"] is None or timestamp < group["_first_seen"]:
                group["_first_seen"] = timestamp
            if group["_last_seen"] is None or timestamp > group["_last_seen"]:
                group["_last_seen"] = timestamp

    result: list[dict[str, Any]] = []
    for values, group in groups.items():
        requests = group["requests"]
        latency = _percentile_metrics(group["_durations"])
        ttft = _percentile_metrics(group["_ttfts"])
        error_rate = group["errors"] / requests if requests else 0.0
        p95 = latency["p95"]
        status = "healthy"
        if error_rate > 0 or (p95 is not None and p95 >= 1_000):
            status = "degraded" if error_rate >= 0.05 or (p95 or 0) >= 2_000 else "watch"
        item: dict[str, Any] = {
            "requests": requests,
            "successes": group["successes"],
            "errors": group["errors"],
            "failed_requests": group["failed_requests"],
            "error_rate": error_rate,
            "retries": group["retries"],
            "total_tokens": group["total_tokens"],
            "latency_ms": latency,
            "ttft_ms": ttft,
            "users": len(group["_users"]),
            "workspaces": len(group["_workspaces"]),
            "error_kinds": sorted(group["_error_kinds"]),
            "first_seen": group["_first_seen"].isoformat() if group["_first_seen"] else None,
            "last_seen": group["_last_seen"].isoformat() if group["_last_seen"] else None,
            "status": status,
        }
        for key, value in zip(keys, values):
            item[key.removeprefix("_")] = value
        if dimension == "component":
            item["label"] = f"{item['kind']} · {item['name']}"
        elif dimension == "model":
            item["label"] = f"{item['provider']} · {item['provider_model']}"
        result.append(item)
    return sorted(
        result,
        key=lambda item: (
            item["status"] == "healthy",
            -item["errors"],
            -item["requests"],
            str(item.get("label") or item.get("provider") or item.get("name") or ""),
        ),
    )[:MAX_SUMMARY_DIMENSIONS]


def _percentile_metrics(values: Iterable[int]) -> dict[str, int | None]:
    values = list(values)
    return {
        "p50": _percentile(values, 0.50),
        "p90": _percentile(values, 0.90),
        "p95": _percentile(values, 0.95),
        "p99": _percentile(values, 0.99),
    }


def _operational_trend(
    traces: list[dict[str, Any]],
    spans: list[dict[str, Any]],
    *,
    end: datetime,
    window_minutes: int,
    bucket_minutes: int,
) -> list[dict[str, Any]]:
    bucket_minutes = max(1, bucket_minutes)
    window_minutes = max(bucket_minutes, window_minutes)
    bucket_ms = bucket_minutes * 60 * 1000
    # Anchor the window to the actual current time. Rounding the end upward
    # would create future buckets and make the chart disagree with `to`.
    end_ms = end.timestamp() * 1000
    buckets: list[dict[str, Any]] = []
    bucket_count = ceil(window_minutes / bucket_minutes)
    start_ms = end_ms - window_minutes * 60 * 1000
    for index in range(bucket_count):
        bucket_start_ms = start_ms + index * bucket_ms
        bucket_end_ms = min(bucket_start_ms + bucket_ms, end_ms)
        timestamp = datetime.fromtimestamp(
            bucket_start_ms / 1000, tz=timezone.utc
        )
        buckets.append(
            {
                "period_start": timestamp.isoformat(),
                "period_end": datetime.fromtimestamp(
                    bucket_end_ms / 1000, tz=timezone.utc
                ).isoformat(),
                "granularity": "five_minute" if bucket_minutes == 5 else f"{bucket_minutes}_minute",
                "requests": 0,
                "component_calls": 0,
                "errors": 0,
                "component_errors": 0,
                "retries": 0,
                "total_tokens": 0,
                "component_tokens": 0,
                "_durations": [],
                "_component_durations": [],
                "_ttfts": [],
                "_users": set(),
            }
        )

    failed_span_trace_ids = {
        str(row.get("trace_id") or "")
        for row in spans
        if str(row.get("status") or "").lower() in _FAILURE_STATUSES
        or bool(row.get("error_kind"))
    }

    def bucket_for(row: dict[str, Any]) -> dict[str, Any] | None:
        timestamp = _parse_datetime(row.get("completed_at") or row.get("started_at"))
        if timestamp is None:
            return None
        index = int((timestamp.timestamp() * 1000 - start_ms) // bucket_ms)
        return buckets[index] if 0 <= index < len(buckets) else None

    for row in traces:
        bucket = bucket_for(row)
        if bucket is None:
            continue
        status = str(row.get("status") or "unknown").lower()
        bucket["requests"] += 1
        trace_id = str(row.get("trace_id") or "")
        bucket["errors"] += int(
            status in _FAILURE_STATUSES
            or bool(row.get("error_kind"))
            or trace_id in failed_span_trace_ids
        )
        bucket["total_tokens"] += _usage_value(row, "total_tokens")
        bucket["_users"].add(str(row.get("user_id") or "unknown"))
        if row.get("duration_ms") is not None:
            bucket["_durations"].append(int(row.get("duration_ms") or 0))
        ttft_ms = _ttft_value(row)
        if ttft_ms is not None:
            bucket["_ttfts"].append(ttft_ms)
    for row in spans:
        bucket = bucket_for(row)
        if bucket is None:
            continue
        status = str(row.get("status") or "unknown").lower()
        bucket["component_calls"] += 1
        bucket["component_errors"] += int(status in _FAILURE_STATUSES or bool(row.get("error_kind")))
        bucket["retries"] += int(int(row.get("attempt") or 1) > 1)
        bucket["component_tokens"] += _usage_value(row, "total_tokens")
        if row.get("duration_ms") is not None:
            bucket["_component_durations"].append(int(row.get("duration_ms") or 0))

    result = []
    for bucket in buckets:
        requests = bucket["requests"]
        duration = _percentile_metrics(bucket.pop("_durations"))
        component_duration = _percentile_metrics(bucket.pop("_component_durations"))
        ttft = _percentile_metrics(bucket.pop("_ttfts"))
        users = len(bucket.pop("_users"))
        result.append(
            {
                **bucket,
                "error_rate": bucket["errors"] / requests if requests else 0.0,
                "active_users": users,
                "latency_ms": duration,
                "component_latency_ms": component_duration,
                "ttft_ms": ttft,
            }
        )
    return result


def build_dependency_health(
    traces: Iterable[dict[str, Any]],
    spans: Iterable[dict[str, Any]],
    days: int = 30,
    *,
    now: datetime | None = None,
    window_minutes: int = 120,
    bucket_minutes: int = 5,
) -> dict[str, Any]:
    """Build bounded all-user dependency health data for the monitor page."""
    end = _utc(now or datetime.now(timezone.utc))
    since = end - timedelta(days=max(1, days))
    trace_rows = _period_rows(traces, since)
    span_rows = _period_rows(spans, since)
    context_spans = _span_context_rows(trace_rows, span_rows)
    components = _health_dimensions(
        context_spans, keys=("kind", "name"), dimension="component"
    )
    providers = _health_dimensions(
        (row for row in context_spans if row.get("_provider") != "unknown"),
        keys=("_provider",),
        dimension="provider",
    )
    models = _health_dimensions(
        (
            row
            for row in context_spans
            if row.get("_provider_model") != "unknown"
        ),
        keys=("_provider", "_provider_model", "_model_profile"),
        dimension="model",
    )
    durations = [int(row["duration_ms"]) for row in trace_rows if row.get("duration_ms") is not None]
    ttfts = [ttft_ms for row in trace_rows if (ttft_ms := _ttft_value(row)) is not None]
    failed_span_trace_ids = {
        str(row.get("_trace_id") or "")
        for row in context_spans
        if str(row.get("status") or "").lower() in _FAILURE_STATUSES
        or bool(row.get("error_kind"))
    }
    errors = sum(
        str(row.get("status") or "").lower() in _FAILURE_STATUSES
        or bool(row.get("error_kind"))
        or str(row.get("trace_id") or "") in failed_span_trace_ids
        for row in trace_rows
    )
    anomalies = []
    for kind, rows in (("component", components), ("provider", providers), ("model", models)):
        for row in rows:
            if row["status"] != "healthy":
                anomalies.append(
                    {
                        "type": kind,
                        "key": row.get("label") or row.get("name") or row.get("provider"),
                        "status": row["status"],
                        "errors": row["errors"],
                        "error_rate": row["error_rate"],
                        "p95_ms": row["latency_ms"]["p95"],
                        "last_seen": row["last_seen"],
                    }
                )
    anomalies.sort(key=lambda row: (-row["errors"], -(row["p95_ms"] or 0), str(row["key"])))
    return {
        "scope": "system",
        "period_days": max(1, days),
        "from": since.isoformat(),
        "to": end.isoformat(),
        "summary": {
            "requests": len(trace_rows),
            "component_calls": len(context_spans),
            "errors": errors,
            "component_errors": sum(
                str(row.get("status") or "").lower() in _FAILURE_STATUSES or bool(row.get("error_kind"))
                for row in context_spans
            ),
            "error_rate": errors / len(trace_rows) if trace_rows else 0.0,
            "active_users": len({str(row.get("user_id") or "unknown") for row in trace_rows}),
            "active_workspaces": len(
                {str(row.get("workspace_id") or "unknown") for row in trace_rows}
            ),
            "latency_ms": _percentile_metrics(durations),
            "ttft_ms": _percentile_metrics(ttfts),
            "total_tokens": sum(_usage_value(row, "total_tokens") for row in trace_rows),
        },
        "trend": _operational_trend(
            trace_rows,
            context_spans,
            end=end,
            window_minutes=window_minutes,
            bucket_minutes=bucket_minutes,
        ),
        "components": components,
        "providers": providers,
        "models": models,
        "anomalies": anomalies[:20],
        "dimension_limit": MAX_SUMMARY_DIMENSIONS,
    }


def build_error_analysis(
    traces: Iterable[dict[str, Any]],
    spans: Iterable[dict[str, Any]],
    days: int = 30,
    *,
    limit: int = 100,
    offset: int = 0,
    now: datetime | None = None,
    window_minutes: int = 120,
    bucket_minutes: int = 5,
) -> dict[str, Any]:
    """Aggregate actionable error fingerprints while omitting sensitive payloads."""
    end = _utc(now or datetime.now(timezone.utc))
    since = end - timedelta(days=max(1, days))
    trace_rows = _period_rows(traces, since)
    span_rows = _period_rows(spans, since)
    context_spans = _span_context_rows(trace_rows, span_rows)
    groups: dict[str, dict[str, Any]] = {}
    success_times: dict[tuple[str, str], list[datetime]] = defaultdict(list)
    failure_times: dict[tuple[str, str], list[datetime]] = defaultdict(list)
    affected_users: set[str] = set()
    affected_trace_ids: set[str] = set()
    span_failure_trace_ids = {
        str(row.get("_trace_id") or "")
        for row in context_spans
        if str(row.get("status") or "").lower() in _FAILURE_STATUSES
        or row.get("error_kind")
    }

    def add_success(kind: str, name: str, row: dict[str, Any]) -> None:
        timestamp = _parse_datetime(row.get("completed_at") or row.get("started_at"))
        if timestamp is None:
            return
        key = (kind, name)
        success_times[key].append(timestamp)

    def add_occurrence(
        row: dict[str, Any],
        *,
        kind: str,
        name: str,
        user_id: str,
        workspace_id: str,
        trace_id: str,
        chain_name: str,
        provider_model: str | None = None,
    ) -> None:
        status = str(row.get("status") or "unknown").lower()
        error_kind = str(row.get("error_kind") or status or "unknown")
        fingerprint = f"{error_kind}|{kind}|{name}"
        timestamp = _parse_datetime(row.get("completed_at") or row.get("started_at"))
        group = groups.setdefault(
            fingerprint,
            {
                "fingerprint": fingerprint,
                "error_kind": error_kind,
                "kind": kind,
                "name": name,
                "count": 0,
                "_trace_ids": set(),
                "_users": set(),
                "_workspaces": set(),
                "_provider_models": set(),
                "_chains": set(),
                "_timestamps": [],
                "_durations": [],
                "sample_trace_id": trace_id,
            },
        )
        group["count"] += 1
        affected_users.add(user_id)
        if trace_id:
            affected_trace_ids.add(trace_id)
        if trace_id:
            group["_trace_ids"].add(trace_id)
        group["_users"].add(user_id)
        group["_workspaces"].add(workspace_id)
        if provider_model:
            group["_provider_models"].add(provider_model)
        if chain_name:
            group["_chains"].add(chain_name)
        if timestamp is not None:
            group["_timestamps"].append(timestamp)
            failure_times[(kind, name)].append(timestamp)
        if row.get("duration_ms") is not None:
            group["_durations"].append(int(row.get("duration_ms") or 0))

    for row in trace_rows:
        status = str(row.get("status") or "unknown").lower()
        attributes = row.get("attributes") or {}
        if not isinstance(attributes, dict):
            attributes = {}
        identity = trace_chain_identity(row)
        kind = "trace"
        name = str(attributes.get("entrypoint") or row.get("source") or "request")
        trace_id = str(row.get("trace_id") or "")
        if (status in _FAILURE_STATUSES or row.get("error_kind")) and trace_id not in span_failure_trace_ids:
            add_occurrence(
                row,
                kind=kind,
                name=name,
                user_id=str(row.get("user_id") or "unknown"),
                workspace_id=str(row.get("workspace_id") or "unknown"),
                trace_id=trace_id,
                chain_name=identity["chain_name"],
            )
        elif status == "ok":
            add_success(kind, name, row)

    for row in context_spans:
        status = str(row.get("status") or "unknown").lower()
        kind = str(row.get("kind") or "unknown")
        name = str(row.get("name") or "unknown")
        if status in _FAILURE_STATUSES or row.get("error_kind"):
            provider = row.get("_provider")
            model = row.get("_provider_model")
            provider_model = None
            if provider and provider != "unknown" and model and model != "unknown":
                provider_model = f"{provider} / {model}"
            add_occurrence(
                row,
                kind=kind,
                name=name,
                user_id=str(row.get("_user_id") or "unknown"),
                workspace_id=str(row.get("_workspace_id") or "unknown"),
                trace_id=str(row.get("_trace_id") or ""),
                chain_name=str(row.get("_chain_name") or "unknown"),
                provider_model=provider_model,
            )
        elif status == "ok":
            add_success(kind, name, row)

    items: list[dict[str, Any]] = []
    for group in groups.values():
        timestamps = group.pop("_timestamps")
        durations = group.pop("_durations")
        trace_ids = group.pop("_trace_ids")
        users = group.pop("_users")
        workspaces = group.pop("_workspaces")
        provider_models = group.pop("_provider_models")
        chains = group.pop("_chains")
        first_seen = min(timestamps) if timestamps else None
        last_seen = max(timestamps) if timestamps else None
        component_key = (group["kind"], group["name"])
        next_failure = min(
            (
                value
                for value in failure_times[component_key]
                if last_seen is not None and value > last_seen
            ),
            default=None,
        )
        recovered_after_incident = any(
            last_seen is not None
            and value > last_seen
            and (next_failure is None or value < next_failure)
            for value in success_times[component_key]
        )
        if recovered_after_incident:
            recovery_status = "recovered"
        elif last_seen and (end - last_seen).total_seconds() <= 15 * 60:
            recovery_status = "ongoing"
        else:
            recovery_status = "stale"
        items.append(
            {
                **group,
                "trace_count": len(trace_ids),
                "affected_users": len(users),
                "affected_workspaces": len(workspaces),
                "provider_models": sorted(provider_models),
                "chains": sorted(chains),
                "first_seen": first_seen.isoformat() if first_seen else None,
                "last_seen": last_seen.isoformat() if last_seen else None,
                "latency_ms": _percentile_metrics(durations),
                "recovery_status": recovery_status,
            }
        )
    items.sort(key=lambda row: (row["count"], row["last_seen"] or "", row["fingerprint"]), reverse=True)
    total = len(items)
    page = items[max(0, offset) : max(0, offset) + min(max(1, limit), 500)]
    ongoing = sum(row["recovery_status"] == "ongoing" for row in items)
    recovered = sum(row["recovery_status"] == "recovered" for row in items)
    return {
        "scope": "system",
        "period_days": max(1, days),
        "from": since.isoformat(),
        "to": end.isoformat(),
        "summary": {
            "error_groups": total,
            "total_errors": sum(row["count"] for row in items),
            "affected_requests": len(affected_trace_ids),
            "affected_users": len(affected_users),
            "ongoing_groups": ongoing,
            "recovered_groups": recovered,
            "error_rate": (
                len(affected_trace_ids) / len(trace_rows)
                if trace_rows
                else 0.0
            ),
        },
        "trend": _operational_trend(
            trace_rows,
            context_spans,
            end=end,
            window_minutes=window_minutes,
            bucket_minutes=bucket_minutes,
        ),
        "items": page,
        "total": total,
        "offset": max(0, offset),
        "limit": min(max(1, limit), 500),
        "has_more": max(0, offset) + len(page) < total,
    }


def build_telemetry_overview(
    traces: Iterable[dict[str, Any]],
    spans: Iterable[dict[str, Any]],
    events: Iterable[dict[str, Any]],
    days: int = 30,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build an all-user overview without exposing high-cardinality IDs as tags.

    Trace rows represent logical requests; span rows represent component attempts.
    Keeping those two populations separate prevents retries from inflating the
    user-facing request/error rate while still making retry/fallback pressure
    visible in component and model tables.
    """

    end = _utc(now or datetime.now(timezone.utc))
    since = end - timedelta(days=max(1, days))
    trace_rows = [
        row for row in traces if row.get("completed_at") and _in_period(row, since)
    ]
    span_rows = [
        row for row in spans if row.get("completed_at") and _in_period(row, since)
    ]
    event_rows = [row for row in events if _in_period(row, since, event=True)]
    statuses = Counter(str(row.get("status") or "unknown") for row in trace_rows)
    errors = sum(row.get("status") in _ERROR_STATUSES for row in trace_rows)
    failed_requests = sum(row.get("status") in _FAILURE_STATUSES for row in trace_rows)
    durations = [int(row["duration_ms"]) for row in trace_rows if row.get("duration_ms") is not None]
    ttfts = [ttft_ms for row in trace_rows if (ttft_ms := _ttft_value(row)) is not None]
    timestamps = [
        _parse_datetime(row.get("completed_at") or row.get("started_at"))
        for row in trace_rows
    ]
    latest_trace = max((value for value in timestamps if value is not None), default=None)
    event_timestamps = [
        _parse_datetime(row.get("timestamp"))
        for row in event_rows
    ]
    latest_event = max((value for value in event_timestamps if value is not None), default=None)
    span_kind_rows = _aggregate_rows(span_rows, key_name="kind", keys=("kind",))
    component_rows = _aggregate_rows(span_rows, key_name="name", keys=("kind", "name"))
    for row in component_rows:
        row["label"] = f"{row['kind']} · {row['name']}"
        row.pop("kind", None)

    return {
        "period_days": max(1, days),
        "from": since.isoformat(),
        "to": end.isoformat(),
        "requests": len(trace_rows),
        "successes": statuses.get("ok", 0),
        "errors": errors,
        "failed_requests": failed_requests,
        "error_rate": errors / len(trace_rows) if trace_rows else 0.0,
        "failure_rate": failed_requests / len(trace_rows) if trace_rows else 0.0,
        "latency_ms": {
            "p50": _percentile(durations, 0.50),
            "p90": _percentile(durations, 0.90),
            "p95": _percentile(durations, 0.95),
            "p99": _percentile(durations, 0.99),
        },
        "ttft_ms": {
            "p50": _percentile(ttfts, 0.50),
            "p90": _percentile(ttfts, 0.90),
            "p95": _percentile(ttfts, 0.95),
            "p99": _percentile(ttfts, 0.99),
        },
        "tokens": _token_totals(trace_rows),
        "active_users": len({str(row.get("user_id") or "unknown") for row in trace_rows}),
        "active_workspaces": len(
            {str(row.get("workspace_id") or "unknown") for row in trace_rows}
        ),
        "active_sessions": len(
            {str(row.get("session_id") or "unknown") for row in trace_rows}
        ),
        "status_breakdown": [
            {"value": value, "requests": count}
            for value, count in sorted(statuses.items(), key=lambda item: (-item[1], item[0]))
        ],
        "tags": {
            "channels": _dimension_counts(row.get("channel") for row in trace_rows),
            "sources": _dimension_counts(row.get("source") for row in trace_rows),
            "span_kinds": _dimension_counts(row.get("kind") for row in span_rows),
        },
        "component_spans": component_rows[:MAX_SUMMARY_DIMENSIONS],
        "span_kinds": span_kind_rows[:MAX_SUMMARY_DIMENSIONS],
        "models": _model_rows(span_rows)[:MAX_SUMMARY_DIMENSIONS],
        "error_groups": _error_groups(span_rows)[:MAX_SUMMARY_DIMENSIONS],
        "events_by_level": dict(
            sorted(Counter(str(row.get("level") or "unknown") for row in event_rows).items())
        ),
        "event_names": _dimension_counts(row.get("name") for row in event_rows),
        "top_users": _user_summary(trace_rows)[:MAX_SUMMARY_DIMENSIONS],
        "dimension_limit": MAX_SUMMARY_DIMENSIONS,
        "latest_trace_at": latest_trace.isoformat() if latest_trace else None,
        "latest_event_at": latest_event.isoformat() if latest_event else None,
    }
