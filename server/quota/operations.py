"""Phase 4 read-side operations, reconciliation, and recovery tools.

This module is deliberately outside the Turn admission path.  Every method
operates on immutable UsageEvent/Ledger facts and writes either derived read
models or append-only correction records.
"""

from __future__ import annotations

import hashlib
import uuid
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Mapping

from sqlalchemy import Engine, case, create_engine, delete, func, insert, select, update
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError

from server.infrastructure.mysql.models import (
    ClassroomMemberModel,
    RoleModel,
    UserModel,
    UserRoleModel,
)
from server.quota.management import QuotaManagementService
from server.quota.models import (
    QuotaAlertModel,
    QuotaAdjustmentModel,
    QuotaBucketModel,
    QuotaCreditOperationModel,
    QuotaCreditScopeLockModel,
    QuotaDailyRollupModel,
    QuotaGrantModel,
    QuotaLedgerEntryModel,
    QuotaPolicyModel,
    QuotaProviderBillingModel,
    QuotaRoleCreditOperationModel,
    QuotaUsageArchiveBatchModel,
    UsageEventModel,
)


UTC = timezone.utc
TOKEN_FIELDS = (
    "input_tokens",
    "cached_input_tokens",
    "cache_write_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
    "total_tokens",
)
_REPLAY_ENTRY_TYPES = (
    "reserve",
    "settle",
    "reconcile",
    "release",
    "billing_adjustment",
)
_OWNER_TYPES = {"user", "workspace", "classroom"}
_BUCKET_TYPES = {"daily", "weekly"}


def _bounded_idempotency_key(prefix: str, *parts: object) -> str:
    """Keep derived Ledger keys within the MySQL VARCHAR(255) limit."""
    value = ":".join((prefix, *(str(part) for part in parts)))
    if len(value) <= 255:
        return value
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return f"{prefix}:sha256:{digest}"


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _db_time(value: datetime) -> datetime:
    return _utc(value).replace(tzinfo=None)


def _day_bounds(value: date) -> tuple[datetime, datetime]:
    start = datetime(value.year, value.month, value.day, tzinfo=UTC)
    return start, start + timedelta(days=1)


def _payload_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return _utc(value).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return value


def _json_safe(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return _payload_value(value)
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _credit_scope_key(kwargs: Mapping[str, Any]) -> str:
    return _bounded_idempotency_key(
        "credit-scope",
        kwargs["owner_type"],
        kwargs["owner_id"],
        kwargs["bucket_type"],
        _db_time(kwargs["period_start"]).isoformat(),
        _db_time(kwargs["period_end"]).isoformat(),
    )


def _credit_idempotency_scope_key(operation_key: str) -> str:
    return _bounded_idempotency_key("credit-idempotency", operation_key)


@dataclass(frozen=True)
class BucketReplay:
    bucket_id: str
    stored_consumed_micro: int
    stored_reserved_micro: int
    expected_consumed_micro: int
    expected_reserved_micro: int
    expected_over_limit: bool
    ledger_entries: int
    needs_repair: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "bucket_id": self.bucket_id,
            "stored_consumed_micro": self.stored_consumed_micro,
            "stored_reserved_micro": self.stored_reserved_micro,
            "expected_consumed_micro": self.expected_consumed_micro,
            "expected_reserved_micro": self.expected_reserved_micro,
            "expected_over_limit": self.expected_over_limit,
            "ledger_entries": self.ledger_entries,
            "needs_repair": self.needs_repair,
        }


class QuotaOperationsService:
    """Run durable Phase 4 jobs without adding latency to Turn execution."""

    def __init__(self, database: str | Engine) -> None:
        if isinstance(database, str):
            if database.startswith("mysql+aiomysql://"):
                database = database.replace("mysql+aiomysql://", "mysql+pymysql://", 1)
            self._engine = create_engine(database, pool_pre_ping=True)
            self._owns_engine = True
        else:
            self._engine = database
            self._owns_engine = False

    @property
    def engine(self) -> Engine:
        return self._engine

    def close(self) -> None:
        if self._owns_engine:
            self._engine.dispose()

    # ---- Ledger replay and repair -------------------------------------

    def replay_bucket(self, bucket_id: str) -> BucketReplay:
        with self._engine.connect() as connection:
            return self._replay_bucket(connection, bucket_id)

    def repair_bucket(
        self,
        bucket_id: str,
        *,
        actor_user_id: str,
        reason: str,
        idempotency_key: str,
    ) -> BucketReplay:
        if not reason.strip() or not idempotency_key.strip():
            raise ValueError("reason and idempotency_key are required")
        with self._engine.begin() as connection:
            existing = connection.execute(
                select(QuotaLedgerEntryModel)
                .where(
                    QuotaLedgerEntryModel.idempotency_key
                    == _bounded_idempotency_key("balance-repair", idempotency_key)
                )
                .with_for_update()
            ).mappings().first()
            if existing is not None:
                if existing["bucket_id"] != bucket_id:
                    raise ValueError("balance repair idempotency key conflicts with another bucket")
                return self._replay_bucket(connection, bucket_id)
            bucket = connection.execute(
                select(QuotaBucketModel)
                .where(QuotaBucketModel.id == bucket_id)
                .with_for_update()
            ).mappings().first()
            if bucket is None:
                raise KeyError(bucket_id)
            replay = self._replay_bucket(connection, bucket_id)
            connection.execute(
                update(QuotaBucketModel)
                .where(QuotaBucketModel.id == bucket_id)
                .values(
                    consumed_micro=replay.expected_consumed_micro,
                    reserved_micro=replay.expected_reserved_micro,
                    over_limit=replay.expected_over_limit,
                    version=int(bucket["version"]) + 1,
                    updated_at=_db_time(datetime.now(UTC)),
                )
            )
            connection.execute(
                insert(QuotaLedgerEntryModel).values(
                    id=str(uuid.uuid4()),
                    reservation_id=None,
                    bucket_id=bucket_id,
                    grant_id=None,
                    entry_type="balance_repair",
                    amount_micro=(
                        replay.expected_consumed_micro
                        - replay.stored_consumed_micro
                    ),
                    reserved_delta_micro=0,
                    consumed_delta_micro=0,
                    idempotency_key=_bounded_idempotency_key(
                        "balance-repair", idempotency_key
                    ),
                    actor_user_id=actor_user_id,
                    reason=reason,
                    metadata_json={
                        "bucket_id": bucket_id,
                        "stored_consumed_micro": replay.stored_consumed_micro,
                        "stored_reserved_micro": replay.stored_reserved_micro,
                        "expected_consumed_micro": replay.expected_consumed_micro,
                        "expected_reserved_micro": replay.expected_reserved_micro,
                        "source": "ledger_replay",
                    },
                    created_at=_db_time(datetime.now(UTC)),
                )
            )
            return self._replay_bucket(connection, bucket_id)

    @staticmethod
    def _replay_bucket(connection: Connection, bucket_id: str) -> BucketReplay:
        bucket = connection.execute(
            select(QuotaBucketModel).where(QuotaBucketModel.id == bucket_id)
        ).mappings().first()
        if bucket is None:
            raise KeyError(bucket_id)
        entries = connection.execute(
            select(
                QuotaLedgerEntryModel.reserved_delta_micro,
                QuotaLedgerEntryModel.consumed_delta_micro,
            ).where(
                QuotaLedgerEntryModel.bucket_id == bucket_id,
                QuotaLedgerEntryModel.entry_type.in_(_REPLAY_ENTRY_TYPES),
            )
        ).mappings().all()
        expected_reserved = max(
            0, sum(int(row["reserved_delta_micro"] or 0) for row in entries)
        )
        expected_consumed = max(
            0, sum(int(row["consumed_delta_micro"] or 0) for row in entries)
        )
        expected_over_limit = QuotaOperationsService._bucket_over_limit(
            connection,
            bucket,
            consumed_micro=expected_consumed,
            at=datetime.now(UTC),
        )
        stored_consumed = int(bucket["consumed_micro"])
        stored_reserved = int(bucket["reserved_micro"])
        return BucketReplay(
            bucket_id=bucket_id,
            stored_consumed_micro=stored_consumed,
            stored_reserved_micro=stored_reserved,
            expected_consumed_micro=expected_consumed,
            expected_reserved_micro=expected_reserved,
            expected_over_limit=expected_over_limit,
            ledger_entries=len(entries),
            needs_repair=(
                stored_consumed != expected_consumed
                or stored_reserved != expected_reserved
                or bool(bucket["over_limit"]) != expected_over_limit
            ),
        )

    # ---- Provider billing reconciliation ------------------------------

    def reconcile_provider_billing(
        self, statements: Iterable[Mapping[str, Any]], *, now: datetime | None = None
    ) -> dict[str, Any]:
        checked_at = _utc(now or datetime.now(UTC))
        items: list[dict[str, Any]] = []
        with self._engine.begin() as connection:
            for statement in statements:
                item = self._reconcile_billing_line(connection, statement, checked_at)
                items.append(item)
        return {
            "total": len(items),
            "matched": sum(item["status"] == "matched" for item in items),
            "discrepancies": sum(item["status"] == "discrepancy" for item in items),
            "unmatched": sum(item["status"] == "unmatched" for item in items),
            "pending": sum(item["status"] == "pending" for item in items),
            "items": items,
        }

    def list_billing_reconciliation(
        self, *, status: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        with self._engine.connect() as connection:
            statement = select(QuotaProviderBillingModel).order_by(
                QuotaProviderBillingModel.billed_at.desc()
            ).limit(max(1, min(limit, 1000)))
            if status:
                statement = statement.where(QuotaProviderBillingModel.status == status)
            return [self._billing_payload(row) for row in connection.execute(statement).mappings()]

    def repair_billing(
        self,
        billing_id: str,
        *,
        actor_user_id: str,
        reason: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        if not reason.strip() or not idempotency_key.strip():
            raise ValueError("reason and idempotency_key are required")
        with self._engine.begin() as connection:
            billing = connection.execute(
                select(QuotaProviderBillingModel)
                .where(QuotaProviderBillingModel.id == billing_id)
                .with_for_update()
            ).mappings().first()
            if billing is None:
                raise KeyError(billing_id)
            if billing["status"] == "repaired":
                return self._billing_payload(billing)
            if billing["status"] != "discrepancy" or billing["difference_micro"] is None:
                raise ValueError("only a billing discrepancy can be repaired")
            usage = connection.execute(
                select(UsageEventModel)
                .where(UsageEventModel.id == billing["matched_usage_event_id"])
            ).mappings().first()
            if usage is None:
                raise ValueError("billing discrepancy has no UsageEvent")
            reservation_id = usage["reservation_id"]
            bucket_ids: list[str] = []
            if reservation_id:
                bucket_ids = list(
                    connection.execute(
                        select(QuotaLedgerEntryModel.bucket_id)
                        .where(
                            QuotaLedgerEntryModel.reservation_id == reservation_id,
                            QuotaLedgerEntryModel.entry_type == "reserve",
                            QuotaLedgerEntryModel.bucket_id.is_not(None),
                        )
                        .distinct()
                    ).scalars()
                )
            delta = int(billing["difference_micro"])
            for bucket_id in bucket_ids:
                bucket = connection.execute(
                    select(QuotaBucketModel)
                    .where(QuotaBucketModel.id == bucket_id)
                    .with_for_update()
                ).mappings().one()
                applied_delta = delta
                if delta < 0:
                    applied_delta = max(-int(bucket["consumed_micro"]), delta)
                next_consumed = int(bucket["consumed_micro"]) + applied_delta
                connection.execute(
                    update(QuotaBucketModel)
                    .where(QuotaBucketModel.id == bucket_id)
                    .values(
                        consumed_micro=next_consumed,
                        over_limit=self._bucket_over_limit(
                            connection,
                            bucket,
                            consumed_micro=next_consumed,
                            at=datetime.now(UTC),
                        ),
                        version=int(bucket["version"]) + 1,
                        updated_at=_db_time(datetime.now(UTC)),
                    )
                )
                connection.execute(
                    insert(QuotaLedgerEntryModel).values(
                        id=str(uuid.uuid4()),
                        reservation_id=reservation_id,
                        bucket_id=bucket_id,
                        grant_id=None,
                        entry_type="billing_adjustment",
                        amount_micro=applied_delta,
                        reserved_delta_micro=0,
                        consumed_delta_micro=applied_delta,
                        idempotency_key=_bounded_idempotency_key(
                            "billing-repair", billing_id, idempotency_key, bucket_id
                        ),
                        actor_user_id=actor_user_id,
                        reason=reason,
                        metadata_json={
                            "billing_id": billing_id,
                            "operation_id": billing["operation_id"],
                            "provider_difference_micro": delta,
                        },
                        created_at=_db_time(datetime.now(UTC)),
                    )
                )
            connection.execute(
                update(QuotaProviderBillingModel)
                .where(QuotaProviderBillingModel.id == billing_id)
                .values(
                    status="repaired",
                    reconciled_at=_db_time(datetime.now(UTC)),
                )
            )
            updated = connection.execute(
                select(QuotaProviderBillingModel)
                .where(QuotaProviderBillingModel.id == billing_id)
            ).mappings().one()
            return self._billing_payload(updated)

    def _reconcile_billing_line(
        self, connection: Connection, statement: Mapping[str, Any], checked_at: datetime
    ) -> dict[str, Any]:
        required = ("provider", "statement_id", "operation_id", "idempotency_key")
        missing = [key for key in required if not str(statement.get(key) or "").strip()]
        if missing:
            raise ValueError(f"billing statement missing fields: {', '.join(missing)}")
        provider = str(statement["provider"])
        statement_id = str(statement["statement_id"])
        operation_id = str(statement["operation_id"])
        idempotency_key = str(statement["idempotency_key"])
        billed_at = _utc(statement.get("billed_at") or checked_at)
        billed_credits = statement.get("billed_credits_micro")
        if billed_credits is not None:
            if isinstance(billed_credits, bool) or not isinstance(billed_credits, int) or billed_credits < 0:
                raise ValueError("billed_credits_micro must be a non-negative integer")
        billed_tokens = dict(statement.get("billed_tokens") or {})
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in billed_tokens.values()
        ):
            raise ValueError("billed_tokens values must be non-negative integers")
        raw_payload = _json_safe(statement)
        existing = connection.execute(
            select(QuotaProviderBillingModel)
            .where(QuotaProviderBillingModel.idempotency_key == idempotency_key)
            .with_for_update()
        ).mappings().first()
        if existing is not None:
            if (
                existing["provider"] != provider
                or existing["statement_id"] != statement_id
                or existing["operation_id"] != operation_id
                or existing["billed_at"] != _db_time(billed_at)
                or existing["billed_credits_micro"] != billed_credits
                or existing["billed_tokens_json"] != billed_tokens
            ):
                raise ValueError("billing idempotency key conflicts with existing statement")
            return self._refresh_billing_match(connection, existing, checked_at)
        statement_existing = connection.execute(
            select(QuotaProviderBillingModel)
            .where(
                QuotaProviderBillingModel.provider == provider,
                QuotaProviderBillingModel.statement_id == statement_id,
            )
            .with_for_update()
        ).mappings().first()
        if statement_existing is not None:
            if (
                statement_existing["operation_id"] != operation_id
                or statement_existing["billed_at"] != _db_time(billed_at)
                or statement_existing["billed_credits_micro"] != billed_credits
                or statement_existing["billed_tokens_json"] != billed_tokens
            ):
                raise ValueError("provider statement conflicts with an existing billing line")
            return self._refresh_billing_match(connection, statement_existing, checked_at)
        usage = connection.execute(
            select(UsageEventModel)
            .where(
                UsageEventModel.operation_id == operation_id,
                UsageEventModel.provider == provider,
            )
        ).mappings().first()
        values = self._billing_values(
            provider=provider,
            statement_id=statement_id,
            operation_id=operation_id,
            billed_at=billed_at,
            billed_credits_micro=billed_credits,
            billed_tokens=billed_tokens,
            idempotency_key=idempotency_key,
            raw_payload=raw_payload,
            usage=usage,
            checked_at=checked_at,
        )
        try:
            with connection.begin_nested():
                connection.execute(insert(QuotaProviderBillingModel).values(**values))
        except IntegrityError:
            winner = connection.execute(
                select(QuotaProviderBillingModel)
                .where(QuotaProviderBillingModel.idempotency_key == idempotency_key)
                .with_for_update()
            ).mappings().first()
            if winner is None:
                # A concurrent line may use a different idempotency key while
                # claiming the provider's unique statement identity.  Resolve
                # the committed winner by that business key before deciding
                # whether the request is a replay or a conflict.
                winner = connection.execute(
                    select(QuotaProviderBillingModel)
                    .where(
                        QuotaProviderBillingModel.provider == provider,
                        QuotaProviderBillingModel.statement_id == statement_id,
                    )
                    .with_for_update()
                ).mappings().first()
            if winner is None:
                raise
            if (
                winner["operation_id"] != operation_id
                or winner["billed_at"] != _db_time(billed_at)
                or winner["billed_credits_micro"] != billed_credits
                or winner["billed_tokens_json"] != billed_tokens
            ):
                raise ValueError("provider statement conflicts with an existing billing line")
            return self._refresh_billing_match(connection, winner, checked_at)
        row = connection.execute(
            select(QuotaProviderBillingModel)
            .where(QuotaProviderBillingModel.id == values["id"])
        ).mappings().one()
        return self._billing_payload(row)

    def _refresh_billing_match(
        self, connection: Connection, row: Mapping[str, Any], checked_at: datetime
    ) -> dict[str, Any]:
        if row["status"] == "repaired":
            return self._billing_payload(row)
        usage = connection.execute(
            select(UsageEventModel).where(
                UsageEventModel.operation_id == row["operation_id"],
                UsageEventModel.provider == row["provider"],
            )
        ).mappings().first()
        local = usage["credits_micro"] if usage is not None else None
        billed = row["billed_credits_micro"]
        if usage is None:
            status = "unmatched"
        elif billed is None or local is None:
            status = "pending"
        elif int(billed) == int(local):
            status = "matched"
        else:
            status = "discrepancy"
        difference = None if billed is None or local is None else int(billed) - int(local)
        connection.execute(
            update(QuotaProviderBillingModel)
            .where(QuotaProviderBillingModel.id == row["id"])
            .values(
                matched_usage_event_id=usage["id"] if usage is not None else None,
                local_credits_micro=local,
                difference_micro=difference,
                status=status,
                reconciled_at=_db_time(checked_at),
            )
        )
        updated = connection.execute(
            select(QuotaProviderBillingModel)
            .where(QuotaProviderBillingModel.id == row["id"])
        ).mappings().one()
        return self._billing_payload(updated)

    @staticmethod
    def _billing_values(**kwargs: Any) -> dict[str, Any]:
        usage = kwargs.pop("usage")
        billed = kwargs.pop("billed_credits_micro")
        local = usage["credits_micro"] if usage is not None else None
        if usage is None:
            status = "unmatched"
        elif billed is None or local is None:
            status = "pending"
        elif int(billed) == int(local):
            status = "matched"
        else:
            status = "discrepancy"
        return {
            "id": str(uuid.uuid4()),
            "provider": kwargs["provider"],
            "statement_id": kwargs["statement_id"],
            "operation_id": kwargs["operation_id"],
            "billed_at": _db_time(kwargs["billed_at"]),
            "billed_credits_micro": billed,
            "billed_tokens_json": kwargs["billed_tokens"],
            "matched_usage_event_id": usage["id"] if usage is not None else None,
            "local_credits_micro": local,
            "difference_micro": None if billed is None or local is None else int(billed) - int(local),
            "status": status,
            "idempotency_key": kwargs["idempotency_key"],
            "raw_payload_json": kwargs["raw_payload"],
            "reconciled_at": _db_time(kwargs["checked_at"]),
        }

    @staticmethod
    def _billing_payload(row: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "billing_id": row["id"],
            "provider": row["provider"],
            "statement_id": row["statement_id"],
            "operation_id": row["operation_id"],
            "billed_at": _payload_value(row["billed_at"]),
            "billed_credits_micro": row["billed_credits_micro"],
            "billed_tokens": row["billed_tokens_json"],
            "matched_usage_event_id": row["matched_usage_event_id"],
            "local_credits_micro": row["local_credits_micro"],
            "difference_micro": row["difference_micro"],
            "status": row["status"],
            "idempotency_key": row["idempotency_key"],
            "reconciled_at": _payload_value(row["reconciled_at"]),
        }

    @staticmethod
    def _bucket_over_limit(
        connection: Connection,
        bucket: Mapping[str, Any],
        *,
        consumed_micro: int,
        at: datetime,
    ) -> bool:
        limit = bucket["limit_micro"]
        if limit is None:
            return False
        at_db = _db_time(at)
        grant = connection.execute(
            select(func.coalesce(func.sum(QuotaGrantModel.allocated_micro), 0)).where(
                QuotaGrantModel.owner_type == bucket["owner_type"],
                QuotaGrantModel.owner_id == bucket["owner_id"],
                QuotaGrantModel.bucket_type == bucket["bucket_type"],
                QuotaGrantModel.period_start == bucket["period_start"],
                QuotaGrantModel.period_end == bucket["period_end"],
                QuotaGrantModel.status == "active",
                QuotaGrantModel.effective_from <= at_db,
                (QuotaGrantModel.expires_at.is_(None)) | (QuotaGrantModel.expires_at > at_db),
            )
        ).scalar_one()
        adjustment = connection.execute(
            select(func.coalesce(func.sum(QuotaAdjustmentModel.amount_micro), 0)).where(
                QuotaAdjustmentModel.owner_type == bucket["owner_type"],
                QuotaAdjustmentModel.owner_id == bucket["owner_id"],
                QuotaAdjustmentModel.bucket_type == bucket["bucket_type"],
                QuotaAdjustmentModel.period_start == bucket["period_start"],
                QuotaAdjustmentModel.period_end == bucket["period_end"],
            )
        ).scalar_one()
        max_overdraft = connection.execute(
            select(func.coalesce(QuotaPolicyModel.max_overdraft_micro, 0)).where(
                QuotaPolicyModel.id == bucket["policy_id"]
            )
        ).scalar_one()
        return int(consumed_micro) > (
            int(limit)
            + int(grant or 0)
            + int(adjustment or 0)
            + int(max_overdraft or 0)
        )

    # ---- Credit operations --------------------------------------------

    def gift_credits(self, management: QuotaManagementService, **kwargs: Any) -> dict[str, Any]:
        return self._credit_operation(management, operation_type="gift", source_type="grant", **kwargs)

    def reset_credits(self, management: QuotaManagementService, **kwargs: Any) -> dict[str, Any]:
        return self._credit_operation(
            management, operation_type="reset", source_type="reset", **kwargs
        )

    @staticmethod
    def _lock_credit_scope(connection: Connection, scope_key: str) -> None:
        row = connection.execute(
            select(QuotaCreditScopeLockModel)
            .where(QuotaCreditScopeLockModel.scope_key == scope_key)
            .with_for_update()
        ).mappings().first()
        if row is None:
            now = _db_time(datetime.now(UTC))
            try:
                with connection.begin_nested():
                    connection.execute(
                        insert(QuotaCreditScopeLockModel).values(
                            scope_key=scope_key,
                            created_at=now,
                            updated_at=now,
                        )
                    )
            except IntegrityError:
                # Another process inserted the lock row.  The outer
                # transaction now waits on the same row below.
                pass
            connection.execute(
                select(QuotaCreditScopeLockModel)
                .where(QuotaCreditScopeLockModel.scope_key == scope_key)
                .with_for_update()
            ).mappings().one()
        else:
            connection.execute(
                update(QuotaCreditScopeLockModel)
                .where(QuotaCreditScopeLockModel.scope_key == scope_key)
                .values(updated_at=_db_time(datetime.now(UTC)))
            )

    def _credit_operation(
        self,
        management: QuotaManagementService,
        *,
        operation_type: str,
        source_type: str,
        connection: Connection | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        required = ("owner_type", "owner_id", "bucket_type", "period_start", "period_end", "amount_micro", "actor_user_id", "reason", "idempotency_key", "effective_from")
        missing = [key for key in required if key not in kwargs]
        if missing:
            raise ValueError(f"credit operation missing fields: {', '.join(missing)}")
        if kwargs["owner_type"] not in _OWNER_TYPES or kwargs["bucket_type"] not in _BUCKET_TYPES:
            raise ValueError("unsupported credit owner or bucket type")
        amount = kwargs["amount_micro"]
        if isinstance(amount, bool) or not isinstance(amount, int) or amount < 0:
            raise ValueError("amount_micro must be a non-negative integer")
        operation_key = str(kwargs["idempotency_key"])
        transaction = (
            self._engine.begin()
            if connection is None
            else nullcontext(connection)
        )
        with transaction as connection:
            # Serialize both the global idempotency key and the target
            # owner-period scope before inspecting or writing either record.
            # This keeps a concurrent reset from expiring another reset's
            # newly-created Grant.
            self._lock_credit_scope(
                connection, _credit_idempotency_scope_key(operation_key)
            )
            self._lock_credit_scope(connection, _credit_scope_key(kwargs))
            existing = connection.execute(
                select(QuotaCreditOperationModel)
                .where(QuotaCreditOperationModel.idempotency_key == operation_key)
                .with_for_update()
            ).mappings().first()
            if existing is not None:
                self._assert_credit_operation_matches(existing, operation_type, kwargs)
                return self._credit_payload(connection, existing)
            grant = management.create_grant_in_transaction(
                connection,
                owner_type=kwargs["owner_type"],
                owner_id=kwargs["owner_id"],
                bucket_type=kwargs["bucket_type"],
                period_start=kwargs["period_start"],
                period_end=kwargs["period_end"],
                allocated_micro=amount,
                source_type=source_type,
                source_id=kwargs.get("source_id"),
                created_by=kwargs["actor_user_id"],
                reason=kwargs["reason"],
                idempotency_key=_bounded_idempotency_key(
                    f"credit-{operation_type}", operation_key
                ),
                effective_from=kwargs["effective_from"],
                expires_at=kwargs.get("expires_at"),
            )
            if operation_type == "reset":
                grant_id = grant["grant_id"]
                prior = connection.execute(
                    select(QuotaGrantModel)
                    .where(
                        QuotaGrantModel.owner_type == kwargs["owner_type"],
                        QuotaGrantModel.owner_id == kwargs["owner_id"],
                        QuotaGrantModel.bucket_type == kwargs["bucket_type"],
                        QuotaGrantModel.period_start == _db_time(kwargs["period_start"]),
                        QuotaGrantModel.period_end == _db_time(kwargs["period_end"]),
                        QuotaGrantModel.status == "active",
                        QuotaGrantModel.id != grant_id,
                    )
                    .with_for_update()
                ).mappings().all()
                for row in prior:
                    connection.execute(
                        update(QuotaGrantModel)
                        .where(QuotaGrantModel.id == row["id"])
                        .values(
                            status="expired",
                            updated_at=_db_time(datetime.now(UTC)),
                        )
                    )
                    connection.execute(
                        insert(QuotaLedgerEntryModel).values(
                            id=str(uuid.uuid4()),
                            reservation_id=None,
                            bucket_id=None,
                            grant_id=row["id"],
                            entry_type="grant_reset",
                            amount_micro=-int(row["allocated_micro"]),
                            reserved_delta_micro=0,
                            consumed_delta_micro=0,
                            idempotency_key=_bounded_idempotency_key(
                                "grant-reset", operation_key, row["id"]
                            ),
                            actor_user_id=kwargs["actor_user_id"],
                            reason=kwargs["reason"],
                            metadata_json={"replaced_by_grant_id": grant_id},
                            created_at=_db_time(datetime.now(UTC)),
                        )
                    )
            values = {
                "id": str(uuid.uuid4()),
                "operation_type": operation_type,
                "owner_type": kwargs["owner_type"],
                "owner_id": kwargs["owner_id"],
                "bucket_type": kwargs["bucket_type"],
                "period_start": _db_time(kwargs["period_start"]),
                "period_end": _db_time(kwargs["period_end"]),
                "amount_micro": amount,
                "grant_id": grant["grant_id"],
                "actor_user_id": kwargs["actor_user_id"],
                "reason": kwargs["reason"],
                "effective_from": _db_time(kwargs["effective_from"]),
                "expires_at": (
                    _db_time(kwargs["expires_at"])
                    if kwargs.get("expires_at") is not None
                    else None
                ),
                "idempotency_key": operation_key,
                "created_at": _db_time(datetime.now(UTC)),
            }
            try:
                with connection.begin_nested():
                    connection.execute(insert(QuotaCreditOperationModel).values(**values))
            except IntegrityError:
                winner = connection.execute(
                    select(QuotaCreditOperationModel)
                    .where(QuotaCreditOperationModel.idempotency_key == operation_key)
                    .with_for_update()
                ).mappings().first()
                if winner is None:
                    raise
                self._assert_credit_operation_matches(winner, operation_type, kwargs)
                return self._credit_payload(connection, winner)
            row = connection.execute(
                select(QuotaCreditOperationModel)
                .where(QuotaCreditOperationModel.id == values["id"])
            ).mappings().one()
            return self._credit_payload(connection, row)

    def gift_credits_for_role(
        self,
        management: QuotaManagementService,
        *,
        role_code: str,
        bucket_type: str,
        period_start: datetime,
        period_end: datetime,
        amount_micro: int,
        actor_user_id: str,
        reason: str,
        idempotency_key: str,
        effective_from: datetime,
        expires_at: datetime | None = None,
    ) -> dict[str, Any]:
        """Gift credits to every active user currently assigned to a role.

        Role is only a selection scope.  The durable grants remain user-owned
        so admission, settlement, snapshots, and later revocation all use the
        same bucket semantics as an individually gifted grant.  A durable
        batch record retains the complete request and recipient list, while
        deterministic per-recipient keys make retries safe.
        """
        role_code = str(role_code).strip()
        if not role_code:
            raise ValueError("role_code is required")
        if not idempotency_key:
            raise ValueError("idempotency_key is required")
        with self._engine.begin() as connection:
            self._lock_credit_scope(
                connection, _credit_idempotency_scope_key(idempotency_key)
            )
            existing = connection.execute(
                select(QuotaRoleCreditOperationModel)
                .where(
                    QuotaRoleCreditOperationModel.idempotency_key
                    == idempotency_key
                )
                .with_for_update()
            ).mappings().first()
            if existing is not None:
                self._assert_role_credit_operation_matches(
                    existing,
                    role_code=role_code,
                    bucket_type=bucket_type,
                    period_start=period_start,
                    period_end=period_end,
                    amount_micro=amount_micro,
                    actor_user_id=actor_user_id,
                    reason=reason,
                    effective_from=effective_from,
                    expires_at=expires_at,
                )
                user_ids = [str(user_id) for user_id in existing["recipient_user_ids"]]
                items = [
                    self._credit_operation(
                        management,
                        operation_type="gift",
                        source_type="role",
                        connection=connection,
                        owner_type="user",
                        owner_id=user_id,
                        bucket_type=bucket_type,
                        period_start=period_start,
                        period_end=period_end,
                        amount_micro=amount_micro,
                        source_id=role_code,
                        actor_user_id=actor_user_id,
                        reason=reason,
                        idempotency_key=_bounded_idempotency_key(
                            "role-gift", idempotency_key, role_code, user_id
                        ),
                        effective_from=effective_from,
                        expires_at=expires_at,
                    )
                    for user_id in user_ids
                ]
                return {
                    "operation_type": "gift",
                    "target_type": "role",
                    "target_id": role_code,
                    "recipient_count": len(items),
                    "items": items,
                    "idempotency_key": idempotency_key,
                }

            role_id = connection.execute(
                select(RoleModel.id)
                .where(
                    RoleModel.code == role_code,
                    RoleModel.status == "active",
                )
                .with_for_update()
            ).scalar_one_or_none()
            if role_id is None:
                raise ValueError("active role does not exist")

            now = _db_time(datetime.now(UTC))
            user_ids = connection.execute(
                select(UserModel.id)
                .join(UserRoleModel, UserRoleModel.user_id == UserModel.id)
                .where(
                    UserRoleModel.role_id == role_id,
                    UserModel.status == "active",
                    UserModel.deleted_at.is_(None),
                    (
                        UserRoleModel.expires_at.is_(None)
                        | (UserRoleModel.expires_at > now)
                    ),
                )
                .order_by(UserModel.id)
            ).scalars().all()
            if not user_ids:
                raise ValueError("role has no active users")

            items = [
                self._credit_operation(
                    management,
                    operation_type="gift",
                    source_type="role",
                    connection=connection,
                    owner_type="user",
                    owner_id=user_id,
                    bucket_type=bucket_type,
                    period_start=period_start,
                    period_end=period_end,
                    amount_micro=amount_micro,
                    source_id=role_code,
                    actor_user_id=actor_user_id,
                    reason=reason,
                    idempotency_key=_bounded_idempotency_key(
                        "role-gift", idempotency_key, role_code, user_id
                    ),
                    effective_from=effective_from,
                    expires_at=expires_at,
                )
                for user_id in user_ids
            ]
            connection.execute(
                insert(QuotaRoleCreditOperationModel).values(
                    id=str(uuid.uuid4()),
                    role_code=role_code,
                    bucket_type=bucket_type,
                    period_start=_db_time(period_start),
                    period_end=_db_time(period_end),
                    amount_micro=amount_micro,
                    actor_user_id=actor_user_id,
                    reason=reason,
                    effective_from=_db_time(effective_from),
                    expires_at=(
                        _db_time(expires_at) if expires_at is not None else None
                    ),
                    idempotency_key=idempotency_key,
                    recipient_user_ids=user_ids,
                    created_at=_db_time(datetime.now(UTC)),
                )
            )
            return {
                "operation_type": "gift",
                "target_type": "role",
                "target_id": role_code,
                "recipient_count": len(items),
                "items": items,
                "idempotency_key": idempotency_key,
            }

    @staticmethod
    def _assert_role_credit_operation_matches(
        row: Mapping[str, Any],
        *,
        role_code: str,
        bucket_type: str,
        period_start: datetime,
        period_end: datetime,
        amount_micro: int,
        actor_user_id: str,
        reason: str,
        effective_from: datetime,
        expires_at: datetime | None,
    ) -> None:
        same = (
            row["role_code"] == role_code
            and row["bucket_type"] == bucket_type
            and row["period_start"] == _db_time(period_start)
            and row["period_end"] == _db_time(period_end)
            and int(row["amount_micro"]) == int(amount_micro)
            and row["actor_user_id"] == actor_user_id
            and row["reason"] == reason
            and row["effective_from"] == _db_time(effective_from)
            and row["expires_at"]
            == (_db_time(expires_at) if expires_at is not None else None)
        )
        if not same:
            raise ValueError(
                "role credit operation idempotency key conflicts with existing operation"
            )

    @staticmethod
    def _assert_credit_operation_matches(row: Mapping[str, Any], operation_type: str, kwargs: Mapping[str, Any]) -> None:
        same = (
            row["operation_type"] == operation_type
            and row["owner_type"] == kwargs["owner_type"]
            and row["owner_id"] == kwargs["owner_id"]
            and row["bucket_type"] == kwargs["bucket_type"]
            and row["period_start"] == _db_time(kwargs["period_start"])
            and row["period_end"] == _db_time(kwargs["period_end"])
            and int(row["amount_micro"]) == int(kwargs["amount_micro"])
            and row["actor_user_id"] == kwargs["actor_user_id"]
            and row["reason"] == kwargs["reason"]
            and row["effective_from"] == _db_time(kwargs["effective_from"])
            and row["expires_at"]
            == (
                _db_time(kwargs["expires_at"])
                if kwargs.get("expires_at") is not None
                else None
            )
        )
        if not same:
            raise ValueError("credit operation idempotency key conflicts with existing operation")

    @staticmethod
    def _credit_payload(connection: Connection, row: Mapping[str, Any]) -> dict[str, Any]:
        grant_status = connection.execute(
            select(QuotaGrantModel.status).where(QuotaGrantModel.id == row["grant_id"])
        ).scalar_one_or_none()
        return {
            "operation_id": row["id"],
            "operation_type": row["operation_type"],
            "owner_type": row["owner_type"],
            "owner_id": row["owner_id"],
            "bucket_type": row["bucket_type"],
            "period_start": _payload_value(row["period_start"]),
            "period_end": _payload_value(row["period_end"]),
            "amount_micro": row["amount_micro"],
            "grant_id": row["grant_id"],
            "reason": row["reason"],
            "idempotency_key": row["idempotency_key"],
            "effective_from": _payload_value(row["effective_from"]),
            "expires_at": _payload_value(row["expires_at"]),
            "status": grant_status or "unknown",
            "created_at": _payload_value(row["created_at"]),
        }

    def list_credit_operations(self, *, limit: int = 100) -> list[dict[str, Any]]:
        """Return operator gift/reset history, including role batches.

        The individual grant rows are the authoritative per-owner records;
        role batches are included as separate entries so the developer can
        audit the original intent and recipient count without expanding every
        recipient in the page.
        """
        bounded_limit = max(1, min(limit, 1000))
        with self._engine.connect() as connection:
            operations = [
                self._credit_payload(connection, row)
                for row in connection.execute(
                    select(QuotaCreditOperationModel)
                    .order_by(QuotaCreditOperationModel.created_at.desc())
                    .limit(bounded_limit)
                ).mappings()
            ]
            role_operations = [
                {
                    "operation_id": row["id"],
                    "operation_type": "gift",
                    "owner_type": "role",
                    "owner_id": row["role_code"],
                    "bucket_type": row["bucket_type"],
                    "period_start": _payload_value(row["period_start"]),
                    "period_end": _payload_value(row["period_end"]),
                    "amount_micro": row["amount_micro"],
                    "grant_id": None,
                    "reason": row["reason"],
                    "idempotency_key": row["idempotency_key"],
                    "effective_from": _payload_value(row["effective_from"]),
                    "expires_at": _payload_value(row["expires_at"]),
                    "status": "batch",
                    "recipient_count": len(row["recipient_user_ids"] or []),
                    "created_at": _payload_value(row["created_at"]),
                }
                for row in connection.execute(
                    select(QuotaRoleCreditOperationModel)
                    .order_by(QuotaRoleCreditOperationModel.created_at.desc())
                    .limit(bounded_limit)
                ).mappings()
            ]
        return sorted(
            (*operations, *role_operations),
            key=lambda item: str(item.get("created_at") or ""),
            reverse=True,
        )[:bounded_limit]

    def list_buckets(
        self,
        *,
        owner_type: str | None = None,
        owner_id: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """List materialized buckets for recovery selection.

        Recovery is intentionally allowed to inspect historical periods, so
        this endpoint does not filter by the current time.  It only exposes
        identifiers and counters needed to choose a bucket; replay remains
        the authority for the expected balance.
        """
        bounded_limit = max(1, min(limit, 1000))
        with self._engine.connect() as connection:
            statement = select(QuotaBucketModel).order_by(
                QuotaBucketModel.updated_at.desc()
            )
            if owner_type:
                statement = statement.where(QuotaBucketModel.owner_type == owner_type)
            if owner_id:
                statement = statement.where(QuotaBucketModel.owner_id == owner_id)
            rows = connection.execute(statement.limit(bounded_limit)).mappings()
            return [
                {
                    "bucket_id": row["id"],
                    "owner_type": row["owner_type"],
                    "owner_id": row["owner_id"],
                    "bucket_type": row["bucket_type"],
                    "period_start": _payload_value(row["period_start"]),
                    "period_end": _payload_value(row["period_end"]),
                    "limit_micro": row["limit_micro"],
                    "consumed_micro": row["consumed_micro"],
                    "reserved_micro": row["reserved_micro"],
                    "over_limit": bool(row["over_limit"]),
                    "updated_at": _payload_value(row["updated_at"]),
                }
                for row in rows
            ]

    def list_archive_batches(self, *, limit: int = 100) -> list[dict[str, Any]]:
        """List non-destructive archive manifests for operator audit."""
        bounded_limit = max(1, min(limit, 1000))
        with self._engine.connect() as connection:
            rows = connection.execute(
                select(QuotaUsageArchiveBatchModel)
                .order_by(QuotaUsageArchiveBatchModel.created_at.desc())
                .limit(bounded_limit)
            ).mappings()
            return [
                {
                    "batch_id": row["id"],
                    "cutoff_at": _payload_value(row["cutoff_at"]),
                    "event_count": row["event_count"],
                    "status": row["status"],
                    "actor_user_id": row["actor_user_id"],
                    "created_at": _payload_value(row["created_at"]),
                    "completed_at": _payload_value(row["completed_at"]),
                }
                for row in rows
            ]

    # ---- Daily rollups, alerts, and archive ---------------------------

    def build_daily_rollup(self, rollup_date: date) -> int:
        start, end = _day_bounds(rollup_date)
        with self._engine.begin() as connection:
            connection.execute(
                delete(QuotaDailyRollupModel).where(
                    QuotaDailyRollupModel.rollup_date == rollup_date
                )
            )
            exact = case((UsageEventModel.usage_status == "exact", 1), else_=0)
            estimated = case((UsageEventModel.usage_status == "estimated", 1), else_=0)
            pending = case((UsageEventModel.usage_status == "pending", 1), else_=0)
            unavailable = case((UsageEventModel.usage_status == "unavailable", 1), else_=0)
            priced = case((UsageEventModel.credits_micro.is_not(None), UsageEventModel.credits_micro), else_=0)
            statement = (
                select(
                    UsageEventModel.user_id,
                    UsageEventModel.workspace_id,
                    UsageEventModel.provider,
                    UsageEventModel.provider_model,
                    UsageEventModel.purpose,
                    func.count().label("event_count"),
                    func.sum(exact).label("exact_events"),
                    func.sum(estimated).label("estimated_events"),
                    func.sum(pending).label("pending_events"),
                    func.sum(unavailable).label("unavailable_events"),
                    func.coalesce(func.sum(priced), 0).label("priced_credits_micro"),
                    *(func.coalesce(func.sum(getattr(UsageEventModel, field)), 0).label(field) for field in TOKEN_FIELDS),
                )
                .where(
                    UsageEventModel.occurred_at >= _db_time(start),
                    UsageEventModel.occurred_at < _db_time(end),
                    # Rollups are an operational read model.  Rebuilding a
                    # period must not reintroduce events already archived.
                    UsageEventModel.archived_at.is_(None),
                )
                .group_by(
                    UsageEventModel.user_id,
                    UsageEventModel.workspace_id,
                    UsageEventModel.provider,
                    UsageEventModel.provider_model,
                    UsageEventModel.purpose,
                )
            )
            rows = connection.execute(statement).mappings().all()
            for row in rows:
                rollup_values = {
                    "id": str(
                        uuid.uuid5(
                            uuid.NAMESPACE_URL,
                            ":".join(
                                (
                                    "pro-nlp:quota-rollup",
                                    rollup_date.isoformat(),
                                    str(row["user_id"]),
                                    str(row["workspace_id"] or ""),
                                    str(row["provider"]),
                                    str(row["provider_model"]),
                                    str(row["purpose"]),
                                )
                            ),
                        )
                    ),
                    "rollup_date": rollup_date,
                    "user_id": row["user_id"],
                    "workspace_id": row["workspace_id"],
                    "provider": row["provider"],
                    "provider_model": row["provider_model"],
                    "purpose": row["purpose"],
                    "event_count": int(row["event_count"] or 0),
                    "exact_events": int(row["exact_events"] or 0),
                    "estimated_events": int(row["estimated_events"] or 0),
                    "pending_events": int(row["pending_events"] or 0),
                    "unavailable_events": int(row["unavailable_events"] or 0),
                    "priced_credits_micro": int(row["priced_credits_micro"] or 0),
                    **{field: int(row[field] or 0) for field in TOKEN_FIELDS},
                    "created_at": _db_time(datetime.now(UTC)),
                    "updated_at": _db_time(datetime.now(UTC)),
                }
                update_values = {
                    key: value
                    for key, value in rollup_values.items()
                    if key not in {"id", "created_at"}
                }
                try:
                    with connection.begin_nested():
                        connection.execute(
                            insert(QuotaDailyRollupModel).values(**rollup_values)
                        )
                except IntegrityError:
                    connection.execute(
                        update(QuotaDailyRollupModel)
                        .where(QuotaDailyRollupModel.id == rollup_values["id"])
                        .values(**update_values)
                    )
            return len(rows)

    def list_daily_rollups(
        self, *, start: date, end: date, user_id: str | None = None, workspace_id: str | None = None
    ) -> list[dict[str, Any]]:
        with self._engine.connect() as connection:
            statement = select(QuotaDailyRollupModel).where(
                QuotaDailyRollupModel.rollup_date >= start,
                QuotaDailyRollupModel.rollup_date < end,
            ).order_by(QuotaDailyRollupModel.rollup_date, QuotaDailyRollupModel.user_id)
            if user_id:
                statement = statement.where(QuotaDailyRollupModel.user_id == user_id)
            if workspace_id:
                statement = statement.where(QuotaDailyRollupModel.workspace_id == workspace_id)
            return [self._rollup_payload(row) for row in connection.execute(statement).mappings()]

    def detect_usage_anomalies(
        self,
        *,
        day: date,
        baseline_days: int = 7,
        threshold_multiplier: int = 3,
        min_actual_micro: int = 1,
    ) -> dict[str, Any]:
        if baseline_days <= 0 or threshold_multiplier <= 1:
            raise ValueError("baseline_days must be positive and threshold_multiplier must exceed one")
        baseline_start = day - timedelta(days=baseline_days)
        with self._engine.begin() as connection:
            rows = connection.execute(
                select(QuotaDailyRollupModel).where(
                    QuotaDailyRollupModel.rollup_date >= baseline_start,
                    QuotaDailyRollupModel.rollup_date <= day,
                )
            ).mappings().all()
            grouped: dict[tuple[str, str | None], dict[date, int]] = {}
            for row in rows:
                key = (row["user_id"], row["workspace_id"])
                values = grouped.setdefault(key, {})
                values[row["rollup_date"]] = values.get(row["rollup_date"], 0) + int(row["priced_credits_micro"] or 0)
            items: list[dict[str, Any]] = []
            created = 0
            for (user_id, workspace_id), values in grouped.items():
                actual = values.get(day, 0)
                if actual < min_actual_micro:
                    continue
                baseline_values = [values.get(day - timedelta(days=index), 0) for index in range(1, baseline_days + 1)]
                baseline = sum(baseline_values) / len(baseline_values)
                if actual <= max(min_actual_micro - 1, baseline * threshold_multiplier):
                    continue
                dedupe_key = f"usage-spike:{user_id}:{workspace_id or '-'}:{day.isoformat()}"
                existing = connection.execute(
                    select(QuotaAlertModel).where(QuotaAlertModel.dedupe_key == dedupe_key)
                ).mappings().first()
                if existing is None:
                    try:
                        with connection.begin_nested():
                            connection.execute(
                                insert(QuotaAlertModel).values(
                                    id=str(uuid.uuid4()),
                                    alert_type="usage_spike",
                                    severity="high" if actual >= baseline * threshold_multiplier * 2 else "medium",
                                    owner_type="user",
                                    owner_id=user_id,
                                    window_start=_db_time(datetime(day.year, day.month, day.day, tzinfo=UTC)),
                                    window_end=_db_time(datetime(day.year, day.month, day.day, tzinfo=UTC) + timedelta(days=1)),
                                    baseline_micro=max(0, int(baseline)),
                                    actual_micro=actual,
                                    threshold_multiplier=threshold_multiplier,
                                    status="open",
                                    dedupe_key=dedupe_key,
                                    metadata_json={"workspace_id": workspace_id, "baseline_days": baseline_days},
                                    created_at=_db_time(datetime.now(UTC)),
                                    resolved_at=None,
                                )
                            )
                        created += 1
                    except IntegrityError:
                        pass
                alert = connection.execute(
                    select(QuotaAlertModel).where(QuotaAlertModel.dedupe_key == dedupe_key)
                ).mappings().one()
                items.append(self._alert_payload(alert))
            return {"day": day.isoformat(), "created": created, "items": items}

    def list_alerts(self, *, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        with self._engine.connect() as connection:
            statement = select(QuotaAlertModel).order_by(QuotaAlertModel.created_at.desc()).limit(max(1, min(limit, 1000)))
            if status:
                statement = statement.where(QuotaAlertModel.status == status)
            return [self._alert_payload(row) for row in connection.execute(statement).mappings()]

    def update_alert(
        self,
        alert_id: str,
        *,
        status: str,
        actor_user_id: str,
        reason: str,
    ) -> dict[str, Any]:
        if status not in {"acknowledged", "resolved"}:
            raise ValueError("alert status must be acknowledged or resolved")
        if not reason.strip():
            raise ValueError("reason is required")
        with self._engine.begin() as connection:
            row = connection.execute(
                select(QuotaAlertModel)
                .where(QuotaAlertModel.id == alert_id)
                .with_for_update()
            ).mappings().first()
            if row is None:
                raise KeyError(alert_id)
            if row["status"] == "resolved" and status != "resolved":
                raise ValueError("resolved alert cannot be reopened")
            if row["status"] == status:
                return self._alert_payload(row)
            now = datetime.now(UTC)
            connection.execute(
                update(QuotaAlertModel)
                .where(QuotaAlertModel.id == alert_id)
                .values(
                    status=status,
                    resolved_at=_db_time(now) if status == "resolved" else None,
                )
            )
            updated = connection.execute(
                select(QuotaAlertModel).where(QuotaAlertModel.id == alert_id)
            ).mappings().one()
            return self._alert_payload(updated)

    def run_maintenance(self, *, now: datetime | None = None) -> dict[str, int]:
        """Refresh the previous UTC day and evaluate its anomaly alerts.

        The reaper invokes this outside the Turn transaction. Rebuilding one
        day is safe because rollups are derived rows, while raw events and
        Ledger entries remain append-only.
        """
        at = _utc(now or datetime.now(UTC))
        target_day = (at - timedelta(days=1)).date()
        rollup_rows = self.build_daily_rollup(target_day)
        alerts = self.detect_usage_anomalies(day=target_day)
        return {"rollup_rows": rollup_rows, "alerts_created": alerts["created"]}

    def archive_usage_events(
        self, *, before: datetime, actor_user_id: str | None = None, batch_size: int = 10_000
    ) -> dict[str, Any]:
        cutoff = _utc(before)
        with self._engine.begin() as connection:
            event_ids = list(
                connection.execute(
                    select(UsageEventModel.id)
                    .where(
                        UsageEventModel.occurred_at < _db_time(cutoff),
                        UsageEventModel.archived_at.is_(None),
                    )
                    .order_by(UsageEventModel.occurred_at)
                    .limit(max(1, min(batch_size, 100_000)))
                    .with_for_update(skip_locked=True)
                ).scalars()
            )
            if not event_ids:
                return {
                    "batch_id": None,
                    "archived_events": 0,
                    "cutoff_at": cutoff.isoformat(),
                    "deleted_events": 0,
                }
            batch_id = str(uuid.uuid4())
            if event_ids:
                connection.execute(
                    update(UsageEventModel)
                    .where(
                        UsageEventModel.id.in_(event_ids),
                        UsageEventModel.archived_at.is_(None),
                    )
                    .values(
                        archived_at=_db_time(datetime.now(UTC)),
                        archive_batch_id=batch_id,
                    )
                )
            claimed_ids = list(
                connection.execute(
                    select(UsageEventModel.id).where(
                        UsageEventModel.archive_batch_id == batch_id
                    )
                ).scalars()
            )
            connection.execute(
                insert(QuotaUsageArchiveBatchModel).values(
                    id=batch_id,
                    cutoff_at=_db_time(cutoff),
                    event_count=len(claimed_ids),
                    status="completed",
                    actor_user_id=actor_user_id,
                    created_at=_db_time(datetime.now(UTC)),
                    completed_at=_db_time(datetime.now(UTC)),
                )
            )
            return {
                "batch_id": batch_id,
                "archived_events": len(claimed_ids),
                "cutoff_at": cutoff.isoformat(),
                "deleted_events": 0,
            }

    def purge_archived_usage_events(
        self, *, before: datetime, actor_user_id: str | None = None, batch_size: int = 10_000
    ) -> dict[str, Any]:
        """Physically remove settled events that were archived previously.

        Archiving is the safe, reversible operational step. Purging is an
        explicit retention action and only accepts rows that have an archive
        marker, have settled usage, and are not referenced by a billing line.
        Ledger and archive manifests remain untouched.
        """
        del actor_user_id  # retained for the audited service boundary
        cutoff = _utc(before)
        with self._engine.begin() as connection:
            event_ids = list(
                connection.execute(
                    select(UsageEventModel.id)
                    .where(
                        UsageEventModel.archived_at.is_not(None),
                        UsageEventModel.occurred_at < _db_time(cutoff),
                        UsageEventModel.usage_status.in_(("exact", "estimated")),
                    )
                    .order_by(UsageEventModel.occurred_at)
                    .limit(max(1, min(batch_size, 100_000)))
                    .with_for_update(skip_locked=True)
                ).scalars()
            )
            if not event_ids:
                return {
                    "purged_events": 0,
                    "deleted_events": 0,
                    "cutoff_at": cutoff.isoformat(),
                }
            billing_references = list(
                connection.execute(
                    select(QuotaProviderBillingModel.id).where(
                        QuotaProviderBillingModel.matched_usage_event_id.in_(event_ids)
                    )
                ).scalars()
            )
            if billing_references:
                raise ValueError(
                    "cannot purge UsageEvents still referenced by provider billing; "
                    "retain or purge the billing records first"
                )
            deleted = connection.execute(
                delete(UsageEventModel).where(
                    UsageEventModel.id.in_(event_ids),
                    UsageEventModel.archived_at.is_not(None),
                    UsageEventModel.usage_status.in_(("exact", "estimated")),
                )
            )
            deleted_events = int(deleted.rowcount or 0)
            return {
                "purged_events": deleted_events,
                "deleted_events": deleted_events,
                "cutoff_at": cutoff.isoformat(),
            }

    @staticmethod
    def partition_strategy(*, start_year: int, start_month: int, months: int = 12) -> dict[str, Any]:
        if months <= 0:
            raise ValueError("months must be positive")
        partitions = []
        cursor = date(start_year, start_month, 1)
        for _ in range(months):
            next_month = date(cursor.year + (cursor.month == 12), 1 if cursor.month == 12 else cursor.month + 1, 1)
            partitions.append({"name": f"p{cursor:%Y%m}", "from": cursor.isoformat(), "to": next_month.isoformat()})
            cursor = next_month
        return {
            "table": UsageEventModel.__tablename__,
            "strategy": "monthly_range_on_occurred_at_plus_non_destructive_archive_marker",
            "partition_key": "occurred_at",
            "partitions": partitions,
            "retention": "archive marker first; physical export/drop is a separate operator-approved step",
        }

    # ---- Teacher classroom aggregate ----------------------------------

    def classroom_usage(
        self,
        classroom_id: str,
        *,
        workspace_id: str,
        start: datetime,
        end: datetime,
    ) -> dict[str, Any]:
        start = _utc(start)
        end = _utc(end)
        if end <= start:
            raise ValueError("end must be after start")
        with self._engine.connect() as connection:
            members = list(
                connection.execute(
                    select(ClassroomMemberModel.user_id).where(
                        ClassroomMemberModel.classroom_id == classroom_id,
                        ClassroomMemberModel.status == "active",
                    )
                ).scalars()
            )
            rows = connection.execute(
                select(UsageEventModel).where(
                    UsageEventModel.user_id.in_(members or ["__none__"]),
                    UsageEventModel.workspace_id == workspace_id,
                    UsageEventModel.occurred_at >= _db_time(start),
                    UsageEventModel.occurred_at < _db_time(end),
                    UsageEventModel.archived_at.is_(None),
                )
            ).mappings().all()
        priced = [row for row in rows if row["credits_micro"] is not None]
        return {
            "classroom_id": classroom_id,
            "from": start.isoformat(),
            "to": end.isoformat(),
            "students": len(members),
            "active_student_ids": members,
            "events": len(rows),
            "priced_events": len(priced),
            "priced_credits_micro": sum(int(row["credits_micro"]) for row in priced),
            "pending_events": sum(row["usage_status"] == "pending" for row in rows),
            "unavailable_events": sum(row["usage_status"] == "unavailable" for row in rows),
            "tokens": {field: sum(int(row[field] or 0) for row in rows) for field in TOKEN_FIELDS},
            "by_user": self._classroom_user_breakdown(rows),
        }

    @staticmethod
    def _classroom_user_breakdown(rows: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
        grouped: dict[str, list[Mapping[str, Any]]] = {}
        for row in rows:
            grouped.setdefault(str(row["user_id"]), []).append(row)
        return [
            {
                "user_id": user_id,
                "events": len(items),
                "priced_credits_micro": sum(int(row["credits_micro"] or 0) for row in items),
                "pending_events": sum(row["usage_status"] == "pending" for row in items),
                "unavailable_events": sum(row["usage_status"] == "unavailable" for row in items),
                "total_tokens": sum(int(row["total_tokens"] or 0) for row in items),
            }
            for user_id, items in sorted(grouped.items())
        ]

    @staticmethod
    def _rollup_payload(row: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "rollup_date": _payload_value(row["rollup_date"]),
            "user_id": row["user_id"],
            "workspace_id": row["workspace_id"],
            "provider": row["provider"],
            "provider_model": row["provider_model"],
            "purpose": row["purpose"],
            "events": row["event_count"],
            "exact_events": row["exact_events"],
            "estimated_events": row["estimated_events"],
            "pending_events": row["pending_events"],
            "unavailable_events": row["unavailable_events"],
            "priced_credits_micro": row["priced_credits_micro"],
            "tokens": {field: row[field] for field in TOKEN_FIELDS},
        }

    @staticmethod
    def _alert_payload(row: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "alert_id": row["id"],
            "alert_type": row["alert_type"],
            "severity": row["severity"],
            "owner_type": row["owner_type"],
            "owner_id": row["owner_id"],
            "window_start": _payload_value(row["window_start"]),
            "window_end": _payload_value(row["window_end"]),
            "baseline_micro": row["baseline_micro"],
            "actual_micro": row["actual_micro"],
            "threshold_multiplier": row["threshold_multiplier"],
            "status": row["status"],
            "metadata": row["metadata_json"],
            "resolved_at": _payload_value(row["resolved_at"]),
        }
