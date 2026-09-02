from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import Column, DateTime, MetaData, String, Table, create_engine, insert, select, update
from sqlalchemy.pool import StaticPool

from server.quota.models import (
    PricingRuleModel,
    PolicyBindingModel,
    QuotaAdjustmentModel,
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
    QuotaAlertModel,
    UsageEventModel,
)
from server.quota.management import QuotaManagementService
from server.quota.operations import QuotaOperationsService
from server.quota.service import QuotaService
from server.quota.usage import UsageReadService
from server.quota.contracts import AdmitTurn


UTC = timezone.utc
NOW = datetime(2026, 8, 30, 8, 0, tzinfo=UTC)
_CLASSROOM_METADATA = MetaData()
_CLASSROOM_MEMBER_TABLE = Table(
    "nlp_classroom_members",
    _CLASSROOM_METADATA,
    Column("classroom_id", String(36), primary_key=True),
    Column("user_id", String(128), primary_key=True),
    Column("member_role", String(16), nullable=False),
    Column("status", String(16), nullable=False),
)
_RBAC_METADATA = MetaData()
_USER_TABLE = Table(
    "nlp_users",
    _RBAC_METADATA,
    Column("id", String(36), primary_key=True),
    Column("status", String(16), nullable=False),
    Column("deleted_at", DateTime, nullable=True),
)
_ROLE_TABLE = Table(
    "nlp_roles",
    _RBAC_METADATA,
    Column("id", String(36), primary_key=True),
    Column("code", String(64), unique=True, nullable=False),
    Column("status", String(16), nullable=False),
)
_USER_ROLE_TABLE = Table(
    "nlp_user_roles",
    _RBAC_METADATA,
    Column("user_id", String(36), primary_key=True),
    Column("role_id", String(36), primary_key=True),
    Column("expires_at", DateTime, nullable=True),
)


def _engine():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    for model in (
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
        QuotaCreditOperationModel,
        QuotaRoleCreditOperationModel,
        QuotaCreditScopeLockModel,
        QuotaDailyRollupModel,
        QuotaProviderBillingModel,
        QuotaUsageArchiveBatchModel,
        QuotaAlertModel,
    ):
        model.__table__.create(engine)
    _CLASSROOM_MEMBER_TABLE.create(engine)
    _RBAC_METADATA.create_all(engine)
    return engine


def _policy_and_binding(engine, *, daily: int = 1_000, max_overdraft: int = 0):
    policy_id = str(uuid4())
    with engine.begin() as connection:
        connection.execute(
            insert(QuotaPolicyModel).values(
                id=policy_id,
                code="phase4",
                version="1",
                name="phase4",
                status="active",
                request_limit_micro=1_000,
                daily_limit_micro=daily,
                weekly_limit_micro=None,
                concurrency_limit=10,
                max_overdraft_micro=max_overdraft,
                allowed_model_profiles=["economy"],
                unlimited=False,
                effective_from=NOW,
                effective_until=None,
                created_by="developer-1",
            )
        )
        connection.execute(
            insert(PolicyBindingModel).values(
                id=str(uuid4()),
                subject_type="role",
                subject_id="student",
                policy_id=policy_id,
                priority=10,
                status="active",
                effective_from=NOW,
                effective_until=None,
            )
        )
    return policy_id


def _command(turn_id: str, *, user_id: str = "user-1", estimated_micro: int = 20):
    return AdmitTurn(
        request_id=f"request-{turn_id}",
        user_id=user_id,
        workspace_id="workspace-1",
        turn_id=turn_id,
        model_profile="economy",
        model_role="coordinator",
        estimated_input_tokens=10,
        estimated_output_tokens=10,
        estimated_micro=estimated_micro,
        idempotency_key=f"idempotency-{turn_id}",
    )


def _usage_event(
    *,
    operation_id: str,
    user_id: str = "user-1",
    workspace_id: str = "workspace-1",
    reservation_id: str | None = None,
    occurred_at: datetime = NOW,
    credits_micro: int | None = 10,
    usage_status: str = "exact",
):
    return {
        "id": str(uuid4()),
        "operation_id": operation_id,
        "reservation_id": reservation_id,
        "user_id": user_id,
        "workspace_id": workspace_id,
        "conversation_id": None,
        "turn_id": None,
        "worker_id": None,
        "parent_operation_id": None,
        "purpose": "chat",
        "provider": "provider-a",
        "provider_model": "model-a",
        "provider_response_id": None,
        "model_profile": "economy",
        "preset": "default",
        "route": None,
        "pricing_key": "provider-a:model-a",
        "attempt": 1,
        "fallback_index": 0,
        "outcome_status": "completed",
        "finish_reason": "stop",
        "error_kind": None,
        "input_tokens": 8,
        "cached_input_tokens": 0,
        "cache_write_input_tokens": 0,
        "output_tokens": 2,
        "reasoning_output_tokens": 0,
        "total_tokens": 10,
        "usage_source": "provider",
        "usage_status": usage_status,
        "pricing_version": "1",
        "credits_micro": credits_micro,
        "raw_usage_json": {"operation_id": operation_id},
        "dedupe_key": operation_id,
        "idempotency_key": operation_id,
        "started_at": occurred_at,
        "occurred_at": occurred_at,
        "created_at": occurred_at,
    }


def test_ledger_replay_repairs_bucket_without_rewriting_original_entries():
    engine = _engine()
    _policy_and_binding(engine)
    admitted = QuotaService(engine).admit_turn(
        _command("turn-replay", estimated_micro=40), role_codes=("student",), now=NOW
    )
    QuotaService(engine).settle_usage(
        reservation_id=admitted.reservation_id,
        operation_id="op-replay",
        credits_micro=25,
        usage_status="exact",
        now=NOW,
    )
    with engine.begin() as connection:
        bucket_id = connection.execute(select(QuotaBucketModel.id)).scalar_one()
        connection.execute(
            update(QuotaBucketModel)
            .where(QuotaBucketModel.id == bucket_id)
            .values(consumed_micro=999, reserved_micro=999)
        )

    operations = QuotaOperationsService(engine)
    replay = operations.replay_bucket(bucket_id)
    assert replay.expected_consumed_micro == 25
    assert replay.expected_reserved_micro == 15
    assert replay.needs_repair is True

    repaired = operations.repair_bucket(
        bucket_id, actor_user_id="operator-1", reason="rebuild after drift", idempotency_key="repair-1"
    )
    assert repaired.needs_repair is False
    with engine.connect() as connection:
        bucket = connection.execute(select(QuotaBucketModel).where(QuotaBucketModel.id == bucket_id)).mappings().one()
        entries = connection.execute(select(QuotaLedgerEntryModel).where(QuotaLedgerEntryModel.bucket_id == bucket_id)).mappings().all()
    assert (bucket["consumed_micro"], bucket["reserved_micro"]) == (25, 15)
    assert any(row["entry_type"] == "balance_repair" for row in entries)
    assert any(row["reason"] == "provider_usage" for row in entries)


def test_ledger_replay_over_limit_includes_grants_adjustments_and_overdraft():
    engine = _engine()
    _policy_and_binding(engine, daily=100, max_overdraft=10)
    service = QuotaService(engine)
    start = NOW.replace(hour=0, minute=0, second=0, microsecond=0)
    admitted = service.admit_turn(
        _command("turn-replay-capacity", estimated_micro=40),
        role_codes=("student",),
        now=NOW,
    )
    service.settle_usage(
        reservation_id=admitted.reservation_id,
        operation_id="op-replay-capacity",
        credits_micro=135,
        usage_status="exact",
        now=NOW,
    )
    management = QuotaManagementService(engine)
    management.create_grant(
        owner_type="user",
        owner_id="user-1",
        bucket_type="daily",
        period_start=start,
        period_end=start + timedelta(days=1),
        allocated_micro=20,
        source_type="grant",
        created_by="developer-1",
        reason="replay capacity",
        idempotency_key="replay-grant-1",
        effective_from=NOW,
    )
    management.create_adjustment(
        owner_type="user",
        owner_id="user-1",
        bucket_type="daily",
        period_start=start,
        period_end=start + timedelta(days=1),
        amount_micro=5,
        actor_user_id="developer-1",
        reason="replay adjustment",
        idempotency_key="replay-adjustment-1",
    )
    with engine.connect() as connection:
        bucket_id = connection.execute(select(QuotaBucketModel.id)).scalar_one()

    operations = QuotaOperationsService(engine)
    replay = operations.replay_bucket(bucket_id)
    candidates = operations.list_buckets(owner_type="user", owner_id="user-1")

    assert replay.expected_consumed_micro == 135
    assert replay.expected_over_limit is False
    assert candidates[0]["bucket_id"] == bucket_id


def test_snapshot_recomputes_over_limit_from_current_grant_capacity():
    engine = _engine()
    _policy_and_binding(engine, daily=100)
    service = QuotaService(engine)
    admitted = service.admit_turn(
        _command("turn-snapshot-capacity", estimated_micro=40),
        role_codes=("student",),
        now=NOW,
    )
    service.settle_usage(
        reservation_id=admitted.reservation_id,
        operation_id="op-snapshot-capacity",
        credits_micro=110,
        usage_status="exact",
        now=NOW,
    )
    management = QuotaManagementService(engine)
    start = NOW.replace(hour=0, minute=0, second=0, microsecond=0)
    management.create_grant(
        owner_type="user",
        owner_id="user-1",
        bucket_type="daily",
        period_start=start,
        period_end=start + timedelta(days=1),
        allocated_micro=20,
        source_type="grant",
        created_by="developer-1",
        reason="snapshot capacity",
        idempotency_key="snapshot-grant-1",
        effective_from=NOW,
    )

    snapshot = service.snapshot(user_id="user-1", workspace_id="workspace-1", now=NOW)

    user_bucket = next(row for row in snapshot["buckets"] if row["owner_type"] == "user")
    assert user_bucket["over_limit"] is False


def test_provider_reconciliation_locates_usage_event_and_preserves_source_fact():
    engine = _engine()
    operation_id = "op-billing-1"
    with engine.begin() as connection:
        connection.execute(insert(UsageEventModel).values(_usage_event(operation_id=operation_id)))

    result = QuotaOperationsService(engine).reconcile_provider_billing(
        [
            {
                "provider": "provider-a",
                "statement_id": "statement-1",
                "operation_id": operation_id,
                "billed_credits_micro": 17,
                "billed_tokens": {"total_tokens": 12},
                "billed_at": NOW,
                "idempotency_key": "statement-1",
            }
        ]
    )
    assert result["discrepancies"] == 1
    with engine.connect() as connection:
        billing = connection.execute(select(QuotaProviderBillingModel)).mappings().one()
        usage = connection.execute(select(UsageEventModel)).mappings().one()
    assert billing["status"] == "discrepancy"
    assert billing["matched_usage_event_id"] == usage["id"]
    assert billing["difference_micro"] == 7
    assert usage["credits_micro"] == 10


def test_provider_reconciliation_refreshes_an_unmatched_line_after_late_usage_arrives():
    engine = _engine()
    operations = QuotaOperationsService(engine)
    statement = {
        "provider": "provider-a",
        "statement_id": "statement-late",
        "operation_id": "op-late",
        "billed_credits_micro": 10,
        "billed_at": NOW,
        "idempotency_key": "statement-late",
    }
    assert operations.reconcile_provider_billing([statement])["unmatched"] == 1
    with engine.begin() as connection:
        connection.execute(insert(UsageEventModel).values(_usage_event(operation_id="op-late")))
    refreshed = operations.reconcile_provider_billing([statement])
    assert refreshed["matched"] == 1
    assert operations.list_billing_reconciliation()[0]["status"] == "matched"
    assert operations.reconcile_provider_billing(
        [{**statement, "idempotency_key": "statement-late-retry"}]
    )["items"][0]["status"] == "matched"
    with pytest.raises(ValueError, match="idempotency key conflicts"):
        operations.reconcile_provider_billing(
            [{**statement, "billed_at": NOW + timedelta(minutes=1)}]
        )


def test_billing_repair_appends_a_correction_ledger_entry():
    engine = _engine()
    _policy_and_binding(engine)
    service = QuotaService(engine)
    admitted = service.admit_turn(
        _command("turn-billing-repair", estimated_micro=40),
        role_codes=("student",),
        now=NOW,
    )
    service.settle_usage(
        reservation_id=admitted.reservation_id,
        operation_id="op-billing-repair",
        credits_micro=10,
        usage_status="exact",
        now=NOW,
    )
    with engine.begin() as connection:
        connection.execute(
            insert(UsageEventModel).values(
                _usage_event(
                    operation_id="op-billing-repair",
                    reservation_id=admitted.reservation_id,
                    credits_micro=10,
                )
            )
        )
    operations = QuotaOperationsService(engine)
    billing = operations.reconcile_provider_billing(
        [
            {
                "provider": "provider-a",
                "statement_id": "statement-repair",
                "operation_id": "op-billing-repair",
                "billed_credits_micro": 30,
                "billed_at": NOW,
                "idempotency_key": "statement-repair",
            }
        ]
    )["items"][0]
    repaired = operations.repair_billing(
        billing["billing_id"],
        actor_user_id="operator-1",
        reason="provider invoice correction",
        idempotency_key="billing-repair-1",
    )
    assert repaired["status"] == "repaired"
    with engine.connect() as connection:
        bucket = connection.execute(select(QuotaBucketModel)).mappings().first()
        entries = connection.execute(select(QuotaLedgerEntryModel)).mappings().all()
    assert bucket["consumed_micro"] == 30
    assert sum(row["consumed_delta_micro"] for row in entries if row["entry_type"] == "billing_adjustment") == 20
    assert any(row["entry_type"] == "settle" for row in entries)
    replay = operations.replay_bucket(bucket["id"])
    assert replay.expected_consumed_micro == 30
    assert replay.needs_repair is False
    assert operations.reconcile_provider_billing(
        [
            {
                "provider": "provider-a",
                "statement_id": "statement-repair",
                "operation_id": "op-billing-repair",
                "billed_credits_micro": 30,
                "billed_at": NOW,
                "idempotency_key": "statement-repair",
            }
        ]
    )["items"][0]["status"] == "repaired"
    with engine.connect() as connection:
        assert connection.execute(select(QuotaLedgerEntryModel).where(QuotaLedgerEntryModel.entry_type == "billing_adjustment")).fetchall().__len__() == 1


def test_derived_ledger_keys_stay_within_mysql_column_limit():
    engine = _engine()
    operations = QuotaOperationsService(engine)
    long_key = "x" * 255
    start = NOW.replace(hour=0)
    gift = operations.gift_credits(
        QuotaManagementService(engine),
        owner_type="user",
        owner_id="user-long-key",
        bucket_type="daily",
        period_start=start,
        period_end=start + timedelta(days=1),
        amount_micro=10,
        actor_user_id="developer-1",
        reason="long key",
        idempotency_key=long_key,
        effective_from=NOW,
    )
    assert gift["grant_id"]


def test_gift_and_reset_credits_are_idempotent_and_append_only():
    engine = _engine()
    management = QuotaManagementService(engine)
    operations = QuotaOperationsService(engine)
    start = NOW.replace(hour=0)
    gift = operations.gift_credits(
        management,
        owner_type="user",
        owner_id="user-1",
        bucket_type="daily",
        period_start=start,
        period_end=start + timedelta(days=1),
        amount_micro=100,
        actor_user_id="developer-1",
        reason="welcome",
        idempotency_key="gift-1",
        effective_from=NOW,
    )
    replay = operations.gift_credits(
        management,
        owner_type="user",
        owner_id="user-1",
        bucket_type="daily",
        period_start=start,
        period_end=start + timedelta(days=1),
        amount_micro=100,
        actor_user_id="developer-1",
        reason="welcome",
        idempotency_key="gift-1",
        effective_from=NOW,
    )
    assert replay["grant_id"] == gift["grant_id"]
    with pytest.raises(ValueError, match="idempotency key conflicts"):
        operations.gift_credits(
            management,
            owner_type="user",
            owner_id="user-1",
            bucket_type="daily",
            period_start=start,
            period_end=start + timedelta(days=1),
            amount_micro=100,
            actor_user_id="developer-1",
            reason="welcome",
            idempotency_key="gift-1",
            effective_from=NOW,
            expires_at=NOW + timedelta(hours=1),
        )
    reset = operations.reset_credits(
        management,
        owner_type="user",
        owner_id="user-1",
        bucket_type="daily",
        period_start=start,
        period_end=start + timedelta(days=1),
        amount_micro=50,
        actor_user_id="developer-1",
        reason="weekly reset",
        idempotency_key="reset-1",
        effective_from=NOW,
    )
    reset_replay = operations.reset_credits(
        management,
        owner_type="user",
        owner_id="user-1",
        bucket_type="daily",
        period_start=start,
        period_end=start + timedelta(days=1),
        amount_micro=50,
        actor_user_id="developer-1",
        reason="weekly reset",
        idempotency_key="reset-1",
        effective_from=NOW,
    )
    assert reset_replay["operation_id"] == reset["operation_id"]
    assert gift["reason"] == "welcome"
    assert gift["status"] == "active"
    with engine.connect() as connection:
        assert connection.execute(select(QuotaGrantModel)).fetchall().__len__() == 2
        assert connection.execute(select(QuotaCreditOperationModel)).fetchall().__len__() == 2


def test_role_gift_materializes_user_grants_and_replays():
    engine = _engine()
    role_id = "role-student"
    with engine.begin() as connection:
        connection.execute(
            insert(_ROLE_TABLE).values(id=role_id, code="student", status="active")
        )
        connection.execute(
            insert(_USER_TABLE),
            [
                {"id": "user-a", "status": "active", "deleted_at": None},
                {"id": "user-b", "status": "active", "deleted_at": None},
                {"id": "user-disabled", "status": "disabled", "deleted_at": None},
            ],
        )
        connection.execute(
            insert(_USER_ROLE_TABLE),
            [
                {"user_id": "user-a", "role_id": role_id, "expires_at": None},
                {"user_id": "user-b", "role_id": role_id, "expires_at": None},
                {"user_id": "user-disabled", "role_id": role_id, "expires_at": None},
            ],
        )

    management = QuotaManagementService(engine)
    operations = QuotaOperationsService(engine)
    start = NOW.replace(hour=0)
    kwargs = {
        "role_code": "student",
        "bucket_type": "daily",
        "period_start": start,
        "period_end": start + timedelta(days=1),
        "amount_micro": 300,
        "actor_user_id": "developer-1",
        "reason": "student welcome",
        "idempotency_key": "role-gift-1",
        "effective_from": NOW,
    }
    result = operations.gift_credits_for_role(management, **kwargs)

    assert result["recipient_count"] == 2
    with engine.connect() as connection:
        grants = connection.execute(
            select(
                QuotaGrantModel.owner_id,
                QuotaGrantModel.allocated_micro,
                QuotaGrantModel.source_type,
            ).order_by(QuotaGrantModel.owner_id)
        ).all()
    assert grants == [("user-a", 300, "role"), ("user-b", 300, "role")]

    replay = operations.gift_credits_for_role(management, **kwargs)
    assert replay["recipient_count"] == 2
    with pytest.raises(ValueError, match="role credit operation idempotency key conflicts"):
        operations.gift_credits_for_role(
            management,
            **{**kwargs, "amount_micro": 400},
        )
    with engine.connect() as connection:
        assert connection.execute(select(QuotaGrantModel)).fetchall().__len__() == 2
        assert connection.execute(select(QuotaRoleCreditOperationModel)).fetchall().__len__() == 1
    history = operations.list_credit_operations()
    role_history = next(item for item in history if item["owner_type"] == "role")
    assert role_history["owner_id"] == "student"
    assert role_history["recipient_count"] == 2


def test_daily_rollup_alert_and_archive_are_off_path_from_usage_events():
    engine = _engine()
    with engine.begin() as connection:
        for index in range(3):
            connection.execute(
                insert(UsageEventModel).values(
                    _usage_event(
                        operation_id=f"op-rollup-{index}",
                        occurred_at=NOW - timedelta(days=1),
                    )
                )
            )
        connection.execute(
            insert(UsageEventModel).values(
                _usage_event(
                    operation_id="op-rollup-spike",
                    occurred_at=NOW,
                    credits_micro=100,
                )
            )
        )

    operations = QuotaOperationsService(engine)
    assert operations.build_daily_rollup(NOW.date()) == 1
    assert operations.build_daily_rollup((NOW - timedelta(days=1)).date()) == 1
    alerts = operations.detect_usage_anomalies(day=NOW.date(), threshold_multiplier=2)
    assert alerts["created"] == 1
    assert alerts["items"][0]["alert_type"] == "usage_spike"
    assert operations.detect_usage_anomalies(day=NOW.date(), threshold_multiplier=2)["created"] == 0

    archive = operations.archive_usage_events(
        before=NOW - timedelta(hours=1), actor_user_id="operator-1"
    )
    assert archive["archived_events"] == 3
    with engine.connect() as connection:
        original = connection.execute(select(UsageEventModel).where(UsageEventModel.operation_id == "op-rollup-0")).mappings().one()
        batch = connection.execute(select(QuotaUsageArchiveBatchModel)).mappings().one()
    assert original["credits_micro"] == 10
    assert original["archive_batch_id"] == batch["id"]

    empty_archive = operations.archive_usage_events(
        before=NOW - timedelta(hours=1), actor_user_id="operator-1"
    )
    assert empty_archive["archived_events"] == 0
    assert empty_archive["batch_id"] is None
    with engine.connect() as connection:
        assert connection.execute(select(QuotaUsageArchiveBatchModel)).fetchall().__len__() == 1

    purged = operations.purge_archived_usage_events(
        before=NOW, actor_user_id="operator-1"
    )
    assert purged["deleted_events"] == 3
    with engine.connect() as connection:
        remaining = connection.execute(select(UsageEventModel)).mappings().all()
        assert len(remaining) == 1
        assert remaining[0]["operation_id"] == "op-rollup-spike"
        assert connection.execute(select(QuotaUsageArchiveBatchModel)).fetchall().__len__() == 1


def test_purge_keeps_archived_event_when_provider_billing_still_references_it():
    engine = _engine()
    with engine.begin() as connection:
        connection.execute(
            insert(UsageEventModel).values(
                _usage_event(
                    operation_id="op-archive-billing",
                    occurred_at=NOW - timedelta(days=2),
                )
            )
        )

    operations = QuotaOperationsService(engine)
    operations.archive_usage_events(before=NOW, actor_user_id="operator-1")
    operations.reconcile_provider_billing(
        [
            {
                "provider": "provider-a",
                "statement_id": "archive-billing-1",
                "operation_id": "op-archive-billing",
                "billed_credits_micro": 10,
                "billed_tokens": {"total_tokens": 10},
                "billed_at": NOW,
                "idempotency_key": "archive-billing-1",
            }
        ]
    )

    with pytest.raises(ValueError, match="still referenced by provider billing"):
        operations.purge_archived_usage_events(before=NOW, actor_user_id="operator-1")
    with engine.connect() as connection:
        assert connection.execute(select(UsageEventModel)).fetchall().__len__() == 1


def test_archived_events_leave_operational_usage_views_but_remain_auditable():
    engine = _engine()
    archived_at = NOW - timedelta(days=1)
    with engine.begin() as connection:
        connection.execute(
            insert(UsageEventModel).values(
                _usage_event(
                    operation_id="op-archive-old",
                    occurred_at=NOW - timedelta(days=2),
                )
            )
        )
        connection.execute(
            insert(UsageEventModel).values(
                _usage_event(
                    operation_id="op-archive-live",
                    occurred_at=NOW - timedelta(hours=1),
                )
            )
        )

    operations = QuotaOperationsService(engine)
    result = operations.archive_usage_events(
        before=archived_at, actor_user_id="operator-1"
    )
    assert result["archived_events"] == 1

    snapshot = UsageReadService(engine).user_snapshot(
        "user-1", days=7, now=NOW
    )
    assert snapshot["events"] == 1
    assert snapshot["tokens"]["total_tokens"] == 10

    assert operations.build_daily_rollup((NOW - timedelta(days=2)).date()) == 0
    with engine.connect() as connection:
        archived = connection.execute(
            select(UsageEventModel).where(
                UsageEventModel.operation_id == "op-archive-old"
            )
        ).mappings().one()
    assert archived["archive_batch_id"] == result["batch_id"]


def test_alert_status_can_be_acknowledged_and_resolved():
    engine = _engine()
    with engine.begin() as connection:
        connection.execute(
            insert(QuotaAlertModel).values(
                id="alert-1",
                alert_type="usage_spike",
                severity="high",
                owner_type="user",
                owner_id="user-1",
                window_start=NOW - timedelta(days=1),
                window_end=NOW,
                baseline_micro=10,
                actual_micro=100,
                threshold_multiplier=2,
                status="open",
                dedupe_key="alert-dedupe-1",
                metadata_json={},
                created_at=NOW,
                resolved_at=None,
            )
        )

    operations = QuotaOperationsService(engine)
    acknowledged = operations.update_alert(
        "alert-1", status="acknowledged", actor_user_id="operator-1", reason="triaged"
    )
    resolved = operations.update_alert(
        "alert-1", status="resolved", actor_user_id="operator-1", reason="handled"
    )

    assert acknowledged["status"] == "acknowledged"
    assert resolved["status"] == "resolved"
    assert resolved["resolved_at"] is not None


def test_classroom_aggregate_sums_members_and_returns_usage_status_counts():
    engine = _engine()
    with engine.begin() as connection:
        connection.execute(
            insert(_CLASSROOM_MEMBER_TABLE).values(
                classroom_id="classroom-1", user_id="user-1", member_role="student", status="active"
            )
        )
        connection.execute(
            insert(_CLASSROOM_MEMBER_TABLE).values(
                classroom_id="classroom-1", user_id="user-2", member_role="student", status="active"
            )
        )
        connection.execute(insert(UsageEventModel).values(_usage_event(operation_id="op-class-1", user_id="user-1", credits_micro=11)))
        connection.execute(insert(UsageEventModel).values(_usage_event(operation_id="op-class-2", user_id="user-2", credits_micro=None, usage_status="pending")))

    aggregate = QuotaOperationsService(engine).classroom_usage(
        "classroom-1", workspace_id="workspace-1", start=NOW - timedelta(days=1), end=NOW + timedelta(days=1)
    )
    assert aggregate["students"] == 2
    assert aggregate["events"] == 2
    assert aggregate["priced_credits_micro"] == 11
    assert aggregate["pending_events"] == 1


def test_classroom_aggregate_does_not_mix_events_from_other_workspaces():
    engine = _engine()
    with engine.begin() as connection:
        connection.execute(
            insert(_CLASSROOM_MEMBER_TABLE).values(
                classroom_id="classroom-1", user_id="user-1", member_role="student", status="active"
            )
        )
        connection.execute(
            insert(UsageEventModel).values(
                _usage_event(operation_id="op-class-workspace-1", workspace_id="workspace-1", credits_micro=11)
            )
        )
        connection.execute(
            insert(UsageEventModel).values(
                _usage_event(operation_id="op-class-workspace-2", workspace_id="workspace-2", credits_micro=99)
            )
        )

    aggregate = QuotaOperationsService(engine).classroom_usage(
        "classroom-1", workspace_id="workspace-1", start=NOW - timedelta(days=1), end=NOW + timedelta(days=1)
    )

    assert aggregate["events"] == 1
    assert aggregate["priced_credits_micro"] == 11
