"""Opt-in MySQL integration coverage for the Phase 2 accounting seam."""

from __future__ import annotations

import asyncio
import os
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, delete, insert, select, text

from core.model_runtime.usage import (
    BillableFeatureUsage,
    CanonicalTokenUsage,
    InvocationOutcome,
    ModelIdentity,
    ModelInvocation,
    UsageAttributionContext,
)
from server.quota.contracts import AdmitTurn
from server.quota.errors import QuotaRejectedError
from server.quota.models import (
    PolicyBindingModel,
    PricingRuleModel,
    QuotaBucketModel,
    QuotaConcurrencyLockModel,
    QuotaCreditOperationModel,
    QuotaCreditScopeLockModel,
    QuotaDailyRollupModel,
    QuotaGrantModel,
    QuotaLedgerEntryModel,
    QuotaPolicyModel,
    QuotaProviderBillingModel,
    QuotaRoleCreditOperationModel,
    QuotaReservationModel,
    QuotaUsageArchiveBatchModel,
    QuotaAlertModel,
    UsageEventModel,
)
from server.quota.operations import QuotaOperationsService
from server.quota.reporting import DurableModelUsageReporter
from server.quota.management import QuotaManagementService
from server.quota.service import QuotaService


NOW = datetime(2026, 8, 30, 8, 0, tzinfo=timezone.utc)


def _mysql_dsn() -> str:
    value = os.getenv("NLP_AGENT_DATABASE_URL", "").strip()
    if not value.startswith("mysql+aiomysql://"):
        pytest.skip("NLP_AGENT_DATABASE_URL must point to the integration MySQL")
    return value


def _process_admission(args: tuple[str, dict]) -> bool:
    dsn, payload = args
    service = QuotaService(dsn, lease_seconds=60)
    try:
        command = AdmitTurn.model_validate(payload)
        service.admit_turn(command, role_codes=("student",), now=NOW)
        return True
    except QuotaRejectedError:
        return False
    finally:
        service.close()


def test_mysql_phase2_schema_contains_counter_and_accounting_constraints():
    dsn = _mysql_dsn()
    engine = create_engine(dsn.replace("mysql+aiomysql://", "mysql+pymysql://"))
    try:
        QuotaService(engine).verify_schema()
        with engine.connect() as connection:
            columns = {
                row[0]
                for row in connection.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema = DATABASE() "
                        "AND table_name = 'nlp_quota_concurrency_locks'"
                    )
                )
            }
            unique_constraints = {
                row[0]
                for row in connection.execute(
                    text(
                        "SELECT constraint_name FROM information_schema.table_constraints "
                        "WHERE table_schema = DATABASE() "
                        "AND table_name = 'nlp_quota_ledger_entries' "
                        "AND constraint_type = 'UNIQUE'"
                    )
                )
            }
        assert {"user_id", "active_units", "version"} <= columns
        assert "uq_nlp_quota_ledger_entries_idempotency_key" in unique_constraints
    finally:
        engine.dispose()


def test_mysql_phase4_schema_contains_operations_tables_and_archive_columns():
    dsn = _mysql_dsn()
    engine = create_engine(dsn.replace("mysql+aiomysql://", "mysql+pymysql://"))
    try:
        QuotaService(engine).verify_schema()
        operations_tables = {
            QuotaCreditOperationModel.__tablename__,
            QuotaRoleCreditOperationModel.__tablename__,
            QuotaCreditScopeLockModel.__tablename__,
            QuotaDailyRollupModel.__tablename__,
            QuotaProviderBillingModel.__tablename__,
            QuotaUsageArchiveBatchModel.__tablename__,
            QuotaAlertModel.__tablename__,
        }
        with engine.connect() as connection:
            tables = {
                row[0]
                for row in connection.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema = DATABASE()"
                    )
                )
            }
            usage_columns = {
                row[0]
                for row in connection.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema = DATABASE() "
                        "AND table_name = 'nlp_usage_events'"
                    )
                )
            }
            pricing_columns = {
                row[0]
                for row in connection.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema = DATABASE() "
                        "AND table_name = 'nlp_pricing_rules'"
                    )
                )
            }
            credit_columns = {
                row[0]
                for row in connection.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema = DATABASE() "
                        "AND table_name = 'nlp_quota_credit_operations'"
                    )
                )
            }
            entry_type_length = connection.execute(
                text(
                    "SELECT character_maximum_length FROM information_schema.columns "
                    "WHERE table_schema = DATABASE() "
                    "AND table_name = 'nlp_quota_ledger_entries' "
                    "AND column_name = 'entry_type'"
                )
            ).scalar_one()
        assert operations_tables <= tables
        assert {"archived_at", "archive_batch_id"} <= usage_columns
        assert {
            "visual_input_tokens",
            "image_units",
            "search_calls",
            "link_pages",
        } <= usage_columns
        assert {
            "visual_input_credits_micro_per_million_tokens",
            "image_unit_credits_micro",
            "search_call_credits_micro",
            "link_page_credits_micro",
        } <= pricing_columns
        assert {"effective_from", "expires_at"} <= credit_columns
        assert entry_type_length >= 32
        assert QuotaOperationsService(engine).partition_strategy(
            start_year=2026, start_month=8, months=2
        )["partitions"] == [
            {"name": "p202608", "from": "2026-08-01", "to": "2026-09-01"},
            {"name": "p202609", "from": "2026-09-01", "to": "2026-10-01"},
        ]
    finally:
        engine.dispose()


def test_mysql_feature_hold_and_usage_event_settle_on_existing_reservation():
    dsn = _mysql_dsn()
    engine = create_engine(dsn.replace("mysql+aiomysql://", "mysql+pymysql://"))
    at = datetime.now(timezone.utc)
    policy_id = str(uuid4())
    user_id = f"feature-mysql-user-{uuid4()}"
    workspace_id = f"feature-mysql-workspace-{uuid4()}"
    turn_id = f"feature-mysql-turn-{uuid4()}"
    operation_id = str(uuid4())
    pricing_key = f"feature-mysql/search-{uuid4()}"
    reservation_id = None
    try:
        service = QuotaService(engine, lease_seconds=60)
        service.verify_schema()
        with engine.begin() as connection:
            connection.execute(
                insert(QuotaPolicyModel).values(
                    id=policy_id,
                    code=f"feature-mysql-{uuid4()}",
                    version="1",
                    name="Feature MySQL settlement",
                    status="active",
                    request_limit_micro=1_000,
                    daily_limit_micro=1_000,
                    weekly_limit_micro=None,
                    concurrency_limit=2,
                    max_overdraft_micro=0,
                    allowed_model_profiles=["economy"],
                    unlimited=False,
                    effective_from=at,
                    effective_until=None,
                    created_by="feature-integration",
                )
            )
            connection.execute(
                insert(PolicyBindingModel).values(
                    id=str(uuid4()),
                    subject_type="user",
                    subject_id=user_id,
                    policy_id=policy_id,
                    priority=1_000,
                    status="active",
                    effective_from=at,
                    effective_until=None,
                )
            )
            connection.execute(
                insert(PricingRuleModel).values(
                    id=str(uuid4()),
                    pricing_key=pricing_key,
                    version="1",
                    effective_from=at,
                    effective_until=None,
                    ordinary_input_credits_micro_per_million_tokens=0,
                    cached_input_credits_micro_per_million_tokens=0,
                    cache_write_credits_micro_per_million_tokens=0,
                    output_credits_micro_per_million_tokens=0,
                    reasoning_output_credits_micro_per_million_tokens=None,
                    visual_input_credits_micro_per_million_tokens=0,
                    image_unit_credits_micro=0,
                    search_call_credits_micro=25,
                    link_page_credits_micro=0,
                    status="active",
                    created_by="feature-integration",
                    created_at=at,
                )
            )
        admitted = service.admit_turn(
            AdmitTurn(
                request_id=f"request-{turn_id}",
                user_id=user_id,
                workspace_id=workspace_id,
                turn_id=turn_id,
                model_profile="economy",
                model_role="coordinator",
                estimated_input_tokens=0,
                estimated_output_tokens=0,
                estimated_micro=10,
                pricing_key=pricing_key,
                idempotency_key=f"idempotency-{turn_id}",
            ),
            now=at,
        )
        reservation_id = admitted.reservation_id
        invocation = ModelInvocation(
            operation_id=operation_id,
            identity=ModelIdentity(
                provider="feature-mysql",
                provider_model="search",
                model_profile="economy",
                preset="search",
                pricing_key=pricing_key,
            ),
            attribution=UsageAttributionContext(
                request_id=f"request-{turn_id}",
                user_id=user_id,
                workspace_id=workspace_id,
                turn_id=turn_id,
                reservation_id=reservation_id,
                purpose="worker",
            ),
            attempt=1,
            fallback_index=0,
            started_at=at,
            feature_usage=BillableFeatureUsage(search_calls=1),
        )
        reporter = DurableModelUsageReporter(engine, quota_service=service)
        asyncio.run(reporter.reserve_feature_usage(invocation))
        asyncio.run(
            reporter.report(
                invocation,
                CanonicalTokenUsage(source="provider"),
                InvocationOutcome(status="succeeded", completed_at=at),
            )
        )
        asyncio.run(
            reporter.report(
                invocation,
                CanonicalTokenUsage(source="provider"),
                InvocationOutcome(status="succeeded", completed_at=at),
            )
        )

        with engine.connect() as connection:
            event = connection.execute(
                select(UsageEventModel.__table__).where(
                    UsageEventModel.operation_id == operation_id
                )
            ).mappings().one()
            reservation = connection.execute(
                select(QuotaReservationModel.__table__).where(
                    QuotaReservationModel.id == reservation_id
                )
            ).mappings().one()
            bucket = connection.execute(
                select(QuotaBucketModel.__table__).where(
                    QuotaBucketModel.owner_id == user_id,
                    QuotaBucketModel.bucket_type == "daily",
                )
            ).mappings().one()
        assert event["search_calls"] == 1
        assert event["credits_micro"] == 25
        assert reservation["reserved_micro"] == 10
        assert reservation["settled_micro"] == 25
        assert bucket["consumed_micro"] == 25
    finally:
        with engine.begin() as connection:
            connection.execute(
                delete(UsageEventModel).where(
                    UsageEventModel.operation_id == operation_id
                )
            )
            if reservation_id is not None:
                connection.execute(
                    delete(QuotaLedgerEntryModel).where(
                        QuotaLedgerEntryModel.reservation_id == reservation_id
                    )
                )
                connection.execute(
                    delete(QuotaReservationModel).where(
                        QuotaReservationModel.id == reservation_id
                    )
                )
            connection.execute(
                delete(QuotaBucketModel).where(QuotaBucketModel.owner_id == user_id)
            )
            connection.execute(
                delete(QuotaConcurrencyLockModel).where(
                    QuotaConcurrencyLockModel.user_id == user_id
                )
            )
            connection.execute(
                delete(PolicyBindingModel).where(
                    PolicyBindingModel.policy_id == policy_id
                )
            )
            connection.execute(
                delete(QuotaPolicyModel).where(QuotaPolicyModel.id == policy_id)
            )
            connection.execute(
                delete(PricingRuleModel).where(
                    PricingRuleModel.pricing_key == pricing_key
                )
            )
        engine.dispose()


def test_mysql_twenty_processes_cannot_breach_one_user_concurrency_slot():
    dsn = _mysql_dsn()
    engine = create_engine(dsn.replace("mysql+aiomysql://", "mysql+pymysql://"))
    policy_id = str(uuid4())
    user_id = f"phase2-mysql-{uuid4()}"
    try:
        QuotaService(engine).verify_schema()
        with engine.begin() as connection:
            connection.execute(
                insert(QuotaPolicyModel).values(
                    id=policy_id,
                    code=f"phase2-mysql-{uuid4()}",
                    version="1",
                    name="Phase 2 MySQL concurrency test",
                    status="active",
                    request_limit_micro=10_000,
                    daily_limit_micro=10_000,
                    weekly_limit_micro=10_000,
                    concurrency_limit=1,
                    max_overdraft_micro=0,
                    allowed_model_profiles=["economy"],
                    unlimited=False,
                    effective_from=NOW,
                    effective_until=None,
                    created_by="phase2-integration",
                )
            )
            connection.execute(
                insert(PolicyBindingModel).values(
                    id=str(uuid4()),
                    subject_type="role",
                    subject_id="student",
                    policy_id=policy_id,
                    priority=100,
                    status="active",
                    effective_from=NOW,
                    effective_until=None,
                )
            )
        payloads = [
            {
                "request_id": f"phase2-request-{index}-{uuid4()}",
                "user_id": user_id,
                "workspace_id": "phase2-integration",
                "turn_id": f"phase2-turn-{index}-{uuid4()}",
                "model_profile": "economy",
                "model_role": "coordinator",
                "estimated_input_tokens": 1,
                "estimated_output_tokens": 1,
                "estimated_micro": 1,
                "idempotency_key": f"phase2-idempotency-{index}-{uuid4()}",
            }
            for index in range(20)
        ]
        with ProcessPoolExecutor(max_workers=20) as pool:
            outcomes = list(pool.map(_process_admission, [(dsn, payload) for payload in payloads]))

        assert sum(outcomes) == 1
        with engine.connect() as connection:
            lock = connection.execute(
                select(QuotaConcurrencyLockModel.__table__).where(
                    QuotaConcurrencyLockModel.user_id == user_id
                )
            ).mappings().one()
        assert lock["active_units"] == 1
    finally:
        with engine.begin() as connection:
            reservation_ids = select(QuotaReservationModel.id).where(
                QuotaReservationModel.user_id == user_id
            )
            connection.execute(
                delete(QuotaLedgerEntryModel).where(
                    QuotaLedgerEntryModel.reservation_id.in_(reservation_ids)
                )
            )
            connection.execute(
                delete(QuotaReservationModel).where(
                    QuotaReservationModel.user_id == user_id
                )
            )
            connection.execute(
                delete(QuotaBucketModel).where(QuotaBucketModel.owner_id == user_id)
            )
            connection.execute(
                delete(QuotaConcurrencyLockModel).where(
                    QuotaConcurrencyLockModel.user_id == user_id
                )
            )
            connection.execute(
                delete(PolicyBindingModel).where(PolicyBindingModel.policy_id == policy_id)
            )
            connection.execute(delete(QuotaPolicyModel).where(QuotaPolicyModel.id == policy_id))
        engine.dispose()


def test_mysql_two_users_are_billed_separately_and_share_workspace_budget():
    """Exercise exact billing, grant isolation, and shared workspace accounting."""
    dsn = _mysql_dsn()
    engine = create_engine(dsn.replace("mysql+aiomysql://", "mysql+pymysql://"))
    policy_ids = [str(uuid4()) for _ in range(3)]
    role_policy_id, user_b_policy_id, workspace_policy_id = policy_ids
    user_a = f"phase4-mysql-user-a-{uuid4()}"
    user_b = f"phase4-mysql-user-b-{uuid4()}"
    workspace_id = f"phase4-mysql-workspace-{uuid4()}"
    pricing_key = f"phase4-mysql/model-{uuid4()}"
    turn_ids = [f"phase4-turn-{uuid4()}" for _ in range(2)]
    operation_ids = [str(uuid4()) for _ in range(2)]
    reservation_ids: list[str] = []
    grant_id: str | None = None
    try:
        service = QuotaService(engine, lease_seconds=60)
        service.verify_schema()
        with engine.begin() as connection:
            for policy_id, code, daily_limit in (
                (role_policy_id, "phase4-role", 1_000),
                (user_b_policy_id, "phase4-user-b", 10_000),
                (workspace_policy_id, "phase4-workspace", 20_000),
            ):
                connection.execute(
                    insert(QuotaPolicyModel).values(
                        id=policy_id,
                        code=f"{code}-{uuid4()}",
                        version="1",
                        name=code,
                        status="active",
                        request_limit_micro=10_000,
                        daily_limit_micro=daily_limit,
                        weekly_limit_micro=None,
                        concurrency_limit=2,
                        max_overdraft_micro=0,
                        allowed_model_profiles=["economy"],
                        unlimited=False,
                        effective_from=NOW,
                        effective_until=None,
                        created_by="phase4-integration",
                    )
                )
            connection.execute(
                insert(PolicyBindingModel),
                [
                    {
                        "id": str(uuid4()),
                        "subject_type": "role",
                        "subject_id": "student",
                        "policy_id": role_policy_id,
                        "priority": 100,
                        "status": "active",
                        "effective_from": NOW,
                        "effective_until": None,
                    },
                    {
                        "id": str(uuid4()),
                        "subject_type": "user",
                        "subject_id": user_b,
                        "policy_id": user_b_policy_id,
                        "priority": 200,
                        "status": "active",
                        "effective_from": NOW,
                        "effective_until": None,
                    },
                    {
                        "id": str(uuid4()),
                        "subject_type": "workspace",
                        "subject_id": workspace_id,
                        "policy_id": workspace_policy_id,
                        "priority": 100,
                        "status": "active",
                        "effective_from": NOW,
                        "effective_until": None,
                    },
                ],
            )
            connection.execute(
                insert(PricingRuleModel).values(
                    id=str(uuid4()),
                    pricing_key=pricing_key,
                    version="1",
                    effective_from=NOW,
                    effective_until=None,
                    ordinary_input_credits_micro_per_million_tokens=1_000_000,
                    cached_input_credits_micro_per_million_tokens=0,
                    cache_write_credits_micro_per_million_tokens=0,
                    output_credits_micro_per_million_tokens=2_000_000,
                    reasoning_output_credits_micro_per_million_tokens=None,
                    status="active",
                    created_by="phase4-integration",
                    created_at=NOW,
                )
            )

        grant = QuotaManagementService(engine).create_grant(
            owner_type="user",
            owner_id=user_a,
            bucket_type="daily",
            period_start=NOW.replace(hour=0),
            period_end=NOW.replace(hour=0) + timedelta(days=1),
            allocated_micro=5_000,
            source_type="grant",
            created_by="developer-1",
            reason="Phase 4 MySQL multi-user grant isolation",
            idempotency_key=f"phase4-grant-{uuid4()}",
            effective_from=NOW,
        )
        grant_id = grant["grant_id"]

        def admit(user_id: str, turn_id: str, estimate: int):
            return service.admit_turn(
                AdmitTurn(
                    request_id=f"request-{turn_id}",
                    user_id=user_id,
                    workspace_id=workspace_id,
                    turn_id=turn_id,
                    model_profile="economy",
                    model_role="coordinator",
                    estimated_input_tokens=1,
                    estimated_output_tokens=1,
                    estimated_micro=estimate,
                    idempotency_key=f"idempotency-{turn_id}",
                    pricing_key=pricing_key,
                ),
                role_codes=("student",),
                now=NOW,
            )

        admitted_a = admit(user_a, turn_ids[0], 3_400)
        admitted_b = admit(user_b, turn_ids[1], 6_200)
        reservation_ids.extend([admitted_a.reservation_id, admitted_b.reservation_id])
        reporter = DurableModelUsageReporter(engine, quota_service=service)

        def report(user_id: str, turn_id: str, operation_id: str, reservation_id: str, input_tokens: int, output_tokens: int):
            invocation = ModelInvocation(
                operation_id=operation_id,
                identity=ModelIdentity(
                    provider="phase4-mysql",
                    provider_model="phase4-model",
                    model_profile="economy",
                    preset="economy",
                    route="coordinator",
                    pricing_key=pricing_key,
                ),
                attribution=UsageAttributionContext(
                    request_id=f"request-{turn_id}",
                    user_id=user_id,
                    workspace_id=workspace_id,
                    turn_id=turn_id,
                    reservation_id=reservation_id,
                    purpose="coordinator",
                ),
                attempt=1,
                fallback_index=0,
                started_at=NOW,
            )
            usage = CanonicalTokenUsage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=input_tokens + output_tokens,
                source="provider",
                semantics="final",
            )
            outcome = InvocationOutcome(status="succeeded", finish_reason="stop", completed_at=NOW)
            asyncio.run(reporter.report(invocation, usage, outcome))

        report(user_a, turn_ids[0], operation_ids[0], admitted_a.reservation_id, 1_000, 1_200)
        report(user_b, turn_ids[1], operation_ids[1], admitted_b.reservation_id, 2_000, 2_100)

        with engine.connect() as connection:
            events = connection.execute(
                select(UsageEventModel.__table__).where(
                    UsageEventModel.operation_id.in_(operation_ids)
                )
            ).mappings().all()
            buckets = connection.execute(
                select(QuotaBucketModel.__table__).where(
                    QuotaBucketModel.owner_id.in_([user_a, user_b, workspace_id]),
                    QuotaBucketModel.bucket_type == "daily",
                )
            ).mappings().all()
        a_snapshot = service.snapshot(
            user_id=user_a, workspace_id=workspace_id, now=NOW
        )
        b_snapshot = service.snapshot(
            user_id=user_b, workspace_id=workspace_id, now=NOW
        )
        assert {(event["user_id"], event["credits_micro"], event["usage_status"]) for event in events} == {
            (user_a, 3_400, "exact"),
            (user_b, 6_200, "exact"),
        }
        by_owner = {bucket["owner_id"]: bucket for bucket in buckets}
        assert by_owner[user_a]["consumed_micro"] == 3_400
        assert by_owner[user_b]["consumed_micro"] == 6_200
        def snapshot_bucket(snapshot: dict, owner_id: str) -> dict:
            return next(
                bucket
                for bucket in snapshot["buckets"]
                if bucket["owner_id"] == owner_id and bucket["bucket_type"] == "daily"
            )

        assert snapshot_bucket(a_snapshot, user_a)["grant_micro"] == 5_000
        assert snapshot_bucket(b_snapshot, user_b)["grant_micro"] == 0
        assert snapshot_bucket(a_snapshot, workspace_id)["consumed_micro"] == 9_600
    finally:
        with engine.begin() as connection:
            if operation_ids:
                connection.execute(
                    delete(UsageEventModel).where(
                        UsageEventModel.operation_id.in_(operation_ids)
                    )
                )
            if reservation_ids:
                reservation_select = select(QuotaReservationModel.id).where(
                    QuotaReservationModel.id.in_(reservation_ids)
                )
                connection.execute(
                    delete(QuotaLedgerEntryModel).where(
                        QuotaLedgerEntryModel.reservation_id.in_(reservation_select)
                    )
                )
                connection.execute(
                    delete(QuotaReservationModel).where(
                        QuotaReservationModel.id.in_(reservation_ids)
                    )
                )
            if grant_id is not None:
                connection.execute(
                    delete(QuotaLedgerEntryModel).where(
                        QuotaLedgerEntryModel.grant_id == grant_id
                    )
                )
                connection.execute(
                    delete(QuotaGrantModel).where(QuotaGrantModel.id == grant_id)
                )
            connection.execute(
                delete(QuotaBucketModel).where(
                    QuotaBucketModel.owner_id.in_([user_a, user_b, workspace_id])
                )
            )
            connection.execute(
                delete(QuotaConcurrencyLockModel).where(
                    QuotaConcurrencyLockModel.user_id.in_([user_a, user_b])
                )
            )
            connection.execute(
                delete(PolicyBindingModel).where(
                    PolicyBindingModel.policy_id.in_(policy_ids)
                )
            )
            connection.execute(
                delete(QuotaPolicyModel).where(QuotaPolicyModel.id.in_(policy_ids))
            )
            connection.execute(
                delete(PricingRuleModel).where(PricingRuleModel.pricing_key == pricing_key)
            )
        engine.dispose()


def test_mysql_late_provider_usage_reconciles_a_closed_reservation():
    dsn = _mysql_dsn()
    engine = create_engine(dsn.replace("mysql+aiomysql://", "mysql+pymysql://"))
    policy_id = str(uuid4())
    reservation_id = None
    user_id = f"phase2-mysql-reconcile-{uuid4()}"
    pricing_key = f"phase2-mysql/model-{uuid4()}"
    operation_id = str(uuid4())
    try:
        service = QuotaService(engine, lease_seconds=60)
        service.verify_schema()
        with engine.begin() as connection:
            connection.execute(
                insert(QuotaPolicyModel).values(
                    id=policy_id,
                    code=f"phase2-mysql-reconcile-{uuid4()}",
                    version="1",
                    name="Phase 2 MySQL late settlement test",
                    status="active",
                    request_limit_micro=100,
                    daily_limit_micro=10_000,
                    weekly_limit_micro=10_000,
                    concurrency_limit=1,
                    max_overdraft_micro=0,
                    allowed_model_profiles=["economy"],
                    unlimited=False,
                    effective_from=NOW,
                    effective_until=None,
                    created_by="phase2-integration",
                )
            )
            connection.execute(
                insert(PolicyBindingModel).values(
                    id=str(uuid4()),
                    subject_type="role",
                    subject_id="student",
                    policy_id=policy_id,
                    priority=100,
                    status="active",
                    effective_from=NOW,
                    effective_until=None,
                )
            )
        admitted = service.admit_turn(
            AdmitTurn(
                request_id=f"phase2-reconcile-request-{uuid4()}",
                user_id=user_id,
                workspace_id="phase2-integration",
                turn_id=f"phase2-reconcile-turn-{uuid4()}",
                model_profile="economy",
                model_role="coordinator",
                estimated_input_tokens=2,
                estimated_output_tokens=3,
                estimated_micro=5,
                idempotency_key=f"phase2-reconcile-idempotency-{uuid4()}",
            ),
            role_codes=("student",),
            now=NOW,
        )
        reservation_id = admitted.reservation_id
        # The actual turn id is carried by the reservation; finish it through a
        # direct read so the test exercises delayed settlement after closure.
        with engine.connect() as connection:
            turn_id = connection.execute(
                select(QuotaReservationModel.turn_id).where(
                    QuotaReservationModel.id == reservation_id
                )
            ).scalar_one()
        service.release_reservation(
            reservation_id,
            turn_id=turn_id,
            idempotency_key=f"phase2-reconcile-finish-{uuid4()}",
            now=NOW,
        )

        invocation = ModelInvocation(
            operation_id=operation_id,
            identity=ModelIdentity(
                provider="phase2-mysql",
                provider_model="phase2-model",
                model_profile="economy",
                preset="economy",
                route="coordinator",
                pricing_key=pricing_key,
            ),
            attribution=UsageAttributionContext(
                request_id="phase2-reconcile-request",
                user_id=user_id,
                workspace_id="phase2-integration",
                turn_id=turn_id,
                reservation_id=reservation_id,
                purpose="coordinator",
            ),
            attempt=1,
            fallback_index=0,
            started_at=NOW,
        )
        partial = CanonicalTokenUsage(
            input_tokens=2,
            output_tokens=3,
            total_tokens=5,
            source="provider",
            semantics="partial",
        )
        exact = partial.model_copy(update={"semantics": "final"})
        partial_outcome = InvocationOutcome(
            status="interrupted",
            completed_at=NOW,
        )
        exact_outcome = InvocationOutcome(
            status="succeeded",
            finish_reason="stop",
            completed_at=NOW,
        )
        reporter = DurableModelUsageReporter(engine, quota_service=service)
        asyncio.run(reporter.report(invocation, partial, partial_outcome))
        with engine.begin() as connection:
            connection.execute(
                insert(PricingRuleModel).values(
                    id=str(uuid4()),
                    pricing_key=pricing_key,
                    version="1",
                    effective_from=NOW,
                    effective_until=None,
                    ordinary_input_credits_micro_per_million_tokens=1_000_000,
                    cached_input_credits_micro_per_million_tokens=0,
                    cache_write_credits_micro_per_million_tokens=0,
                    output_credits_micro_per_million_tokens=2_000_000,
                    reasoning_output_credits_micro_per_million_tokens=None,
                    status="active",
                    created_by="phase2-integration",
                    created_at=NOW,
                )
            )
        asyncio.run(reporter.report(invocation, exact, exact_outcome))

        with engine.connect() as connection:
            event = connection.execute(
                select(UsageEventModel.__table__).where(
                    UsageEventModel.operation_id == operation_id
                )
            ).mappings().one()
            bucket = connection.execute(
                select(QuotaBucketModel.__table__).where(
                    QuotaBucketModel.owner_id == user_id,
                    QuotaBucketModel.bucket_type == "daily",
                )
            ).mappings().one()
            reconcile_count = connection.execute(
                select(QuotaLedgerEntryModel.id).where(
                    QuotaLedgerEntryModel.reservation_id == reservation_id,
                    QuotaLedgerEntryModel.entry_type == "reconcile",
                )
            ).fetchall()
        assert event["usage_status"] == "exact"
        assert event["credits_micro"] == 8
        assert bucket["consumed_micro"] == 8
        assert len(reconcile_count) == 2
    finally:
        with engine.begin() as connection:
            if reservation_id is not None:
                connection.execute(
                    delete(QuotaLedgerEntryModel).where(
                        QuotaLedgerEntryModel.reservation_id == reservation_id
                    )
                )
                connection.execute(
                    delete(UsageEventModel).where(
                        UsageEventModel.operation_id == operation_id
                    )
                )
                connection.execute(
                    delete(QuotaReservationModel).where(
                        QuotaReservationModel.id == reservation_id
                    )
                )
            connection.execute(
                delete(QuotaBucketModel).where(QuotaBucketModel.owner_id == user_id)
            )
            connection.execute(
                delete(QuotaConcurrencyLockModel).where(
                    QuotaConcurrencyLockModel.user_id == user_id
                )
            )
            connection.execute(
                delete(PolicyBindingModel).where(PolicyBindingModel.policy_id == policy_id)
            )
            connection.execute(delete(QuotaPolicyModel).where(QuotaPolicyModel.id == policy_id))
            connection.execute(
                delete(PricingRuleModel).where(PricingRuleModel.pricing_key == pricing_key)
            )
        engine.dispose()
