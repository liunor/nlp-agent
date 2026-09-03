"""Unit tests for QuotaService (Milestone 3).

Tests core admission, dynamic additional reservation, settlement,
reservation release, concurrency enforcement, and reporter integration.
"""

from __future__ import annotations

from datetime import datetime, timezone
import uuid

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.pool import StaticPool

from core.model_runtime.usage import (
    CanonicalTokenUsage,
    InvocationOutcome,
    ModelIdentity,
    ModelInvocation,
    UsageAttributionContext,
)
from server.quota.contracts import (
    AdmitTurn,
    FinishTurn,
    PolicyBinding,
    QuotaGrant,
    QuotaPolicy,
)
from server.quota.errors import QuotaErrorCode, QuotaRejectedError
from server.quota.models import (
    PricingRuleModel,
    QuotaLedgerEntryModel,
    QuotaReservationModel,
)
from server.quota.reporting import DurableModelUsageReporter
from server.quota.service import QuotaService

UTC = timezone.utc
AT_START = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)


@pytest.fixture
def standalone_sqlite_engine():
    """In-memory SQLite engine with StaticPool."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    yield engine
    engine.dispose()


@pytest.fixture
def service(standalone_sqlite_engine):
    """QuotaService instance with auto-created tables."""
    return QuotaService(standalone_sqlite_engine)


def _seed_pricing_rule(engine, pricing_key: str = "deepseek/deepseek-v4-pro") -> None:
    with engine.begin() as conn:
        conn.execute(
            PricingRuleModel.__table__.insert().values(
                id=str(uuid.uuid4()),
                pricing_key=pricing_key,
                version="2026-09-01",
                effective_from=datetime(2026, 1, 1, tzinfo=UTC),
                effective_until=None,
                ordinary_input_credits_micro_per_million_tokens=2_000_000,
                cached_input_credits_micro_per_million_tokens=500_000,
                cache_write_credits_micro_per_million_tokens=1_000_000,
                output_credits_micro_per_million_tokens=8_000_000,
                reasoning_output_credits_micro_per_million_tokens=6_000_000,
                status="active",
                created_by="admin",
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        )


def test_quota_service_initialization(standalone_sqlite_engine):
    """QuotaService accepts Engine and ensures table creation."""
    svc = QuotaService(standalone_sqlite_engine)
    assert svc.engine is standalone_sqlite_engine
    with standalone_sqlite_engine.connect() as conn:
        res = conn.execute(
            text("select name from sqlite_master where type='table' and name='nlp_quota_reservations'")
        ).scalar()
        assert res == "nlp_quota_reservations"


def test_admit_turn_success_positive_balance(service, standalone_sqlite_engine):
    """Feature 12: Standard turn admission succeeds and creates active reservation."""
    _seed_pricing_rule(standalone_sqlite_engine)
    cmd = AdmitTurn(
        request_id="req-unit-1",
        user_id="user-unit-1",
        turn_id="turn-unit-1",
        model_profile="deepseek",
        model_role="coordinator",
        estimated_input_tokens=200,
        estimated_output_tokens=100,
        idempotency_key="idem-unit-1",
    )
    res = service.admit_turn(cmd)
    assert res.allowed is True
    assert res.reservation_id is not None
    assert res.reserved_micro > 0

    with standalone_sqlite_engine.connect() as conn:
        row = conn.execute(
            select(QuotaReservationModel.__table__).where(QuotaReservationModel.id == res.reservation_id)
        ).mappings().one()
        assert row["status"] == "reserved"
        assert row["user_id"] == "user-unit-1"


def test_admit_turn_conservative_estimation_fallback(service, standalone_sqlite_engine):
    """Feature 13: None estimated_input_tokens falls back to conservative floor, never 0."""
    _seed_pricing_rule(standalone_sqlite_engine)
    cmd = AdmitTurn(
        request_id="req-unit-2",
        user_id="user-unit-2",
        turn_id="turn-unit-2",
        model_profile="deepseek",
        model_role="coordinator",
        estimated_input_tokens=None,
        estimated_output_tokens=500,
        idempotency_key="idem-unit-2",
    )
    res = service.admit_turn(cmd)
    assert res.allowed is True
    assert res.reserved_micro > 0


def test_admit_turn_idempotent_duplicate_call(service, standalone_sqlite_engine):
    """Admitting the same turn_id returns duplicate=True with identical reservation_id."""
    cmd = AdmitTurn(
        request_id="req-unit-3",
        user_id="user-unit-3",
        turn_id="turn-unit-3",
        model_profile="deepseek",
        model_role="coordinator",
        estimated_input_tokens=100,
        estimated_output_tokens=100,
        idempotency_key="idem-unit-3",
    )
    res1 = service.admit_turn(cmd)
    res2 = service.admit_turn(cmd)
    assert res1.reservation_id == res2.reservation_id
    assert res2.duplicate is True


def test_admit_turn_concurrency_limit_enforced(service, standalone_sqlite_engine):
    """Feature 12: Exceeding policy concurrency_limit raises CONCURRENCY_LIMIT."""
    policy = QuotaPolicy(
        policy_id="policy-conc-1",
        code="conc-strict",
        version="1",
        concurrency_limit=1,
        allowed_model_profiles=("deepseek",),
    )
    binding = PolicyBinding(
        subject_type="user",
        subject_id="user-conc",
        policy=policy,
        priority=10,
        effective_from=datetime(2026, 1, 1, tzinfo=UTC),
    )
    service.create_policy(policy)
    service.bind_policy(binding)

    cmd1 = AdmitTurn(
        request_id="req-c1",
        user_id="user-conc",
        turn_id="turn-c1",
        model_profile="deepseek",
        model_role="coordinator",
        estimated_input_tokens=100,
        estimated_output_tokens=100,
        idempotency_key="idem-c1",
    )
    res1 = service.admit_turn(cmd1)
    assert res1.allowed is True

    cmd2 = AdmitTurn(
        request_id="req-c2",
        user_id="user-conc",
        turn_id="turn-c2",
        model_profile="deepseek",
        model_role="coordinator",
        estimated_input_tokens=100,
        estimated_output_tokens=100,
        idempotency_key="idem-c2",
    )
    with pytest.raises(QuotaRejectedError, match="quota_concurrency_limit"):
        service.admit_turn(cmd2)


def test_admit_turn_daily_bucket_exhausted(service, standalone_sqlite_engine):
    """Feature 16: Zero balance daily bucket raises quota_daily_exhausted."""
    with standalone_sqlite_engine.begin() as conn:
        conn.execute(
            text(
                "insert into nlp_quota_buckets (id, owner_type, owner_id, bucket_type, balance_micro) "
                "values ('b-unit', 'user', 'user-empty', 'daily', 0)"
            )
        )
    cmd = AdmitTurn(
        request_id="req-empty",
        user_id="user-empty",
        turn_id="turn-empty",
        model_profile="deepseek",
        model_role="coordinator",
        estimated_input_tokens=100,
        estimated_output_tokens=100,
        idempotency_key="idem-empty",
    )
    with pytest.raises(QuotaRejectedError, match="quota_daily_exhausted"):
        service.admit_turn(cmd)


def test_reserve_additional_and_ledger_entry(service, standalone_sqlite_engine):
    """Feature 14: Dynamic additional reservation records reserve_increment in ledger."""
    cmd = AdmitTurn(
        request_id="req-add-1",
        user_id="user-add-1",
        turn_id="turn-add-1",
        model_profile="deepseek",
        model_role="coordinator",
        estimated_input_tokens=100,
        estimated_output_tokens=100,
        idempotency_key="idem-add-1",
    )
    res = service.admit_turn(cmd)
    initial_reserved = res.reserved_micro

    service.reserve_additional(
        reservation_id=res.reservation_id,
        additional_micro=25_000,
        idempotency_key="tool-add-key-1",
    )

    with standalone_sqlite_engine.connect() as conn:
        res_row = conn.execute(
            select(QuotaReservationModel.__table__).where(QuotaReservationModel.id == res.reservation_id)
        ).mappings().one()
        assert res_row["reserved_micro"] == initial_reserved + 25_000

        ledger_rows = conn.execute(
            select(QuotaLedgerEntryModel.__table__).where(QuotaLedgerEntryModel.reservation_id == res.reservation_id)
        ).mappings().all()
        assert any(r["entry_type"] == "reserve_increment" and r["amount_micro"] == 25_000 for r in ledger_rows)


def test_finish_turn_release_unused_reservation(service, standalone_sqlite_engine):
    """Feature 15: finish_turn releases remaining reserved credits and sets completed status."""
    cmd = AdmitTurn(
        request_id="req-fin-1",
        user_id="user-fin-1",
        turn_id="turn-fin-1",
        model_profile="deepseek",
        model_role="coordinator",
        estimated_input_tokens=100,
        estimated_output_tokens=100,
        idempotency_key="idem-fin-1",
    )
    res = service.admit_turn(cmd)
    service.finish_turn(FinishTurn(reservation_id=res.reservation_id, status="completed"))

    with standalone_sqlite_engine.connect() as conn:
        row = conn.execute(
            select(QuotaReservationModel.__table__).where(QuotaReservationModel.id == res.reservation_id)
        ).mappings().one()
        assert row["status"] == "completed"
        assert row["reserved_micro"] == 0

        ledger_rows = conn.execute(
            select(QuotaLedgerEntryModel.__table__).where(QuotaLedgerEntryModel.reservation_id == res.reservation_id)
        ).mappings().all()
        assert any(r["entry_type"] == "release" for r in ledger_rows)


@pytest.mark.asyncio
async def test_reporter_integration_and_settlement(service, standalone_sqlite_engine):
    """Features 5, 15: DurableModelUsageReporter coordinates settlement with QuotaService."""
    _seed_pricing_rule(standalone_sqlite_engine)
    reporter = DurableModelUsageReporter(standalone_sqlite_engine, quota_service=service)

    cmd = AdmitTurn(
        request_id="req-rep-1",
        user_id="user-rep-1",
        turn_id="turn-rep-1",
        model_profile="deepseek",
        model_role="coordinator",
        estimated_input_tokens=100,
        estimated_output_tokens=100,
        idempotency_key="idem-rep-1",
    )
    res = service.admit_turn(cmd)

    inv = ModelInvocation(
        operation_id=str(uuid.uuid4()),
        identity=ModelIdentity(
            provider="deepseek",
            provider_model="deepseek-v4-pro",
            model_profile="deepseek",
            preset="coordinator-pro",
            route="coordinator",
            pricing_key="deepseek/deepseek-v4-pro",
        ),
        attribution=UsageAttributionContext(
            request_id="req-rep-1",
            user_id="user-rep-1",
            turn_id="turn-rep-1",
            reservation_id=res.reservation_id,
            purpose="coordinator",
        ),
        attempt=1,
        fallback_index=0,
        started_at=datetime.now(UTC),
    )
    usage = CanonicalTokenUsage(
        input_tokens=1000,
        output_tokens=500,
        total_tokens=1500,
        source="provider",
    )
    outcome = InvocationOutcome(
        status="succeeded",
        finish_reason="stop",
        completed_at=datetime.now(UTC),
    )

    await reporter.report(inv, usage, outcome)

    with standalone_sqlite_engine.connect() as conn:
        res_row = conn.execute(
            select(QuotaReservationModel.__table__).where(QuotaReservationModel.id == res.reservation_id)
        ).mappings().one()
        assert res_row["settled_micro"] > 0

        ledger = conn.execute(
            select(QuotaLedgerEntryModel.__table__).where(
                QuotaLedgerEntryModel.reservation_id == res.reservation_id,
                QuotaLedgerEntryModel.entry_type == "settle",
            )
        ).mappings().one()
        assert ledger["consumed_delta_micro"] > 0


def test_get_balance_calculation(service, standalone_sqlite_engine):
    """Querying get_balance returns accurate allocation and balance."""
    grant = QuotaGrant(
        grant_id="grant-bal-1",
        owner_type="user",
        owner_id="user-bal-1",
        source_type="grant",
        allocated_micro=500_000,
        effective_from=datetime(2026, 1, 1, tzinfo=UTC),
        created_by="admin",
        idempotency_key="grant-bal-key-1",
    )
    service.create_grant(grant)
    balance = service.get_balance("user-bal-1")
    assert balance.allocated_micro == 500_000
    assert balance.available_micro == 500_000
