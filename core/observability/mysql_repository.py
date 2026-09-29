"""MySQL-backed telemetry repository used by production runtime."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from collections.abc import Iterable, Sequence
from math import ceil
from typing import Any

from sqlalchemy import Integer, String, and_, case, cast, create_engine, delete, false, func, or_, select, text
from sqlalchemy.dialects.mysql import insert
from sqlalchemy.orm import aliased

from core.observability.models import TelemetryEnvelope
from core.observability.reset_lock import runtime_write_transaction
from core.observability.summary import (
    build_dependency_health,
    build_error_analysis,
    build_telemetry_overview,
)
from core.observability.redaction import redact_telemetry_mapping
from core.observability.traces import build_trace_group_page
from server.infrastructure.mysql.models import ObservabilityRecordModel


MAX_ANALYSIS_ROWS = 50_000
MAX_TRACE_DETAIL_TRACES = 1_000
MAX_TRACE_DETAIL_CHILDREN = 5_000
_PERCENTILES = (("p50", 0.50), ("p90", 0.90), ("p95", 0.95), ("p99", 0.99))


def _record_key(kind: str, payload: dict[str, Any]) -> str:
    """Select the envelope's own identity so sibling spans never overwrite."""
    field = {"trace": "trace_id", "span": "span_id", "event": "event_id"}.get(kind)
    if field is None:
        raise ValueError(f"unsupported telemetry envelope kind: {kind}")
    value = payload.get(field)
    if not value:
        raise ValueError(f"telemetry {kind} is missing {field}")
    return str(value)


class MySQLTelemetryRepository:
    def __init__(self, database_url: str) -> None:
        self._engine = create_engine(database_url.replace("mysql+aiomysql://", "mysql+pymysql://"), pool_pre_ping=True)
        self._runtime_lock_enabled = True

    def _runtime_begin(self):
        return runtime_write_transaction(
            self._engine, enabled=getattr(self, "_runtime_lock_enabled", False)
        )

    def write_batch(self, envelopes: Iterable[TelemetryEnvelope]) -> None:
        with self._runtime_begin() as connection:
            for envelope in envelopes:
                payload = envelope.model_dump(mode="json")
                item = dict(payload.get("payload") or {})
                if envelope.kind in {"trace", "span"}:
                    item["error_message"] = None
                    if isinstance(item.get("attributes"), dict):
                        item["attributes"] = redact_telemetry_mapping(item["attributes"])
                elif isinstance(item.get("payload"), dict):
                    item["payload"] = redact_telemetry_mapping(item["payload"])
                payload["payload"] = item
                key = _record_key(envelope.kind, item)
                statement = insert(ObservabilityRecordModel).values(
                    id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"nlp-agent-observability:{envelope.kind}:{key}")), kind=envelope.kind, record_key=key,
                    trace_id=item.get("trace_id"), session_id=item.get("session_id"),
                    turn_id=item.get("turn_id"), status=item.get("status"), payload_json=payload,
                ).on_duplicate_key_update(payload_json=payload, status=item.get("status"))
                connection.execute(statement)

    def clear(self, *, _lock_held: bool = False) -> dict[str, int]:
        transaction = self._engine.begin() if _lock_held else self._runtime_begin()
        with transaction as connection:
            counts = {kind: int(connection.scalar(select(func.count()).select_from(ObservabilityRecordModel).where(ObservabilityRecordModel.kind == kind)) or 0) for kind in ("trace", "span", "event")}
            connection.execute(delete(ObservabilityRecordModel))
        # MySQL stores only the canonical envelope rows; it has no separate
        # daily_metrics table.  Returning the count of spans here used to
        # falsely report that another store had been cleaned.
        return {"traces": counts["trace"], "spans": counts["span"], "events": counts["event"], "daily_metrics": 0}

    def health(self) -> dict[str, Any]:
        with self._engine.connect() as connection:
            count = int(connection.scalar(select(func.count()).select_from(ObservabilityRecordModel)) or 0)
            counts = {kind: int(connection.scalar(select(func.count()).select_from(ObservabilityRecordModel).where(ObservabilityRecordModel.kind == kind)) or 0) for kind in ("trace", "span", "event")}
            database_bytes = int(
                connection.scalar(
                    text(
                        "SELECT COALESCE(SUM(data_length + index_length), 0) "
                        "FROM information_schema.tables "
                        "WHERE table_schema = DATABASE() "
                        "AND table_name = 'nlp_observability_records'"
                    )
                )
                or 0
            )
        return {"database": "mysql", "records": count, "traces": counts["trace"], "spans": counts["span"], "events": counts["event"], "database_bytes": database_bytes}

    @staticmethod
    def _payload_text(column, path: str):
        return func.json_unquote(func.json_extract(column, path))

    @classmethod
    def _chain_id_expression(cls, table):
        return func.coalesce(
            cls._payload_text(table.payload_json, "$.payload.chain_id"),
            cls._payload_text(table.payload_json, "$.payload.attributes.chain_id"),
            cls._payload_text(table.payload_json, "$.payload.attributes.trace_group_id"),
            cls._payload_text(table.payload_json, "$.payload.attributes.workflow_run_id"),
            cls._payload_text(table.payload_json, "$.payload.attributes.evaluation_run_id"),
            cls._payload_text(table.payload_json, "$.payload.attributes.run_id"),
            table.trace_id,
        )

    @staticmethod
    def _decode_payloads(
        payloads: Iterable[dict[str, Any] | None], kind: str
    ) -> list[dict[str, Any]]:
        output = []
        for payload in payloads:
            item = (payload or {}).get("payload", {})
            if kind in {"trace", "span"}:
                item = {**item, **(item.get("usage") or {})}
            if "error_message" in item:
                item["error_message"] = None
            if isinstance(item.get("attributes"), dict):
                item["attributes"] = redact_telemetry_mapping(item["attributes"])
            if isinstance(item.get("payload"), dict):
                item["payload"] = redact_telemetry_mapping(item["payload"])
            output.append(item)
        return output

    def _rows(
        self,
        kind: str,
        *,
        since: datetime | None = None,
        limit: int | None = None,
        offset: int = 0,
        trace_id: str | None = None,
        trace_ids: Sequence[str] | None = None,
        session_id: str | None = None,
        status: str | None = None,
        user_id: str | None = None,
        workspace_ids: frozenset[str] | None = None,
        level: str | None = None,
        chain_id: str | None = None,
        chain_ids: Sequence[str] | None = None,
        ascending: bool = False,
    ) -> list[dict[str, Any]]:
        statement = select(ObservabilityRecordModel.payload_json).where(
            ObservabilityRecordModel.kind == kind
        )
        if since is not None:
            statement = statement.where(ObservabilityRecordModel.created_at >= since)
        if trace_id is not None:
            statement = statement.where(ObservabilityRecordModel.trace_id == trace_id)
        if trace_ids is not None:
            statement = statement.where(
                ObservabilityRecordModel.trace_id.in_(list(trace_ids))
            )
        if session_id is not None:
            statement = statement.where(ObservabilityRecordModel.session_id == session_id)
        if status is not None:
            statement = statement.where(ObservabilityRecordModel.status == status)
        if user_id is not None:
            statement = statement.where(
                self._payload_text(
                    ObservabilityRecordModel.payload_json, "$.payload.user_id"
                )
                == user_id
            )
        if workspace_ids is not None and "*" not in workspace_ids:
            if not workspace_ids:
                statement = statement.where(false())
            else:
                statement = statement.where(
                    self._payload_text(
                        ObservabilityRecordModel.payload_json, "$.payload.workspace_id"
                    ).in_(sorted(workspace_ids))
                )
        if level is not None:
            statement = statement.where(
                self._payload_text(
                    ObservabilityRecordModel.payload_json, "$.payload.level"
                )
                == level
            )
        if chain_id is not None:
            statement = statement.where(
                self._chain_id_expression(ObservabilityRecordModel) == chain_id
            )
        if chain_ids is not None:
            statement = statement.where(
                self._chain_id_expression(ObservabilityRecordModel).in_(list(chain_ids))
            )
        statement = statement.order_by(
            ObservabilityRecordModel.created_at.asc()
            if ascending
            else ObservabilityRecordModel.created_at.desc()
        )
        if limit is not None:
            statement = statement.limit(min(max(1, limit), MAX_ANALYSIS_ROWS)).offset(max(0, offset))
        with self._engine.connect() as connection:
            rows = connection.execute(statement).scalars().all()
        return self._decode_payloads(rows, kind)

    def overview(self, days: int = 30) -> dict[str, Any]:
        since = datetime.now(timezone.utc) - timedelta(days=max(1, days))
        traces, traces_truncated = self._analysis_rows("trace", since=since)
        spans, spans_truncated = self._analysis_rows("span", since=since)
        events, events_truncated = self._analysis_rows("event", since=since)
        result = build_telemetry_overview(traces, spans, events, days)
        result.update(self._exact_trace_percentiles(since))
        result["analysis"] = {
            "truncated": traces_truncated or spans_truncated or events_truncated,
            "row_limit": MAX_ANALYSIS_ROWS,
        }
        return result

    def dependency_health(
        self,
        days: int = 30,
        *,
        window_minutes: int = 120,
        bucket_minutes: int = 5,
    ) -> dict[str, Any]:
        since = datetime.now(timezone.utc) - timedelta(days=max(1, days))
        traces, traces_truncated = self._analysis_rows("trace", since=since)
        spans, spans_truncated = self._analysis_rows("span", since=since)
        result = build_dependency_health(
            traces,
            spans,
            days,
            window_minutes=window_minutes,
            bucket_minutes=bucket_minutes,
        )
        exact = self._exact_trace_percentiles(since)
        result["summary"]["latency_ms"] = exact["latency_ms"]
        result["summary"]["ttft_ms"] = exact["ttft_ms"]
        result["analysis"] = {
            "truncated": traces_truncated or spans_truncated,
            "row_limit": MAX_ANALYSIS_ROWS,
        }
        return result

    def error_analysis(
        self,
        days: int = 30,
        *,
        limit: int = 100,
        offset: int = 0,
        window_minutes: int = 120,
        bucket_minutes: int = 5,
    ) -> dict[str, Any]:
        since = datetime.now(timezone.utc) - timedelta(days=max(1, days))
        traces, traces_truncated = self._analysis_rows("trace", since=since)
        spans, spans_truncated = self._analysis_rows("span", since=since)
        result = build_error_analysis(
            traces,
            spans,
            days,
            limit=limit,
            offset=offset,
            window_minutes=window_minutes,
            bucket_minutes=bucket_minutes,
        )
        result["analysis"] = {
            "truncated": traces_truncated or spans_truncated,
            "row_limit": MAX_ANALYSIS_ROWS,
        }
        return result

    def _analysis_rows(
        self, kind: str, *, since: datetime
    ) -> tuple[list[dict[str, Any]], bool]:
        """Keep diagnostic materialization bounded even before retention runs."""
        count_query = select(func.count()).select_from(ObservabilityRecordModel).where(
            ObservabilityRecordModel.kind == kind,
            ObservabilityRecordModel.created_at >= since,
        )
        with self._engine.connect() as connection:
            total = int(connection.scalar(count_query) or 0)
        return (
            self._rows(kind, since=since, limit=MAX_ANALYSIS_ROWS),
            total > MAX_ANALYSIS_ROWS,
        )

    def _exact_trace_percentiles(self, since: datetime) -> dict[str, dict[str, int | None]]:
        """Calculate top-level percentiles without materializing the window."""
        table = ObservabilityRecordModel
        base = [table.kind == "trace", table.created_at >= since]
        output: dict[str, dict[str, int | None]] = {}
        with self._engine.connect() as connection:
            for path, output_name in (
                ("$.payload.duration_ms", "latency_ms"),
                ("$.payload.ttft_ms", "ttft_ms"),
            ):
                value = cast(self._payload_text(table.payload_json, path), Integer)
                if output_name == "ttft_ms":
                    value = cast(
                        func.coalesce(
                            self._payload_text(table.payload_json, "$.payload.ttft_ms"),
                            self._payload_text(table.payload_json, "$.payload.attributes.ttft_ms"),
                        ),
                        Integer,
                    )
                condition = [*base, value.is_not(None)]
                count = int(connection.scalar(select(func.count()).where(*condition)) or 0)
                values: dict[str, int | None] = {}
                for name, fraction in _PERCENTILES:
                    if not count:
                        values[name] = None
                        continue
                    rank = max(1, ceil(count * fraction))
                    percentile = connection.scalar(
                        select(value)
                        .where(*condition)
                        .order_by(value.asc())
                        .limit(1)
                        .offset(rank - 1)
                    )
                    values[name] = int(percentile) if percentile is not None else None
                output[output_name] = values
        return output

    def list_traces(self, *, limit: int = 100, session_id: str | None = None, status: str | None = None, user_id: str | None = None, workspace_ids: frozenset[str] | None = None) -> list[dict[str, Any]]:
        return self._rows(
            "trace",
            session_id=session_id,
            status=status,
            user_id=user_id,
            workspace_ids=workspace_ids,
            limit=limit,
        )

    def trace_groups(self, *, days: int = 30, limit: int = 24, offset: int = 0, query: str | None = None, focus: str = "all") -> dict[str, Any]:
        since = datetime.now(timezone.utc) - timedelta(days=max(1, days))
        trace_table = aliased(ObservabilityRecordModel)
        span_table = aliased(ObservabilityRecordModel)
        chain_id = self._chain_id_expression(trace_table)
        statement = (
            select(chain_id.label("chain_id"), func.max(trace_table.created_at).label("last_seen"))
            .select_from(trace_table)
            .outerjoin(
                span_table,
                and_(
                    span_table.kind == "span",
                    span_table.trace_id == trace_table.trace_id,
                ),
            )
            .where(trace_table.kind == "trace", trace_table.created_at >= since)
        )
        failure = or_(
            trace_table.status.in_(
                ("error", "timeout", "cancelled", "denied")
            ),
            self._payload_text(
                trace_table.payload_json, "$.payload.error_kind"
            ).is_not(None),
            span_table.status.in_(
                ("error", "timeout", "cancelled", "denied")
            ),
            self._payload_text(
                span_table.payload_json, "$.payload.error_kind"
            ).is_not(None),
        )
        slow = or_(
            cast(
                self._payload_text(
                    trace_table.payload_json, "$.payload.duration_ms"
                ),
                Integer,
            )
            >= 1000,
            cast(
                self._payload_text(
                    span_table.payload_json, "$.payload.duration_ms"
                ),
                Integer,
            )
            >= 1000,
        )
        if focus == "errors":
            statement = statement.where(failure)
        elif focus == "slow":
            statement = statement.where(slow)
        normalized_query = (query or "").strip().lower()
        query_tokens = [
            item
            for item in normalized_query.replace("|", " ").replace("·", " ").split()
            if item
        ]
        for token in query_tokens:
            pattern = f"%{token}%"
            statement = statement.where(
                or_(
                    func.lower(cast(trace_table.payload_json, String)).like(pattern),
                    func.lower(cast(span_table.payload_json, String)).like(pattern),
                )
            )
        statement = statement.group_by(chain_id).order_by(
            func.max(case((failure, 1), else_=0)).desc(),
            func.max(case((slow, 1), else_=0)).desc(),
            func.max(trace_table.created_at).desc(),
            chain_id,
        )
        safe_limit = min(max(1, limit), 100)
        safe_offset = max(0, offset)
        with self._engine.connect() as connection:
            total = int(
                connection.scalar(
                    select(func.count()).select_from(statement.order_by(None).subquery())
                )
                or 0
            )
            selected_chain_ids = [
                str(row[0])
                for row in connection.execute(
                    statement.limit(safe_limit).offset(safe_offset)
                ).all()
                if row[0]
            ]
        if not selected_chain_ids:
            return {
                "scope": "system",
                "items": [],
                "total": total,
                "offset": safe_offset,
                "limit": safe_limit,
                "has_more": False,
                "period_days": days,
            }
        traces = self._rows(
            "trace", since=since, chain_ids=selected_chain_ids, limit=MAX_ANALYSIS_ROWS
        )
        trace_ids = [str(row["trace_id"]) for row in traces if row.get("trace_id")]
        spans = self._rows("span", since=since, trace_ids=trace_ids, limit=MAX_ANALYSIS_ROWS)
        page = build_trace_group_page(
            traces,
            spans=spans,
            limit=safe_limit,
            offset=0,
            query=query,
            focus=focus,
        )
        page["total"] = total
        page["offset"] = safe_offset
        page["has_more"] = safe_offset + len(page["items"]) < total
        page["period_days"] = days
        return page

    def trace_group_detail(self, chain_id: str) -> dict[str, Any] | None:
        raw_traces = self._rows(
            "trace",
            chain_id=chain_id,
            limit=MAX_TRACE_DETAIL_TRACES + 1,
            ascending=True,
        )
        traces_truncated = len(raw_traces) > MAX_TRACE_DETAIL_TRACES
        traces = raw_traces[:MAX_TRACE_DETAIL_TRACES]
        if not traces:
            return None
        trace_ids = {str(row["trace_id"]) for row in traces}
        raw_spans = self._rows(
            "span",
            trace_ids=sorted(trace_ids),
            limit=MAX_TRACE_DETAIL_CHILDREN + 1,
            ascending=True,
        )
        spans_truncated = len(raw_spans) > MAX_TRACE_DETAIL_CHILDREN
        spans = raw_spans[:MAX_TRACE_DETAIL_CHILDREN]
        raw_events = self._rows(
            "event",
            trace_ids=sorted(trace_ids),
            limit=MAX_TRACE_DETAIL_CHILDREN + 1,
            ascending=True,
        )
        events_truncated = len(raw_events) > MAX_TRACE_DETAIL_CHILDREN
        events = raw_events[:MAX_TRACE_DETAIL_CHILDREN]
        chain = build_trace_group_page(traces, spans=spans, limit=1)["items"][0]
        return {
            "chain": chain,
            "traces": traces,
            "spans": spans,
            "events": events,
            "detail_limits": {
                "traces": MAX_TRACE_DETAIL_TRACES,
                "children": MAX_TRACE_DETAIL_CHILDREN,
                "traces_truncated": traces_truncated,
                "children_truncated": spans_truncated or events_truncated,
            },
        }

    def recent_events(self, *, limit: int = 200, level: str | None = None, trace_id: str | None = None) -> list[dict[str, Any]]:
        return self._rows("event", limit=limit, level=level, trace_id=trace_id)

    def trace_detail(self, trace_id: str) -> dict[str, Any] | None:
        traces = self._rows("trace", trace_id=trace_id, limit=1)
        if not traces: return None
        raw_spans = self._rows(
            "span", trace_id=trace_id, limit=MAX_TRACE_DETAIL_CHILDREN + 1, ascending=True
        )
        raw_events = self._rows(
            "event", trace_id=trace_id, limit=MAX_TRACE_DETAIL_CHILDREN + 1, ascending=True
        )
        spans = raw_spans[:MAX_TRACE_DETAIL_CHILDREN]
        events = raw_events[:MAX_TRACE_DETAIL_CHILDREN]
        return {
            "trace": traces[0],
            "spans": spans,
            "events": events,
            "detail_limits": {
                "children": MAX_TRACE_DETAIL_CHILDREN,
                "children_truncated": (
                    len(raw_spans) > MAX_TRACE_DETAIL_CHILDREN
                    or len(raw_events) > MAX_TRACE_DETAIL_CHILDREN
                ),
            },
        }

    def errors(self, days: int = 30, limit: int = 100) -> list[dict[str, Any]]:
        since = datetime.now(timezone.utc) - timedelta(days=max(1, days))
        return build_telemetry_overview(
            [], self._rows("span", since=since, limit=MAX_ANALYSIS_ROWS), [], days
        )["error_groups"][:limit]

    def usage(self, days: int = 30) -> list[dict[str, Any]]:
        since_date = datetime.now(timezone.utc).date() - timedelta(days=max(1, days))
        since = since_date.isoformat()
        since_datetime = datetime.combine(
            since_date, datetime.min.time(), tzinfo=timezone.utc
        )
        metrics: dict[tuple[str, str, str], dict[str, Any]] = {}
        token_fields = (
            "input_tokens",
            "output_tokens",
            "cached_tokens",
            "cache_miss_tokens",
            "reasoning_tokens",
            "total_tokens",
        )

        for row in self._rows("span", since=since_datetime, limit=MAX_ANALYSIS_ROWS):
            completed_at = row.get("completed_at")
            if not completed_at:
                continue

            day = str(completed_at)[:10]
            if day < since:
                continue

            component = str(row.get("kind") or "unknown")
            name = str(row.get("name") or "unknown")
            attributes = row.get("attributes") or {}
            if component == "model" and attributes.get("model"):
                name = f"{name}:{attributes['model']}"

            key = (day, component, name)
            metric = metrics.setdefault(
                key,
                {
                    "day": day,
                    "component": component,
                    "name": name,
                    "requests": 0,
                    "successes": 0,
                    "errors": 0,
                    "duration_sum_ms": 0,
                    **{field: 0 for field in token_fields},
                },
            )

            status = row.get("status")
            metric["requests"] += 1
            metric["successes"] += int(status == "ok")
            metric["errors"] += int(status in {"error", "timeout"})
            metric["duration_sum_ms"] += int(row.get("duration_ms") or 0)
            for field in token_fields:
                metric[field] += int(row.get(field, 0) or 0)

        return [metrics[key] for key in sorted(metrics)]

    def prune(self, trace_days: int = 30, event_days: int = 30) -> dict[str, int]:
        """Delete expired telemetry without touching the quota usage ledger."""
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        trace_before = now - timedelta(days=max(1, trace_days))
        event_before = now - timedelta(days=max(1, event_days))

        with self._runtime_begin() as connection:
            expired_trace_ids = select(ObservabilityRecordModel.trace_id).where(
                ObservabilityRecordModel.kind == "trace",
                ObservabilityRecordModel.created_at < trace_before,
            ).subquery("expired_trace_ids")
            expired_trace_id_select = select(expired_trace_ids.c.trace_id)
            spans = connection.execute(
                delete(ObservabilityRecordModel).where(
                    ObservabilityRecordModel.kind == "span",
                    or_(
                        ObservabilityRecordModel.created_at < trace_before,
                        ObservabilityRecordModel.trace_id.in_(expired_trace_id_select),
                    ),
                )
            )
            events = connection.execute(
                delete(ObservabilityRecordModel).where(
                    ObservabilityRecordModel.kind == "event",
                    or_(
                        ObservabilityRecordModel.created_at < event_before,
                        ObservabilityRecordModel.trace_id.in_(expired_trace_id_select),
                    ),
                )
            )
            # Keep this after child cleanup: the subquery is intentionally
            # evaluated again by MySQL and must still see the expired traces.
            traces = connection.execute(
                delete(ObservabilityRecordModel).where(
                    ObservabilityRecordModel.kind == "trace",
                    ObservabilityRecordModel.created_at < trace_before,
                )
            )
        return {
            "traces": max(0, int(traces.rowcount or 0)),
            "spans": max(0, int(spans.rowcount or 0)),
            "events": max(0, int(events.rowcount or 0)),
            "daily_metrics": 0,
        }

    def delete_session(self, session_id: str) -> None:
        with self._runtime_begin() as connection:
            connection.execute(delete(ObservabilityRecordModel).where(ObservabilityRecordModel.session_id == session_id))

    def close(self) -> None:
        self._engine.dispose()

    def flush(self) -> None:
        return None

    def __getattr__(self, name: str) -> Any:
        # Query endpoints remain safe during the migration window; new telemetry is
        # durable in MySQL even when no historical rows exist.
        if name in {"overview", "list_traces", "trace_detail", "usage", "recent_events", "errors"}:
            return lambda *args, **kwargs: [] if name != "overview" else {"period_days": kwargs.get("days", 30), "requests": 0, "successes": 0, "errors": 0, "error_rate": 0.0, "latency_ms": {"p50": None, "p90": None, "p95": None, "p99": None}, "ttft_ms": {"p50": None, "p90": None, "p95": None, "p99": None}, "tokens": {}}
        raise AttributeError(name)
