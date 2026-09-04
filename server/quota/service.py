"""Transactional quota admission, reservation, settlement, and expiry."""

from __future__ import annotations

import threading
import time
import uuid
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Sequence, TypeVar

from sqlalchemy import Engine, and_, create_engine, func, insert, or_, select, update
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError, IntegrityError

from core.model_runtime.usage import BillableFeatureUsage
from server.quota.contracts import (
    AdmitTurn,
    FinishTurn,
    PolicyBinding,
    QuotaPolicy,
    QuotaProblem,
    TurnAdmissionResult,
    TurnFinishResult,
    UsageRecordResult,
    UsageStatus,
)
from server.quota.errors import QuotaDomainError, QuotaErrorCode, QuotaRejectedError
from server.quota.models import (
    PolicyBindingModel,
    PricingRuleModel,
    QuotaAdjustmentModel,
    QuotaAlertModel,
    QuotaBucketModel,
    QuotaConcurrencyLockModel,
    QuotaCreditOperationModel,
    QuotaCreditScopeLockModel,
    QuotaDailyRollupModel,
    QuotaGrantModel,
    QuotaLedgerEntryModel,
    QuotaPolicyModel,
    QuotaProviderBillingModel,
    QuotaReservationModel,
    QuotaRoleCreditOperationModel,
    QuotaUsageArchiveBatchModel,
    UsageEventModel,
)
from server.infrastructure.mysql.models import ClassroomModel
from server.quota.policy import resolve_effective_policy


UTC = timezone.utc
logger = logging.getLogger(__name__)
_GLOBAL_QUOTA_LOCK = threading.RLock()
_ACTIVE_RESERVATION_STATUSES = ("reserved", "running", "settling")
_RETRYABLE_MYSQL_TRANSACTION_ERRORS = frozenset({1205, 1213})
_MYSQL_TRANSACTION_ATTEMPTS = 6
_MYSQL_TRANSACTION_RETRY_BASE_S = 0.02
_TransactionResult = TypeVar("_TransactionResult")


def _utc(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _db_time(value: datetime) -> datetime:
    """MySQL DATETIME stores UTC without a timezone marker."""
    return _utc(value).replace(tzinfo=None)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else _utc(value)


def _now_factory() -> datetime:
    return datetime.now(UTC)


def _mysql_error_code(error: DBAPIError) -> int | None:
    args = getattr(getattr(error, "orig", None), "args", ())
    if not args:
        return None
    try:
        return int(args[0])
    except (TypeError, ValueError):
        return None


def _is_retryable_mysql_transaction_error(error: DBAPIError) -> bool:
    return (
        not error.connection_invalidated
        and _mysql_error_code(error) in _RETRYABLE_MYSQL_TRANSACTION_ERRORS
    )


def retry_mysql_transaction(
    operation: Callable[[], _TransactionResult],
) -> _TransactionResult:
    """Run a complete MySQL transaction operation with bounded deadlock retry.

    MySQL invalidates the current transaction after lock wait timeout (1205)
    or deadlock victim selection (1213).  The callable must therefore own the
    complete ``engine.begin()`` scope so every read, write, and commit is
    repeated against a fresh transaction.
    """
    for attempt in range(_MYSQL_TRANSACTION_ATTEMPTS):
        try:
            return operation()
        except DBAPIError as error:
            if (
                not _is_retryable_mysql_transaction_error(error)
                or attempt + 1 >= _MYSQL_TRANSACTION_ATTEMPTS
            ):
                raise
            time.sleep(_MYSQL_TRANSACTION_RETRY_BASE_S * (2**attempt))
    raise RuntimeError("unreachable MySQL transaction retry state")


class QuotaService:
    """The single write seam for Phase 2 quota state.

    Admission and settlement are each one database transaction.  The process
    lock keeps the SQLite test/embedded runtime deterministic; MySQL still
    takes row locks with ``FOR UPDATE`` so separate workers cannot reserve the
    same remaining balance.
    """

    def __init__(
        self,
        database: str | Engine,
        *,
        lease_seconds: int = 300,
        snapshot_notifier: Callable[..., Any] | Any | None = None,
    ) -> None:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        if isinstance(database, str):
            if database.startswith("mysql+aiomysql://"):
                database = database.replace("mysql+aiomysql://", "mysql+pymysql://", 1)
            self._engine = create_engine(database, pool_pre_ping=True)
            self._owns_engine = True
        else:
            self._engine = database
            self._owns_engine = False
        self.lease_seconds = lease_seconds
        self._snapshot_notifier = snapshot_notifier

    @property
    def engine(self) -> Engine:
        return self._engine

    def set_snapshot_notifier(
        self, notifier: Callable[..., Any] | Any | None
    ) -> None:
        """Attach the process-wide publisher used after committed mutations."""
        self._snapshot_notifier = notifier

    def notify_reservation(self, reservation_id: str) -> None:
        """Notify both user and workspace subscribers for a reservation."""
        with self._engine.connect() as connection:
            row = connection.execute(
                select(
                    QuotaReservationModel.user_id,
                    QuotaReservationModel.workspace_id,
                ).where(QuotaReservationModel.id == reservation_id)
            ).mappings().first()
        if row is None:
            return
        self._notify_snapshot(owner_type="user", owner_id=row["user_id"])
        if row["workspace_id"]:
            self._notify_snapshot(
                owner_type="workspace", owner_id=row["workspace_id"]
            )

    def _notify_snapshot(
        self, *, owner_type: str | None = None, owner_id: str | None = None
    ) -> None:
        notifier = self._snapshot_notifier
        if notifier is None:
            return
        try:
            publish = getattr(notifier, "publish", notifier)
            publish(owner_type=owner_type, owner_id=owner_id)
        except Exception:
            # Accounting is durable and must not be rolled back because a
            # best-effort UI notification backend is unavailable.
            logger.exception("quota snapshot notification failed")

    @staticmethod
    def reservation_id_for_turn(turn_id: str) -> str:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"pro-nlp:quota-reservation:{turn_id}"))

    @staticmethod
    def _classrooms_in_workspace(
        connection: Connection,
        *,
        classroom_ids: Sequence[str],
        workspace_id: str | None,
    ) -> tuple[str, ...]:
        """Return only active classrooms owned by the request workspace.

        Authenticated principals carry classroom IDs across requests, but a
        classroom ID has no meaning outside its owning workspace.  Filtering
        at this database-backed quota seam keeps direct callers and Gateway
        callers on the same accounting boundary.
        """
        if not classroom_ids or workspace_id is None:
            return ()
        rows = connection.execute(
            select(ClassroomModel.id).where(
                ClassroomModel.id.in_(set(classroom_ids)),
                ClassroomModel.workspace_id == workspace_id,
                ClassroomModel.status == "active",
            )
        ).scalars().all()
        return tuple(sorted(set(rows)))

    def admit_turn(
        self,
        command: AdmitTurn,
        *,
        role_codes: Sequence[str] = (),
        classroom_ids: Sequence[str] = (),
        now: datetime | None = None,
    ) -> TurnAdmissionResult:
        def admit_once() -> TurnAdmissionResult:
            # Keep the process lock inside the retry operation.  A retried
            # attempt must re-enter both the lock and a fresh DB transaction.
            with _GLOBAL_QUOTA_LOCK:
                with self._engine.begin() as connection:
                    return self.admit_in_transaction(
                        connection,
                        command,
                        role_codes=role_codes,
                        classroom_ids=classroom_ids,
                        now=now,
                    )

        result = retry_mysql_transaction(admit_once)
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
        classroom_ids = self._classrooms_in_workspace(
            connection,
            classroom_ids=classroom_ids,
            workspace_id=command.workspace_id,
        )
        existing = self._find_existing_reservation(connection, command)
        rearm_existing = False
        if existing is not None:
            if (
                existing["turn_id"] != command.turn_id
                or existing["user_id"] != command.user_id
                or existing["idempotency_key"] != command.idempotency_key
            ):
                raise self._rejection(
                    QuotaErrorCode.RESERVATION_CONFLICT,
                    "The idempotency key or Turn is already bound to another request",
                )
            lease_expires_at = _aware(existing["lease_expires_at"])
            if (
                existing["status"] in _ACTIVE_RESERVATION_STATUSES
                and lease_expires_at is not None
                and lease_expires_at > at
            ):
                return self._admission_result(existing, duplicate=True)
            if (
                existing["status"] in {"released", "expired"}
                and int(existing["settled_micro"]) == 0
            ) or (
                existing["status"] in _ACTIVE_RESERVATION_STATUSES
                and (lease_expires_at is None or lease_expires_at <= at)
            ):
                rearm_existing = True
            else:
                return self._admission_result(existing, duplicate=True)

        binding = self._effective_binding(
            connection,
            command,
            role_codes=role_codes,
            classroom_ids=classroom_ids,
            at=at,
            subject_types={"default", "role", "user", "classroom"},
        )
        policy = binding.policy
        workspace_binding = self._effective_binding(
            connection,
            command,
            role_codes=role_codes,
            classroom_ids=classroom_ids,
            at=at,
            subject_types={"workspace"},
            optional=True,
        )
        workspace_policy = workspace_binding.policy if workspace_binding is not None else None
        classroom_policies: list[tuple[str, PolicyBinding]] = []
        for classroom_id in sorted(set(classroom_ids)):
            classroom_binding = self._effective_binding(
                connection,
                command,
                role_codes=(),
                classroom_ids=(classroom_id,),
                at=at,
                subject_types={"classroom"},
                optional=True,
            )
            if classroom_binding is None:
                if self._has_active_classroom_capacity(
                    connection, classroom_id=classroom_id, at=at
                ):
                    raise self._rejection(
                        QuotaErrorCode.ADMISSION_DENIED,
                        f"Classroom {classroom_id!r} has capacity but no active classroom policy",
                    )
                continue
            classroom_policies.append((classroom_id, classroom_binding))
        policies = [
            policy,
            *([workspace_policy] if workspace_policy is not None else []),
            *(binding.policy for _, binding in classroom_policies),
        ]
        for candidate in policies:
            if candidate.allowed_model_profiles and command.model_profile not in candidate.allowed_model_profiles:
                raise self._rejection(
                    QuotaErrorCode.MODEL_NOT_ALLOWED,
                    f"Model profile {command.model_profile!r} is not allowed by policy {candidate.code}",
                    allowed_model_profiles=candidate.allowed_model_profiles,
                )

        reservation_micro = int(command.estimated_micro)
        if reservation_micro == 0:
            try:
                reservation_micro = self._estimate_micro(connection, command, at)
            except QuotaDomainError as error:
                raise self._rejection(error.code, str(error), retryable=True) from error
        request_limits = [candidate.request_limit_micro for candidate in policies if candidate.request_limit_micro is not None]
        request_limit = min(request_limits) if request_limits else None
        if request_limit is not None and reservation_micro > request_limit:
            raise self._rejection(
                QuotaErrorCode.REQUEST_LIMIT,
                "The estimated cost exceeds the per-request quota",
                remaining_micro=request_limit,
            )

        if policy.concurrency_limit is not None:
            # Materialize and lock the per-user serialization row before any
            # period Bucket can be initialized.  Without this ordering, a
            # burst of first-time requests can concurrently upsert several
            # Bucket unique indexes and MySQL may choose one as a deadlock
            # victim before the concurrency counter is reached.
            self._get_or_create_concurrency_lock(
                connection,
                user_id=command.user_id,
                now=at,
            )

        self._expire_stale_in_transaction(connection, at)

        buckets: list[dict[str, Any]] = []
        subjects = [("user", command.user_id, policy)]
        if workspace_policy is not None and command.workspace_id is not None:
            subjects.append(("workspace", command.workspace_id, workspace_policy))
        subjects.extend(
            ("classroom", classroom_id, classroom_binding.policy)
            for classroom_id, classroom_binding in classroom_policies
        )
        for owner_type, owner_id, subject_policy in subjects:
            for bucket_type, start, end, limit in self._periods(subject_policy, at):
                bucket = self._get_or_create_bucket(
                    connection,
                    owner_type=owner_type,
                    owner_id=owner_id,
                    bucket_type=bucket_type,
                    period_start=start,
                    period_end=end,
                    policy=subject_policy,
                    limit=limit,
                    now=at,
                )
                extra_capacity = self._additional_capacity(
                    connection,
                    owner_type=owner_type,
                    owner_id=owner_id,
                    bucket_type=bucket_type,
                    period_start=start,
                    period_end=end,
                    at=at,
                )
                available = self._available(bucket, subject_policy.max_overdraft_micro, extra_capacity)
                if reservation_micro > available:
                    if owner_type == "workspace":
                        code = QuotaErrorCode.WORKSPACE_EXHAUSTED
                    else:
                        code = (
                            QuotaErrorCode.DAILY_EXHAUSTED
                            if bucket_type == "daily"
                            else QuotaErrorCode.WEEKLY_EXHAUSTED
                        )
                    raise self._rejection(
                        code,
                        f"The {owner_type} {bucket_type} quota is exhausted",
                        remaining_micro=available,
                        reset_at=end,
                        retryable=True,
                    )
                buckets.append(bucket)

        if policy.concurrency_limit is not None:
            self._reserve_concurrency(
                connection,
                user_id=command.user_id,
                concurrency_limit=policy.concurrency_limit,
                now=at,
            )

        reservation_id = self.reservation_id_for_turn(command.turn_id)
        db_now = _db_time(at)
        lease_expires = _db_time(at + timedelta(seconds=self.lease_seconds))
        snapshot = {
            "policy_id": policy.policy_id,
            "code": policy.code,
            "version": policy.version,
            "request_limit_micro": policy.request_limit_micro,
            "daily_limit_micro": policy.daily_limit_micro,
            "weekly_limit_micro": policy.weekly_limit_micro,
            "concurrency_limit": policy.concurrency_limit,
            "max_overdraft_micro": policy.max_overdraft_micro,
            "overdrafts": {
                "user": policy.max_overdraft_micro,
                **(
                    {"workspace": workspace_policy.max_overdraft_micro}
                    if workspace_policy is not None
                    else {}
                ),
                **{
                    f"classroom:{classroom_id}": classroom_binding.policy.max_overdraft_micro
                    for classroom_id, classroom_binding in classroom_policies
                },
            },
            "allowed_model_profiles": list(policy.allowed_model_profiles),
            "unlimited": policy.unlimited,
            "sources": {
                "base": {
                    "subject_type": binding.subject_type,
                    "subject_id": binding.subject_id,
                    "policy_id": policy.policy_id,
                    "version": policy.version,
                },
                "workspace": (
                    {
                        "subject_type": workspace_binding.subject_type,
                        "subject_id": workspace_binding.subject_id,
                        "policy_id": workspace_policy.policy_id,
                        "version": workspace_policy.version,
                    }
                    if workspace_binding is not None and workspace_policy is not None
                    else None
                ),
                "classrooms": [
                    {
                        "subject_type": "classroom",
                        "subject_id": classroom_id,
                        "policy_id": classroom_binding.policy.policy_id,
                        "version": classroom_binding.policy.version,
                    }
                    for classroom_id, classroom_binding in classroom_policies
                ],
            },
        }
        if rearm_existing:
            connection.execute(
                update(QuotaReservationModel)
                .where(QuotaReservationModel.id == reservation_id)
                .values(
                    policy_id=policy.policy_id,
                    policy_version=policy.version,
                    policy_snapshot_json=snapshot,
                    reserved_micro=reservation_micro,
                    settled_micro=0,
                    status="reserved",
                    lease_expires_at=lease_expires,
                    last_heartbeat_at=db_now,
                    max_overdraft_micro=policy.max_overdraft_micro,
                    over_limit=False,
                    updated_at=db_now,
                )
            )
        else:
            connection.execute(
                insert(QuotaReservationModel).values(
                    id=reservation_id,
                    turn_id=command.turn_id,
                    user_id=command.user_id,
                    workspace_id=command.workspace_id,
                    policy_id=policy.policy_id,
                    policy_version=policy.version,
                    policy_snapshot_json=snapshot,
                    idempotency_key=command.idempotency_key,
                    reserved_micro=reservation_micro,
                    settled_micro=0,
                    status="reserved",
                    lease_expires_at=lease_expires,
                    last_heartbeat_at=db_now,
                    max_overdraft_micro=policy.max_overdraft_micro,
                    concurrency_units=1,
                    over_limit=False,
                    created_at=db_now,
                    updated_at=db_now,
                )
            )
        reserve_generation = 1
        if rearm_existing:
            reserve_generation = len(
                connection.execute(
                    select(QuotaLedgerEntryModel.id)
                    .where(
                        QuotaLedgerEntryModel.reservation_id == reservation_id,
                        QuotaLedgerEntryModel.entry_type == "reserve",
                    )
                ).scalars().all()
            ) + 1
        reserve_suffix = "" if reserve_generation == 1 else f":retry{reserve_generation}"
        if not buckets:
            self._insert_ledger(
                connection,
                reservation_id=reservation_id,
                bucket_id=None,
                entry_type="reserve",
                amount_micro=reservation_micro,
                reserved_delta_micro=reservation_micro,
                consumed_delta_micro=0,
                idempotency_key=f"reserve:{reservation_id}:none{reserve_suffix}",
                reason="turn_admission",
                metadata={"turn_id": command.turn_id},
                created_at=db_now,
            )
        for bucket in buckets:
            connection.execute(
                update(QuotaBucketModel)
                .where(QuotaBucketModel.id == bucket["id"])
                .values(
                    reserved_micro=int(bucket["reserved_micro"]) + reservation_micro,
                    version=int(bucket["version"]) + 1,
                    updated_at=db_now,
                )
            )
            self._insert_ledger(
                connection,
                reservation_id=reservation_id,
                bucket_id=bucket["id"],
                entry_type="reserve",
                amount_micro=reservation_micro,
                reserved_delta_micro=reservation_micro,
                consumed_delta_micro=0,
                idempotency_key=f"reserve:{reservation_id}:{bucket['id']}{reserve_suffix}",
                reason="turn_admission",
                metadata={"turn_id": command.turn_id, "bucket_type": bucket["bucket_type"]},
                created_at=db_now,
            )
        return TurnAdmissionResult(
            allowed=True,
            reservation_id=reservation_id,
            reserved_micro=reservation_micro,
            policy_id=policy.policy_id,
            policy_version=policy.version,
        )

    def reserve_feature_usage(
        self,
        *,
        reservation_id: str,
        operation_id: str,
        pricing_key: str,
        feature_usage: BillableFeatureUsage,
        now: datetime | None = None,
    ) -> int:
        """Add a feature hold to the existing Turn Reservation.

        This deliberately does not create an Agent-step Reservation. The hold
        is recorded on the Turn's existing Reservation/Ledger and is consumed
        by the UsageEvent settlement that uses the same operation id.
        """

        with _GLOBAL_QUOTA_LOCK:
            with self._engine.begin() as connection:
                reserved = self.reserve_feature_usage_in_transaction(
                    connection,
                    reservation_id=reservation_id,
                    operation_id=operation_id,
                    pricing_key=pricing_key,
                    feature_usage=feature_usage,
                    now=now,
                )
        self.notify_reservation(reservation_id)
        return reserved

    def reserve_feature_usage_in_transaction(
        self,
        connection: Connection,
        *,
        reservation_id: str,
        operation_id: str,
        pricing_key: str,
        feature_usage: BillableFeatureUsage,
        now: datetime | None = None,
    ) -> int:
        at = _utc(now)
        db_now = _db_time(at)
        prefix = f"reserve-feature:{operation_id}:"
        reservation = connection.execute(
            select(QuotaReservationModel)
            .where(QuotaReservationModel.id == reservation_id)
            .with_for_update()
        ).mappings().first()
        lease_expires_at = _aware(reservation["lease_expires_at"]) if reservation else None
        if (
            reservation is None
            or reservation["status"] not in _ACTIVE_RESERVATION_STATUSES
            or lease_expires_at is None
            or lease_expires_at <= at
        ):
            raise self._rejection(
                QuotaErrorCode.RESERVATION_NOT_ACTIVE,
                f"Reservation {reservation_id!r} is not active",
            )

        existing = connection.execute(
            select(QuotaLedgerEntryModel)
            .where(
                QuotaLedgerEntryModel.reservation_id == reservation_id,
                QuotaLedgerEntryModel.entry_type == "reserve",
                QuotaLedgerEntryModel.idempotency_key.like(f"{prefix}%"),
            )
        ).mappings().all()
        feature_facts = feature_usage.model_dump(mode="json")
        if existing:
            metadata = existing[0]["metadata_json"]
            if (
                metadata.get("pricing_key") != pricing_key
                or metadata.get("feature_usage") != feature_facts
            ):
                raise self._rejection(
                    QuotaErrorCode.RESERVATION_CONFLICT,
                    "Feature reservation replay has different usage facts",
                )
            return int(metadata["reserved_micro"])

        feature_micro = self._estimate_feature_micro(
            connection,
            pricing_key=pricing_key,
            feature_usage=feature_usage,
            at=at,
        )
        snapshot = reservation["policy_snapshot_json"] or {}
        request_limit = snapshot.get("request_limit_micro")
        request_total = (
            int(reservation["settled_micro"])
            + int(reservation["reserved_micro"])
            + feature_micro
        )
        if request_limit is not None and request_total > int(request_limit):
            raise self._rejection(
                QuotaErrorCode.REQUEST_LIMIT,
                "The feature hold exceeds the per-request quota",
                remaining_micro=max(
                    0,
                    int(request_limit)
                    - int(reservation["settled_micro"])
                    - int(reservation["reserved_micro"]),
                ),
            )

        bucket_rows = self._reservation_buckets(connection, reservation_id)
        for bucket in bucket_rows:
            available = self._available(
                bucket,
                self._reservation_overdraft(
                    reservation, bucket["owner_type"], bucket["owner_id"]
                ),
                self._additional_capacity_for_bucket(connection, bucket, at),
            )
            if feature_micro > available:
                if bucket["owner_type"] == "workspace":
                    code = QuotaErrorCode.WORKSPACE_EXHAUSTED
                else:
                    code = (
                        QuotaErrorCode.DAILY_EXHAUSTED
                        if bucket["bucket_type"] == "daily"
                        else QuotaErrorCode.WEEKLY_EXHAUSTED
                    )
                raise self._rejection(
                    code,
                    f"The {bucket['owner_type']} {bucket['bucket_type']} quota is exhausted",
                    remaining_micro=available,
                    reset_at=_aware(bucket["period_end"]),
                    retryable=True,
                )

        metadata = {
            "operation_id": operation_id,
            "pricing_key": pricing_key,
            "feature_usage": feature_facts,
            "reserved_micro": feature_micro,
        }
        if not bucket_rows:
            self._insert_ledger(
                connection,
                reservation_id=reservation_id,
                bucket_id=None,
                entry_type="reserve",
                amount_micro=feature_micro,
                reserved_delta_micro=feature_micro,
                consumed_delta_micro=0,
                idempotency_key=f"{prefix}none",
                reason="feature_admission",
                metadata=metadata,
                created_at=db_now,
            )
        for bucket in bucket_rows:
            connection.execute(
                update(QuotaBucketModel)
                .where(QuotaBucketModel.id == bucket["id"])
                .values(
                    reserved_micro=int(bucket["reserved_micro"]) + feature_micro,
                    version=int(bucket["version"]) + 1,
                    updated_at=db_now,
                )
            )
            self._insert_ledger(
                connection,
                reservation_id=reservation_id,
                bucket_id=bucket["id"],
                entry_type="reserve",
                amount_micro=feature_micro,
                reserved_delta_micro=feature_micro,
                consumed_delta_micro=0,
                idempotency_key=f"{prefix}{bucket['id']}",
                reason="feature_admission",
                metadata={**metadata, "bucket_type": bucket["bucket_type"]},
                created_at=db_now,
            )
        connection.execute(
            update(QuotaReservationModel)
            .where(QuotaReservationModel.id == reservation_id)
            .values(
                reserved_micro=int(reservation["reserved_micro"]) + feature_micro,
                updated_at=db_now,
            )
        )
        return feature_micro

    def release_feature_usage(
        self,
        *,
        reservation_id: str,
        operation_id: str,
        now: datetime | None = None,
    ) -> int:
        """Release an unused feature hold after the paid operation did not run."""

        with _GLOBAL_QUOTA_LOCK:
            with self._engine.begin() as connection:
                released = self.release_feature_usage_in_transaction(
                    connection,
                    reservation_id=reservation_id,
                    operation_id=operation_id,
                    now=now,
                )
        self.notify_reservation(reservation_id)
        return released

    def release_feature_usage_in_transaction(
        self,
        connection: Connection,
        *,
        reservation_id: str,
        operation_id: str,
        now: datetime | None = None,
    ) -> int:
        db_now = _db_time(_utc(now))
        reserve_prefix = f"reserve-feature:{operation_id}:"
        release_prefix = f"release-feature:{operation_id}:"
        reservation = connection.execute(
            select(QuotaReservationModel)
            .where(QuotaReservationModel.id == reservation_id)
            .with_for_update()
        ).mappings().first()
        if reservation is None:
            raise QuotaDomainError(
                QuotaErrorCode.RESERVATION_NOT_ACTIVE,
                f"Reservation {reservation_id!r} does not exist",
            )
        prior_release = connection.execute(
            select(QuotaLedgerEntryModel.id)
            .where(
                QuotaLedgerEntryModel.reservation_id == reservation_id,
                QuotaLedgerEntryModel.entry_type == "release",
                QuotaLedgerEntryModel.idempotency_key.like(f"{release_prefix}%"),
            )
            .limit(1)
        ).first()
        if prior_release is not None:
            return 0
        reserves = connection.execute(
            select(QuotaLedgerEntryModel)
            .where(
                QuotaLedgerEntryModel.reservation_id == reservation_id,
                QuotaLedgerEntryModel.entry_type == "reserve",
                QuotaLedgerEntryModel.idempotency_key.like(f"{reserve_prefix}%"),
            )
        ).mappings().all()
        if not reserves:
            return 0
        requested = int(reserves[0]["metadata_json"]["reserved_micro"])
        released = min(int(reservation["reserved_micro"]), requested)
        bucket_rows = self._reservation_buckets(connection, reservation_id)
        if not bucket_rows:
            self._insert_ledger(
                connection,
                reservation_id=reservation_id,
                bucket_id=None,
                entry_type="release",
                amount_micro=-released,
                reserved_delta_micro=-released,
                consumed_delta_micro=0,
                idempotency_key=f"{release_prefix}none",
                reason="feature_execution_failed",
                metadata={"operation_id": operation_id, "released_micro": released},
                created_at=db_now,
            )
        for bucket in bucket_rows:
            bucket_release = min(int(bucket["reserved_micro"]), released)
            self._insert_ledger(
                connection,
                reservation_id=reservation_id,
                bucket_id=bucket["id"],
                entry_type="release",
                amount_micro=-bucket_release,
                reserved_delta_micro=-bucket_release,
                consumed_delta_micro=0,
                idempotency_key=f"{release_prefix}{bucket['id']}",
                reason="feature_execution_failed",
                metadata={"operation_id": operation_id, "released_micro": released},
                created_at=db_now,
            )
            connection.execute(
                update(QuotaBucketModel)
                .where(QuotaBucketModel.id == bucket["id"])
                .values(
                    reserved_micro=max(
                        0, int(bucket["reserved_micro"]) - bucket_release
                    ),
                    version=int(bucket["version"]) + 1,
                    updated_at=db_now,
                )
            )
        connection.execute(
            update(QuotaReservationModel)
            .where(QuotaReservationModel.id == reservation_id)
            .values(
                reserved_micro=max(
                    0, int(reservation["reserved_micro"]) - released
                ),
                updated_at=db_now,
            )
        )
        return released

    def settle_usage(
        self,
        *,
        reservation_id: str,
        operation_id: str,
        credits_micro: int,
        usage_status: UsageStatus,
        usage_source: str = "provider",
        pricing_key: str | None = None,
        pricing_version: str | None = None,
        now: datetime | None = None,
    ) -> UsageRecordResult:
        with _GLOBAL_QUOTA_LOCK:
            with self._engine.begin() as connection:
                result = self.settle_usage_in_transaction(
                    connection,
                    reservation_id=reservation_id,
                    operation_id=operation_id,
                    credits_micro=credits_micro,
                    usage_status=usage_status,
                    usage_source=usage_source,
                    pricing_key=pricing_key,
                    pricing_version=pricing_version,
                    now=now,
                )
        self.notify_reservation(reservation_id)
        return result

    def settle_usage_in_transaction(
        self,
        connection: Connection,
        *,
        reservation_id: str,
        operation_id: str,
        credits_micro: int,
        usage_status: UsageStatus,
        usage_source: str = "provider",
        pricing_key: str | None = None,
        pricing_version: str | None = None,
        now: datetime | None = None,
    ) -> UsageRecordResult:
        if isinstance(credits_micro, bool) or not isinstance(credits_micro, int) or credits_micro < 0:
            raise QuotaDomainError(
                QuotaErrorCode.INVALID_USAGE,
                "credits_micro must be a non-negative integer",
            )
        if usage_status not in {"exact", "estimated", "pending", "unavailable"}:
            raise QuotaDomainError(
                QuotaErrorCode.INVALID_USAGE,
                f"Unknown usage status {usage_status!r}",
            )
        db_now = _db_time(_utc(now))
        prefix = f"settle:{operation_id}:"
        reservation = connection.execute(
            select(QuotaReservationModel)
            .where(QuotaReservationModel.id == reservation_id)
            .with_for_update()
        ).mappings().first()
        if reservation is None:
            raise QuotaDomainError(
                QuotaErrorCode.RESERVATION_NOT_ACTIVE,
                f"Reservation {reservation_id!r} does not exist",
            )
        # Lock the reservation before reading the replay marker.  This turns
        # cross-process MySQL retries into a current read after the first
        # settlement commits, instead of relying on a stale REPEATABLE READ
        # snapshot and then surfacing a unique-key error.
        existing_rows = connection.execute(
            select(QuotaLedgerEntryModel)
            .where(
                QuotaLedgerEntryModel.reservation_id == reservation_id,
                QuotaLedgerEntryModel.entry_type == "settle",
                QuotaLedgerEntryModel.idempotency_key.like(f"{prefix}%"),
            )
        ).mappings().all()
        if existing_rows:
            metadata = existing_rows[0]["metadata_json"]
            if (
                metadata.get("usage_status") in {"pending", "unavailable"}
                and usage_status in {"exact", "estimated"}
            ):
                return self.reconcile_usage_in_transaction(
                    connection,
                    reservation_id=reservation_id,
                    operation_id=operation_id,
                    credits_micro=credits_micro,
                    usage_status=usage_status,
                    usage_source=usage_source,
                    pricing_key=pricing_key,
                    pricing_version=pricing_version,
                    now=now,
                )
            if (
                int(metadata.get("credits_micro", -1)) != credits_micro
                or metadata.get("usage_status") != usage_status
            ):
                raise QuotaDomainError(
                    QuotaErrorCode.SETTLEMENT_CONFLICT,
                    "Settlement replay has different usage facts",
                )
            return UsageRecordResult(
                operation_id=operation_id,
                usage_source=metadata.get("usage_source", usage_source),
                credits_micro=credits_micro,
                usage_status=usage_status,
                over_limit=any(
                    bool(row["metadata_json"].get("over_limit", False))
                    for row in existing_rows
                ),
                pricing_key=metadata.get("pricing_key", pricing_key),
                pricing_version=metadata.get("pricing_version", pricing_version),
            )
        terminal_reservation = reservation["status"] in {
            "released",
            "expired",
            "settled",
        }
        metadata_base = {
            "operation_id": operation_id,
            "credits_micro": credits_micro,
            "usage_status": usage_status,
            "usage_source": usage_source,
            "pricing_key": pricing_key,
            "pricing_version": pricing_version,
        }
        if usage_status in {"pending", "unavailable"}:
            self._insert_ledger(
                connection,
                reservation_id=reservation_id,
                bucket_id=None,
                entry_type="settle",
                amount_micro=0,
                reserved_delta_micro=0,
                consumed_delta_micro=0,
                idempotency_key=f"{prefix}none",
                reason="usage_pending" if usage_status == "pending" else "usage_unavailable",
                metadata={**metadata_base, "over_limit": False},
                created_at=db_now,
            )
            if not terminal_reservation:
                connection.execute(
                    update(QuotaReservationModel)
                    .where(QuotaReservationModel.id == reservation_id)
                    .values(status="settling", updated_at=db_now)
                )
            return UsageRecordResult(
                operation_id=operation_id,
                usage_source=usage_source if usage_source in {"provider", "estimated", "none"} else "provider",
                credits_micro=credits_micro,
                usage_status=usage_status,
                over_limit=False,
                pricing_key=pricing_key,
                pricing_version=pricing_version,
            )

        bucket_rows = self._reservation_buckets(connection, reservation_id)
        feature_reserve = connection.execute(
            select(QuotaLedgerEntryModel.metadata_json)
            .where(
                QuotaLedgerEntryModel.reservation_id == reservation_id,
                QuotaLedgerEntryModel.entry_type == "reserve",
                QuotaLedgerEntryModel.idempotency_key.like(
                    f"reserve-feature:{operation_id}:%"
                ),
            )
            .limit(1)
        ).scalar_one_or_none()
        feature_hold_micro = int(
            (feature_reserve or {}).get("reserved_micro", 0)
        )
        # When a conservative image-unit hold is later replaced by cheaper
        # exact visual Tokens, release the unused part with this settlement.
        release_amount = min(
            int(reservation["reserved_micro"]),
            max(credits_micro, feature_hold_micro),
        )
        over_limit = False
        if not bucket_rows:
            self._insert_ledger(
                connection,
                reservation_id=reservation_id,
                bucket_id=None,
                entry_type="settle",
                amount_micro=credits_micro,
                reserved_delta_micro=-release_amount,
                consumed_delta_micro=credits_micro,
                idempotency_key=f"{prefix}none",
                reason="provider_usage",
                metadata={**metadata_base, "over_limit": False},
                created_at=db_now,
            )
        for bucket in bucket_rows:
            bucket_release = min(int(bucket["reserved_micro"]), release_amount)
            new_consumed = int(bucket["consumed_micro"]) + credits_micro
            limit = bucket["limit_micro"]
            extra_capacity = self._additional_capacity_for_bucket(connection, bucket, db_now)
            bucket_over_limit = (
                limit is not None
                and new_consumed
                > int(limit)
                + extra_capacity
                + self._reservation_overdraft(
                    reservation, bucket["owner_type"], bucket["owner_id"]
                )
            )
            over_limit = over_limit or bucket_over_limit
            self._insert_ledger(
                connection,
                reservation_id=reservation_id,
                bucket_id=bucket["id"],
                entry_type="settle",
                amount_micro=credits_micro,
                reserved_delta_micro=-bucket_release,
                consumed_delta_micro=credits_micro,
                idempotency_key=f"{prefix}{bucket['id']}",
                reason="provider_usage",
                metadata={**metadata_base, "over_limit": bucket_over_limit},
                created_at=db_now,
            )
            connection.execute(
                update(QuotaBucketModel)
                .where(QuotaBucketModel.id == bucket["id"])
                .values(
                    consumed_micro=new_consumed,
                    reserved_micro=max(0, int(bucket["reserved_micro"]) - bucket_release),
                    over_limit=bucket_over_limit,
                    version=int(bucket["version"]) + 1,
                    updated_at=db_now,
                )
            )
        connection.execute(
            update(QuotaReservationModel)
            .where(QuotaReservationModel.id == reservation_id)
            .values(
                reserved_micro=max(0, int(reservation["reserved_micro"]) - release_amount),
                settled_micro=int(reservation["settled_micro"]) + credits_micro,
                status="settled" if terminal_reservation else "settling",
                over_limit=over_limit or bool(reservation["over_limit"]),
                updated_at=db_now,
            )
        )
        return UsageRecordResult(
            operation_id=operation_id,
            usage_source=usage_source if usage_source in {"provider", "estimated", "none"} else "provider",
            credits_micro=credits_micro,
            usage_status=usage_status,
            over_limit=over_limit,
            pricing_key=pricing_key,
            pricing_version=pricing_version,
        )

    def reconcile_usage(
        self,
        *,
        reservation_id: str,
        operation_id: str,
        credits_micro: int,
        usage_status: UsageStatus,
        usage_source: str = "provider",
        pricing_key: str | None = None,
        pricing_version: str | None = None,
        now: datetime | None = None,
    ) -> UsageRecordResult:
        """Replace a pending/unavailable fact with priced usage.

        Reconciliation is allowed after Turn finalization.  The original
        reservation ledger may already have released its hold, so this method
        writes explicit ``reconcile`` entries against the original buckets and
        charges the actual usage exactly once by operation id.
        """
        with _GLOBAL_QUOTA_LOCK:
            with self._engine.begin() as connection:
                result = self.reconcile_usage_in_transaction(
                    connection,
                    reservation_id=reservation_id,
                    operation_id=operation_id,
                    credits_micro=credits_micro,
                    usage_status=usage_status,
                    usage_source=usage_source,
                    pricing_key=pricing_key,
                    pricing_version=pricing_version,
                    now=now,
                )
        self.notify_reservation(reservation_id)
        return result

    def reconcile_usage_in_transaction(
        self,
        connection: Connection,
        *,
        reservation_id: str,
        operation_id: str,
        credits_micro: int,
        usage_status: UsageStatus,
        usage_source: str = "provider",
        pricing_key: str | None = None,
        pricing_version: str | None = None,
        now: datetime | None = None,
    ) -> UsageRecordResult:
        if isinstance(credits_micro, bool) or not isinstance(credits_micro, int) or credits_micro < 0:
            raise QuotaDomainError(
                QuotaErrorCode.INVALID_USAGE,
                "credits_micro must be a non-negative integer",
            )
        if usage_status not in {"exact", "estimated"}:
            raise QuotaDomainError(
                QuotaErrorCode.INVALID_USAGE,
                "reconciliation requires exact or estimated usage",
            )
        db_now = _db_time(_utc(now))
        prefix = f"reconcile:{operation_id}:"
        reservation = connection.execute(
            select(QuotaReservationModel)
            .where(QuotaReservationModel.id == reservation_id)
            .with_for_update()
        ).mappings().first()
        if reservation is None:
            raise QuotaDomainError(
                QuotaErrorCode.RESERVATION_NOT_ACTIVE,
                f"Reservation {reservation_id!r} does not exist",
            )
        existing_rows = connection.execute(
            select(QuotaLedgerEntryModel)
            .where(
                QuotaLedgerEntryModel.reservation_id == reservation_id,
                QuotaLedgerEntryModel.entry_type == "reconcile",
                QuotaLedgerEntryModel.idempotency_key.like(f"{prefix}%"),
            )
        ).mappings().all()
        if existing_rows:
            metadata = existing_rows[0]["metadata_json"]
            if (
                int(metadata.get("credits_micro", -1)) != credits_micro
                or metadata.get("usage_status") != usage_status
            ):
                raise QuotaDomainError(
                    QuotaErrorCode.SETTLEMENT_CONFLICT,
                    "Usage reconciliation has different usage facts",
                )
            return UsageRecordResult(
                operation_id=operation_id,
                usage_source=metadata.get("usage_source", usage_source),
                credits_micro=credits_micro,
                usage_status=usage_status,
                over_limit=any(
                    bool(row["metadata_json"].get("over_limit", False))
                    for row in existing_rows
                ),
                pricing_key=metadata.get("pricing_key", pricing_key),
                pricing_version=metadata.get("pricing_version", pricing_version),
            )
        pending = connection.execute(
            select(QuotaLedgerEntryModel)
            .where(
                QuotaLedgerEntryModel.reservation_id == reservation_id,
                QuotaLedgerEntryModel.entry_type == "settle",
                QuotaLedgerEntryModel.idempotency_key.like(
                    f"settle:{operation_id}:%"
                ),
            )
        ).mappings().first()
        if pending is None or pending["metadata_json"].get("usage_status") not in {
            "pending",
            "unavailable",
        }:
            raise QuotaDomainError(
                QuotaErrorCode.SETTLEMENT_CONFLICT,
                "Only pending or unavailable usage can be reconciled",
            )

        metadata_base = {
            "operation_id": operation_id,
            "credits_micro": credits_micro,
            "usage_status": usage_status,
            "usage_source": usage_source,
            "pricing_key": pricing_key,
            "pricing_version": pricing_version,
            "reconciled_from": pending["metadata_json"].get("usage_status"),
        }
        bucket_rows = self._reservation_buckets(connection, reservation_id)
        release_amount = min(int(reservation["reserved_micro"]), credits_micro)
        over_limit = False
        if not bucket_rows:
            self._insert_ledger(
                connection,
                reservation_id=reservation_id,
                bucket_id=None,
                entry_type="reconcile",
                amount_micro=credits_micro,
                reserved_delta_micro=-release_amount,
                consumed_delta_micro=credits_micro,
                idempotency_key=f"{prefix}none",
                reason="usage_reconcile",
                metadata={**metadata_base, "over_limit": False},
                created_at=db_now,
            )
        for bucket in bucket_rows:
            bucket_release = min(int(bucket["reserved_micro"]), release_amount)
            new_consumed = int(bucket["consumed_micro"]) + credits_micro
            limit = bucket["limit_micro"]
            extra_capacity = self._additional_capacity_for_bucket(connection, bucket, db_now)
            bucket_over_limit = (
                limit is not None
                and new_consumed
                > int(limit)
                + extra_capacity
                + self._reservation_overdraft(
                    reservation, bucket["owner_type"], bucket["owner_id"]
                )
            )
            over_limit = over_limit or bucket_over_limit
            self._insert_ledger(
                connection,
                reservation_id=reservation_id,
                bucket_id=bucket["id"],
                entry_type="reconcile",
                amount_micro=credits_micro,
                reserved_delta_micro=-bucket_release,
                consumed_delta_micro=credits_micro,
                idempotency_key=f"{prefix}{bucket['id']}",
                reason="usage_reconcile",
                metadata={**metadata_base, "over_limit": bucket_over_limit},
                created_at=db_now,
            )
            connection.execute(
                update(QuotaBucketModel)
                .where(QuotaBucketModel.id == bucket["id"])
                .values(
                    consumed_micro=new_consumed,
                    reserved_micro=max(0, int(bucket["reserved_micro"]) - bucket_release),
                    over_limit=bucket_over_limit,
                    version=int(bucket["version"]) + 1,
                    updated_at=db_now,
                )
            )
        terminal_reservation = reservation["status"] in {
            "released",
            "expired",
            "settled",
        }
        connection.execute(
            update(QuotaReservationModel)
            .where(QuotaReservationModel.id == reservation_id)
            .values(
                reserved_micro=max(0, int(reservation["reserved_micro"]) - release_amount),
                settled_micro=int(reservation["settled_micro"]) + credits_micro,
                status="settled" if terminal_reservation else "settling",
                over_limit=over_limit or bool(reservation["over_limit"]),
                updated_at=db_now,
            )
        )
        return UsageRecordResult(
            operation_id=operation_id,
            usage_source=usage_source,
            credits_micro=credits_micro,
            usage_status=usage_status,
            over_limit=over_limit,
            pricing_key=pricing_key,
            pricing_version=pricing_version,
        )

    def begin_reservation(
        self,
        reservation_id: str,
        *,
        now: datetime | None = None,
    ) -> bool:
        """Move an admitted reservation into the running lease state."""
        at = _utc(now)
        with _GLOBAL_QUOTA_LOCK:
            with self._engine.begin() as connection:
                reservation = connection.execute(
                    select(QuotaReservationModel)
                    .where(QuotaReservationModel.id == reservation_id)
                    .with_for_update()
                ).mappings().first()
                if reservation is None or reservation["status"] not in _ACTIVE_RESERVATION_STATUSES:
                    return False
                if _aware(reservation["lease_expires_at"]) <= at:
                    self.finish_in_transaction(
                        connection,
                        FinishTurn(
                            reservation_id=reservation_id,
                            turn_id=reservation["turn_id"],
                            idempotency_key=f"begin-expiry:{reservation_id}",
                        ),
                        now=at,
                        terminal_status="expired",
                    )
                    return False
                connection.execute(
                    update(QuotaReservationModel)
                    .where(QuotaReservationModel.id == reservation_id)
                    .values(
                        status="running",
                        lease_expires_at=_db_time(at + timedelta(seconds=self.lease_seconds)),
                        last_heartbeat_at=_db_time(at),
                        updated_at=_db_time(at),
                    )
                )
                return True

    def finish_turn(
        self,
        command: FinishTurn,
        *,
        now: datetime | None = None,
    ) -> TurnFinishResult:
        with _GLOBAL_QUOTA_LOCK:
            with self._engine.begin() as connection:
                result = self.finish_in_transaction(connection, command, now=now)
        self.notify_reservation(command.reservation_id)
        return result

    def finish_in_transaction(
        self,
        connection: Connection,
        command: FinishTurn,
        *,
        now: datetime | None = None,
        terminal_status: str | None = None,
    ) -> TurnFinishResult:
        db_now = _db_time(_utc(now))
        prefix = f"finish:{command.idempotency_key}:"
        reservation = connection.execute(
            select(QuotaReservationModel)
            .where(QuotaReservationModel.id == command.reservation_id)
            .with_for_update()
        ).mappings().first()
        if reservation is None:
            raise QuotaDomainError(
                QuotaErrorCode.RESERVATION_NOT_ACTIVE,
                f"Reservation {command.reservation_id!r} does not exist",
            )
        existing = connection.execute(
            select(QuotaLedgerEntryModel)
            .where(
                QuotaLedgerEntryModel.reservation_id == command.reservation_id,
                QuotaLedgerEntryModel.entry_type == "release",
                QuotaLedgerEntryModel.idempotency_key.like(f"{prefix}%"),
            )
            .limit(1)
        ).mappings().first()
        if existing is not None:
            metadata = existing["metadata_json"]
            return TurnFinishResult(
                reservation_id=command.reservation_id,
                status=metadata["status"],
                released_micro=int(metadata["released_micro"]),
            )
        if reservation["turn_id"] != command.turn_id:
            raise QuotaDomainError(
                QuotaErrorCode.RESERVATION_CONFLICT,
                "Finish command does not match the reservation Turn",
            )
        if reservation["status"] in {"settled", "released", "expired"}:
            return TurnFinishResult(
                reservation_id=command.reservation_id,
                status=reservation["status"],
                released_micro=0,
            )
        released = int(reservation["reserved_micro"])
        bucket_rows = self._reservation_buckets(connection, command.reservation_id)
        status = terminal_status or ("settled" if int(reservation["settled_micro"]) > 0 else "released")
        if not bucket_rows:
            self._insert_ledger(
                connection,
                reservation_id=command.reservation_id,
                bucket_id=None,
                entry_type="release",
                amount_micro=-released,
                reserved_delta_micro=-released,
                consumed_delta_micro=0,
                idempotency_key=f"{prefix}none",
                reason="turn_finished" if status == "settled" else "reservation_expired",
                metadata={"status": status, "released_micro": released},
                created_at=db_now,
            )
        for bucket in bucket_rows:
            bucket_release = min(int(bucket["reserved_micro"]), released)
            self._insert_ledger(
                connection,
                reservation_id=command.reservation_id,
                bucket_id=bucket["id"],
                entry_type="release",
                amount_micro=-bucket_release,
                reserved_delta_micro=-bucket_release,
                consumed_delta_micro=0,
                idempotency_key=f"{prefix}{bucket['id']}",
                reason="turn_finished" if status == "settled" else "reservation_expired",
                metadata={"status": status, "released_micro": released},
                created_at=db_now,
            )
            connection.execute(
                update(QuotaBucketModel)
                .where(QuotaBucketModel.id == bucket["id"])
                .values(
                    reserved_micro=max(0, int(bucket["reserved_micro"]) - bucket_release),
                    version=int(bucket["version"]) + 1,
                    updated_at=db_now,
                )
            )
        if reservation["status"] in _ACTIVE_RESERVATION_STATUSES:
            self._release_concurrency(
                connection,
                user_id=reservation["user_id"],
                concurrency_units=int(reservation["concurrency_units"]),
                now=_utc(now),
            )
        connection.execute(
            update(QuotaReservationModel)
            .where(QuotaReservationModel.id == command.reservation_id)
            .values(
                reserved_micro=0,
                status=status,
                lease_expires_at=db_now,
                updated_at=db_now,
            )
        )
        return TurnFinishResult(
            reservation_id=command.reservation_id,
            status=status,
            released_micro=released,
        )

    def release_reservation(
        self,
        reservation_id: str,
        *,
        turn_id: str,
        idempotency_key: str,
        now: datetime | None = None,
    ) -> TurnFinishResult:
        return self.finish_turn(
            FinishTurn(
                reservation_id=reservation_id,
                turn_id=turn_id,
                idempotency_key=idempotency_key,
            ),
            now=now,
        )

    def expire_reservations(self, *, now: datetime | None = None) -> int:
        at = _utc(now)
        count = 0
        with _GLOBAL_QUOTA_LOCK:
            with self._engine.begin() as connection:
                stale = connection.execute(
                    select(QuotaReservationModel.id, QuotaReservationModel.turn_id)
                    .where(
                        QuotaReservationModel.status.in_(_ACTIVE_RESERVATION_STATUSES),
                        QuotaReservationModel.lease_expires_at <= _db_time(at),
                    )
                    .with_for_update()
                ).mappings().all()
                for row in stale:
                    self.finish_in_transaction(
                        connection,
                        FinishTurn(
                            reservation_id=row["id"],
                            turn_id=row["turn_id"],
                            idempotency_key=f"expiry:{row['id']}",
                        ),
                        now=at,
                        terminal_status="expired",
                    )
                    count += 1
        if count:
            self._notify_snapshot()
        return count

    def expire_grants(self, *, now: datetime | None = None) -> int:
        """Close expired developer grants from the same production reaper."""
        from server.quota.management import QuotaManagementService

        count = QuotaManagementService(self._engine).expire_grants(
            now=_utc(now)
        )
        if count:
            self._notify_snapshot()
        return count

    def heartbeat(
        self,
        reservation_id: str,
        *,
        now: datetime | None = None,
    ) -> bool:
        at = _utc(now)
        with _GLOBAL_QUOTA_LOCK:
            with self._engine.begin() as connection:
                reservation = connection.execute(
                    select(QuotaReservationModel)
                    .where(QuotaReservationModel.id == reservation_id)
                    .with_for_update()
                ).mappings().first()
                if reservation is None or reservation["status"] not in _ACTIVE_RESERVATION_STATUSES:
                    return False
                if _aware(reservation["lease_expires_at"]) <= at:
                    self.finish_in_transaction(
                        connection,
                        FinishTurn(
                            reservation_id=reservation_id,
                            turn_id=reservation["turn_id"],
                            idempotency_key=f"heartbeat-expiry:{reservation_id}",
                        ),
                        now=at,
                        terminal_status="expired",
                    )
                    return False
                connection.execute(
                    update(QuotaReservationModel)
                    .where(QuotaReservationModel.id == reservation_id)
                    .values(
                        lease_expires_at=_db_time(at + timedelta(seconds=self.lease_seconds)),
                        last_heartbeat_at=_db_time(at),
                        updated_at=_db_time(at),
                    )
                )
                return True

    def snapshot(
        self,
        *,
        user_id: str,
        workspace_id: str | None = None,
        classroom_ids: Sequence[str] = (),
        now: datetime | None = None,
    ) -> dict[str, Any]:
        at = _utc(now)
        with self._engine.connect() as connection:
            classroom_ids = self._classrooms_in_workspace(
                connection,
                classroom_ids=classroom_ids,
                workspace_id=workspace_id,
            )
            owners = [("user", user_id)]
            if workspace_id is not None:
                owners.append(("workspace", workspace_id))
            owners.extend(
                ("classroom", classroom_id)
                for classroom_id in sorted(set(classroom_ids))
            )
            owner_filter = or_(
                *(
                    and_(
                        QuotaBucketModel.owner_type == owner_type,
                        QuotaBucketModel.owner_id == owner_id,
                    )
                    for owner_type, owner_id in owners
                )
            )
            rows = connection.execute(
                select(QuotaBucketModel)
                .where(
                    owner_filter,
                    QuotaBucketModel.period_start <= _db_time(at),
                    QuotaBucketModel.period_end > _db_time(at),
                )
                .order_by(QuotaBucketModel.owner_type, QuotaBucketModel.bucket_type)
            ).mappings().all()
            bucket_rows: dict[tuple[str, str, datetime, datetime], dict[str, Any]] = {
                (
                    row["owner_type"],
                    row["owner_id"],
                    row["period_start"],
                    row["period_end"],
                ): dict(row)
                for row in rows
            }
            virtual_periods = connection.execute(
                select(
                    QuotaGrantModel.owner_type,
                    QuotaGrantModel.owner_id,
                    QuotaGrantModel.bucket_type,
                    QuotaGrantModel.period_start,
                    QuotaGrantModel.period_end,
                )
                .where(
                    or_(
                        *(
                            and_(
                                QuotaGrantModel.owner_type == owner_type,
                                QuotaGrantModel.owner_id == owner_id,
                            )
                            for owner_type, owner_id in owners
                        )
                    ),
                    QuotaGrantModel.status == "active",
                    QuotaGrantModel.effective_from <= _db_time(at),
                    (QuotaGrantModel.expires_at.is_(None))
                    | (QuotaGrantModel.expires_at > _db_time(at)),
                    QuotaGrantModel.period_start <= _db_time(at),
                    QuotaGrantModel.period_end > _db_time(at),
                )
                .distinct()
            ).mappings().all()
            adjustment_periods = connection.execute(
                select(
                    QuotaAdjustmentModel.owner_type,
                    QuotaAdjustmentModel.owner_id,
                    QuotaAdjustmentModel.bucket_type,
                    QuotaAdjustmentModel.period_start,
                    QuotaAdjustmentModel.period_end,
                )
                .where(
                    or_(
                        *(
                            and_(
                                QuotaAdjustmentModel.owner_type == owner_type,
                                QuotaAdjustmentModel.owner_id == owner_id,
                            )
                            for owner_type, owner_id in owners
                        )
                    ),
                    QuotaAdjustmentModel.period_start <= _db_time(at),
                    QuotaAdjustmentModel.period_end > _db_time(at),
                )
                .distinct()
            ).mappings().all()
            for period in [*virtual_periods, *adjustment_periods]:
                key = (
                    period["owner_type"],
                    period["owner_id"],
                    period["period_start"],
                    period["period_end"],
                )
                bucket_rows.setdefault(
                    key,
                    {
                        "owner_type": period["owner_type"],
                        "owner_id": period["owner_id"],
                        "bucket_type": period["bucket_type"],
                        "period_start": period["period_start"],
                        "period_end": period["period_end"],
                        # A grant/adjustment can exist before the first
                        # admission creates a policy-backed Bucket.  A zero
                        # base capacity keeps this read-only projection
                        # explicit while preserving the additional capacity.
                        "limit_micro": 0,
                        "consumed_micro": 0,
                        "reserved_micro": 0,
                        "over_limit": False,
                    },
                )
            rendered_rows = []
            for row in sorted(
                bucket_rows.values(),
                key=lambda item: (
                    item["owner_type"],
                    item["bucket_type"],
                    item["period_start"],
                ),
            ):
                grant_micro, adjustment_micro = self._capacity_components(connection, row, at)
                policy_id = row.get("policy_id")
                max_overdraft_micro = 0
                if policy_id is not None:
                    max_overdraft_micro = int(
                        connection.execute(
                            select(func.coalesce(QuotaPolicyModel.max_overdraft_micro, 0)).where(
                                QuotaPolicyModel.id == policy_id
                            )
                        ).scalar_one()
                        or 0
                    )
                limit_micro = row["limit_micro"]
                consumed_micro = int(row["consumed_micro"])
                current_over_limit = (
                    limit_micro is not None
                    and consumed_micro
                    > int(limit_micro)
                    + grant_micro
                    + adjustment_micro
                    + max_overdraft_micro
                )
                rendered_rows.append(
                    {
                        "owner_type": row["owner_type"],
                        "owner_id": row["owner_id"],
                        "bucket_type": row["bucket_type"],
                        "limit_micro": row["limit_micro"],
                        "grant_micro": grant_micro,
                        "adjustment_micro": adjustment_micro,
                        "consumed_micro": row["consumed_micro"],
                        "reserved_micro": row["reserved_micro"],
                        "remaining_micro": self._available(
                            row,
                            max_overdraft_micro,
                            grant_micro + adjustment_micro,
                        ),
                        "reset_at": _aware(row["period_end"]).isoformat(),
                        "over_limit": current_over_limit,
                    }
                )
        return {
            "user_id": user_id,
            "workspace_id": workspace_id,
            "buckets": rendered_rows,
        }

    def close(self) -> None:
        if self._owns_engine:
            self._engine.dispose()

    def verify_schema(self) -> None:
        """Fail startup when the quota-management migrations were not applied."""
        with self._engine.connect() as connection:
            for model in (
                QuotaPolicyModel,
                PolicyBindingModel,
                QuotaBucketModel,
                QuotaConcurrencyLockModel,
                QuotaReservationModel,
                QuotaLedgerEntryModel,
                QuotaGrantModel,
                QuotaAdjustmentModel,
                QuotaCreditOperationModel,
                QuotaRoleCreditOperationModel,
                QuotaCreditScopeLockModel,
                QuotaDailyRollupModel,
                QuotaProviderBillingModel,
                QuotaUsageArchiveBatchModel,
                QuotaAlertModel,
            ):
                primary_key = next(iter(model.__table__.primary_key.columns))
                connection.execute(select(primary_key).limit(1)).first()
            # A legacy database can still have the quota-management tables but
            # miss the daily/weekly policy column introduced by the period
            # migration. Probe the fields used by both admission and policy
            # explanation so readiness fails before requests reach the route.
            connection.execute(
                select(
                    QuotaPolicyModel.daily_limit_micro,
                    QuotaPolicyModel.weekly_limit_micro,
                ).limit(1)
            ).first()
            # The scope-lock migration also adds the timestamps used to make
            # Gift/Reset requests replay-safe.  Probe those columns so a
            # partially upgraded production process fails readiness early.
            connection.execute(
                select(
                    QuotaCreditOperationModel.effective_from,
                    QuotaCreditOperationModel.expires_at,
                ).limit(1)
            ).first()
            # Feature metering depends on additive pricing and immutable usage
            # columns. Probe them before serving requests so an unapplied
            # migration cannot fail only after a paid tool call completes.
            connection.execute(
                select(
                    PricingRuleModel.visual_input_credits_micro_per_million_tokens,
                    PricingRuleModel.image_unit_credits_micro,
                    PricingRuleModel.search_call_credits_micro,
                    PricingRuleModel.link_page_credits_micro,
                ).limit(1)
            ).first()
            connection.execute(
                select(
                    UsageEventModel.visual_input_tokens,
                    UsageEventModel.image_units,
                    UsageEventModel.search_calls,
                    UsageEventModel.link_pages,
                ).limit(1)
            ).first()

    @staticmethod
    def _find_existing_reservation(
        connection: Connection, command: AdmitTurn
    ) -> dict[str, Any] | None:
        by_key = connection.execute(
            select(QuotaReservationModel)
            .where(QuotaReservationModel.idempotency_key == command.idempotency_key)
            .with_for_update()
        ).mappings().first()
        if by_key is not None:
            return dict(by_key)
        by_turn = connection.execute(
            select(QuotaReservationModel)
            .where(QuotaReservationModel.turn_id == command.turn_id)
            .with_for_update()
        ).mappings().first()
        return dict(by_turn) if by_turn is not None else None

    @staticmethod
    def _admission_result(row: dict[str, Any], *, duplicate: bool) -> TurnAdmissionResult:
        return TurnAdmissionResult(
            allowed=True,
            reservation_id=row["id"],
            reserved_micro=int(row["reserved_micro"]),
            policy_id=row["policy_id"],
            policy_version=row["policy_version"],
            duplicate=duplicate,
        )

    @staticmethod
    def _reservation_overdraft(
        reservation: dict[str, Any], owner_type: str, owner_id: str | None = None
    ) -> int:
        snapshot = reservation.get("policy_snapshot_json") or {}
        overdrafts = snapshot.get("overdrafts") or {}
        if owner_type == "classroom" and owner_id is not None:
            return int(
                overdrafts.get(
                    f"classroom:{owner_id}",
                    reservation["max_overdraft_micro"],
                )
            )
        return int(overdrafts.get(owner_type, reservation["max_overdraft_micro"]))

    @staticmethod
    def _has_active_classroom_capacity(
        connection: Connection, *, classroom_id: str, at: datetime
    ) -> bool:
        timestamp = _db_time(at)
        grant_exists = connection.execute(
            select(QuotaGrantModel.id)
            .where(
                QuotaGrantModel.owner_type == "classroom",
                QuotaGrantModel.owner_id == classroom_id,
                QuotaGrantModel.status == "active",
                QuotaGrantModel.effective_from <= timestamp,
                (QuotaGrantModel.expires_at.is_(None))
                | (QuotaGrantModel.expires_at > timestamp),
                QuotaGrantModel.period_start <= timestamp,
                QuotaGrantModel.period_end > timestamp,
            )
            .limit(1)
        ).first()
        if grant_exists is not None:
            return True
        adjustment_exists = connection.execute(
            select(QuotaAdjustmentModel.id)
            .where(
                QuotaAdjustmentModel.owner_type == "classroom",
                QuotaAdjustmentModel.owner_id == classroom_id,
                QuotaAdjustmentModel.period_start <= timestamp,
                QuotaAdjustmentModel.period_end > timestamp,
            )
            .limit(1)
        ).first()
        return adjustment_exists is not None

    def _effective_binding(
        self,
        connection: Connection,
        command: AdmitTurn,
        *,
        role_codes: Sequence[str],
        classroom_ids: Sequence[str],
        at: datetime,
        subject_types: set[str] | None = None,
        optional: bool = False,
    ) -> PolicyBinding:
        policy_rows = connection.execute(select(QuotaPolicyModel)).mappings().all()
        policies = {row["id"]: row for row in policy_rows}
        bindings: list[PolicyBinding] = []
        for row in connection.execute(select(PolicyBindingModel)).mappings().all():
            if subject_types is not None and row["subject_type"] not in subject_types:
                continue
            policy_row = policies.get(row["policy_id"])
            if policy_row is None or row["status"] != "active" or policy_row["status"] != "active":
                continue
            effective_from = _aware(row["effective_from"])
            effective_until = _aware(row["effective_until"])
            if effective_from is None or not (effective_from <= at and (effective_until is None or at < effective_until)):
                continue
            policy_from = _aware(policy_row["effective_from"])
            policy_until = _aware(policy_row["effective_until"])
            if policy_from is None or not (policy_from <= at and (policy_until is None or at < policy_until)):
                continue
            bindings.append(
                PolicyBinding(
                    subject_type=row["subject_type"],
                    subject_id=row["subject_id"],
                    policy=QuotaPolicy(
                        policy_id=policy_row["id"],
                        code=policy_row["code"],
                        version=policy_row["version"],
                        request_limit_micro=policy_row["request_limit_micro"],
                        daily_limit_micro=policy_row["daily_limit_micro"],
                        weekly_limit_micro=policy_row["weekly_limit_micro"],
                        concurrency_limit=policy_row["concurrency_limit"],
                        max_overdraft_micro=policy_row["max_overdraft_micro"],
                        allowed_model_profiles=tuple(policy_row["allowed_model_profiles"] or ()),
                        unlimited=bool(policy_row["unlimited"]),
                    ),
                    priority=row["priority"],
                    effective_from=effective_from,
                    effective_until=effective_until,
                )
            )
        try:
            return resolve_effective_policy(
                bindings,
                user_id=command.user_id,
                workspace_id=command.workspace_id,
                role_codes=role_codes,
                classroom_ids=classroom_ids,
                at=at,
            )
        except QuotaDomainError as error:
            if optional and error.code is QuotaErrorCode.POLICY_NOT_FOUND:
                return None
            raise self._rejection(error.code, str(error)) from error

    @staticmethod
    def _periods(policy: QuotaPolicy, at: datetime):
        if policy.unlimited:
            return []
        periods = []
        if policy.daily_limit_micro is not None:
            start = at.replace(hour=0, minute=0, second=0, microsecond=0)
            periods.append(("daily", start, start + timedelta(days=1), policy.daily_limit_micro))
        if policy.weekly_limit_micro is not None:
            start = (at - timedelta(days=at.weekday())).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            end = start + timedelta(days=7)
            periods.append(("weekly", start, end, policy.weekly_limit_micro))
        return periods

    @staticmethod
    def _estimate_micro(
        connection: Connection,
        command: AdmitTurn,
        at: datetime,
    ) -> int:
        """Conservatively price the input estimate plus the output reserve.

        A configured Gateway admission must never become a free request just
        because its pricing rule is missing.  Callers without a pricing key
        (the pure-domain tests and non-model administrative commands) retain
        an explicit zero estimate.
        """
        if command.pricing_key is None:
            return 0
        rows = connection.execute(
            select(PricingRuleModel)
            .where(
                PricingRuleModel.pricing_key == command.pricing_key,
                PricingRuleModel.status == "active",
                PricingRuleModel.effective_from <= _db_time(at),
                (PricingRuleModel.effective_until.is_(None))
                | (PricingRuleModel.effective_until > _db_time(at)),
            )
            .order_by(PricingRuleModel.effective_from.desc())
        ).mappings().all()
        if len(rows) != 1:
            raise QuotaDomainError(
                QuotaErrorCode.ADMISSION_DENIED,
                f"No unique active pricing rule exists for {command.pricing_key!r}",
            )
        row = rows[0]
        input_tokens = int(command.estimated_input_tokens or 0)
        output_tokens = int(command.estimated_output_tokens)
        output_rate = max(
            int(row["output_credits_micro_per_million_tokens"]),
            int(row["reasoning_output_credits_micro_per_million_tokens"] or 0),
        )
        numerator = (
            input_tokens
            * int(row["ordinary_input_credits_micro_per_million_tokens"])
            + output_tokens * output_rate
        )
        return (numerator + 1_000_000 - 1) // 1_000_000

    @staticmethod
    def _estimate_feature_micro(
        connection: Connection,
        *,
        pricing_key: str,
        feature_usage: BillableFeatureUsage,
        at: datetime,
    ) -> int:
        rows = connection.execute(
            select(PricingRuleModel)
            .where(
                PricingRuleModel.pricing_key == pricing_key,
                PricingRuleModel.status == "active",
                PricingRuleModel.effective_from <= _db_time(at),
                (PricingRuleModel.effective_until.is_(None))
                | (PricingRuleModel.effective_until > _db_time(at)),
            )
            .order_by(PricingRuleModel.effective_from.desc())
        ).mappings().all()
        if len(rows) != 1:
            raise QuotaDomainError(
                QuotaErrorCode.ADMISSION_DENIED,
                f"No unique active pricing rule exists for {pricing_key!r}",
            )
        row = rows[0]
        required_rates = (
            (feature_usage.visual_input_tokens, "visual_input_credits_micro_per_million_tokens"),
            (feature_usage.image_units, "image_unit_credits_micro"),
            (feature_usage.search_calls, "search_call_credits_micro"),
            (feature_usage.link_pages, "link_page_credits_micro"),
        )
        for units, field in required_rates:
            if units and row[field] is None:
                raise QuotaDomainError(
                    QuotaErrorCode.ADMISSION_DENIED,
                    f"Feature usage has no configured price in {field}",
                )
        token_numerator = feature_usage.visual_input_tokens * int(
            row["visual_input_credits_micro_per_million_tokens"] or 0
        )
        return (
            (token_numerator + 1_000_000 - 1) // 1_000_000
            + feature_usage.image_units * int(row["image_unit_credits_micro"] or 0)
            + feature_usage.search_calls * int(row["search_call_credits_micro"] or 0)
            + feature_usage.link_pages * int(row["link_page_credits_micro"] or 0)
        )

    @staticmethod
    def _available(
        bucket: dict[str, Any], overdraft_micro: int, additional_capacity_micro: int = 0
    ) -> int:
        limit = bucket["limit_micro"]
        if limit is None:
            return 2**63 - 1
        return (
            int(limit)
            + overdraft_micro
            + additional_capacity_micro
            - int(bucket["consumed_micro"])
            - int(bucket["reserved_micro"])
        )

    @staticmethod
    def _capacity_statement(
        model: Any,
        *,
        owner_type: str,
        owner_id: str,
        bucket_type: str,
        period_start: datetime,
        period_end: datetime,
        at: datetime,
    ) -> Any:
        statement = select(func.coalesce(func.sum(model.allocated_micro), 0)).where(
            model.owner_type == owner_type,
            model.owner_id == owner_id,
            model.bucket_type == bucket_type,
            model.period_start == _db_time(period_start),
            model.period_end == _db_time(period_end),
        )
        if model is QuotaGrantModel:
            statement = statement.where(
                model.status == "active",
                model.effective_from <= _db_time(at),
                (model.expires_at.is_(None)) | (model.expires_at > _db_time(at)),
            )
        return statement

    @classmethod
    def _additional_capacity(
        cls,
        connection: Connection,
        *,
        owner_type: str,
        owner_id: str,
        bucket_type: str,
        period_start: datetime,
        period_end: datetime,
        at: datetime,
    ) -> int:
        grant = connection.execute(
            cls._capacity_statement(
                QuotaGrantModel,
                owner_type=owner_type,
                owner_id=owner_id,
                bucket_type=bucket_type,
                period_start=period_start,
                period_end=period_end,
                at=at,
            )
        ).scalar_one()
        adjustment = connection.execute(
            select(func.coalesce(func.sum(QuotaAdjustmentModel.amount_micro), 0)).where(
                QuotaAdjustmentModel.owner_type == owner_type,
                QuotaAdjustmentModel.owner_id == owner_id,
                QuotaAdjustmentModel.bucket_type == bucket_type,
                QuotaAdjustmentModel.period_start == _db_time(period_start),
                QuotaAdjustmentModel.period_end == _db_time(period_end),
            )
        ).scalar_one()
        return int(grant or 0) + int(adjustment or 0)

    @classmethod
    def _additional_capacity_for_bucket(
        cls, connection: Connection, bucket: dict[str, Any], at: datetime
    ) -> int:
        return cls._additional_capacity(
            connection,
            owner_type=bucket["owner_type"],
            owner_id=bucket["owner_id"],
            bucket_type=bucket["bucket_type"],
            period_start=bucket["period_start"],
            period_end=bucket["period_end"],
            at=_aware(at) or _utc(at),
        )

    @classmethod
    def _capacity_components(
        cls, connection: Connection, bucket: dict[str, Any], at: datetime
    ) -> tuple[int, int]:
        grant = connection.execute(
            cls._capacity_statement(
                QuotaGrantModel,
                owner_type=bucket["owner_type"],
                owner_id=bucket["owner_id"],
                bucket_type=bucket["bucket_type"],
                period_start=bucket["period_start"],
                period_end=bucket["period_end"],
                at=at,
            )
        ).scalar_one()
        adjustment = connection.execute(
            select(func.coalesce(func.sum(QuotaAdjustmentModel.amount_micro), 0)).where(
                QuotaAdjustmentModel.owner_type == bucket["owner_type"],
                QuotaAdjustmentModel.owner_id == bucket["owner_id"],
                QuotaAdjustmentModel.bucket_type == bucket["bucket_type"],
                QuotaAdjustmentModel.period_start == _db_time(bucket["period_start"]),
                QuotaAdjustmentModel.period_end == _db_time(bucket["period_end"]),
            )
        ).scalar_one()
        return int(grant or 0), int(adjustment or 0)

    @staticmethod
    def _get_or_create_bucket(
        connection: Connection,
        *,
        owner_type: str,
        owner_id: str,
        bucket_type: str,
        period_start: datetime,
        period_end: datetime,
        policy: QuotaPolicy,
        limit: int | None,
        now: datetime,
    ) -> dict[str, Any]:
        where = and_(
            QuotaBucketModel.owner_type == owner_type,
            QuotaBucketModel.owner_id == owner_id,
            QuotaBucketModel.bucket_type == bucket_type,
            QuotaBucketModel.period_start == _db_time(period_start),
            QuotaBucketModel.period_end == _db_time(period_end),
        )
        values = {
            "id": str(uuid.uuid4()),
            "owner_type": owner_type,
            "owner_id": owner_id,
            "policy_id": policy.policy_id,
            "policy_version": policy.version,
            "bucket_type": bucket_type,
            "period_start": _db_time(period_start),
            "period_end": _db_time(period_end),
            "limit_micro": limit,
            "consumed_micro": 0,
            "reserved_micro": 0,
            "limit_revision": 1,
            "effective_policy_version": policy.version,
            "version": 1,
            "over_limit": False,
            "created_at": _db_time(now),
            "updated_at": _db_time(now),
        }
        if connection.dialect.name == "mysql":
            # Do not probe a missing unique key with FOR UPDATE first: that
            # creates an InnoDB gap lock.  Let the unique scope and atomic
            # upsert arbitrate first creation, then lock the materialized row.
            connection.execute(
                mysql_insert(QuotaBucketModel.__table__)
                .values(**values)
                .on_duplicate_key_update(
                    id=QuotaBucketModel.__table__.c.id
                )
            )
            row = connection.execute(
                select(QuotaBucketModel).where(where).with_for_update()
            ).mappings().one()
        else:
            row = connection.execute(
                select(QuotaBucketModel).where(where).with_for_update()
            ).mappings().first()
            if row is None:
                if connection.dialect.name == "sqlite":
                    # Admission is serialized by _GLOBAL_QUOTA_LOCK for the
                    # embedded/SQLite runtime, so a savepoint is unnecessary
                    # and would make rollback behavior harder to reason about.
                    connection.execute(insert(QuotaBucketModel).values(**values))
                else:
                    try:
                        with connection.begin_nested():
                            connection.execute(insert(QuotaBucketModel).values(**values))
                    except IntegrityError:
                        pass
                row = connection.execute(
                    select(QuotaBucketModel).where(where).with_for_update()
                ).mappings().one()
        if row["limit_micro"] != limit or row["policy_version"] != policy.version:
            connection.execute(
                update(QuotaBucketModel)
                .where(QuotaBucketModel.id == row["id"])
                .values(
                    limit_micro=limit,
                    policy_id=policy.policy_id,
                    policy_version=policy.version,
                    effective_policy_version=policy.version,
                    limit_revision=int(row["limit_revision"]) + 1,
                    over_limit=(
                        limit is not None and int(row["consumed_micro"]) > int(limit)
                    ),
                    version=int(row["version"]) + 1,
                    updated_at=_db_time(now),
                )
            )
            row = connection.execute(
                select(QuotaBucketModel).where(QuotaBucketModel.id == row["id"]).with_for_update()
            ).mappings().one()
        return dict(row)

    @staticmethod
    def _reservation_buckets(connection: Connection, reservation_id: str) -> list[dict[str, Any]]:
        bucket_ids = connection.execute(
            select(QuotaLedgerEntryModel.bucket_id).distinct()
            .where(
                QuotaLedgerEntryModel.reservation_id == reservation_id,
                QuotaLedgerEntryModel.entry_type == "reserve",
                QuotaLedgerEntryModel.bucket_id.is_not(None),
            )
        ).scalars().all()
        if not bucket_ids:
            return []
        rows = connection.execute(
            select(QuotaBucketModel)
            .where(QuotaBucketModel.id.in_(bucket_ids))
            .with_for_update()
        ).mappings().all()
        return [dict(row) for row in rows]

    @staticmethod
    def _insert_ledger(
        connection: Connection,
        *,
        reservation_id: str | None,
        bucket_id: str | None,
        entry_type: str,
        amount_micro: int,
        reserved_delta_micro: int,
        consumed_delta_micro: int,
        idempotency_key: str,
        reason: str,
        metadata: dict[str, Any],
        created_at: datetime,
    ) -> None:
        connection.execute(
            insert(QuotaLedgerEntryModel).values(
                id=str(uuid.uuid4()),
                reservation_id=reservation_id,
                bucket_id=bucket_id,
                grant_id=None,
                entry_type=entry_type,
                amount_micro=amount_micro,
                reserved_delta_micro=reserved_delta_micro,
                consumed_delta_micro=consumed_delta_micro,
                idempotency_key=idempotency_key,
                actor_user_id=None,
                reason=reason,
                metadata_json=metadata,
                created_at=created_at,
            )
        )

    def _expire_stale_in_transaction(self, connection: Connection, at: datetime) -> None:
        rows = connection.execute(
            select(QuotaReservationModel.id, QuotaReservationModel.turn_id)
            .where(
                QuotaReservationModel.status.in_(_ACTIVE_RESERVATION_STATUSES),
                QuotaReservationModel.lease_expires_at <= _db_time(at),
            )
            .with_for_update()
        ).mappings().all()
        for row in rows:
            self.finish_in_transaction(
                connection,
                FinishTurn(
                    reservation_id=row["id"],
                    turn_id=row["turn_id"],
                    idempotency_key=f"expiry:{row['id']}",
                ),
                now=at,
                terminal_status="expired",
            )

    @staticmethod
    def _get_or_create_concurrency_lock(
        connection: Connection,
        *,
        user_id: str,
        now: datetime,
    ) -> dict[str, Any]:
        """Create and lock the per-user counter before changing it.

        The row is deliberately separate from Reservations: counting active
        Reservations after a normal SELECT is not an atomic admission check
        when multiple Web/Worker processes share MySQL.
        """
        values = {
            "user_id": user_id,
            "active_units": 0,
            "version": 1,
            "updated_at": _db_time(now),
        }
        if connection.dialect.name == "mysql":
            # Materialize the row before locking it.  A probe-then-insert
            # sequence takes an InnoDB gap lock when the user is new; twenty
            # first requests can then deadlock while each waits to insert the
            # same unique key.  The atomic upsert lets MySQL arbitrate that
            # unique scope first, after which this transaction locks exactly
            # one existing row.
            connection.execute(
                mysql_insert(QuotaConcurrencyLockModel.__table__)
                .values(**values)
                .on_duplicate_key_update(
                    user_id=QuotaConcurrencyLockModel.__table__.c.user_id
                )
            )
        else:
            row = connection.execute(
                select(QuotaConcurrencyLockModel)
                .where(QuotaConcurrencyLockModel.user_id == user_id)
                .with_for_update()
            ).mappings().first()
            if row is None:
                if connection.dialect.name == "sqlite":
                    connection.execute(
                        insert(QuotaConcurrencyLockModel)
                        .prefix_with("OR IGNORE")
                        .values(**values)
                    )
                else:
                    try:
                        with connection.begin_nested():
                            connection.execute(
                                insert(QuotaConcurrencyLockModel).values(**values)
                            )
                    except IntegrityError:
                        pass
        row = connection.execute(
            select(QuotaConcurrencyLockModel)
            .where(QuotaConcurrencyLockModel.user_id == user_id)
            .with_for_update()
        ).mappings().one()
        return dict(row)

    @classmethod
    def _reserve_concurrency(
        cls,
        connection: Connection,
        *,
        user_id: str,
        concurrency_limit: int,
        now: datetime,
    ) -> None:
        cls._get_or_create_concurrency_lock(connection, user_id=user_id, now=now)
        result = connection.execute(
            update(QuotaConcurrencyLockModel)
            .where(
                QuotaConcurrencyLockModel.user_id == user_id,
                QuotaConcurrencyLockModel.active_units + 1 <= concurrency_limit,
            )
            .values(
                active_units=QuotaConcurrencyLockModel.active_units + 1,
                version=QuotaConcurrencyLockModel.version + 1,
                updated_at=_db_time(now),
            )
        )
        if result.rowcount != 1:
            raise cls._rejection(
                QuotaErrorCode.CONCURRENCY_LIMIT,
                "The user has reached the concurrent Turn limit",
                remaining_micro=0,
                retryable=True,
            )

    @classmethod
    def _release_concurrency(
        cls,
        connection: Connection,
        *,
        user_id: str,
        concurrency_units: int,
        now: datetime,
    ) -> None:
        if concurrency_units <= 0:
            return
        row = connection.execute(
            select(QuotaConcurrencyLockModel)
            .where(QuotaConcurrencyLockModel.user_id == user_id)
            .with_for_update()
        ).mappings().first()
        if row is None:
            return
        connection.execute(
            update(QuotaConcurrencyLockModel)
            .where(QuotaConcurrencyLockModel.user_id == user_id)
            .values(
                active_units=max(0, int(row["active_units"]) - concurrency_units),
                version=int(row["version"]) + 1,
                updated_at=_db_time(now),
            )
        )

    @staticmethod
    def _rejection(
        code: QuotaErrorCode,
        reason: str,
        *,
        remaining_micro: int = 0,
        reset_at: datetime | None = None,
        allowed_model_profiles: Sequence[str] = (),
        retryable: bool = False,
    ) -> QuotaRejectedError:
        return QuotaRejectedError(
            QuotaProblem(
                code=code,
                reason=reason,
                remaining_micro=remaining_micro,
                reset_at=_utc(reset_at) if reset_at is not None else None,
                allowed_model_profiles=tuple(allowed_model_profiles),
                retryable=retryable,
            )
        )
