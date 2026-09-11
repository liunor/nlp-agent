"""Read-side usage snapshots and Shadow comparison reports."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from collections import defaultdict
from typing import Any, Literal, Mapping

from sqlalchemy import BigInteger, Engine, case, cast, create_engine, func, literal, literal_column, select

from server.infrastructure.mysql.models import ObservabilityRecordModel
from server.quota.models import UsageEventModel
from server.quota.service import QuotaService


TOKEN_FIELDS = (
    "input_tokens",
    "cached_input_tokens",
    "cache_miss_input_tokens",
    "cache_write_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
    "total_tokens",
)

UsageGranularity = Literal["day", "week", "five_minute"]
SystemUsageDimension = Literal["users", "workspaces", "providers", "purposes", "models"]
SYSTEM_DIMENSION_PREVIEW_LIMIT = 50


def _cache_metrics(input_tokens: Any, cached_input_tokens: Any) -> dict[str, Any]:
    """Return Provider-only cache totals and a rate from bounded token facts."""
    input_value = max(0, int(input_tokens or 0))
    cached_value = min(max(0, int(cached_input_tokens or 0)), input_value)
    return {
        "cache_input_tokens": input_value,
        "cache_cached_input_tokens": cached_value,
        "cache_hit_rate": cached_value / input_value if input_value else None,
    }


def _provider_cache_tokens(rows: list[Mapping[str, Any]]) -> dict[str, int]:
    """Return cache-rate inputs from Provider-measured UsageEvents only."""
    provider_rows = [row for row in rows if row.get("usage_source") == "provider"]
    return {
        "input_tokens": sum(
            int(row.get("input_tokens") or 0) for row in provider_rows
        ),
        "cached_input_tokens": sum(
            int(row.get("cached_input_tokens") or 0) for row in provider_rows
        ),
    }


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
        if granularity not in {"day", "week", "five_minute"}:
            raise ValueError("granularity must be 'day', 'week', or 'five_minute'")
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

    def system_snapshot(
        self,
        *,
        days: int = 30,
        granularity: UsageGranularity = "day",
        include_users: bool = True,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Return the detailed, unscoped usage view used by the monitor.

        This is intentionally separate from ``user_snapshot``: the latter is a
        self-service view, while the monitor is an administrator surface that
        needs one canonical total plus drill-downs for every user, workspace,
        provider, model, and purpose.
        """
        if granularity not in {"day", "week", "five_minute"}:
            raise ValueError("granularity must be 'day', 'week', or 'five_minute'")
        end = _utc(now or _utc_now())
        start = end - timedelta(days=max(1, days))
        if granularity == "day":
            return self._system_snapshot_sql(
                start=start,
                end=end,
                include_users=include_users,
            )
        rows = self._usage_rows(start=start, end=end)
        snapshot = self._snapshot(
            user_id=None,
            workspace_id=None,
            start=start,
            end=end,
            rows=rows,
            granularity=granularity,
        )
        snapshot.update(
            {
                "scope": "system",
                "users": self._dimension_snapshots(rows, "user_id"),
                "workspaces": self._dimension_snapshots(rows, "workspace_id"),
                "providers": self._dimension_snapshots(rows, "provider"),
                "purposes": self._dimension_snapshots(rows, "purpose"),
                "models": self._dimension_snapshots(
                    rows, "provider_model", secondary_key="provider"
                ),
            }
        )
        return snapshot

    @staticmethod
    def _aggregate_expressions(table, *, include_provider_cache: bool = False):
        expressions = [
            func.count().label("events"),
            func.coalesce(
                func.sum(case((table.c.credits_micro.is_(None), 1), else_=0)), 0
            ).label("unpriced_events"),
            func.coalesce(
                func.sum(case((table.c.credits_micro.is_not(None), 1), else_=0)), 0
            ).label("priced_events"),
            func.coalesce(
                func.sum(
                    case((table.c.credits_micro.is_not(None), table.c.credits_micro), else_=0)
                ),
                0,
            ).label("priced_credits_micro"),
            *[
                func.coalesce(func.sum(table.c[field]), 0).label(field)
                for field in TOKEN_FIELDS
            ],
        ]
        if include_provider_cache:
            expressions.extend(
                [
                    func.coalesce(
                        func.sum(
                            case(
                                (
                                    table.c.usage_source == "provider",
                                    table.c.input_tokens,
                                ),
                                else_=0,
                            )
                        ),
                        0,
                    ).label("_provider_input_tokens"),
                    func.coalesce(
                        func.sum(
                            case(
                                (
                                    table.c.usage_source == "provider",
                                    table.c.cached_input_tokens,
                                ),
                                else_=0,
                            )
                        ),
                        0,
                    ).label("_provider_cached_input_tokens"),
                ]
            )
        return expressions

    @staticmethod
    def _dimension_item(row, key: str, *, secondary_key: str | None = None) -> dict[str, Any]:
        events = int(row["events"] or 0)
        unpriced_events = int(row["unpriced_events"] or 0)
        priced_credits_micro = int(row["priced_credits_micro"] or 0)
        item: dict[str, Any] = {
            key: str(row[key] or "unknown"),
            "events": events,
            "priced_events": int(row["priced_events"] or 0),
            "unpriced_events": unpriced_events,
            "credits_complete": unpriced_events == 0,
            "credit_status": "complete" if unpriced_events == 0 else "partial",
            "credits_micro": priced_credits_micro if unpriced_events == 0 else None,
            "priced_credits_micro": priced_credits_micro,
            "tokens": {
                field: int(row[field] or 0) for field in TOKEN_FIELDS
            },
        }
        if secondary_key is not None:
            item[secondary_key] = str(row[secondary_key] or "unknown")
        return item

    def _system_snapshot_sql(
        self,
        *,
        start: datetime,
        end: datetime,
        include_users: bool,
    ) -> dict[str, Any]:
        """Build the day-granularity monitor snapshot using SQL rollups.

        The monitor must not materialize every user's raw fact rows just to
        render totals.  Aggregates remain bounded by the number of dimensions
        and days; the optional user list is loaded only for the explicit
        drill-down request, which has its own paged endpoint.
        """
        table = UsageEventModel.__table__
        conditions = (
            table.c.occurred_at >= start,
            table.c.occurred_at < end,
            table.c.archived_at.is_(None),
        )
        totals_query = select(
            *self._aggregate_expressions(table, include_provider_cache=True)
        ).where(*conditions)
        period = func.date(table.c.occurred_at).label("period_label")
        # Keep the overview trend one row per day. Grouping by purpose,
        # provider, and model made this lightweight snapshot grow with every
        # integration; those dimensions have their own paginated endpoint.
        breakdown_query = (
            select(period, *self._aggregate_expressions(table))
            .where(*conditions)
            .group_by(period)
            .order_by(period)
        )

        dimensions = {
            "workspaces": ("workspace_id", None),
            "providers": ("provider", None),
            "purposes": ("purpose", None),
            "models": ("provider_model", "provider"),
        }
        if include_users:
            dimensions["users"] = ("user_id", None)

        with self._engine.connect() as connection:
            totals = connection.execute(totals_query).mappings().one()
            breakdown_rows = connection.execute(breakdown_query).mappings().all()
            dimension_rows = {}
            for name, (key, secondary_key) in dimensions.items():
                columns = [table.c[key]]
                if secondary_key is not None:
                    columns.append(table.c[secondary_key])
                query = (
                    select(*columns, *self._aggregate_expressions(table))
                    .where(*conditions)
                    .group_by(*columns)
                    .order_by(func.count().desc(), *columns)
                    .limit(SYSTEM_DIMENSION_PREVIEW_LIMIT)
                )
                dimension_rows[name] = connection.execute(query).mappings().all()

        unpriced_events = int(totals["unpriced_events"] or 0)
        priced_credits_micro = int(totals["priced_credits_micro"] or 0)
        output_breakdown = []
        for row in breakdown_rows:
            day = str(row["period_label"] or "")[:10]
            period_start = datetime.fromisoformat(f"{day}T00:00:00+00:00")
            output_breakdown.append(
                {
                    "day": day,
                    "period_start": period_start.isoformat(),
                    "period_end": (period_start + timedelta(days=1)).isoformat(),
                    "granularity": "day",
                    "purpose": "all",
                    "provider": "all",
                    "provider_model": "all",
                    "events": int(row["events"] or 0),
                    "priced_events": int(row["priced_events"] or 0),
                    "unpriced_events": int(row["unpriced_events"] or 0),
                    "priced_credits_micro": int(row["priced_credits_micro"] or 0),
                    **{field: int(row[field] or 0) for field in TOKEN_FIELDS},
                }
            )

        tokens = {field: int(totals[field] or 0) for field in TOKEN_FIELDS}
        output = {
            "scope": "system",
            "user_id": None,
            "workspace_id": None,
            "period_days": max(1, (end - start).days),
            "from": start.isoformat(),
            "to": end.isoformat(),
            "granularity": "day",
            "events": int(totals["events"] or 0),
            "priced_events": int(totals["priced_events"] or 0),
            "unpriced_events": unpriced_events,
            "credits_complete": unpriced_events == 0,
            "credit_status": "complete" if unpriced_events == 0 else "partial",
            "credits_micro": priced_credits_micro if unpriced_events == 0 else None,
            "priced_credits_micro": priced_credits_micro,
            "tokens": tokens,
            **_cache_metrics(
                totals.get("_provider_input_tokens", 0),
                totals.get("_provider_cached_input_tokens", 0),
            ),
            "breakdown": output_breakdown,
        }
        for name, (key, secondary_key) in dimensions.items():
            output[name] = [
                self._dimension_item(row, key, secondary_key=secondary_key)
                for row in dimension_rows[name]
            ]
        for name in ("users", "workspaces", "providers", "purposes", "models"):
            output.setdefault(name, [])
        return output

    def system_dimension_page(
        self,
        *,
        dimension: SystemUsageDimension,
        days: int = 30,
        limit: int = 12,
        offset: int = 0,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Return one SQL-aggregated page for a high-cardinality dimension."""
        dimension_config: dict[SystemUsageDimension, tuple[str, str | None]] = {
            "users": ("user_id", None),
            "workspaces": ("workspace_id", None),
            "providers": ("provider", None),
            "purposes": ("purpose", None),
            "models": ("provider_model", "provider"),
        }
        if dimension not in dimension_config:
            raise ValueError(f"unsupported usage dimension: {dimension}")
        if limit < 1:
            raise ValueError("limit must be positive")
        if offset < 0:
            raise ValueError("offset must not be negative")
        key, secondary_key = dimension_config[dimension]
        end = _utc(now or _utc_now())
        start = end - timedelta(days=max(1, days))
        table = UsageEventModel.__table__
        conditions = (
            table.c.occurred_at >= start,
            table.c.occurred_at < end,
            table.c.archived_at.is_(None),
        )
        columns = [table.c[key]]
        if secondary_key is not None:
            columns.append(table.c[secondary_key])
        grouped = (
            select(*columns)
            .where(*conditions)
            .group_by(*columns)
            .subquery()
        )
        page_query = (
            select(*columns, *self._aggregate_expressions(table))
            .where(*conditions)
            .group_by(*columns)
            .order_by(func.count().desc(), *columns)
            .offset(offset)
            .limit(min(max(1, limit), 100))
        )
        with self._engine.connect() as connection:
            total = int(connection.execute(select(func.count()).select_from(grouped)).scalar_one())
            rows = connection.execute(page_query).mappings().all()
        return {
            "scope": "system",
            "dimension": dimension,
            "period_days": max(1, days),
            "from": start.isoformat(),
            "to": end.isoformat(),
            "items": [
                self._dimension_item(row, key, secondary_key=secondary_key)
                for row in rows
            ],
            "total": total,
            "offset": offset,
            "limit": min(max(1, limit), 100),
            "has_more": offset + len(rows) < total,
        }

    def system_user_page(
        self,
        *,
        days: int = 30,
        limit: int = 12,
        offset: int = 0,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Return one bounded page of the all-user usage ledger.

        Aggregation still uses the canonical UsageEvent facts; this boundary
        keeps the monitor response and DOM bounded as the user count grows.
        """
        if limit < 1:
            raise ValueError("limit must be positive")
        if offset < 0:
            raise ValueError("offset must not be negative")
        end = _utc(now or _utc_now())
        start = end - timedelta(days=max(1, days))
        table = UsageEventModel.__table__
        user_column = table.c.user_id
        conditions = (
            table.c.occurred_at >= start,
            table.c.occurred_at < end,
            table.c.archived_at.is_(None),
        )
        aggregates = [
            func.count().label("events"),
            func.sum(
                case((table.c.credits_micro.is_(None), 1), else_=0)
            ).label("unpriced_events"),
            func.sum(
                case((table.c.credits_micro.is_not(None), table.c.credits_micro), else_=0)
            ).label("priced_credits_micro"),
            *[func.sum(table.c[field]).label(field) for field in TOKEN_FIELDS],
        ]
        count_query = (
            select(user_column)
            .where(*conditions)
            .group_by(user_column)
            .subquery()
        )
        page_query = (
            select(user_column, *aggregates)
            .where(*conditions)
            .group_by(user_column)
            .order_by(func.count().desc(), user_column)
            .offset(offset)
            .limit(limit)
        )
        with self._engine.connect() as connection:
            total = int(connection.execute(select(func.count()).select_from(count_query)).scalar_one())
            page_rows = connection.execute(page_query).mappings()
            page = []
            for row in page_rows:
                events = int(row["events"] or 0)
                unpriced_events = int(row["unpriced_events"] or 0)
                priced_credits_micro = int(row["priced_credits_micro"] or 0)
                page.append(
                    {
                        "user_id": str(row["user_id"] or "unknown"),
                        "events": events,
                        "priced_events": events - unpriced_events,
                        "unpriced_events": unpriced_events,
                        "credits_complete": unpriced_events == 0,
                        "credit_status": "complete" if unpriced_events == 0 else "partial",
                        "credits_micro": priced_credits_micro if unpriced_events == 0 else None,
                        "priced_credits_micro": priced_credits_micro,
                        "tokens": {field: int(row[field] or 0) for field in TOKEN_FIELDS},
                    }
                )
        return {
            "scope": "system",
            "period_days": max(1, days),
            "from": start.isoformat(),
            "to": end.isoformat(),
            "items": page,
            "total": total,
            "offset": offset,
            "limit": limit,
            "has_more": offset + len(page) < total,
        }

    def system_trend(
        self,
        *,
        window_minutes: int = 120,
        bucket_minutes: int = 5,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Return the all-user canonical ledger in a bounded five-minute window."""
        if window_minutes < 5:
            raise ValueError("window_minutes must be at least 5")
        if bucket_minutes < 1 or bucket_minutes > window_minutes:
            raise ValueError("bucket_minutes must be between 1 and window_minutes")
        end = _utc(now or _utc_now())
        start = end - timedelta(minutes=window_minutes)
        table = UsageEventModel.__table__
        conditions = (
            table.c.occurred_at >= start,
            table.c.occurred_at < end,
            table.c.archived_at.is_(None),
        )
        with self._engine.connect() as connection:
            bucket = self._bucket_epoch_expression(
                connection, table, bucket_minutes=bucket_minutes
            )
            query = (
                select(
                    bucket.label("bucket_epoch"),
                    *self._aggregate_expressions(table, include_provider_cache=True),
                )
                .where(*conditions)
                .group_by(bucket)
                .order_by(bucket)
            )
            rows = connection.execute(query).mappings().all()
        # The trend is a chart feed, not a ledger export. Aggregate in the
        # database so a busy five-minute window produces one row per bucket,
        # regardless of how many users or provider attempts created it.
        snapshot = self._aggregate_bucket_snapshot(
            start=start,
            end=end,
            rows=rows,
            bucket_minutes=bucket_minutes,
        )
        snapshot.update(
            {
                "scope": "system",
                "window_minutes": window_minutes,
                "bucket_minutes": bucket_minutes,
                "users": [],
                "workspaces": [],
                "providers": [],
                "purposes": [],
                "models": [],
            }
        )
        return snapshot

    @staticmethod
    def _bucket_epoch_expression(connection: Any, table: Any, *, bucket_minutes: int):
        """Return a portable epoch bucket expression for the configured DB."""
        bucket_seconds = bucket_minutes * 60
        dialect = str(getattr(connection.dialect, "name", "")).lower()
        if dialect == "mysql":
            epoch_seconds = func.timestampdiff(
                literal_column("SECOND"),
                literal("1970-01-01 00:00:00"),
                table.c.occurred_at,
            )
        elif dialect == "sqlite":
            epoch_seconds = cast(func.strftime("%s", table.c.occurred_at), BigInteger)
        else:
            # PostgreSQL-compatible fallback for deployments that use a
            # different analytics database in development or CI.
            epoch_seconds = cast(func.extract("epoch", table.c.occurred_at), BigInteger)
        return cast(
            func.floor(epoch_seconds / bucket_seconds) * bucket_seconds,
            BigInteger,
        )

    @staticmethod
    def _aggregate_bucket_snapshot(
        *,
        start: datetime,
        end: datetime,
        rows: list[dict[str, Any]],
        bucket_minutes: int,
    ) -> dict[str, Any]:
        token_totals = {field: 0 for field in TOKEN_FIELDS}
        events = priced_events = unpriced_events = priced_credits_micro = 0
        breakdown: list[dict[str, Any]] = []
        bucket_delta = timedelta(minutes=bucket_minutes)
        for row in rows:
            bucket_epoch = int(row["bucket_epoch"])
            period_start = datetime.fromtimestamp(bucket_epoch, tz=timezone.utc)
            row_events = int(row["events"] or 0)
            row_priced = int(row["priced_events"] or 0)
            row_unpriced = int(row["unpriced_events"] or 0)
            row_credits = int(row["priced_credits_micro"] or 0)
            events += row_events
            priced_events += row_priced
            unpriced_events += row_unpriced
            priced_credits_micro += row_credits
            token_values = {
                field: int(row[field] or 0) for field in TOKEN_FIELDS
            }
            for field, value in token_values.items():
                token_totals[field] += value
            breakdown.append(
                {
                    "day": period_start.isoformat(),
                    "period_start": period_start.isoformat(),
                    "period_end": (period_start + bucket_delta).isoformat(),
                    "granularity": "five_minute" if bucket_minutes == 5 else f"{bucket_minutes}_minute",
                    "purpose": "all",
                    "provider": "all",
                    "provider_model": "all",
                    "events": row_events,
                    "priced_events": row_priced,
                    "unpriced_events": row_unpriced,
                    "priced_credits_micro": row_credits,
                    **token_values,
                }
            )
        return {
            "user_id": None,
            "workspace_id": None,
            "period_days": max(1, (end - start).days),
            "from": start.isoformat(),
            "to": end.isoformat(),
            "granularity": "five_minute" if bucket_minutes == 5 else f"{bucket_minutes}_minute",
            "events": events,
            "priced_events": priced_events,
            "unpriced_events": unpriced_events,
            "credits_complete": unpriced_events == 0,
            "credit_status": "complete" if unpriced_events == 0 else "partial",
            "credits_micro": priced_credits_micro if unpriced_events == 0 else None,
            "priced_credits_micro": priced_credits_micro,
            "tokens": token_totals,
            **_cache_metrics(
                sum(int(row.get("_provider_input_tokens") or 0) for row in rows),
                sum(
                    int(row.get("_provider_cached_input_tokens") or 0)
                    for row in rows
                ),
            ),
            "breakdown": breakdown,
        }

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
        user_id: str | None,
        workspace_id: str | None,
        start: datetime,
        end: datetime,
        rows: list[dict[str, Any]],
        granularity: UsageGranularity = "day",
        bucket_minutes: int = 5,
    ) -> dict[str, Any]:
        tokens = {
            field: sum(int(row[field] or 0) for row in rows)
            for field in TOKEN_FIELDS
        }
        breakdown: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        for row in rows:
            occurred_at = _utc(row["occurred_at"])
            period_start, period_end = UsageReadService._period_bounds(
                occurred_at, granularity, bucket_minutes
            )
            period_label = (
                period_start.isoformat()
                if granularity == "five_minute"
                else period_start.date().isoformat()
            )
            key = (
                period_label,
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
            **_cache_metrics(**_provider_cache_tokens(rows)),
            "breakdown": [
                breakdown[key] for key in sorted(breakdown)
            ],
        }

    @staticmethod
    def _dimension_snapshots(
        rows: list[dict[str, Any]],
        key: str,
        *,
        secondary_key: str | None = None,
    ) -> list[dict[str, Any]]:
        grouped: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            values = [str(row.get(key) or "unknown")]
            if secondary_key is not None:
                values.append(str(row.get(secondary_key) or "unknown"))
            grouped[tuple(values)].append(row)

        result = []
        for values, grouped_rows in grouped.items():
            unpriced_events = sum(
                row["credits_micro"] is None for row in grouped_rows
            )
            priced_credits_micro = sum(
                int(row["credits_micro"])
                for row in grouped_rows
                if row["credits_micro"] is not None
            )
            item: dict[str, Any] = {
                key: values[0],
                "events": len(grouped_rows),
                "priced_events": len(grouped_rows) - unpriced_events,
                "unpriced_events": unpriced_events,
                "credits_complete": unpriced_events == 0,
                "credit_status": "complete" if unpriced_events == 0 else "partial",
                "credits_micro": (
                    priced_credits_micro if unpriced_events == 0 else None
                ),
                "priced_credits_micro": priced_credits_micro,
                "tokens": {
                    field: sum(int(row[field] or 0) for row in grouped_rows)
                    for field in TOKEN_FIELDS
                },
            }
            if secondary_key is not None:
                item[secondary_key] = values[1]
            result.append(item)
        return sorted(
            result,
            key=lambda item: (-item["events"], str(item.get(key, "")), str(item.get(secondary_key or "", ""))),
        )

    @staticmethod
    def _period_bounds(
        occurred_at: datetime,
        granularity: UsageGranularity,
        bucket_minutes: int = 5,
    ) -> tuple[datetime, datetime]:
        if granularity == "five_minute":
            minute = (occurred_at.minute // bucket_minutes) * bucket_minutes
            period_start = occurred_at.replace(
                minute=minute, second=0, microsecond=0
            )
            return period_start, period_start + timedelta(minutes=bucket_minutes)
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
            "cache_miss_input_tokens": int(
                input_details.get(
                    "cache_miss", usage.get("prompt_cache_miss_tokens", 0)
                )
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
