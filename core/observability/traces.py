"""Stable trace identity and problem-oriented chain summaries."""

from __future__ import annotations

from typing import Any, Iterable


_CHAIN_ID_KEYS = (
    "chain_id",
    "trace_group_id",
    "workflow_run_id",
    "evaluation_run_id",
    "run_id",
)
_CHAIN_NAME_KEYS = (
    "chain_name",
    "workflow_name",
    "operation_name",
    "workflow",
)
_ENTRYPOINT_KEYS = (
    "entrypoint",
    "route",
    "endpoint",
    "operation",
    "request_name",
)
_ERROR_STATUSES = {"error", "timeout", "cancelled", "denied"}
_SLOW_TRACE_MS = 1_000


def _text(attributes: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = attributes.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def derive_trace_identity(
    *,
    session_id: str,
    turn_id: str,
    source: str,
    channel: str | None = None,
    attributes: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Derive a stable chain identity for labeled and ordinary user turns."""
    values = attributes or {}
    chain_id = _text(values, _CHAIN_ID_KEYS) or f"session:{session_id}"
    chain_name = _text(values, _CHAIN_NAME_KEYS)
    if chain_name is None:
        chain_name = "后台恢复" if source == "worker_resume" else "用户会话"
    entrypoint = _text(values, _ENTRYPOINT_KEYS) or (
        f"{channel}:agent.turn" if channel else chain_name
    )
    return {
        "chain_id": chain_id,
        "chain_name": chain_name,
        "entrypoint": entrypoint,
    }


def trace_chain_identity(row: dict[str, Any]) -> dict[str, str]:
    attributes = row.get("attributes") or {}
    if not isinstance(attributes, dict):
        attributes = {}
    derived = derive_trace_identity(
        session_id=str(row.get("session_id") or "unknown"),
        turn_id=str(row.get("turn_id") or row.get("trace_id") or "unknown"),
        source=str(row.get("source") or "user"),
        channel=str(row.get("channel") or "") or None,
        attributes=attributes,
    )
    return {
        "chain_id": str(row.get("chain_id") or derived["chain_id"]),
        "chain_name": str(row.get("chain_name") or derived["chain_name"]),
        "entrypoint": str(row.get("entrypoint") or derived["entrypoint"]),
    }


def _is_error(row: dict[str, Any]) -> bool:
    return str(row.get("status") or "").lower() in _ERROR_STATUSES or bool(row.get("error_kind"))


def _sort_key(group: dict[str, Any]) -> tuple[int, int, int, str]:
    return (
        int(group["error_count"] > 0),
        int(group["max_duration_ms"] >= _SLOW_TRACE_MS or group["slow_span_count"] > 0),
        int(group["last_seen_epoch"]),
        str(group["chain_id"]),
    )


def build_trace_group_page(
    rows: Iterable[dict[str, Any]],
    *,
    spans: Iterable[dict[str, Any]] | None = None,
    limit: int = 24,
    offset: int = 0,
    query: str | None = None,
    focus: str = "all",
) -> dict[str, Any]:
    """Aggregate traces into bounded chain rows for the monitor list."""
    limit = min(max(1, limit), 100)
    offset = max(0, offset)
    normalized_query = (query or "").strip().lower()
    query_tokens = [
        token
        for token in normalized_query.replace("|", " ").replace("·", " ").split()
        if token
    ]
    if focus not in {"all", "errors", "slow"}:
        raise ValueError("focus must be one of all, errors, slow")

    groups: dict[str, dict[str, Any]] = {}
    trace_to_chain: dict[str, str] = {}
    for row in rows:
        identity = trace_chain_identity(row)
        chain_id = identity["chain_id"]
        trace_id = str(row.get("trace_id") or "")
        if trace_id:
            trace_to_chain[trace_id] = chain_id
        started_at = str(row.get("started_at") or "")
        completed_at = str(row.get("completed_at") or started_at)
        status = str(row.get("status") or "unknown")
        duration_ms = int(row.get("duration_ms") or 0)
        is_error = _is_error(row)
        group = groups.get(chain_id)
        if group is None:
            group = {
                **identity,
                "user_ids": set(),
                "workspace_ids": set(),
                "sources": set(),
                "started_at": started_at,
                "last_seen": completed_at or started_at,
                "last_seen_epoch": 0,
                "status": status,
                "trace_count": 0,
                "error_count": 0,
                "span_count": 0,
                "failed_span_count": 0,
                "slow_span_count": 0,
                "total_duration_ms": 0,
                "max_duration_ms": 0,
                "slow_trace_count": 0,
                "total_tokens": 0,
                "sample_trace_id": str(row.get("trace_id") or ""),
                "error_kinds": set(),
                "provider_models": set(),
                "component_names": set(),
                "latest_started_at": "",
            }
            groups[chain_id] = group
        group["user_ids"].add(str(row.get("user_id") or "unknown"))
        group["workspace_ids"].add(str(row.get("workspace_id") or "unknown"))
        group["sources"].add(str(row.get("source") or "unknown"))
        group["trace_count"] += 1
        group["error_count"] += int(is_error)
        group["total_duration_ms"] += duration_ms
        group["max_duration_ms"] = max(group["max_duration_ms"], duration_ms)
        group["slow_trace_count"] += int(duration_ms >= _SLOW_TRACE_MS)
        group["total_tokens"] += int(row.get("total_tokens") or 0)
        if started_at and (not group["started_at"] or started_at < group["started_at"]):
            group["started_at"] = started_at
        if completed_at > group["last_seen"]:
            group["last_seen"] = completed_at
        if started_at >= group["latest_started_at"]:
            group["latest_started_at"] = started_at
            group["status"] = status
        if is_error:
            error_kind = str(row.get("error_kind") or status)
            group["error_kinds"].add(error_kind)
            if started_at >= group.get("latest_error_started_at", ""):
                group["latest_error_started_at"] = started_at
                group["sample_trace_id"] = str(row.get("trace_id") or "")
        # Keep trace-only failures searchable by the same kind/name
        # fingerprint used by the incident page. Without this marker a query
        # such as `TimeoutError|trace|/api/chat` could never match a chain
        # that has no persisted child span.
        group["component_names"].add(f"trace · {identity['entrypoint']}")

    for span in spans or ():
        chain_id = trace_to_chain.get(str(span.get("trace_id") or ""))
        if chain_id is None:
            continue
        group = groups[chain_id]
        group["span_count"] += 1
        span_duration_ms = int(span.get("duration_ms") or 0)
        group["slow_span_count"] += int(span_duration_ms >= _SLOW_TRACE_MS)
        span_error = str(span.get("status") or "").lower() in _ERROR_STATUSES or bool(span.get("error_kind"))
        if span_error:
            if group["error_count"] == 0:
                group["sample_trace_id"] = str(span.get("trace_id") or "")
            group["error_count"] += 1
            group["failed_span_count"] += 1
            group["status"] = "error"
            group["error_kinds"].add(str(span.get("error_kind") or span.get("status") or "span_error"))
        attributes = span.get("attributes") or {}
        if not isinstance(attributes, dict):
            attributes = {}
        provider = _text(attributes, ("provider", "tool_provider"))
        model = _text(attributes, ("model", "provider_model"))
        if provider and model:
            group["provider_models"].add(f"{provider} / {model}")
        elif provider:
            group["provider_models"].add(provider)
        kind = str(span.get("kind") or "unknown")
        name = str(span.get("name") or "unknown")
        group["component_names"].add(f"{kind} · {name}")

    filtered: list[dict[str, Any]] = []
    for group in groups.values():
        haystack = " ".join(
            [
                str(group["chain_id"]),
                str(group["chain_name"]),
                str(group["entrypoint"]),
                *sorted(group["user_ids"]),
                *sorted(group["workspace_ids"]),
                *sorted(group["sources"]),
                *sorted(group["provider_models"]),
                *sorted(group["component_names"]),
                *sorted(group["error_kinds"]),
            ]
        ).lower()
        if query_tokens and not all(token in haystack for token in query_tokens):
            continue
        if focus == "errors" and group["error_count"] == 0:
            continue
        if focus == "slow" and group["max_duration_ms"] < _SLOW_TRACE_MS and group["slow_span_count"] == 0:
            continue
        group["last_seen_epoch"] = _timestamp_epoch(group["last_seen"])
        filtered.append(group)

    filtered.sort(key=_sort_key, reverse=True)
    total = len(filtered)
    page = []
    for group in filtered[offset : offset + limit]:
        page.append(
            {
                key: sorted(value) if isinstance(value, set) else value
                for key, value in group.items()
                if key not in {"last_seen_epoch", "latest_started_at", "latest_error_started_at"}
            }
        )
    return {
        "scope": "system",
        "items": page,
        "total": total,
        "offset": offset,
        "limit": limit,
        "has_more": offset + len(page) < total,
    }


def _timestamp_epoch(value: str) -> int:
    if not value:
        return 0
    try:
        from datetime import datetime

        return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())
    except (TypeError, ValueError, OverflowError):
        return 0
