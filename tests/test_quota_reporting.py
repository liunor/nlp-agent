from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, insert, select, text
from sqlalchemy.pool import StaticPool

from core.model_runtime.usage import (
    CanonicalTokenUsage,
    InvocationOutcome,
    ModelIdentity,
    ModelInvocation,
    UsageAttributionContext,
)
from server.quota.contracts import AdmitTurn, FinishTurn
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
from server.quota.reporting import (
    DurableModelUsageReporter,
    UsageEventConflictError,
)
from server.quota.usage import UsageReadService
from server.quota.service import QuotaService


def _invocation(*, operation_id: str | None = None) -> ModelInvocation:
    return ModelInvocation(
        operation_id=operation_id or str(uuid4()),
        identity=ModelIdentity(
            provider="provider-a",
            provider_model="model-a",
            model_profile="profile-a",
            preset="preset-a",
            route="coordinator",
            pricing_key="provider-a/model-a",
            context_window_tokens=100_000,
            max_output_tokens=8_000,
        ),
        attribution=UsageAttributionContext(
            request_id="req-1",
            user_id="user-1",
            workspace_id="workspace-1",
            conversation_id="conversation-1",
            turn_id="turn-1",
            reservation_id="reservation-1",
            worker_id="worker-1",
            parent_operation_id="parent-1",
            purpose="worker",
        ),
        attempt=2,
        fallback_index=1,
        started_at=datetime(2026, 8, 29, 8, 0, tzinfo=timezone.utc),
    )


def _outcome(*, status: str = "succeeded") -> InvocationOutcome:
    return InvocationOutcome(
        status=status,
        finish_reason="stop",
        completed_at=datetime(2026, 8, 29, 8, 1, tzinfo=timezone.utc),
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
    try:
        yield engine
    finally:
        engine.dispose()


def _insert_pricing_rule(engine) -> None:
    with engine.begin() as connection:
        connection.execute(
            insert(PricingRuleModel).values(
                id=str(uuid4()),
                pricing_key="provider-a/model-a",
                version="2026-08-29",
                effective_from=datetime(2026, 1, 1),
                effective_until=None,
                ordinary_input_credits_micro_per_million_tokens=3_000_000,
                cached_input_credits_micro_per_million_tokens=1_000_000,
                cache_write_credits_micro_per_million_tokens=2_000_000,
                output_credits_micro_per_million_tokens=4_000_000,
                reasoning_output_credits_micro_per_million_tokens=8_000_000,
                status="active",
                created_by="system",
                created_at=datetime(2026, 1, 1),
            )
        )


@pytest.mark.asyncio
async def test_durable_reporter_persists_exact_attempt_and_shadow_credits(quota_engine):
    _insert_pricing_rule(quota_engine)
    reporter = DurableModelUsageReporter(quota_engine)
    invocation = _invocation()
    usage = CanonicalTokenUsage(
        input_tokens=1_000_000,
        cached_input_tokens=100_000,
        cache_write_input_tokens=50_000,
        output_tokens=10_000,
        reasoning_output_tokens=2_000,
        total_tokens=1_010_000,
        source="provider",
        provider_response_id="provider-response-1",
    )

    await reporter.report(invocation, usage, _outcome())

    with quota_engine.connect() as connection:
        row = connection.execute(select(UsageEventModel.__table__)).mappings().one()
    assert row["operation_id"] == invocation.operation_id
    assert row["user_id"] == "user-1"
    assert row["purpose"] == "worker"
    assert row["attempt"] == 2
    assert row["fallback_index"] == 1
    assert row["usage_status"] == "exact"
    assert row["pricing_version"] == "2026-08-29"
    assert row["credits_micro"] == 2_798_000


@pytest.mark.asyncio
async def test_durable_reporter_is_idempotent_for_exact_replay(quota_engine):
    _insert_pricing_rule(quota_engine)
    reporter = DurableModelUsageReporter(quota_engine)
    invocation = _invocation()
    usage = CanonicalTokenUsage(
        input_tokens=20,
        output_tokens=5,
        total_tokens=25,
        source="provider",
    )

    await reporter.report(invocation, usage, _outcome())
    await reporter.report(invocation, usage, _outcome())

    with quota_engine.connect() as connection:
        rows = connection.execute(select(UsageEventModel.__table__)).mappings().all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_durable_reporter_rejects_conflicting_replay(quota_engine):
    _insert_pricing_rule(quota_engine)
    reporter = DurableModelUsageReporter(quota_engine)
    invocation = _invocation()
    first = CanonicalTokenUsage(
        input_tokens=20,
        output_tokens=5,
        total_tokens=25,
        source="provider",
    )
    second = CanonicalTokenUsage(
        input_tokens=21,
        output_tokens=5,
        total_tokens=26,
        source="provider",
    )

    await reporter.report(invocation, first, _outcome())

    with pytest.raises(UsageEventConflictError):
        await reporter.report(invocation, second, _outcome())


@pytest.mark.asyncio
async def test_durable_reporter_settles_provider_usage_once(quota_engine):
    _insert_pricing_rule(quota_engine)
    policy_id = str(uuid4())
    with quota_engine.begin() as connection:
        connection.execute(
            insert(QuotaPolicyModel).values(
                id=policy_id,
                code="student-default",
                version="2026-08-29.1",
                name="Student default",
                status="active",
                request_limit_micro=100,
                daily_limit_micro=1_000,
                weekly_limit_micro=10_000,
                concurrency_limit=2,
                max_overdraft_micro=0,
                allowed_model_profiles=["profile-a"],
                unlimited=False,
                effective_from=datetime(2026, 1, 1),
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
                priority=1,
                status="active",
                effective_from=datetime(2026, 1, 1),
                effective_until=None,
            )
        )
    service = QuotaService(quota_engine)
    admitted = service.admit_turn(
        AdmitTurn(
            request_id="req-1",
            user_id="user-1",
            workspace_id="workspace-1",
            turn_id="turn-1",
            model_profile="profile-a",
            model_role="coordinator",
            estimated_input_tokens=20,
            estimated_output_tokens=40,
            estimated_micro=60,
            idempotency_key="turn-1",
        ),
        role_codes=("student",),
        now=datetime(2026, 8, 29, 8, tzinfo=timezone.utc),
    )
    reporter = DurableModelUsageReporter(quota_engine, quota_service=service)
    base_invocation = _invocation()
    invocation = base_invocation.model_copy(
        update={
            "attribution": base_invocation.attribution.model_copy(
                update={"reservation_id": admitted.reservation_id}
            )
        }
    )
    usage = CanonicalTokenUsage(
        input_tokens=20,
        output_tokens=5,
        total_tokens=25,
        source="provider",
    )

    await reporter.report(invocation, usage, _outcome())
    await reporter.report(invocation, usage, _outcome())

    with quota_engine.connect() as connection:
        event_rows = connection.execute(select(UsageEventModel.__table__)).fetchall()
        settle_rows = connection.execute(
            select(QuotaLedgerEntryModel.__table__).where(
                QuotaLedgerEntryModel.entry_type == "settle"
            )
        ).fetchall()
        bucket = connection.execute(select(QuotaBucketModel.__table__).where(QuotaBucketModel.bucket_type == "daily")).mappings().one()
    assert len(event_rows) == 1
    assert len(settle_rows) == 2
    assert bucket["consumed_micro"] == 80
    assert bucket["reserved_micro"] == 0


@pytest.mark.asyncio
async def test_pending_usage_can_be_reconciled_after_turn_reservation_is_closed(quota_engine):
    policy_id = str(uuid4())
    with quota_engine.begin() as connection:
        connection.execute(
            insert(QuotaPolicyModel).values(
                id=policy_id,
                code="student-default",
                version="2026-08-29.1",
                name="Student default",
                status="active",
                request_limit_micro=100,
                daily_limit_micro=1_000,
                weekly_limit_micro=10_000,
                concurrency_limit=2,
                max_overdraft_micro=0,
                allowed_model_profiles=["profile-a"],
                unlimited=False,
                effective_from=datetime(2026, 1, 1),
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
                priority=1,
                status="active",
                effective_from=datetime(2026, 1, 1),
                effective_until=None,
            )
        )
    service = QuotaService(quota_engine)
    admitted = service.admit_turn(
        AdmitTurn(
            request_id="req-pending",
            user_id="user-1",
            workspace_id="workspace-1",
            turn_id="turn-pending",
            model_profile="profile-a",
            model_role="coordinator",
            estimated_input_tokens=20,
            estimated_output_tokens=40,
            estimated_micro=60,
            idempotency_key="turn-pending",
        ),
        role_codes=("student",),
        now=datetime(2026, 8, 29, 8, tzinfo=timezone.utc),
    )
    reporter = DurableModelUsageReporter(quota_engine, quota_service=service)
    invocation = _invocation().model_copy(
        update={
            "operation_id": str(uuid4()),
            "attribution": _invocation().attribution.model_copy(
                update={"reservation_id": admitted.reservation_id}
            ),
        }
    )
    usage = CanonicalTokenUsage(
        input_tokens=20,
        output_tokens=5,
        total_tokens=25,
        source="provider",
    )

    service.finish_turn(
        FinishTurn(
            reservation_id=admitted.reservation_id,
            turn_id="turn-pending",
            idempotency_key="turn-finished-before-reconcile",
        ),
        now=datetime(2026, 8, 29, 8, 2, tzinfo=timezone.utc),
    )
    await reporter.report(invocation, usage, _outcome())
    _insert_pricing_rule(quota_engine)

    await reporter.report(invocation, usage, _outcome())
    await reporter.report(invocation, usage, _outcome())

    with quota_engine.connect() as connection:
        event = connection.execute(select(UsageEventModel.__table__)).mappings().one()
        bucket = connection.execute(
            select(QuotaBucketModel.__table__).where(
                QuotaBucketModel.bucket_type == "daily"
            )
        ).mappings().one()
        reconciliations = connection.execute(
            select(QuotaLedgerEntryModel.__table__).where(
                QuotaLedgerEntryModel.entry_type == "reconcile"
            )
        ).fetchall()
    assert event["usage_status"] == "exact"
    assert event["credits_micro"] == 80
    assert bucket["consumed_micro"] == 80
    assert len(reconciliations) == 2


@pytest.mark.asyncio
async def test_unknown_provider_usage_is_recorded_without_being_priced_as_free(quota_engine):
    reporter = DurableModelUsageReporter(quota_engine)
    invocation = _invocation()

    await reporter.report(
        invocation,
        CanonicalTokenUsage(source="none"),
        _outcome(status="failed"),
    )

    with quota_engine.connect() as connection:
        row = connection.execute(select(UsageEventModel.__table__)).mappings().one()
    assert row["outcome_status"] == "failed"
    assert row["usage_source"] == "none"
    assert row["usage_status"] == "unavailable"
    assert row["credits_micro"] is None


@pytest.mark.asyncio
async def test_user_usage_snapshot_exposes_unpriced_events_without_zeroing_them(quota_engine):
    _insert_pricing_rule(quota_engine)
    reporter = DurableModelUsageReporter(quota_engine)
    first_invocation = _invocation()
    second_invocation = first_invocation.model_copy(
        update={
            "operation_id": str(uuid4()),
            "identity": first_invocation.identity.model_copy(
                update={"pricing_key": "provider-a/unconfigured-model"}
            ),
        }
    )
    usage = CanonicalTokenUsage(
        input_tokens=20,
        output_tokens=5,
        total_tokens=25,
        source="provider",
    )
    await reporter.report(first_invocation, usage, _outcome())
    await reporter.report(second_invocation, usage, _outcome())

    snapshot = UsageReadService(quota_engine).user_snapshot(
        "user-1",
        days=2,
        now=datetime(2026, 8, 30, tzinfo=timezone.utc),
    )

    assert snapshot["events"] == 2
    assert snapshot["priced_events"] == 1
    assert snapshot["unpriced_events"] == 1
    assert snapshot["credits_complete"] is False
    assert snapshot["credit_status"] == "partial"
    assert snapshot["credits_micro"] is None
    assert snapshot["priced_credits_micro"] == 80
    assert snapshot["tokens"]["input_tokens"] == 40
    assert snapshot["tokens"]["output_tokens"] == 10
    assert snapshot["tokens"]["total_tokens"] == 50


@pytest.mark.asyncio
async def test_user_usage_snapshot_can_be_scoped_to_one_workspace(quota_engine):
    _insert_pricing_rule(quota_engine)
    reporter = DurableModelUsageReporter(quota_engine)
    base = _invocation()
    other_workspace = base.model_copy(
        update={
            "operation_id": str(uuid4()),
            "attribution": base.attribution.model_copy(update={"workspace_id": "workspace-2"}),
        }
    )
    usage = CanonicalTokenUsage(input_tokens=20, output_tokens=5, total_tokens=25, source="provider")
    await reporter.report(base, usage, _outcome())
    await reporter.report(other_workspace, usage, _outcome())

    snapshot = UsageReadService(quota_engine).user_snapshot(
        "user-1",
        workspace_id="workspace-1",
        days=2,
        now=datetime(2026, 8, 30, tzinfo=timezone.utc),
    )

    assert snapshot["workspace_id"] == "workspace-1"
    assert snapshot["events"] == 1
    assert snapshot["tokens"]["total_tokens"] == 25


@pytest.mark.asyncio
async def test_user_usage_snapshot_can_aggregate_token_activity_by_week(quota_engine):
    _insert_pricing_rule(quota_engine)
    reporter = DurableModelUsageReporter(quota_engine)
    base = _invocation()
    monday = base.model_copy(
        update={
            "operation_id": str(uuid4()),
            "started_at": datetime(2026, 8, 24, 8, tzinfo=timezone.utc),
        }
    )
    sunday = base.model_copy(
        update={
            "operation_id": str(uuid4()),
            "started_at": datetime(2026, 8, 30, 8, tzinfo=timezone.utc),
        }
    )
    usage = CanonicalTokenUsage(input_tokens=20, output_tokens=5, total_tokens=25, source="provider")
    await reporter.report(monday, usage, _outcome())
    await reporter.report(sunday, usage, _outcome())

    snapshot = UsageReadService(quota_engine).user_snapshot(
        "user-1",
        days=14,
        granularity="week",
        now=datetime(2026, 8, 31, tzinfo=timezone.utc),
    )

    assert snapshot["granularity"] == "week"
    assert len(snapshot["breakdown"]) == 1
    assert snapshot["breakdown"][0]["period_start"] == "2026-08-24T00:00:00+00:00"
    assert snapshot["breakdown"][0]["period_end"] == "2026-08-31T00:00:00+00:00"
    assert snapshot["breakdown"][0]["total_tokens"] == 50


@pytest.mark.asyncio
async def test_shadow_comparison_matches_model_span_by_operation_id(quota_engine):
    _insert_pricing_rule(quota_engine)
    with quota_engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE nlp_observability_records ("
                "id VARCHAR(36) PRIMARY KEY, kind VARCHAR(16) NOT NULL, "
                "record_key VARCHAR(128) NOT NULL, trace_id VARCHAR(128), "
                "session_id VARCHAR(128), turn_id VARCHAR(128), "
                "status VARCHAR(32), payload_json JSON NOT NULL, "
                "created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL)"
            )
        )
    reporter = DurableModelUsageReporter(quota_engine)
    invocation = _invocation()
    usage = CanonicalTokenUsage(
        input_tokens=20,
        output_tokens=5,
        total_tokens=25,
        source="provider",
    )
    await reporter.report(invocation, usage, _outcome())
    with quota_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO nlp_observability_records "
                "(id,kind,record_key,status,payload_json,created_at,updated_at) "
                "VALUES (:id,'span',:key,'ok',:payload,:created_at,:updated_at)"
            ),
            {
                "id": str(uuid4()),
                "key": invocation.operation_id,
                "payload": (
                    '{"kind":"span","payload":{"kind":"model",'
                    f'"completed_at":"{_outcome().completed_at.isoformat()}",'
                    f'"attributes":{{"operation_id":"{invocation.operation_id}"}},'
                    '"usage":{"input_tokens":20,"output_tokens":5,"'
                    'total_tokens":25,"input_token_details":{},'
                    '"output_token_details":{}}}}'
                ),
                "created_at": datetime(2026, 8, 29),
                "updated_at": datetime(2026, 8, 29),
            },
        )

    report = UsageReadService(quota_engine).shadow_comparison(
        days=2,
        now=datetime(2026, 8, 30, tzinfo=timezone.utc),
    )

    assert report["matched_attempts"] == 1
    assert report["exact_token_matches"] == 1
    assert report["missing_in_observability"] == 0
    assert report["missing_in_usage_events"] == 0
    assert report["token_delta"] == {
        "input_tokens": 0,
        "cached_input_tokens": 0,
        "cache_write_input_tokens": 0,
        "output_tokens": 0,
        "reasoning_output_tokens": 0,
        "total_tokens": 0,
    }
