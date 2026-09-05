from __future__ import annotations

from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import Column, MetaData, String, Table, create_engine, func, insert, select
from sqlalchemy.pool import StaticPool

from server.quota.contracts import AdmitTurn, FinishTurn
from server.quota.errors import QuotaDomainError, QuotaErrorCode, QuotaRejectedError
from server.quota.management import QuotaManagementService
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
from server.quota.service import QuotaService


UTC = timezone.utc
NOW = datetime(2026, 8, 30, 8, 0, tzinfo=UTC)
_CLASSROOM_METADATA = MetaData()
_CLASSROOM_TABLE = Table(
    "nlp_classrooms",
    _CLASSROOM_METADATA,
    Column("id", String(36), primary_key=True),
    Column("workspace_id", String(36), nullable=False),
    Column("name", String(128), nullable=False),
    Column("status", String(16), nullable=False),
)


@pytest.fixture
def quota_engine():
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
    ):
        model.__table__.create(engine)
    _CLASSROOM_TABLE.create(engine)
    with engine.begin() as connection:
        connection.execute(
            _CLASSROOM_TABLE.insert().values(
                id="classroom-a",
                workspace_id="workspace-a",
                name="Workspace A classroom",
                status="active",
            )
        )
        connection.execute(
            _CLASSROOM_TABLE.insert().values(
                id="classroom-b",
                workspace_id="workspace-b",
                name="Workspace B classroom",
                status="active",
            )
        )
        connection.execute(
            _CLASSROOM_TABLE.insert().values(
                id="classroom-1",
                workspace_id="workspace-1",
                name="Workspace 1 classroom",
                status="active",
            )
        )
        connection.execute(
            _CLASSROOM_TABLE.insert().values(
                id="classroom-without-policy",
                workspace_id="workspace-1",
                name="Workspace 1 classroom without policy",
                status="active",
            )
        )
    try:
        yield engine
    finally:
        engine.dispose()


def _policy(
    engine,
    *,
    code: str,
    version: str,
    daily: int | None = 100,
    weekly: int | None = 1_000,
    request: int | None = 100,
    concurrency: int | None = 2,
    allowed_profiles: list[str] | None = None,
    max_overdraft: int = 0,
    status: str = "active",
) -> str:
    policy_id = str(uuid4())
    with engine.begin() as connection:
        connection.execute(
            insert(QuotaPolicyModel).values(
                id=policy_id,
                code=code,
                version=version,
                name=code,
                status=status,
                request_limit_micro=request,
                daily_limit_micro=daily,
                weekly_limit_micro=weekly,
                concurrency_limit=concurrency,
                max_overdraft_micro=max_overdraft,
                allowed_model_profiles=allowed_profiles or ["economy"],
                unlimited=False,
                effective_from=NOW,
                effective_until=None,
                created_by="developer-1",
            )
        )
    return policy_id


def _bind(engine, *, subject_type: str, subject_id: str, policy_id: str, priority: int = 10) -> None:
    with engine.begin() as connection:
        connection.execute(
            insert(PolicyBindingModel).values(
                id=str(uuid4()),
                subject_type=subject_type,
                subject_id=subject_id,
                policy_id=policy_id,
                priority=priority,
                status="active",
                effective_from=NOW,
                effective_until=None,
            )
        )


def test_classroom_policy_from_another_workspace_is_not_applied(quota_engine):
    user_policy = _policy(
        quota_engine, code="workspace-a-user", version="1", daily=100, weekly=None
    )
    foreign_classroom_policy = _policy(
        quota_engine,
        code="workspace-b-classroom",
        version="1",
        daily=1,
        weekly=None,
        request=1,
    )
    _bind(quota_engine, subject_type="role", subject_id="student", policy_id=user_policy)
    _bind(
        quota_engine,
        subject_type="classroom",
        subject_id="classroom-b",
        policy_id=foreign_classroom_policy,
    )

    admitted = QuotaService(quota_engine).admit_turn(
        _command("cross-workspace-classroom", estimated_micro=20).model_copy(
            update={"workspace_id": "workspace-a"}
        ),
        role_codes=("student",),
        classroom_ids=("classroom-a", "classroom-b"),
        now=NOW,
    )

    assert admitted.allowed is True


def _command(turn_id: str, *, estimated_micro: int = 20) -> AdmitTurn:
    return AdmitTurn(
        request_id=f"request-{turn_id}",
        user_id="user-1",
        workspace_id="workspace-1",
        turn_id=turn_id,
        model_profile="economy",
        model_role="coordinator",
        estimated_input_tokens=10,
        estimated_output_tokens=10,
        estimated_micro=estimated_micro,
        idempotency_key=f"idempotency-{turn_id}",
    )


def _management_grant_input(*, idempotency_key: str) -> dict:
    period_start = NOW.replace(hour=0)
    return {
        "owner_type": "user",
        "owner_id": "user-1",
        "bucket_type": "daily",
        "period_start": period_start,
        "period_end": period_start + timedelta(days=1),
        "allocated_micro": 10,
        "source_type": "grant",
        "created_by": "developer-1",
        "reason": "concurrent grant",
        "idempotency_key": idempotency_key,
        "effective_from": NOW,
    }


def test_policy_resolution_returns_one_explainable_base_and_separate_workspace_policy(quota_engine):
    default_id = _policy(quota_engine, code="default", version="1")
    role_a_id = _policy(quota_engine, code="role-a", version="1", daily=200)
    role_b_id = _policy(quota_engine, code="role-b", version="1", daily=300)
    user_id = _policy(quota_engine, code="user-override", version="1", daily=400)
    workspace_id = _policy(quota_engine, code="workspace-budget", version="1", daily=500)
    _bind(quota_engine, subject_type="default", subject_id="*", policy_id=default_id)
    _bind(quota_engine, subject_type="role", subject_id="teacher", policy_id=role_a_id, priority=10)
    _bind(quota_engine, subject_type="role", subject_id="reviewer", policy_id=role_b_id, priority=5)
    _bind(quota_engine, subject_type="user", subject_id="user-1", policy_id=user_id)
    _bind(quota_engine, subject_type="workspace", subject_id="workspace-1", policy_id=workspace_id)

    explanation = QuotaManagementService(quota_engine).explain_policy(
        user_id="user-1",
        workspace_id="workspace-1",
        role_codes=("teacher", "reviewer"),
        at=NOW,
    )

    assert explanation["base"]["policy_id"] == user_id
    assert explanation["base"]["reason"]["subject_type"] == "user"
    assert explanation["workspace"]["policy_id"] == workspace_id
    assert explanation["workspace"]["reason"]["subject_type"] == "workspace"
    assert explanation["candidates"]["role"] == 2


def test_user_and_workspace_buckets_are_reserved_and_settled_together(quota_engine):
    user_policy = _policy(quota_engine, code="user", version="1", daily=100, weekly=None)
    workspace_policy = _policy(quota_engine, code="workspace", version="1", daily=50, weekly=None)
    _bind(quota_engine, subject_type="role", subject_id="student", policy_id=user_policy)
    _bind(quota_engine, subject_type="workspace", subject_id="workspace-1", policy_id=workspace_policy)
    service = QuotaService(quota_engine)

    admitted = service.admit_turn(_command("turn-1", estimated_micro=40), role_codes=("student",), now=NOW)
    assert admitted.allowed is True
    with quota_engine.connect() as connection:
        buckets = connection.execute(select(QuotaBucketModel.__table__)).mappings().all()
    assert {(row["owner_type"], row["owner_id"]) for row in buckets} == {
        ("user", "user-1"),
        ("workspace", "workspace-1"),
    }
    assert all(row["reserved_micro"] == 40 for row in buckets)

    service.finish_turn(
        FinishTurn(
            reservation_id=admitted.reservation_id,
            turn_id="turn-1",
            idempotency_key="finish-1",
        ),
        now=NOW + timedelta(seconds=1),
    )
    snapshot = service.snapshot(user_id="user-1", workspace_id="workspace-1", now=NOW)
    assert {row["owner_type"] for row in snapshot["buckets"]} == {"user", "workspace"}


def test_grant_revoke_expire_and_manual_adjustment_are_idempotent(quota_engine):
    management = QuotaManagementService(quota_engine)
    grant = management.create_grant(
        owner_type="user",
        owner_id="user-1",
        bucket_type="daily",
        period_start=NOW.replace(hour=0),
        period_end=NOW.replace(hour=0) + timedelta(days=1),
        allocated_micro=100,
        source_type="purchase",
        created_by="developer-1",
        reason="purchase",
        idempotency_key="grant-1",
        effective_from=NOW,
    )
    replay = management.create_grant(
        owner_type="user",
        owner_id="user-1",
        bucket_type="daily",
        period_start=NOW.replace(hour=0),
        period_end=NOW.replace(hour=0) + timedelta(days=1),
        allocated_micro=100,
        source_type="purchase",
        created_by="developer-1",
        reason="purchase",
        idempotency_key="grant-1",
        effective_from=NOW,
    )
    assert replay["grant_id"] == grant["grant_id"]

    adjustment_input = dict(
        owner_type="user",
        owner_id="user-1",
        bucket_type="daily",
        period_start=NOW.replace(hour=0),
        period_end=NOW.replace(hour=0) + timedelta(days=1),
        amount_micro=25,
        actor_user_id="developer-1",
        reason="support compensation",
        idempotency_key="adjustment-1",
    )
    adjustment = management.create_adjustment(**adjustment_input)
    assert management.create_adjustment(**adjustment_input)["adjustment_id"] == adjustment["adjustment_id"]
    assert management.get_adjustment(adjustment["adjustment_id"])["reason"] == "support compensation"

    revoked = management.revoke_grant(grant["grant_id"], actor_user_id="developer-1", idempotency_key="revoke-1")
    assert revoked["status"] == "revoked"
    assert management.revoke_grant(grant["grant_id"], actor_user_id="developer-1", idempotency_key="revoke-1")["status"] == "revoked"

    expiring = management.create_grant(
        owner_type="user",
        owner_id="user-1",
        bucket_type="daily",
        period_start=NOW.replace(hour=0),
        period_end=NOW.replace(hour=0) + timedelta(days=1),
        allocated_micro=10,
        source_type="grant",
        created_by="developer-1",
        reason="temporary",
        idempotency_key="grant-expiring",
        effective_from=NOW,
        expires_at=NOW + timedelta(minutes=1),
    )
    assert management.expire_grants(now=NOW + timedelta(minutes=2)) == 1
    assert management.get_grant(expiring["grant_id"])["status"] == "expired"


def test_grant_idempotency_is_scoped_to_owner_without_ledger_collision(quota_engine):
    management = QuotaManagementService(quota_engine)
    period_start = NOW.replace(hour=0)
    period_end = period_start + timedelta(days=1)
    common = dict(
        bucket_type="daily",
        period_start=period_start,
        period_end=period_end,
        allocated_micro=10,
        source_type="grant",
        created_by="developer-1",
        reason="same operator request key on separate owners",
        idempotency_key="shared-grant-key",
        effective_from=NOW,
    )

    user_grant = management.create_grant(
        owner_type="user", owner_id="user-1", **common
    )
    workspace_grant = management.create_grant(
        owner_type="workspace", owner_id="workspace-1", **common
    )

    assert user_grant["grant_id"] != workspace_grant["grant_id"]


def test_snapshot_exposes_active_grant_before_first_admission(quota_engine):
    management = QuotaManagementService(quota_engine)
    period_start = NOW.replace(hour=0)
    management.create_grant(
        owner_type="user",
        owner_id="user-1",
        bucket_type="daily",
        period_start=period_start,
        period_end=period_start + timedelta(days=1),
        allocated_micro=50,
        source_type="grant",
        created_by="developer-1",
        reason="show grant in account snapshot",
        idempotency_key="snapshot-grant",
        effective_from=NOW,
    )

    snapshot = QuotaService(quota_engine).snapshot(user_id="user-1", now=NOW)

    assert snapshot["buckets"] == [
        {
            "owner_type": "user",
            "owner_id": "user-1",
            "bucket_type": "daily",
            "limit_micro": 0,
            "grant_micro": 50,
            "adjustment_micro": 0,
            "consumed_micro": 0,
            "reserved_micro": 0,
            "remaining_micro": 50,
            "reset_at": (period_start + timedelta(days=1)).isoformat(),
            "over_limit": False,
        }
    ]


def test_policy_and_binding_lifecycle_has_safe_crud_semantics(quota_engine):
    management = QuotaManagementService(quota_engine)
    draft = management.create_policy(
        code="developer-crud",
        version="1",
        name="Developer CRUD draft",
        daily_limit_micro=100,
        weekly_limit_micro=500,
        request_limit_micro=50,
        concurrency_limit=2,
        created_by="developer-1",
        effective_from=NOW,
        status="draft",
    )

    assert management.get_policy(draft["policy_id"])["status"] == "draft"
    updated = management.update_policy(
        draft["policy_id"],
        actor_user_id="developer-1",
        name="Developer CRUD updated",
        daily_limit_micro=200,
    )
    assert updated["name"] == "Developer CRUD updated"
    assert updated["daily_limit_micro"] == 200

    archived = management.archive_policy(draft["policy_id"], actor_user_id="developer-1")
    assert archived["status"] == "archived"
    assert management.archive_policy(draft["policy_id"], actor_user_id="developer-2")["status"] == "archived"
    with pytest.raises(QuotaDomainError) as error:
        management.update_policy(
            draft["policy_id"],
            actor_user_id="developer-1",
            name="must not change",
        )
    assert error.value.code is QuotaErrorCode.POLICY_CONFLICT

    active = management.create_policy(
        code="developer-binding",
        version="1",
        name="Developer binding",
        daily_limit_micro=100,
        weekly_limit_micro=None,
        request_limit_micro=100,
        concurrency_limit=2,
        created_by="developer-1",
        effective_from=NOW,
        status="active",
    )
    binding = management.bind_policy(
        subject_type="default",
        subject_id="*",
        policy_id=active["policy_id"],
        priority=1,
        effective_from=NOW,
    )
    assert management.get_binding(binding["binding_id"])["status"] == "active"
    retired = management.retire_binding(binding["binding_id"], actor_user_id="developer-1")
    assert retired["status"] == "retired"
    assert management.retire_binding(binding["binding_id"], actor_user_id="developer-2")["status"] == "retired"


def test_retiring_replacement_binding_restores_previous_effective_policy(quota_engine):
    management = QuotaManagementService(quota_engine)
    previous = management.create_policy(
        code="binding-previous",
        version="1",
        name="Previous policy",
        daily_limit_micro=100,
        weekly_limit_micro=None,
        request_limit_micro=100,
        concurrency_limit=2,
        created_by="developer-1",
        effective_from=NOW,
        status="active",
    )
    replacement = management.create_policy(
        code="binding-replacement",
        version="1",
        name="Replacement policy",
        daily_limit_micro=200,
        weekly_limit_micro=None,
        request_limit_micro=200,
        concurrency_limit=2,
        created_by="developer-1",
        effective_from=NOW,
        status="active",
    )
    management.bind_policy(
        subject_type="user",
        subject_id="user-1",
        policy_id=previous["policy_id"],
        effective_from=NOW,
    )
    current = management.bind_policy(
        subject_type="user",
        subject_id="user-1",
        policy_id=replacement["policy_id"],
        effective_from=NOW,
    )

    management.retire_binding(current["binding_id"], actor_user_id="developer-1")

    explanation = management.explain_policy(
        user_id="user-1",
        workspace_id=None,
        role_codes=(),
        at=NOW,
    )
    assert explanation["base"]["policy_id"] == previous["policy_id"]


def test_pricing_rule_management_preserves_versioned_lifecycle(quota_engine):
    management = QuotaManagementService(quota_engine)
    rule = management.create_pricing_rule(
        pricing_key="deepseek/deepseek-v4-pro",
        version="2026-09-02",
        effective_from=NOW,
        effective_until=None,
        ordinary_input_credits_micro_per_million_tokens=1_000_000,
        cached_input_credits_micro_per_million_tokens=250_000,
        cache_write_credits_micro_per_million_tokens=500_000,
        output_credits_micro_per_million_tokens=2_000_000,
        reasoning_output_credits_micro_per_million_tokens=3_000_000,
        visual_input_credits_micro_per_million_tokens=750_000,
        image_unit_credits_micro=50,
        search_call_credits_micro=75,
        link_page_credits_micro=25,
        created_by="developer-1",
    )

    assert management.get_pricing_rule(rule["pricing_rule_id"])["pricing_key"] == "deepseek/deepseek-v4-pro"
    assert rule["visual_input_credits_micro_per_million_tokens"] == 750_000
    assert rule["image_unit_credits_micro"] == 50
    assert rule["search_call_credits_micro"] == 75
    assert rule["link_page_credits_micro"] == 25
    assert management.list_pricing_rules(pricing_key="deepseek/deepseek-v4-pro")[0]["version"] == "2026-09-02"

    retired = management.retire_pricing_rule(rule["pricing_rule_id"], actor_user_id="developer-1")
    assert retired["status"] == "retired"


def test_policy_version_and_manual_adjustment_are_recorded_without_mutating_history(quota_engine):
    old_id = _policy(quota_engine, code="student", version="1", daily=100)
    _bind(quota_engine, subject_type="role", subject_id="student", policy_id=old_id)
    service = QuotaService(quota_engine)
    first = service.admit_turn(_command("turn-history", estimated_micro=20), role_codes=("student",), now=NOW)
    management = QuotaManagementService(quota_engine)
    new = management.create_policy(
        code="student",
        version="2",
        name="Student v2",
        daily_limit_micro=200,
        weekly_limit_micro=None,
        request_limit_micro=200,
        concurrency_limit=2,
        created_by="developer-1",
        effective_from=NOW + timedelta(hours=1),
        status="draft",
    )
    management.publish_policy(new["policy_id"], actor_user_id="developer-1")
    with quota_engine.connect() as connection:
        reservation = connection.execute(select(QuotaReservationModel.__table__)).mappings().one()
        assert reservation["policy_id"] == old_id
        assert reservation["policy_version"] == "1"
        assert connection.execute(select(QuotaLedgerEntryModel.__table__)).fetchall()
    assert new["version"] == "2"


def test_workspace_budget_rejects_after_user_budget_would_still_allow(quota_engine):
    user_policy = _policy(quota_engine, code="user", version="1", daily=100, weekly=None)
    workspace_policy = _policy(quota_engine, code="workspace", version="1", daily=30, weekly=None)
    _bind(quota_engine, subject_type="role", subject_id="student", policy_id=user_policy)
    _bind(quota_engine, subject_type="workspace", subject_id="workspace-1", policy_id=workspace_policy)
    service = QuotaService(quota_engine)
    service.admit_turn(_command("turn-budget", estimated_micro=20), role_codes=("student",), now=NOW)
    with pytest.raises(QuotaRejectedError) as error:
        service.admit_turn(_command("turn-budget-2", estimated_micro=20), role_codes=("student",), now=NOW)
    assert error.value.problem.code is QuotaErrorCode.WORKSPACE_EXHAUSTED


def test_active_grant_and_manual_adjustment_extend_the_atomic_bucket_balance(quota_engine):
    user_policy = _policy(quota_engine, code="user", version="1", daily=10, weekly=None)
    _bind(quota_engine, subject_type="role", subject_id="student", policy_id=user_policy)
    start = NOW.replace(hour=0)
    end = start + timedelta(days=1)
    management = QuotaManagementService(quota_engine)
    management.create_grant(
        owner_type="user",
        owner_id="user-1",
        bucket_type="daily",
        period_start=start,
        period_end=end,
        allocated_micro=50,
        source_type="grant",
        created_by="developer-1",
        reason="exam week",
        idempotency_key="grant-admission",
        effective_from=NOW,
    )
    management.create_adjustment(
        owner_type="user",
        owner_id="user-1",
        bucket_type="daily",
        period_start=start,
        period_end=end,
        amount_micro=-5,
        actor_user_id="developer-1",
        reason="correction",
        idempotency_key="adjustment-admission",
    )
    service = QuotaService(quota_engine)
    admitted = service.admit_turn(_command("turn-grant", estimated_micro=55), role_codes=("student",), now=NOW)
    assert admitted.allowed is True
    snapshot = service.snapshot(user_id="user-1", now=NOW)
    daily = next(item for item in snapshot["buckets"] if item["bucket_type"] == "daily")
    assert daily["grant_micro"] == 50
    assert daily["adjustment_micro"] == -5
    assert daily["remaining_micro"] == 0


def test_settlement_uses_workspace_policy_overdraft_for_workspace_bucket(quota_engine):
    user_policy = _policy(quota_engine, code="user", version="1", daily=100, weekly=None)
    workspace_policy = _policy(
        quota_engine,
        code="workspace",
        version="1",
        daily=100,
        weekly=None,
        max_overdraft=20,
    )
    _bind(quota_engine, subject_type="role", subject_id="student", policy_id=user_policy)
    _bind(quota_engine, subject_type="workspace", subject_id="workspace-1", policy_id=workspace_policy)
    service = QuotaService(quota_engine)

    admitted = service.admit_turn(
        _command("turn-workspace-overdraft", estimated_micro=10),
        role_codes=("student",),
        now=NOW,
    )
    service.settle_usage(
        reservation_id=admitted.reservation_id,
        operation_id="operation-workspace-overdraft",
        credits_micro=110,
        usage_status="exact",
        now=NOW + timedelta(seconds=1),
    )

    with quota_engine.connect() as connection:
        rows = connection.execute(
            select(QuotaBucketModel.__table__).order_by(QuotaBucketModel.owner_type)
        ).mappings().all()
    assert rows[0]["owner_type"] == "user"
    assert rows[0]["over_limit"] is True
    assert rows[1]["owner_type"] == "workspace"
    assert rows[1]["over_limit"] is False


def test_classroom_grant_is_reserved_in_a_shared_classroom_bucket(quota_engine):
    user_policy = _policy(quota_engine, code="user", version="1", daily=100, weekly=None)
    classroom_policy = _policy(quota_engine, code="classroom", version="1", daily=100, weekly=None)
    _bind(quota_engine, subject_type="role", subject_id="student", policy_id=user_policy)
    _bind(quota_engine, subject_type="classroom", subject_id="classroom-1", policy_id=classroom_policy)
    QuotaManagementService(quota_engine).create_grant(
        owner_type="classroom",
        owner_id="classroom-1",
        bucket_type="daily",
        period_start=NOW.replace(hour=0),
        period_end=NOW.replace(hour=0) + timedelta(days=1),
        allocated_micro=50,
        source_type="grant",
        created_by="developer-1",
        reason="classroom allocation",
        idempotency_key="classroom-grant-1",
        effective_from=NOW,
    )

    service = QuotaService(quota_engine)
    admitted = service.admit_turn(
        _command("classroom-turn", estimated_micro=40),
        role_codes=("student",),
        classroom_ids=("classroom-1",),
        now=NOW,
    )

    with quota_engine.connect() as connection:
        buckets = connection.execute(select(QuotaBucketModel.__table__)).mappings().all()
    assert {row["owner_type"] for row in buckets} == {"user", "classroom"}
    classroom = service.snapshot(
        user_id="user-1",
        workspace_id="workspace-1",
        classroom_ids=("classroom-1",),
        now=NOW,
    )
    classroom_bucket = next(item for item in classroom["buckets"] if item["owner_type"] == "classroom")
    assert classroom_bucket["grant_micro"] == 50
    assert classroom_bucket["reserved_micro"] == 40
    assert admitted.allowed is True


def test_classroom_capacity_without_policy_fails_closed(quota_engine):
    user_policy = _policy(quota_engine, code="user", version="1", daily=100, weekly=None)
    _bind(quota_engine, subject_type="role", subject_id="student", policy_id=user_policy)
    QuotaManagementService(quota_engine).create_grant(
        owner_type="classroom",
        owner_id="classroom-without-policy",
        bucket_type="daily",
        period_start=NOW.replace(hour=0),
        period_end=NOW.replace(hour=0) + timedelta(days=1),
        allocated_micro=50,
        source_type="grant",
        created_by="developer-1",
        reason="must be bound before use",
        idempotency_key="classroom-grant-without-policy",
        effective_from=NOW,
    )

    with pytest.raises(QuotaRejectedError) as error:
        QuotaService(quota_engine).admit_turn(
            _command("classroom-without-policy-turn", estimated_micro=1),
            role_codes=("student",),
            classroom_ids=("classroom-without-policy",),
            now=NOW,
        )
    assert error.value.problem.code is QuotaErrorCode.ADMISSION_DENIED


def test_concurrent_same_grant_idempotency_returns_one_committed_result(quota_engine):
    # A file-backed SQLite database gives each worker a separate connection,
    # matching the cross-process race that MySQL row/unique constraints must handle.
    del quota_engine
    from sqlalchemy import create_engine

    database_path = Path(__file__).with_name("quota-concurrent-review.db")
    engine = create_engine(
        f"sqlite:///{database_path}",
        connect_args={"check_same_thread": False, "timeout": 10},
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
    ):
        model.__table__.create(engine)
    try:
        service = QuotaManagementService(engine)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(
                pool.map(
                    lambda _: service.create_grant(**_management_grant_input(idempotency_key="race-grant")),
                    range(2),
                )
            )
        assert results[0]["grant_id"] == results[1]["grant_id"]
        adjustment_input = {
            "owner_type": "user",
            "owner_id": "user-1",
            "bucket_type": "daily",
            "period_start": NOW.replace(hour=0),
            "period_end": NOW.replace(hour=0) + timedelta(days=1),
            "amount_micro": 5,
            "actor_user_id": "developer-1",
            "reason": "concurrent adjustment",
            "idempotency_key": "race-adjustment",
        }
        with ThreadPoolExecutor(max_workers=2) as pool:
            adjustment_results = list(
                pool.map(
                    lambda _: service.create_adjustment(**adjustment_input),
                    range(2),
                )
            )
        assert adjustment_results[0]["adjustment_id"] == adjustment_results[1]["adjustment_id"]
        with engine.connect() as connection:
            assert connection.execute(select(func.count()).select_from(QuotaGrantModel)).scalar_one() == 1
            assert connection.execute(select(func.count()).select_from(QuotaAdjustmentModel)).scalar_one() == 1
    finally:
        engine.dispose()
        database_path.unlink(missing_ok=True)


def test_quota_mutations_publish_snapshot_notifications(quota_engine):
    policy_id = _policy(quota_engine, code="user", version="1", daily=100, weekly=None)
    _bind(quota_engine, subject_type="role", subject_id="student", policy_id=policy_id)
    notifications: list[tuple[str | None, str | None]] = []
    service = QuotaService(
        quota_engine,
        snapshot_notifier=lambda *, owner_type=None, owner_id=None: notifications.append((owner_type, owner_id)),
    )
    admitted = service.admit_turn(_command("notified-turn", estimated_micro=10), role_codes=("student",), now=NOW)
    service.settle_usage(
        reservation_id=admitted.reservation_id,
        operation_id="notified-operation",
        credits_micro=10,
        usage_status="exact",
        now=NOW + timedelta(seconds=1),
    )
    service.finish_turn(
        FinishTurn(reservation_id=admitted.reservation_id, turn_id="notified-turn", idempotency_key="notified-finish"),
        now=NOW + timedelta(seconds=2),
    )

    assert ("user", "user-1") in notifications
