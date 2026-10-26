"""Fixed Turn billing over the existing quota buckets, reservations and ledger.

No second balance store: admission uses QuotaService's original policies and
grants. Callers own the Turn/Outbox transaction and its terminal row lock.
"""

from datetime import datetime

from sqlalchemy import select, update

from core.model_runtime.snapshot import FixedTurnBillingSnapshot
from server.application.thinking_policy import fixed_turn_thinking_charge
from server.quota.contracts import AdmitTurn
from server.quota.errors import QuotaDomainError, QuotaErrorCode
from server.quota.models import (
    QuotaBucketModel,
    QuotaLedgerEntryModel,
    QuotaReservationModel,
)
from server.quota.service import (
    QuotaService,
    _db_time,
    _utc,
    _ACTIVE_RESERVATION_STATUSES,
)

MICROCREDITS_PER_CREDIT = 1_000_000


def charge_snapshot(level, turn_id: str) -> FixedTurnBillingSnapshot:
    charge = fixed_turn_thinking_charge(level)
    return FixedTurnBillingSnapshot(
        thinking_level=charge.level,
        policy_version=charge.policy_version,
        amount_micro=charge.credits * MICROCREDITS_PER_CREDIT,
        reservation_id=QuotaService.reservation_id_for_turn(turn_id),
    )


def command_for_task(task, *, request_id=None) -> AdmitTurn:
    billing = task.model_snapshot.billing if task.model_snapshot else None
    if billing is None:
        raise ValueError("Turn has no fixed charge snapshot")
    if billing.thinking_level != task.model_snapshot.level:
        raise ValueError("Charge level differs from execution level")
    return AdmitTurn(
        request_id=request_id or task.turn_id,
        user_id=task.context.user_id,
        workspace_id=task.context.workspace_id,
        turn_id=task.turn_id,
        model_profile=task.model_snapshot.profile_name,
        model_role="coordinator",
        estimated_output_tokens=0,
        idempotency_key=f"fixed-turn:{task.turn_id}",
        fixed_charge=billing,
    )


class FixedTurnQuota(QuotaService):
    def admit_in_transaction(self, connection, command, **kwargs):
        if command.fixed_charge is not None:
            if command.fixed_charge.reservation_id != self.reservation_id_for_turn(
                command.turn_id
            ):
                raise ValueError("Charge reservation does not belong to this Turn")
            existing = self._find_existing_reservation(connection, command)
            if existing is not None and (
                existing["user_id"],
                existing["workspace_id"],
                existing["turn_id"],
                existing["idempotency_key"],
            ) != (
                command.user_id,
                command.workspace_id,
                command.turn_id,
                command.idempotency_key,
            ):
                raise QuotaDomainError(
                    QuotaErrorCode.RESERVATION_CONFLICT,
                    "Reservation belongs to another request",
                )
            if existing is not None and existing["policy_snapshot_json"].get(
                "fixed_turn"
            ) != command.fixed_charge.model_dump(mode="json"):
                raise QuotaDomainError(
                    QuotaErrorCode.RESERVATION_CONFLICT, "Frozen Turn charge changed"
                )
            if (
                existing is not None
                and existing["status"] in _ACTIVE_RESERVATION_STATUSES
            ):
                return self._admission_result(existing, duplicate=True)
        return super().admit_in_transaction(connection, command, **kwargs)

    def finish_fixed_in_transaction(
        self,
        connection,
        *,
        turn_id: str,
        status: str,
        has_body: bool,
        now: datetime | None = None,
    ):
        if status not in {"completed", "cancelled", "failed", "interrupted"}:
            return
        reservation = (
            connection.execute(
                select(QuotaReservationModel)
                .where(QuotaReservationModel.turn_id == turn_id)
                .with_for_update()
            )
            .mappings()
            .first()
        )
        if reservation is None or not reservation["policy_snapshot_json"].get(
            "fixed_turn"
        ):
            return
        billing = FixedTurnBillingSnapshot.model_validate(
            reservation["policy_snapshot_json"]["fixed_turn"]
        )
        old_consumed = int(reservation["settled_micro"])
        reserved = int(reservation["reserved_micro"])
        consume = status in {"completed", "cancelled"} and has_body
        # A refunded/closed generation cannot be charged again by a late replay.
        if (
            reservation["status"] not in _ACTIVE_RESERVATION_STATUSES
            and old_consumed == 0
        ):
            return
        target = billing.amount_micro if consume else 0
        delta = target - old_consumed
        if delta == 0 and reserved == 0:
            return
        active_ids = reservation["policy_snapshot_json"]["fixed_bucket_ids"]
        buckets = (
            connection.execute(
                select(QuotaBucketModel)
                .where(QuotaBucketModel.id.in_(active_ids))
                .order_by(QuotaBucketModel.id)
                .with_for_update()
            )
            .mappings()
            .all()
            if active_ids
            else []
        )
        if len(buckets) != len(active_ids):
            raise QuotaDomainError(
                QuotaErrorCode.SETTLEMENT_CONFLICT, "Active quota bucket is missing"
            )
        generation = reservation["policy_snapshot_json"]["fixed_generation"]
        action = "refund" if delta < 0 else "settle" if target else "release"
        at = _db_time(_utc(now))
        for bucket in buckets or [None]:
            bucket_id = bucket["id"] if bucket else None
            if bucket:
                if (
                    int(bucket["reserved_micro"]) < reserved
                    or int(bucket["consumed_micro"]) + delta < 0
                ):
                    raise QuotaDomainError(
                        QuotaErrorCode.SETTLEMENT_CONFLICT,
                        "Quota counters disagree with reservation",
                    )
                connection.execute(
                    update(QuotaBucketModel)
                    .where(QuotaBucketModel.id == bucket_id)
                    .values(
                        reserved_micro=int(bucket["reserved_micro"]) - reserved,
                        consumed_micro=int(bucket["consumed_micro"]) + delta,
                        version=int(bucket["version"]) + 1,
                        updated_at=at,
                    )
                )
            self._insert_ledger(
                connection,
                reservation_id=reservation["id"],
                bucket_id=bucket_id,
                entry_type=action,
                amount_micro=delta if delta else -reserved,
                reserved_delta_micro=-reserved,
                consumed_delta_micro=delta,
                idempotency_key=f"fixed:{reservation['id']}:{generation}:{action}:{bucket_id}",
                reason=f"fixed_turn_{status}",
                metadata={"turn_id": turn_id, **billing.model_dump(mode="json")},
                created_at=at,
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
            .where(QuotaReservationModel.id == reservation["id"])
            .values(
                reserved_micro=0,
                settled_micro=target,
                status="settled" if target else "released",
                updated_at=at,
                lease_expires_at=at,
            )
        )
