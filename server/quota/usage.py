"""Read-side usage snapshots and Shadow comparison reports."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from sqlalchemy import Engine, create_engine, select

from server.infrastructure.mysql.models import ObservabilityRecordModel
from server.quota.models import UsageEventModel
from server.quota.service import QuotaService


TOKEN_FIELDS = (
    "input_tokens",
    "cached_input_tokens",
    "cache_write_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
    "total_tokens",
)

UsageGranularity = Literal["day", "week"]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class UsageReadService:
    """One small read interface over immutable usage events.

    The service deliberately sums facts rather than reading quota balances.
    Callers receive priced totals, token totals, completeness, and—when Phase
    2 enforcement is enabled—the current daily/weekly bucket snapshot.
    """

    def __init__(self, database: str | Engine, *, quota_enforcement: bool = False) -> None:
        if isinstance(database, str):
            if database.startswith("mysql+aiomysql://"):
                database = database.replace("mysql+aiomysql://", "mysql+pymysql://", 1)
            self._engine = create_engine(database, pool_pre_ping=True)
            self._owns_engine = True
        else:
            self._engine = database
            self._owns_engine = False
        self._quota_service = QuotaService(self._engine) if quota_enforcement else None

    def user_snapshot(
        self,
        user_id: str,
        *,
        workspace_id: str | None = None,
        days: int = 30,
        granularity: UsageGranularity = "day",
        now: datetime | None = None,
    ) -> dict[str, Any]:
        if granularity not in {"day", "week"}:
            raise ValueError("granularity must be 'day' or 'week'")
        end = _utc(now or _utc_now())
        start = end - timedelta(days=max(1, days))
        rows = self._usage_rows(
            user_id=user_id,
            workspace_id=workspace_id,
            start=start,
            end=end,
        )
        snapshot = self._snapshot(
            user_id=user_id,
            workspace_id=workspace_id,
            start=start,
            end=end,
            rows=rows,
            granularity=granularity,
        )
        if self._quota_service is not None:
            snapshot["quota"] = self._quota_service.snapshot(
                user_id=user_id,
                workspace_id=workspace_id,
                now=end,
            )
        return snapshot

    def shadow_comparison(
        self,
        *,
        days: int = 30,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        end = _utc(now or _utc_now())
        start = end - timedelta(days=max(1, days))
        usage_rows = self._usage_rows(start=start, end=end)
        usage_by_operation = {
            row["operation_id"]: row for row in usage_rows
        }
        observability_by_operation = self._observability_model_spans(
            start=start, end=end
        )
        matched = sorted(
            set(usage_by_operation).intersection(observability_by_operation)
        )
        delta = {field: 0 for field in TOKEN_FIELDS}
        exact_token_matches = 0
        for operation_id in matched:
            usage = usage_by_operation[operation_id]
            observed = observability_by_operation[operation_id]
            event_tokens = {
                field: int(usage[field] or 0) for field in TOKEN_FIELDS
            }
            observed_tokens = self._observed_tokens(observed)
            for field in TOKEN_FIELDS:
                delta[field] += event_tokens[field] - observed_tokens[field]
            if event_tokens == observed_tokens:
                exact_token_matches += 1

        return {
            "period_days": max(1, days),
            "from": start.isoformat(),
            "to": end.isoformat(),
            "usage_event_attempts": len(usage_by_operation),
            "observability_model_spans": len(observability_by_operation),
            "matched_attempts": len(matched),
            "exact_token_matches": exact_token_matches,
            "missing_in_observability": len(
                set(usage_by_operation) - set(observability_by_operation)
            ),
            "missing_in_usage_events": len(
                set(observability_by_operation) - set(usage_by_operation)
            ),
            "token_delta": delta,
            "unpriced_usage_events": sum(
                row["credits_micro"] is None for row in usage_rows
            ),
        }

    def _usage_rows(
        self,
        *,
        user_id: str | None = None,
        workspace_id: str | None = None,
        start: datetime,
        end: datetime,
    ) -> list[dict[str, Any]]:
        statement = select(UsageEventModel.__table__).where(
            UsageEventModel.occurred_at >= start,
            UsageEventModel.occurred_at < end,
            # Archived events remain available for reconciliation and audit,
            # but must leave the operational usage view after archiving.
            UsageEventModel.archived_at.is_(None),
        )
        if user_id is not None:
            statement = statement.where(UsageEventModel.user_id == user_id)
        if workspace_id is not None:
            statement = statement.where(
                UsageEventModel.workspace_id == workspace_id
            )
        with self._engine.connect() as connection:
            return [dict(row) for row in connection.execute(statement).mappings()]

    @staticmethod
    def _snapshot(
        *,
        user_id: str,
        workspace_id: str | None,
        start: datetime,
        end: datetime,
        rows: list[dict[str, Any]],
        granularity: UsageGranularity = "day",
    ) -> dict[str, Any]:
        tokens = {
            field: sum(int(row[field] or 0) for row in rows)
            for field in TOKEN_FIELDS
        }
        breakdown: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        for row in rows:
            occurred_at = _utc(row["occurred_at"])
            period_start, period_end = UsageReadService._period_bounds(
                occurred_at, granularity
            )
            key = (
                period_start.date().isoformat(),
                str(row["purpose"]),
                str(row["provider"]),
                str(row["provider_model"]),
            )
            item = breakdown.setdefault(
                key,
                {
                    "day": key[0],
                    "period_start": period_start.isoformat(),
                    "period_end": period_end.isoformat(),
                    "granularity": granularity,
                    "purpose": key[1],
                    "provider": key[2],
                    "provider_model": key[3],
                    "events": 0,
                    "priced_events": 0,
                    "unpriced_events": 0,
                    "priced_credits_micro": 0,
                    **{field: 0 for field in TOKEN_FIELDS},
                },
            )
            item["events"] += 1
            if row["credits_micro"] is None:
                item["unpriced_events"] += 1
            else:
                item["priced_events"] += 1
                item["priced_credits_micro"] += int(row["credits_micro"])
            for field in TOKEN_FIELDS:
                item[field] += int(row[field] or 0)

        unpriced_events = sum(row["credits_micro"] is None for row in rows)
        priced_credits_micro = sum(
            int(row["credits_micro"])
            for row in rows
            if row["credits_micro"] is not None
        )
        return {
            "user_id": user_id,
            "workspace_id": workspace_id,
            "period_days": max(1, (end - start).days),
            "from": start.isoformat(),
            "to": end.isoformat(),
            "granularity": granularity,
            "events": len(rows),
            "priced_events": len(rows) - unpriced_events,
            "unpriced_events": unpriced_events,
            "credits_complete": unpriced_events == 0,
            "credit_status": "complete" if unpriced_events == 0 else "partial",
            "credits_micro": (
                priced_credits_micro if unpriced_events == 0 else None
            ),
            "priced_credits_micro": priced_credits_micro,
            "tokens": tokens,
            "breakdown": [
                breakdown[key] for key in sorted(breakdown)
            ],
        }

    @staticmethod
    def _period_bounds(
        occurred_at: datetime, granularity: UsageGranularity
    ) -> tuple[datetime, datetime]:
        period_start = occurred_at.replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        if granularity == "week":
            period_start -= timedelta(days=period_start.weekday())
            return period_start, period_start + timedelta(days=7)
        return period_start, period_start + timedelta(days=1)

    def _observability_model_spans(
        self, *, start: datetime, end: datetime
    ) -> dict[str, dict[str, Any]]:
        with self._engine.connect() as connection:
            rows = connection.execute(
                select(ObservabilityRecordModel.payload_json).where(
                    ObservabilityRecordModel.kind == "span"
                )
            ).scalars()
        result: dict[str, dict[str, Any]] = {}
        for envelope in rows:
            span = (envelope or {}).get("payload") or {}
            if span.get("kind") != "model":
                continue
            completed_at = span.get("completed_at")
            if not completed_at:
                continue
            try:
                occurred_at = datetime.fromisoformat(str(completed_at))
            except ValueError:
                continue
            occurred_at = _utc(occurred_at)
            if not (start <= occurred_at < end):
                continue
            operation_id = str((span.get("attributes") or {}).get("operation_id") or "")
            if operation_id:
                result[operation_id] = span
        return result

    @staticmethod
    def _observed_tokens(span: dict[str, Any]) -> dict[str, int]:
        usage = span.get("usage") or {}
        input_details = usage.get("input_token_details") or {}
        output_details = usage.get("output_token_details") or {}
        return {
            "input_tokens": int(usage.get("input_tokens") or 0),
            "cached_input_tokens": int(
                input_details.get("cache_read", usage.get("cached_tokens", 0))
                or 0
            ),
            "cache_write_input_tokens": int(
                input_details.get("cache_write", 0) or 0
            ),
            "output_tokens": int(usage.get("output_tokens") or 0),
            "reasoning_output_tokens": int(
                output_details.get("reasoning", usage.get("reasoning_tokens", 0))
                or 0
            ),
            "total_tokens": int(usage.get("total_tokens") or 0),
        }

    def close(self) -> None:
        if self._quota_service is not None:
            self._quota_service.close()
        if self._owns_engine:
            self._engine.dispose()
