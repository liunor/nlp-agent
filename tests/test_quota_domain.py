"""Behavioral tests for Phase 0 quota contracts and state rules."""

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from server.quota.contracts import (
    AdmitTurn,
    FinishTurn,
    PolicyBinding,
    QuotaProblem,
    QuotaGrant,
    QuotaPolicy,
    UsageSnapshotQuery,
    Reservation,
    calculate_balance,
)
from server.quota.errors import QuotaErrorCode, QuotaDomainError
from server.quota.policy import resolve_effective_policy
from server.quota.reservation import begin, expire, release, renew, settle


UTC = timezone.utc
AT_START = datetime(2026, 1, 1, tzinfo=UTC)


def _policy(code: str, version: str = "1") -> QuotaPolicy:
    return QuotaPolicy(
        policy_id=f"policy-{code}",
        code=code,
        version=version,
        request_limit_micro=1_000,
        daily_limit_micro=10_000,
        weekly_limit_micro=100_000,
        concurrency_limit=2,
        max_overdraft_micro=0,
        allowed_model_profiles=("economy",),
    )


def _binding(
    subject_type: str,
    subject_id: str,
    policy: QuotaPolicy,
    *,
    priority: int = 0,
) -> PolicyBinding:
    return PolicyBinding(
        subject_type=subject_type,
        subject_id=subject_id,
        policy=policy,
        priority=priority,
        effective_from=AT_START,
    )


def test_user_policy_wins_over_workspace_and_role_without_summing_limits():
    """Would fail if multiple role/user policies were added together."""
    default = _policy("default")
    role = _policy("teacher")
    workspace = _policy("workspace")
    user = _policy("user")
    bindings = [
        _binding("default", "*", default),
        _binding("role", "teacher", role, priority=20),
        _binding("workspace", "workspace-1", workspace, priority=50),
        _binding("user", "user-1", user, priority=1),
    ]

    selected = resolve_effective_policy(
        bindings,
        user_id="user-1",
        workspace_id="workspace-1",
        role_codes=("student", "teacher"),
        at=AT_START,
    )

    assert selected.policy.policy_id == "policy-user"
    assert selected.policy.daily_limit_micro == 10_000


def test_highest_priority_role_wins_and_role_limits_are_not_summed():
    """Would fail if student and teacher role quotas were aggregated."""
    selected = resolve_effective_policy(
        [
            _binding("default", "*", _policy("default")),
            _binding("role", "student", _policy("student"), priority=10),
            _binding("role", "teacher", _policy("teacher"), priority=20),
        ],
        user_id="user-1",
        workspace_id="workspace-1",
        role_codes=("student", "teacher"),
        at=AT_START,
    )

    assert selected.policy.code == "teacher"


def test_ambiguous_equal_priority_policy_fails_closed():
    """Would fail if policy selection depended on input order."""
    with pytest.raises(QuotaDomainError) as error:
        resolve_effective_policy(
            [
                _binding("default", "*", _policy("default")),
                _binding("role", "student", _policy("student"), priority=10),
                _binding("role", "teacher", _policy("teacher"), priority=10),
            ],
            user_id="user-1",
            workspace_id="workspace-1",
            role_codes=("student", "teacher"),
            at=AT_START,
        )

    assert error.value.code is QuotaErrorCode.POLICY_AMBIGUOUS


def test_balance_excludes_expired_grants_and_tracks_reserved_separately():
    """Would fail if expiration or reserved credits were treated as consumed."""
    balance = calculate_balance(
        [
            QuotaGrant(
                grant_id="grant-active",
                owner_type="user",
                owner_id="user-1",
                source_type="grant",
                allocated_micro=1_000,
                consumed_micro=300,
                reserved_micro=100,
                effective_from=AT_START,
                expires_at=datetime(2026, 2, 1, tzinfo=UTC),
                created_by="developer-1",
                idempotency_key="grant-active-1",
            ),
            QuotaGrant(
                grant_id="grant-expired",
                owner_type="user",
                owner_id="user-1",
                source_type="role",
                allocated_micro=5_000,
                effective_from=datetime(2025, 12, 1, tzinfo=UTC),
                expires_at=datetime(2025, 12, 31, tzinfo=UTC),
                created_by="system",
                idempotency_key="grant-expired-1",
            ),
        ],
        adjustment_micro=-50,
        at=AT_START,
    )

    assert balance.allocated_micro == 1_000
    assert balance.consumed_micro == 300
    assert balance.reserved_micro == 100
    assert balance.adjustment_micro == -50
    assert balance.available_micro == 550


def test_grant_rejects_consumed_and_reserved_above_allocation():
    """Would fail if a Grant could create impossible negative availability."""
    with pytest.raises(ValidationError, match=r"consumed_micro \+ reserved_micro"):
        QuotaGrant(
            grant_id="grant-1",
            owner_type="user",
            owner_id="user-1",
            source_type="grant",
            allocated_micro=100,
            consumed_micro=80,
            reserved_micro=30,
            effective_from=AT_START,
            created_by="developer-1",
            idempotency_key="grant-1-key",
        )


def test_settlement_releases_all_reserved_and_records_actual_usage():
    """Would fail if settlement released only part of the reservation."""
    reservation = Reservation(
        reservation_id="reservation-1",
        reserved_micro=100,
        max_overdraft_micro=0,
        status="reserved",
    )

    settled = settle(reservation, actual_micro=30)

    assert settled.status == "settled"
    assert settled.reserved_micro == 0
    assert settled.settled_micro == 30


def test_reservation_terminal_actions_are_idempotent_but_conflicts_fail():
    """Would fail if replayed settlement mutated balances or hid conflicts."""
    reservation = Reservation(
        reservation_id="reservation-1",
        reserved_micro=100,
        max_overdraft_micro=0,
        status="reserved",
    )
    settled = settle(reservation, actual_micro=30)

    assert settle(settled, actual_micro=30) == settled
    with pytest.raises(QuotaDomainError) as error:
        settle(settled, actual_micro=40)
    assert error.value.code is QuotaErrorCode.RESERVATION_CONFLICT


def test_release_is_idempotent_and_cannot_follow_settlement():
    """Would fail if release could erase an already settled usage."""
    reservation = Reservation(
        reservation_id="reservation-1",
        reserved_micro=100,
        max_overdraft_micro=0,
        status="reserved",
    )
    released = release(reservation)

    assert release(released) == released
    with pytest.raises(QuotaDomainError) as error:
        release(settle(reservation, actual_micro=30))
    assert error.value.code is QuotaErrorCode.RESERVATION_CONFLICT


def test_reservation_lease_can_start_renew_and_expire():
    """Would fail if crashed workers could retain a concurrency slot forever."""
    reservation = Reservation(
        reservation_id="reservation-1",
        reserved_micro=100,
        max_overdraft_micro=0,
        status="reserved",
    )

    running = begin(reservation, now=AT_START, lease_seconds=30)
    renewed_at = AT_START + timedelta(seconds=10)
    renewed = renew(running, now=renewed_at, lease_seconds=30)
    expired = expire(renewed, at=renewed_at + timedelta(seconds=30))

    assert running.status == "running"
    assert running.lease_expires_at == AT_START + timedelta(seconds=30)
    assert renewed.lease_expires_at == renewed_at + timedelta(seconds=30)
    assert expired.status == "expired"
    assert expired.reserved_micro == 0


def test_phase0_commands_keep_admission_and_snapshot_inputs_typed():
    """Would fail if callers could omit identity or pass estimates as floats."""
    admission = AdmitTurn(
        request_id="request-1",
        user_id="user-1",
        workspace_id="workspace-1",
        turn_id="turn-1",
        model_profile="economy",
        model_role="coordinator",
        estimated_input_tokens=None,
        estimated_output_tokens=2_000,
        idempotency_key="turn-1-admission",
    )
    snapshot = UsageSnapshotQuery(
        user_id="user-1",
        workspace_id="workspace-1",
        window="week",
        at=AT_START,
    )
    finish = FinishTurn(
        reservation_id="reservation-1",
        turn_id="turn-1",
        idempotency_key="turn-1-finish",
    )

    assert admission.model_role == "coordinator"
    assert admission.estimated_input_tokens is None
    assert snapshot.window == "week"
    assert finish.reservation_id == "reservation-1"

    with pytest.raises(ValidationError):
        AdmitTurn(
            request_id="request-1",
            user_id="user-1",
            workspace_id="workspace-1",
            turn_id="turn-1",
            model_profile="economy",
            model_role="coordinator",
            estimated_input_tokens=1.5,
            estimated_output_tokens=2_000,
            idempotency_key="turn-1-admission-2",
        )


def test_quota_problem_preserves_machine_code_and_reset_metadata():
    """Would fail if HTTP adapters had to reconstruct quota errors from text."""
    problem = QuotaProblem(
        code=QuotaErrorCode.DAILY_EXHAUSTED,
        reason="daily_limit_reached",
        remaining_micro=0,
        reset_at=datetime(2026, 1, 2, tzinfo=UTC),
        allowed_model_profiles=("economy",),
        retryable=False,
    )

    assert problem.code == "quota_daily_exhausted"
    assert problem.reset_at == datetime(2026, 1, 2, tzinfo=UTC)
    assert problem.retryable is False

    with pytest.raises(ValidationError):
        QuotaProblem(
            code="not-a-quota-code",
            reason="invalid",
            remaining_micro=0,
            retryable=False,
        )
