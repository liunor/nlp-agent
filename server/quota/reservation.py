"""Pure Reservation lifecycle transitions with replay-safe terminal actions."""

from __future__ import annotations

from datetime import datetime, timedelta

from server.quota.contracts import Reservation
from server.quota.errors import QuotaDomainError, QuotaErrorCode


def begin(reservation: Reservation, *, now: datetime, lease_seconds: int) -> Reservation:
    if reservation.status != "reserved":
        raise QuotaDomainError(
            QuotaErrorCode.RESERVATION_NOT_ACTIVE,
            f"Cannot begin reservation in status {reservation.status!r}",
        )
    if lease_seconds <= 0:
        raise ValueError("lease_seconds must be positive")
    return reservation.model_copy(
        update={
            "status": "running",
            "lease_expires_at": now + timedelta(seconds=lease_seconds),
            "last_heartbeat_at": now,
        }
    )


def renew(reservation: Reservation, *, now: datetime, lease_seconds: int) -> Reservation:
    if reservation.status != "running":
        raise QuotaDomainError(
            QuotaErrorCode.RESERVATION_NOT_ACTIVE,
            f"Cannot renew reservation in status {reservation.status!r}",
        )
    if lease_seconds <= 0:
        raise ValueError("lease_seconds must be positive")
    if reservation.lease_expires_at is not None and now >= reservation.lease_expires_at:
        raise QuotaDomainError(
            QuotaErrorCode.RESERVATION_NOT_ACTIVE,
            "Cannot renew an expired reservation lease",
        )
    return reservation.model_copy(
        update={
            "lease_expires_at": now + timedelta(seconds=lease_seconds),
            "last_heartbeat_at": now,
        }
    )


def expire(reservation: Reservation, *, at: datetime) -> Reservation:
    if reservation.status == "expired":
        return reservation
    if reservation.status in {"settled", "released"}:
        raise QuotaDomainError(
            QuotaErrorCode.RESERVATION_CONFLICT,
            f"Cannot expire terminal reservation in status {reservation.status!r}",
        )
    if reservation.lease_expires_at is None or at < reservation.lease_expires_at:
        raise QuotaDomainError(
            QuotaErrorCode.RESERVATION_NOT_ACTIVE,
            "Reservation lease has not expired",
        )
    return reservation.model_copy(
        update={"status": "expired", "reserved_micro": 0, "settled_micro": 0}
    )


def settle(reservation: Reservation, *, actual_micro: int) -> Reservation:
    if isinstance(actual_micro, bool) or not isinstance(actual_micro, int) or actual_micro < 0:
        raise ValueError("actual_micro must be a non-negative strict integer")
    if reservation.status == "settled":
        if reservation.settled_micro == actual_micro:
            return reservation
        raise QuotaDomainError(
            QuotaErrorCode.RESERVATION_CONFLICT,
            "Settlement replay has a different actual amount",
        )
    if reservation.status in {"released", "expired"}:
        raise QuotaDomainError(
            QuotaErrorCode.RESERVATION_CONFLICT,
            f"Cannot settle reservation in status {reservation.status!r}",
        )
    return reservation.model_copy(
        update={"status": "settled", "reserved_micro": 0, "settled_micro": actual_micro}
    )


def release(reservation: Reservation) -> Reservation:
    if reservation.status == "released":
        return reservation
    if reservation.status in {"settled", "expired"}:
        raise QuotaDomainError(
            QuotaErrorCode.RESERVATION_CONFLICT,
            f"Cannot release reservation in status {reservation.status!r}",
        )
    return reservation.model_copy(
        update={"status": "released", "reserved_micro": 0, "settled_micro": 0}
    )
