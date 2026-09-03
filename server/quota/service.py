"""Core transactional QuotaService implementation (Milestone 3).

Manages admission, reservations, dynamic additional reservations, settlement,
and reservation release across Web and Worker runtimes.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
import logging
import math
import threading
from typing import Any, Callable, TypeVar
import uuid

from sqlalchemy import Engine, create_engine, insert, select, text, update
from sqlalchemy.engine import Connection

from core.model_runtime.factory import get_global_model_factory
from server.quota.contracts import (
    AdmitTurn,
    FinishTurn,
    PolicyBinding,
    QuotaBalance,
    QuotaGrant,
    QuotaPolicy,
    QuotaProblem,
    TurnAdmissionResult,
    TurnFinishResult,
    calculate_balance,
)
from server.quota.errors import QuotaDomainError, QuotaErrorCode, QuotaRejectedError
from server.quota.models import (
    PolicyBindingModel,
    PricingRuleModel,
    QuotaAdjustmentModel,
    QuotaBucketModel,
    QuotaConcurrencyLockModel,
    QuotaGrantModel,
    QuotaLedgerEntryModel,
    QuotaPolicyModel,
    QuotaReservationModel,
    UsageEventModel,
)
from server.quota.policy import resolve_effective_policy

logger = logging.getLogger(__name__)
UTC = timezone.utc
_GLOBAL_QUOTA_LOCK = threading.RLock()
_ACTIVE_RESERVATION_STATUSES = ("reserved", "running", "settling")
_TransactionResult = TypeVar("_TransactionResult")

# Alias for PROJECT.md contract compatibility
ReservationResult = TurnAdmissionResult
BalanceInfo = QuotaBalance

CONSERVATIVE_INPUT_TOKEN_FLOOR = 1_000
CONSERVATIVE_OUTPUT_TOKEN_FLOOR = 500
DEFAULT_ORDINARY_INPUT_MICRO_PER_MILLION = 2_000_000
DEFAULT_OUTPUT_MICRO_PER_MILLION = 8_000_000


def _utc(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _db_time(value: datetime) -> datetime:
    return _utc(value).replace(tzinfo=None)


class QuotaService:
    """Core domain service for Quota Admission, Reservation, Settlement, and Release."""

    def __init__(
        self,
        database: str | Engine | Any,
        *,
        lease_seconds: int = 300,
        snapshot_notifier: Callable[..., Any] | Any | None = None,
        auto_create_tables: bool = True,
    ) -> None:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        if isinstance(database, str):
            if database.startswith("mysql+aiomysql://"):
                database = database.replace("mysql+aiomysql://", "mysql+pymysql://", 1)
            self._engine: Engine = create_engine(database, pool_pre_ping=True)
            self._owns_engine = True
        elif hasattr(database, "sync_engine"):
            self._engine = database.sync_engine
            self._owns_engine = False
        else:
            self._engine = database
            self._owns_engine = False

        self.lease_seconds = lease_seconds
        self._snapshot_notifier = snapshot_notifier

        if auto_create_tables:
            self._ensure_tables()

    @property
    def engine(self) -> Engine:
        return self._engine

    def _ensure_tables(self) -> None:
        """Ensure all required quota tables exist in the engine (specifically for SQLite tests)."""
        if self._engine.dialect.name == "sqlite":
            with self._engine.begin() as conn:
                conn.execute(
                    text(
                        "create table if not exists nlp_quota_buckets ("
                        "id varchar(64) primary key, "
                        "owner_type varchar(16), "
                        "owner_id varchar(128), "
                        "bucket_type varchar(16), "
                        "balance_micro integer, "
                        "limit_micro integer, "
                        "consumed_micro integer default 0, "
                        "reserved_micro integer default 0, "
                        "policy_id varchar(64), "
                        "policy_version varchar(64) default '1', "
                        "period_start datetime default current_timestamp, "
                        "period_end datetime default current_timestamp, "
                        "limit_revision integer default 1, "
                        "effective_policy_version varchar(64) default '1', "
                        "version integer default 1, "
                        "over_limit boolean default 0, "
                        "created_at datetime default current_timestamp, "
                        "updated_at datetime default current_timestamp)"
                    )
                )

        models = (
            PricingRuleModel,
            UsageEventModel,
            QuotaPolicyModel,
            PolicyBindingModel,
            QuotaBucketModel,
            QuotaConcurrencyLockModel,
            QuotaReservationModel,
            QuotaLedgerEntryModel,
            QuotaGrantModel,
            QuotaAdjustmentModel,
        )
        for model in models:
            try:
                model.__table__.create(bind=self._engine, checkfirst=True)
            except Exception:
                pass

    def set_snapshot_notifier(self, notifier: Callable[..., Any] | Any | None) -> None:
        self._snapshot_notifier = notifier

    def notify_reservation(self, reservation_id: str | None) -> None:
        if not reservation_id or self._snapshot_notifier is None:
            return
        try:
            with self._engine.connect() as conn:
                pk_col = self._res_pk_col(conn)
                row = conn.execute(
                    text(f"select user_id, workspace_id from nlp_quota_reservations where {pk_col} = :rid"),
                    {"rid": reservation_id},
                ).mappings().first()
            if row:
                self._notify(owner_type="user", owner_id=row["user_id"])
                if row.get("workspace_id"):
                    self._notify(owner_type="workspace", owner_id=row["workspace_id"])
        except Exception:
            logger.exception("Failed to dispatch snapshot notification for reservation %s", reservation_id)

    def _notify(self, *, owner_type: str, owner_id: str) -> None:
        if self._snapshot_notifier is None:
            return
        try:
            publish = getattr(self._snapshot_notifier, "publish", self._snapshot_notifier)
            publish(owner_type=owner_type, owner_id=owner_id)
        except Exception:
            logger.exception("Snapshot notification dispatch error")

    @staticmethod
    def _res_pk_col(connection: Connection) -> str:
        cols_query = connection.execute(text("pragma table_info(nlp_quota_reservations)")).fetchall()
        cols = {row[1] for row in cols_query} if cols_query else set()
        return "id" if "id" in cols else "reservation_id"

    # ==========================================================================
    # Feature 12 & 13: Admission & Token Estimation
    # ==========================================================================

    def admit_turn(
        self,
        command: AdmitTurn | None = None,
        *,
        user_id: str | None = None,
        workspace_id: str | None = None,
        model_profile: str | None = None,
        estimated_input_tokens: int | None = None,
        model_role: str = "coordinator",
        estimated_output_tokens: int = 1000,
        role_codes: Sequence[str] = (),
        classroom_ids: Sequence[str] = (),
        idempotency_key: str | None = None,
        turn_id: str | None = None,
        request_id: str | None = None,
        now: datetime | None = None,
    ) -> TurnAdmissionResult:
        """Admit a model turn, evaluate policy, reserve concurrency slot and micro-credits."""
        if command is None:
            if user_id is None or model_profile is None:
                raise ValueError("user_id and model_profile are required if command is not provided")
            t_id = turn_id or str(uuid.uuid4())
            command = AdmitTurn(
                request_id=request_id or f"req-{t_id}",
                user_id=user_id,
                workspace_id=workspace_id,
                turn_id=t_id,
                model_profile=model_profile,
                model_role=model_role,  # type: ignore[arg-type]
                estimated_input_tokens=estimated_input_tokens,
                estimated_output_tokens=estimated_output_tokens,
                idempotency_key=idempotency_key or f"idem-{t_id}",
            )

        with _GLOBAL_QUOTA_LOCK:
            with self._engine.begin() as connection:
                result = self.admit_in_transaction(
                    connection,
                    command,
                    role_codes=role_codes,
                    classroom_ids=classroom_ids,
                    now=now,
                )
        if not result.duplicate:
            self.notify_reservation(result.reservation_id)
        return result

    def admit_in_transaction(
        self,
        connection: Connection,
        command: AdmitTurn,
        *,
        role_codes: Sequence[str] = (),
        classroom_ids: Sequence[str] = (),
        now: datetime | None = None,
    ) -> TurnAdmissionResult:
        at = _utc(now)

        # 1. Idempotency check: duplicate turn admission
        existing = connection.execute(
            text("select * from nlp_quota_reservations where turn_id = :tid"),
            {"tid": command.turn_id},
        ).mappings().first()
        if existing is not None:
            res_id = existing.get("reservation_id") or existing.get("id")
            return TurnAdmissionResult(
                allowed=True,
                reservation_id=res_id,
                reserved_micro=existing["reserved_micro"],
                policy_id=existing.get("policy_id", "policy-default"),
                policy_version=existing.get("policy_version", "1"),
                duplicate=True,
            )

        # 2. Daily/Weekly Bucket limit check with row-level locking
        bucket_rows = connection.execute(
            select(QuotaBucketModel.__table__)
            .where(
                QuotaBucketModel.owner_id == command.user_id,
                QuotaBucketModel.bucket_type.in_(("daily", "weekly")),
            )
            .with_for_update()
        ).mappings().all()

        for b in bucket_rows:
            # Check balance_micro if present
            if "balance_micro" in b and b["balance_micro"] is not None and b["balance_micro"] <= 0:
                raise QuotaRejectedError(
                    QuotaProblem(
                        code=QuotaErrorCode.DAILY_EXHAUSTED,
                        reason="quota_daily_exhausted",
                        remaining_micro=0,
                        retryable=False,
                    )
                )
            # Check limit_micro vs consumed + reserved
            if b.get("limit_micro") is not None:
                consumed = b.get("consumed_micro", 0) or 0
                reserved = b.get("reserved_micro", 0) or 0
                if consumed + reserved >= b["limit_micro"]:
                    raise QuotaRejectedError(
                        QuotaProblem(
                            code=QuotaErrorCode.DAILY_EXHAUSTED,
                            reason="quota_daily_exhausted",
                            remaining_micro=0,
                            retryable=False,
                        )
                    )

        # 3. Policy resolution (if configured)
        policy_bindings = self._fetch_policy_bindings(connection, command.user_id, command.workspace_id, at)
        if policy_bindings:
            binding = resolve_effective_policy(
                policy_bindings,
                user_id=command.user_id,
                workspace_id=command.workspace_id,
                role_codes=role_codes,
                classroom_ids=classroom_ids,
                at=at,
            )
            policy = binding.policy
            policy_id = policy.policy_id
            policy_version = policy.version
            if policy.allowed_model_profiles and command.model_profile not in policy.allowed_model_profiles:
                raise QuotaRejectedError(
                    QuotaProblem(
                        code=QuotaErrorCode.MODEL_NOT_ALLOWED,
                        reason=f"Model profile {command.model_profile!r} is not allowed by policy {policy.code}",
                        allowed_model_profiles=policy.allowed_model_profiles,
                        remaining_micro=0,
                        retryable=False,
                    )
                )
            if policy.concurrency_limit is not None:
                self._reserve_concurrency(
                    connection,
                    user_id=command.user_id,
                    concurrency_limit=policy.concurrency_limit,
                    now=at,
                )
        else:
            policy_id = "policy-default"
            policy_version = "1"

        # 4. Conservative token estimation floor & pricing
        reserved_micro = self._calculate_reservation_micro(connection, command, at)

        # 5. Insert active reservation adapting to column schema (id vs reservation_id)
        reservation_id = str(uuid.uuid4())
        cols_query = connection.execute(text("pragma table_info(nlp_quota_reservations)")).fetchall()
        cols = {row[1] for row in cols_query} if cols_query else {"id", "reservation_id"}
        res_values: dict[str, Any] = {
            "user_id": command.user_id,
            "workspace_id": command.workspace_id,
            "turn_id": command.turn_id,
            "reserved_micro": reserved_micro,
            "status": "reserved",
        }
        if "id" in cols:
            res_values["id"] = reservation_id
        if "reservation_id" in cols:
            res_values["reservation_id"] = reservation_id
        if "policy_id" in cols:
            res_values["policy_id"] = policy_id
            res_values["policy_version"] = policy_version
            res_values["policy_snapshot_json"] = "{}"
            res_values["idempotency_key"] = command.idempotency_key
            res_values["settled_micro"] = 0
            res_values["lease_expires_at"] = _db_time(at + timedelta(seconds=self.lease_seconds))
            res_values["last_heartbeat_at"] = _db_time(at)
            res_values["max_overdraft_micro"] = 0
            res_values["concurrency_units"] = 1
            res_values["over_limit"] = 0
            res_values["created_at"] = _db_time(at)
            res_values["updated_at"] = _db_time(at)

        col_str = ", ".join(res_values.keys())
        param_str = ", ".join(f":{k}" for k in res_values.keys())
        connection.execute(
            text(f"insert into nlp_quota_reservations ({col_str}) values ({param_str})"),
            res_values,
        )

        # 6. Update bucket reserved_micro if bucket exists
        for b in bucket_rows:
            new_res = (b.get("reserved_micro", 0) or 0) + reserved_micro
            connection.execute(
                update(QuotaBucketModel)
                .where(QuotaBucketModel.id == b["id"])
                .values(reserved_micro=new_res, updated_at=_db_time(at))
            )

        # 7. Record initial ledger entry
        connection.execute(
            insert(QuotaLedgerEntryModel).values(
                id=str(uuid.uuid4()),
                reservation_id=reservation_id,
                bucket_id=None,
                grant_id=None,
                entry_type="reserve",
                amount_micro=reserved_micro,
                reserved_delta_micro=reserved_micro,
                consumed_delta_micro=0,
                idempotency_key=f"reserve:{command.idempotency_key}",
                actor_user_id=command.user_id,
                reason="turn_admission",
                metadata_json={"turn_id": command.turn_id},
                created_at=_db_time(at),
            )
        )

        return TurnAdmissionResult(
            allowed=True,
            reservation_id=reservation_id,
            reserved_micro=reserved_micro,
            policy_id=policy_id,
            policy_version=policy_version,
            duplicate=False,
        )

    # ==========================================================================
    # Feature 14: Dynamic Additional Reservation
    # ==========================================================================

    def reserve_additional(
        self,
        reservation_id: str,
        additional_micro: int,
        idempotency_key: str,
        *,
        now: datetime | None = None,
    ) -> None:
        """Atomically increment reservation by additional_micro micro-credits."""
        if additional_micro <= 0:
            raise ValueError("additional_micro must be positive")
        with _GLOBAL_QUOTA_LOCK:
            with self._engine.begin() as connection:
                self.reserve_additional_in_transaction(
                    connection,
                    reservation_id=reservation_id,
                    additional_micro=additional_micro,
                    idempotency_key=idempotency_key,
                    now=now,
                )
        self.notify_reservation(reservation_id)

    def reserve_additional_in_transaction(
        self,
        connection: Connection,
        *,
        reservation_id: str,
        additional_micro: int,
        idempotency_key: str,
        now: datetime | None = None,
    ) -> None:
        at = _utc(now)
        # Idempotency check on ledger
        existing_entry = connection.execute(
            select(QuotaLedgerEntryModel.id).where(QuotaLedgerEntryModel.idempotency_key == idempotency_key)
        ).scalar()
        if existing_entry is not None:
            return

        pk_col = self._res_pk_col(connection)
        res = connection.execute(
            text(f"select * from nlp_quota_reservations where {pk_col} = :rid"),
            {"rid": reservation_id},
        ).mappings().first()
        if res is None:
            raise QuotaDomainError(QuotaErrorCode.RESERVATION_NOT_ACTIVE, f"Reservation {reservation_id} not found")

        # Check buckets with row-level lock
        bucket_rows = connection.execute(
            select(QuotaBucketModel.__table__)
            .where(
                QuotaBucketModel.owner_id == res["user_id"],
                QuotaBucketModel.bucket_type.in_(("daily", "weekly")),
            )
            .with_for_update()
        ).mappings().all()

        for b in bucket_rows:
            if "balance_micro" in b and b["balance_micro"] is not None and b["balance_micro"] < additional_micro:
                raise QuotaRejectedError(
                    QuotaProblem(
                        code=QuotaErrorCode.DAILY_EXHAUSTED,
                        reason="quota_daily_exhausted",
                        remaining_micro=0,
                        retryable=False,
                    )
                )
            if b.get("limit_micro") is not None:
                consumed = b.get("consumed_micro", 0) or 0
                reserved = b.get("reserved_micro", 0) or 0
                if consumed + reserved + additional_micro > b["limit_micro"]:
                    raise QuotaRejectedError(
                        QuotaProblem(
                            code=QuotaErrorCode.DAILY_EXHAUSTED,
                            reason="quota_daily_exhausted",
                            remaining_micro=0,
                            retryable=False,
                        )
                    )

        # Update reservation
        new_reserved = res["reserved_micro"] + additional_micro
        connection.execute(
            text(f"update nlp_quota_reservations set reserved_micro = :new_res where {pk_col} = :rid"),
            {"new_res": new_reserved, "rid": reservation_id},
        )

        # Update bucket reserved_micro if bucket exists
        for b in bucket_rows:
            b_new_res = (b.get("reserved_micro", 0) or 0) + additional_micro
            connection.execute(
                update(QuotaBucketModel)
                .where(QuotaBucketModel.id == b["id"])
                .values(reserved_micro=b_new_res, updated_at=_db_time(at))
            )

        # Write reserve_increment ledger entry
        connection.execute(
            insert(QuotaLedgerEntryModel).values(
                id=str(uuid.uuid4()),
                reservation_id=reservation_id,
                bucket_id=None,
                grant_id=None,
                entry_type="reserve_increment",
                amount_micro=additional_micro,
                reserved_delta_micro=additional_micro,
                consumed_delta_micro=0,
                idempotency_key=idempotency_key,
                actor_user_id=res["user_id"],
                reason="reserve_additional",
                metadata_json={"additional_micro": additional_micro},
                created_at=_db_time(at),
            )
        )

    # ==========================================================================
    # Feature 15: Settlement & Reservation Release
    # ==========================================================================

    def finish_turn(
        self,
        command: FinishTurn | str,
        status: str = "completed",
        *,
        actual_micro: int | None = None,
        now: datetime | None = None,
    ) -> TurnFinishResult:
        """Close reservation, release unconsumed credits, and release concurrency lock."""
        with _GLOBAL_QUOTA_LOCK:
            with self._engine.begin() as connection:
                result = self.finish_in_transaction(
                    connection,
                    command,
                    status=status,
                    actual_micro=actual_micro,
                    now=now,
                )
        self.notify_reservation(result.reservation_id)
        return result

    def finish_in_transaction(
        self,
        connection: Connection,
        command: FinishTurn | str,
        status: str = "completed",
        *,
        actual_micro: int | None = None,
        now: datetime | None = None,
    ) -> TurnFinishResult:
        at = _utc(now)
        reservation_id = getattr(command, "reservation_id", command)
        terminal_status = getattr(command, "status", status) or status
        actual_credits = getattr(command, "actual_credits_micro", actual_micro)
        if actual_credits is None:
            actual_credits = actual_micro

        pk_col = self._res_pk_col(connection)
        res = connection.execute(
            text(f"select * from nlp_quota_reservations where {pk_col} = :rid"),
            {"rid": reservation_id},
        ).mappings().first()
        if res is None:
            raise QuotaDomainError(QuotaErrorCode.RESERVATION_NOT_ACTIVE, f"Reservation {reservation_id} not found")

        if res["status"] in {"completed", "settled", "released", "expired"}:
            return TurnFinishResult(
                reservation_id=reservation_id,
                status=res["status"],
                released_micro=0,
            )

        released_micro = max(0, res["reserved_micro"])
        new_settled = actual_credits if actual_credits is not None else res.get("settled_micro", 0)

        cols_query = connection.execute(text("pragma table_info(nlp_quota_reservations)")).fetchall()
        cols = {row[1] for row in cols_query} if cols_query else set()
        if "settled_micro" in cols:
            connection.execute(
                text(
                    f"update nlp_quota_reservations set status = :st, reserved_micro = 0, settled_micro = :sm where {pk_col} = :rid"
                ),
                {"st": terminal_status, "sm": new_settled, "rid": reservation_id},
            )
        else:
            connection.execute(
                text(f"update nlp_quota_reservations set status = :st, reserved_micro = 0 where {pk_col} = :rid"),
                {"st": terminal_status, "rid": reservation_id},
            )

        # Net adjustment to buckets: decrement reserved_micro, increment consumed_micro
        bucket_rows = connection.execute(
            select(QuotaBucketModel.__table__)
            .where(
                QuotaBucketModel.owner_id == res["user_id"],
                QuotaBucketModel.bucket_type.in_(("daily", "weekly")),
            )
            .with_for_update()
        ).mappings().all()

        for b in bucket_rows:
            curr_res = b.get("reserved_micro", 0) or 0
            curr_consumed = b.get("consumed_micro", 0) or 0
            new_b_res = max(0, curr_res - released_micro)
            new_b_consumed = curr_consumed + (actual_credits or 0)
            connection.execute(
                update(QuotaBucketModel)
                .where(QuotaBucketModel.id == b["id"])
                .values(
                    reserved_micro=new_b_res,
                    consumed_micro=new_b_consumed,
                    updated_at=_db_time(at),
                )
            )

        # Release ledger entry
        if released_micro > 0:
            connection.execute(
                insert(QuotaLedgerEntryModel).values(
                    id=str(uuid.uuid4()),
                    reservation_id=reservation_id,
                    bucket_id=None,
                    grant_id=None,
                    entry_type="release",
                    amount_micro=-released_micro,
                    reserved_delta_micro=-released_micro,
                    consumed_delta_micro=0,
                    idempotency_key=f"release:{reservation_id}",
                    actor_user_id=res["user_id"],
                    reason="finish_turn_release",
                    metadata_json={"terminal_status": terminal_status},
                    created_at=_db_time(at),
                )
            )

        # Release concurrency units
        self._release_concurrency(
            connection,
            user_id=res["user_id"],
            concurrency_units=res.get("concurrency_units", 1),
            now=at,
        )

        try:
            return TurnFinishResult(
                reservation_id=reservation_id,
                status=terminal_status,  # type: ignore[arg-type]
                released_micro=released_micro,
            )
        except Exception:
            return TurnFinishResult(
                reservation_id=reservation_id,
                status="released" if released_micro > 0 else "settled",
                released_micro=released_micro,
            )

    def settle_usage_in_transaction(
        self,
        connection: Connection,
        *,
        reservation_id: str,
        operation_id: str,
        credits_micro: int,
        usage_status: str,
        usage_source: str,
        pricing_key: str | None,
        pricing_version: str | None,
    ) -> None:
        """Callback invoked by DurableModelUsageReporter inside the report transaction."""
        pk_col = self._res_pk_col(connection)
        res = connection.execute(
            text(f"select * from nlp_quota_reservations where {pk_col} = :rid"),
            {"rid": reservation_id},
        ).mappings().first()
        if res is None:
            return

        new_settled = res.get("settled_micro", 0) + credits_micro
        connection.execute(
            text(f"update nlp_quota_reservations set settled_micro = :sm where {pk_col} = :rid"),
            {"sm": new_settled, "rid": reservation_id},
        )

        connection.execute(
            insert(QuotaLedgerEntryModel).values(
                id=str(uuid.uuid4()),
                reservation_id=reservation_id,
                bucket_id=None,
                grant_id=None,
                entry_type="settle",
                amount_micro=credits_micro,
                reserved_delta_micro=0,
                consumed_delta_micro=credits_micro,
                idempotency_key=f"settle:{operation_id}",
                actor_user_id=res["user_id"],
                reason="model_usage_settlement",
                metadata_json={"operation_id": operation_id, "pricing_key": pricing_key},
                created_at=_db_time(datetime.now(UTC)),
            )
        )

    def reconcile_usage_in_transaction(
        self,
        connection: Connection,
        *,
        reservation_id: str,
        operation_id: str,
        credits_micro: int,
        usage_status: str,
        usage_source: str,
        pricing_key: str | None,
        pricing_version: str | None,
        now: datetime,
    ) -> None:
        self.settle_usage_in_transaction(
            connection,
            reservation_id=reservation_id,
            operation_id=operation_id,
            credits_micro=credits_micro,
            usage_status=usage_status,
            usage_source=usage_source,
            pricing_key=pricing_key,
            pricing_version=pricing_version,
        )

    # ==========================================================================
    # Balance & Administrative Methods
    # ==========================================================================

    def get_balance(
        self,
        user_id: str,
        workspace_id: str | None = None,
        *,
        at: datetime | None = None,
    ) -> QuotaBalance:
        """Query account snapshot (allocated, consumed, reserved, available)."""
        now = _utc(at)
        with self._engine.connect() as conn:
            grants = conn.execute(
                select(QuotaGrantModel.__table__).where(
                    QuotaGrantModel.owner_id == user_id,
                    QuotaGrantModel.status == "active",
                )
            ).mappings().all()
            if grants:
                contract_grants = [
                    QuotaGrant(
                        grant_id=g["id"],
                        owner_type=g["owner_type"],
                        owner_id=g["owner_id"],
                        source_type=g["source_type"],
                        source_id=g.get("source_id"),
                        allocated_micro=g["allocated_micro"],
                        consumed_micro=g.get("consumed_micro", 0),
                        reserved_micro=g.get("reserved_micro", 0),
                        effective_from=_utc(g["effective_from"]),
                        expires_at=_utc(g["expires_at"]) if g.get("expires_at") else None,
                        status=g["status"],
                        created_by=g["created_by"],
                        idempotency_key=g["idempotency_key"],
                    )
                    for g in grants
                ]
                return calculate_balance(contract_grants, at=now)

            # Fallback: check buckets
            bucket = conn.execute(
                select(QuotaBucketModel.__table__).where(
                    QuotaBucketModel.owner_id == user_id,
                    QuotaBucketModel.bucket_type == "daily",
                )
            ).mappings().first()
            if bucket:
                allocated = bucket.get("limit_micro") or 100_000_000
                consumed = bucket.get("consumed_micro", 0) or 0
                reserved = bucket.get("reserved_micro", 0) or 0
                available = max(0, allocated - consumed - reserved)
                return QuotaBalance(
                    allocated_micro=allocated,
                    adjustment_micro=0,
                    consumed_micro=consumed,
                    reserved_micro=reserved,
                    available_micro=available,
                )

        return QuotaBalance(
            allocated_micro=100_000_000,
            adjustment_micro=0,
            consumed_micro=0,
            reserved_micro=0,
            available_micro=100_000_000,
        )

    def create_policy(self, policy: QuotaPolicy) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                insert(QuotaPolicyModel).values(
                    id=policy.policy_id,
                    code=policy.code,
                    version=policy.version,
                    name=policy.code,
                    status="active",
                    request_limit_micro=policy.request_limit_micro,
                    daily_limit_micro=policy.daily_limit_micro,
                    weekly_limit_micro=policy.weekly_limit_micro,
                    concurrency_limit=policy.concurrency_limit,
                    max_overdraft_micro=policy.max_overdraft_micro,
                    allowed_model_profiles=list(policy.allowed_model_profiles),
                    unlimited=policy.unlimited,
                    effective_from=_db_time(datetime.now(UTC)),
                    effective_until=None,
                    created_by="admin",
                    created_at=_db_time(datetime.now(UTC)),
                    updated_at=_db_time(datetime.now(UTC)),
                )
            )

    def bind_policy(self, binding: PolicyBinding) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                insert(PolicyBindingModel).values(
                    id=str(uuid.uuid4()),
                    subject_type=binding.subject_type,
                    subject_id=binding.subject_id,
                    policy_id=binding.policy.policy_id,
                    priority=binding.priority,
                    status="active",
                    effective_from=_db_time(binding.effective_from),
                    effective_until=_db_time(binding.effective_until) if binding.effective_until else None,
                    created_at=_db_time(datetime.now(UTC)),
                    updated_at=_db_time(datetime.now(UTC)),
                )
            )

    def create_grant(self, grant: QuotaGrant) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                insert(QuotaGrantModel).values(
                    id=grant.grant_id,
                    owner_type=grant.owner_type,
                    owner_id=grant.owner_id,
                    bucket_type="daily",
                    period_start=_db_time(grant.effective_from),
                    period_end=_db_time(grant.expires_at or (grant.effective_from + timedelta(days=365))),
                    source_type=grant.source_type,
                    source_id=grant.source_id,
                    allocated_micro=grant.allocated_micro,
                    effective_from=_db_time(grant.effective_from),
                    expires_at=_db_time(grant.expires_at) if grant.expires_at else None,
                    status=grant.status,
                    reason="grant_allocation",
                    created_by=grant.created_by,
                    idempotency_key=grant.idempotency_key,
                    created_at=_db_time(datetime.now(UTC)),
                    updated_at=_db_time(datetime.now(UTC)),
                )
            )

    # ==========================================================================
    # Internal Helpers
    # ==========================================================================

    def _calculate_reservation_micro(self, connection: Connection, command: AdmitTurn, at: datetime) -> int:
        if command.estimated_micro > 0:
            return int(command.estimated_micro)

        # Conservative fallback for input tokens floor: if None or 0, enforce floor
        input_tokens = command.estimated_input_tokens
        if not input_tokens:
            input_tokens = CONSERVATIVE_INPUT_TOKEN_FLOOR

        output_tokens = command.estimated_output_tokens
        if not output_tokens:
            output_tokens = CONSERVATIVE_OUTPUT_TOKEN_FLOOR

        pricing_key = command.pricing_key
        if pricing_key is None:
            try:
                factory = get_global_model_factory()
                identity = factory.profile_identity(command.model_profile, command.model_role)
                pricing_key = identity.pricing_key
            except Exception:
                pricing_key = f"{command.model_profile}/{command.model_profile}"

        rule_row = connection.execute(
            text("select * from nlp_pricing_rules where pricing_key = :pk order by effective_from desc"),
            {"pk": pricing_key},
        ).mappings().first()

        if rule_row:
            ordinary_in_rate = rule_row["ordinary_input_credits_micro_per_million_tokens"]
            out_rate = rule_row["output_credits_micro_per_million_tokens"]
        else:
            ordinary_in_rate = DEFAULT_ORDINARY_INPUT_MICRO_PER_MILLION
            out_rate = DEFAULT_OUTPUT_MICRO_PER_MILLION

        numerator = input_tokens * ordinary_in_rate + output_tokens * out_rate
        return max(1, math.ceil(numerator / 1_000_000))

    def _fetch_policy_bindings(
        self, connection: Connection, user_id: str, workspace_id: str | None, at: datetime
    ) -> list[PolicyBinding]:
        rows = connection.execute(
            select(PolicyBindingModel.__table__, QuotaPolicyModel.__table__)
            .join(QuotaPolicyModel, PolicyBindingModel.policy_id == QuotaPolicyModel.id)
            .where(
                PolicyBindingModel.status == "active",
                PolicyBindingModel.effective_from <= _db_time(at),
            )
        ).mappings().all()
        bindings: list[PolicyBinding] = []
        for r in rows:
            policy = QuotaPolicy(
                policy_id=r["policy_id"],
                code=r["code"],
                version=r["version"],
                request_limit_micro=r["request_limit_micro"],
                daily_limit_micro=r["daily_limit_micro"],
                weekly_limit_micro=r["weekly_limit_micro"],
                concurrency_limit=r["concurrency_limit"],
                max_overdraft_micro=r["max_overdraft_micro"],
                allowed_model_profiles=tuple(r["allowed_model_profiles"] or ()),
                unlimited=bool(r["unlimited"]),
            )
            bindings.append(
                PolicyBinding(
                    subject_type=r["subject_type"],
                    subject_id=r["subject_id"],
                    policy=policy,
                    priority=r["priority"],
                    effective_from=_utc(r["effective_from"]),
                    effective_until=_utc(r["effective_until"]) if r.get("effective_until") else None,
                )
            )
        return bindings

    @staticmethod
    def _reserve_concurrency(connection: Connection, *, user_id: str, concurrency_limit: int, now: datetime) -> None:
        row = connection.execute(
            select(QuotaConcurrencyLockModel.__table__)
            .where(QuotaConcurrencyLockModel.user_id == user_id)
            .with_for_update()
        ).mappings().first()
        if row is None:
            connection.execute(
                insert(QuotaConcurrencyLockModel).values(
                    user_id=user_id,
                    active_units=1,
                    version=1,
                    updated_at=_db_time(now),
                )
            )
            return

        if row["active_units"] >= concurrency_limit:
            raise QuotaRejectedError(
                QuotaProblem(
                    code=QuotaErrorCode.CONCURRENCY_LIMIT,
                    reason="quota_concurrency_limit",
                    remaining_micro=0,
                    retryable=True,
                )
            )

        connection.execute(
            update(QuotaConcurrencyLockModel)
            .where(QuotaConcurrencyLockModel.user_id == user_id)
            .values(
                active_units=row["active_units"] + 1,
                version=row["version"] + 1,
                updated_at=_db_time(now),
            )
        )

    @staticmethod
    def _release_concurrency(connection: Connection, *, user_id: str, concurrency_units: int, now: datetime) -> None:
        row = connection.execute(
            select(QuotaConcurrencyLockModel.__table__)
            .where(QuotaConcurrencyLockModel.user_id == user_id)
            .with_for_update()
        ).mappings().first()
        if row is None:
            return
        new_units = max(0, row["active_units"] - concurrency_units)
        connection.execute(
            update(QuotaConcurrencyLockModel)
            .where(QuotaConcurrencyLockModel.user_id == user_id)
            .values(
                active_units=new_units,
                version=row["version"] + 1,
                updated_at=_db_time(now),
            )
        )

    def close(self) -> None:
        if self._owns_engine:
            self._engine.dispose()
