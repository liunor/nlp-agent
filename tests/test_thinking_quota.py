"""Fixed Turn accounting against the existing quota bucket/ledger schema."""

from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, insert, select

from server.quota import models
from server.quota.contracts import AdmitTurn
from server.quota.fixed_turn import FixedTurnQuota, charge_snapshot
from server.quota.errors import QuotaRejectedError

NOW = datetime(2026, 10, 26, tzinfo=timezone.utc)


@pytest.fixture
def quota(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'quota.sqlite3').as_posix()}")
    tables = [
        value.__table__
        for value in vars(models).values()
        if isinstance(value, type)
        and value.__module__ == models.__name__
        and hasattr(value, "__table__")
    ]
    for table in tables:
        table.create(engine)
    with engine.begin() as c:
        c.execute(
            insert(models.QuotaPolicyModel).values(
                id="policy",
                code="existing",
                version="v1",
                name="Existing allocation",
                daily_limit_micro=10_000_000,
                weekly_limit_micro=50_000_000,
                concurrency_limit=10,
                max_overdraft_micro=0,
                allowed_model_profiles=[],
                unlimited=False,
                effective_from=NOW,
                created_by="existing-admin",
            )
        )
        c.execute(
            insert(models.PolicyBindingModel).values(
                id="binding",
                subject_type="default",
                subject_id="*",
                policy_id="policy",
                priority=0,
                status="active",
                effective_from=NOW,
            )
        )
    yield FixedTurnQuota(engine)
    engine.dispose()


def admit(quota, level="light", user="a", turn=None, connection=None):
    turn = turn or str(uuid4())
    charge = charge_snapshot(level, turn)
    command = AdmitTurn(
        request_id=turn,
        user_id=user,
        workspace_id="w",
        turn_id=turn,
        model_profile="deepseek",
        model_role="coordinator",
        estimated_output_tokens=0,
        idempotency_key=turn,
        fixed_charge=charge,
    )
    result = (
        quota.admit_in_transaction(connection, command, now=NOW)
        if connection
        else quota.admit_turn(command, now=NOW)
    )
    return turn, result


def test_management_replay_preserves_fixed_turn_refunds(quota):
    from server.quota.operations import QuotaOperationsService

    turn, _ = admit(quota)
    with quota.engine.begin() as connection:
        quota.finish_fixed_in_transaction(connection, turn_id=turn, status="completed", has_body=True)
        quota.finish_fixed_in_transaction(connection, turn_id=turn, status="failed", has_body=False)
    operations = QuotaOperationsService(quota.engine)
    with quota.engine.connect() as connection:
        ids = connection.execute(select(models.QuotaBucketModel.id)).scalars().all()
    for bucket_id in ids:
        replay = operations.replay_bucket(bucket_id)
        assert replay.stored_consumed_micro == 0
        assert replay.expected_consumed_micro == 0
        assert replay.needs_repair is False
        repaired = operations.repair_bucket(bucket_id, actor_user_id="admin", reason="audit", idempotency_key=bucket_id)
        assert repaired.stored_consumed_micro == 0


@pytest.mark.parametrize(
    "level,amount", [("quick", 1), ("light", 2), ("deep", 4), ("extreme", 8)]
)
def test_fixed_price_settles_once_and_is_user_owned(quota, level, amount):
    turn, result = admit(quota, level)
    assert result.reserved_micro == amount * 1_000_000
    with quota.engine.begin() as c:
        quota.finish_fixed_in_transaction(
            c, turn_id=turn, status="completed", has_body=True, now=NOW
        )
        quota.finish_fixed_in_transaction(
            c, turn_id=turn, status="completed", has_body=True, now=NOW
        )
        rows = c.execute(select(models.QuotaBucketModel)).mappings().all()
        assert all(
            row["owner_id"] == "a"
            and row["consumed_micro"] == amount * 1_000_000
            and row["reserved_micro"] == 0
            for row in rows
        )


@pytest.mark.parametrize(
    "status,body,expected",
    [
        ("completed", False, 0),
        ("cancelled", False, 0),
        ("cancelled", True, 2_000_000),
        ("failed", True, 0),
        ("interrupted", False, 0),
    ],
)
def test_release_and_cancel_rules(quota, status, body, expected):
    turn, _ = admit(quota)
    with quota.engine.begin() as c:
        quota.finish_fixed_in_transaction(
            c, turn_id=turn, status=status, has_body=body, now=NOW
        )
        rows = c.execute(select(models.QuotaBucketModel)).mappings().all()
        assert all(
            r["consumed_micro"] == expected and r["reserved_micro"] == 0 for r in rows
        )


def test_refund_is_append_only_and_replay_safe(quota):
    turn, _ = admit(quota)
    with quota.engine.begin() as c:
        quota.finish_fixed_in_transaction(
            c, turn_id=turn, status="completed", has_body=True, now=NOW
        )
        quota.finish_fixed_in_transaction(
            c, turn_id=turn, status="failed", has_body=True, now=NOW
        )
        quota.finish_fixed_in_transaction(
            c, turn_id=turn, status="failed", has_body=True, now=NOW
        )
        assert (
            len(
                c.execute(
                    select(models.QuotaLedgerEntryModel).where(
                        models.QuotaLedgerEntryModel.entry_type == "refund"
                    )
                ).all()
            )
            == 2
        )
        assert all(
            r.consumed_micro == 0 for r in c.execute(select(models.QuotaBucketModel))
        )


def test_parallel_admission_does_not_overspend_or_share_users(quota):
    def attempt(_):
        try:
            return admit(quota, "extreme")[1].allowed
        except QuotaRejectedError:
            return False

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sum(pool.map(attempt, range(4))) == 1
    assert admit(quota, "extreme", user="b")[1].allowed


def test_transaction_rollback_removes_reservation_and_counters(quota):
    with pytest.raises(RuntimeError):
        with quota.engine.begin() as c:
            admit(quota, connection=c)
            raise RuntimeError("Turn or Outbox insert failed")
    with quota.engine.connect() as c:
        assert not c.execute(select(models.QuotaReservationModel)).all()
        assert not c.execute(select(models.QuotaLedgerEntryModel)).all()


def test_usage_settlement_cannot_double_charge_a_fixed_turn(quota):
    turn, result = admit(quota)
    with quota.engine.begin() as c:
        quota.settle_usage_in_transaction(
            c,
            reservation_id=result.reservation_id,
            operation_id=str(uuid4()),
            credits_micro=999_000_000,
            usage_status="exact",
        )
        assert all(
            r.consumed_micro == 0 for r in c.execute(select(models.QuotaBucketModel))
        )
        quota.finish_fixed_in_transaction(
            c, turn_id=turn, status="completed", has_body=True, now=NOW
        )
        assert all(
            r.consumed_micro == 2_000_000
            for r in c.execute(select(models.QuotaBucketModel))
        )


def test_fixed_reservation_never_expires_before_turn_recovery(quota):
    from datetime import timedelta

    turn, result = admit(quota)
    assert quota.expire_reservations(now=NOW + timedelta(days=2)) == 0
    with quota.engine.connect() as c:
        row = c.execute(select(models.QuotaReservationModel)).mappings().one()
        assert row["status"] == "reserved"


def test_dispatch_failure_retry_reuses_frozen_charge_and_can_release_again(quota):
    turn, _ = admit(quota)
    with quota.engine.begin() as c:
        quota.finish_fixed_in_transaction(
            c, turn_id=turn, status="failed", has_body=False, now=NOW
        )
    admit(quota, turn=turn)
    with quota.engine.begin() as c:
        quota.finish_fixed_in_transaction(
            c, turn_id=turn, status="failed", has_body=False, now=NOW
        )
        assert all(
            r.reserved_micro == 0 for r in c.execute(select(models.QuotaBucketModel))
        )


def test_mysql_admission_uses_same_connection_as_turn_and_outbox(monkeypatch):
    from unittest.mock import MagicMock, patch
    from gateway.mysql_repository import MySQLGatewayRepository
    from gateway.redis_transport import TurnTaskCodec
    from test_thinking_execution import task_for
    from core.model_runtime.factory import ModelFactory
    from core.model_runtime.registry import ProviderRegistry
    from core.runtime_config import load_runtime_config

    monkeypatch.setattr(ModelFactory, "_api_key", lambda *_args: "test")
    factory = ModelFactory(
        ModelFactory.config_from_raw(load_runtime_config()), registry=ProviderRegistry()
    )
    frozen = factory.snapshot(
        profile_name="deepseek",
        level="light",
        user_id="a",
        workspace_id="w",
        session_id="s",
        turn_id="t",
    )
    task = task_for(
        frozen.model_copy(update={"billing": charge_snapshot("light", "t")})
    )
    repo = object.__new__(MySQLGatewayRepository)
    repo._engine = MagicMock()
    c = repo._engine.begin.return_value.__enter__.return_value
    c.execute.return_value.mappings.return_value.first.return_value = None
    c.execute.return_value.scalar_one.return_value = 1
    repo._ensure_conversation = MagicMock()
    repo._row = MagicMock(return_value={})
    repo._record = lambda row: row
    repo.quota_service = MagicMock()
    repo._lock_turn_capacity = MagicMock()
    repo._reserve_model_quota = MagicMock()
    payload = TurnTaskCodec.dumps(task)
    with patch("gateway.mysql_repository.SyncStorageQuota"):
        repo.create_turn(
            turn_id="t",
            session_id="s",
            user_id="a",
            workspace_id="w",
            input_text="hello",
            idempotency_key=None,
            quota_admission=__import__("server.quota.fixed_turn", fromlist=["command_for_task"]).command_for_task(task),
            persisted_task=payload,
            dispatch_payload=payload,
        )
    assert repo.quota_service.admit_in_transaction.call_args.args[0] is c


def test_retry_next_day_does_not_touch_historical_or_other_turn_buckets(quota):
    from datetime import timedelta

    turn, _ = admit(quota)
    with quota.engine.begin() as c:
        quota.finish_fixed_in_transaction(
            c, turn_id=turn, status="failed", has_body=False, now=NOW
        )
    other, _ = admit(quota, "deep")
    later = NOW + timedelta(days=1)
    charge = charge_snapshot("light", turn)
    command = AdmitTurn(
        request_id=turn,
        user_id="a",
        workspace_id="w",
        turn_id=turn,
        model_profile="deepseek",
        model_role="coordinator",
        estimated_output_tokens=0,
        idempotency_key=turn,
        fixed_charge=charge,
    )
    quota.admit_turn(command, now=later)
    with quota.engine.begin() as c:
        quota.finish_fixed_in_transaction(
            c, turn_id=turn, status="completed", has_body=True, now=later
        )
        rows = c.execute(select(models.QuotaBucketModel)).mappings().all()
        old_daily = next(
            r
            for r in rows
            if r["bucket_type"] == "daily" and r["period_start"].date() == NOW.date()
        )
        assert old_daily["reserved_micro"] == 4_000_000
        assert old_daily["consumed_micro"] == 0


@pytest.mark.parametrize("already_running", [False, True])
def test_running_fixed_turn_can_recover_after_quota_lease_without_rereserving(
    quota, already_running
):
    from datetime import timedelta
    from server.quota.service import _aware

    turn, result = admit(quota)
    if already_running:
        assert quota.begin_reservation(result.reservation_id, now=NOW)
    charge = charge_snapshot("light", turn)
    command = AdmitTurn(
        request_id=turn,
        user_id="a",
        workspace_id="w",
        turn_id=turn,
        model_profile="deepseek",
        model_role="coordinator",
        estimated_output_tokens=0,
        idempotency_key=turn,
        fixed_charge=charge,
    )
    recovered_at = NOW + timedelta(days=1)
    assert quota.admit_turn(command, now=recovered_at).duplicate
    assert quota.begin_reservation(result.reservation_id, now=recovered_at)
    # A delayed heartbeat must not release a live fixed charge either.
    heartbeat_at = recovered_at + timedelta(seconds=quota.lease_seconds + 1)
    assert quota.heartbeat(result.reservation_id, now=heartbeat_at)
    with quota.engine.connect() as c:
        reservation = c.execute(select(models.QuotaReservationModel)).mappings().one()
        assert reservation["status"] == "running"
        assert _aware(reservation["lease_expires_at"]) == heartbeat_at + timedelta(
            seconds=quota.lease_seconds
        )
        assert reservation["policy_snapshot_json"]["fixed_turn"] == charge.model_dump(mode="json")
        assert len(c.execute(select(models.QuotaLedgerEntryModel)).all()) == 2
        assert all(
            row.reserved_micro == 2_000_000 and row.consumed_micro == 0
            for row in c.execute(select(models.QuotaBucketModel))
        )
    with quota.engine.begin() as c:
        for _ in range(2):
            quota.finish_fixed_in_transaction(
                c, turn_id=turn, status="completed", has_body=True, now=heartbeat_at
            )
        assert all(
            row.reserved_micro == 0 and row.consumed_micro == 2_000_000
            for row in c.execute(select(models.QuotaBucketModel))
        )


@pytest.mark.parametrize("method", ["begin_reservation", "heartbeat"])
@pytest.mark.parametrize("status", ["completed", "cancelled", "failed"])
def test_fixed_quota_lease_cannot_revive_a_finished_reservation(quota, method, status):
    from datetime import timedelta

    turn, result = admit(quota)
    with quota.engine.begin() as c:
        quota.finish_fixed_in_transaction(
            c, turn_id=turn, status=status, has_body=True, now=NOW
        )
        before = c.execute(select(models.QuotaLedgerEntryModel)).all()
        balances = c.execute(select(models.QuotaBucketModel)).all()
    assert not getattr(quota, method)(
        result.reservation_id, now=NOW + timedelta(seconds=quota.lease_seconds + 1)
    )
    with quota.engine.connect() as c:
        assert c.execute(select(models.QuotaLedgerEntryModel)).all() == before
        assert c.execute(select(models.QuotaBucketModel)).all() == balances


@pytest.mark.parametrize("method", ["begin_reservation", "heartbeat"])
def test_usage_based_reservations_still_expire_at_the_original_lease(quota, method):
    from datetime import timedelta

    result = quota.admit_turn(AdmitTurn(
        request_id="usage", user_id="a", workspace_id="w", turn_id="usage",
        model_profile="deepseek", model_role="coordinator", estimated_output_tokens=0,
        estimated_micro=2_000_000, idempotency_key="usage",
    ), now=NOW)
    assert not getattr(quota, method)(
        result.reservation_id, now=NOW + timedelta(seconds=quota.lease_seconds + 1)
    )
    with quota.engine.connect() as c:
        reservation = c.execute(select(models.QuotaReservationModel)).mappings().one()
        assert reservation["status"] == "expired"
        assert all(
            row.reserved_micro == 0 and row.consumed_micro == 0
            for row in c.execute(select(models.QuotaBucketModel))
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "remote,user_cancel,expected",
    [
        (True, False, ["running"]),
        (True, True, ["running", "cancelled"]),
        (False, False, ["running", "interrupted"]),
    ],
)
async def test_user_cancellation_differs_from_platform_interruption(
    remote, user_cancel, expected
):
    import asyncio
    from types import SimpleNamespace
    from core.learning import TeachingMaterials
    from core.session_context import SessionContext
    from gateway.dispatch import TurnTask
    from gateway.turn_execution import InProcessTurnExecutor
    from test_gateway_core import FakeEngine

    started = asyncio.Event()
    statuses = []

    class Engine(FakeEngine):
        async def run_turn(self, context, turn_id, content):
            started.set()
            await asyncio.Event().wait()

    class Repository:
        def ensure_event(self, **kwargs):
            pass

        def update_turn(self, turn_id, status, **kwargs):
            statuses.append(status.value)
            return SimpleNamespace(status=status)

        def cancellation_requested(self, turn_id):
            return user_cancel

    async def emit(*_args):
        pass

    task = TurnTask(
        context=SessionContext(session_id="s", user_id="a"),
        turn_id="t",
        content="x",
        learning_context=None,
        learning_progress=None,
        exercise_state=None,
        teaching_materials=TeachingMaterials(),
        guided_session_id=None,
        exercise_session_id=None,
    )
    running = asyncio.create_task(
        InProcessTurnExecutor(Engine(), Repository(), emit).run(
            task, SimpleNamespace(claim_generation=1) if remote else None
        )
    )
    await started.wait()
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    assert statuses == expected


def test_existing_reservation_cannot_be_replayed_by_another_user(quota):
    from server.quota.errors import QuotaDomainError

    turn, _ = admit(quota)
    with pytest.raises(QuotaDomainError):
        admit(quota, user="b", turn=turn)


@pytest.mark.asyncio
async def test_usage_facts_survive_refund_without_token_deductions(quota):
    from core.model_runtime.usage import (
        ModelInvocation,
        ModelIdentity,
        UsageAttributionContext,
        CanonicalTokenUsage,
        InvocationOutcome,
    )
    from server.quota.reporting import DurableModelUsageReporter

    turn, result = admit(quota)
    with quota.engine.begin() as c:
        c.execute(
            insert(models.PricingRuleModel).values(
                id="price",
                pricing_key="deepseek/test",
                version="v1",
                effective_from=NOW,
                ordinary_input_credits_micro_per_million_tokens=1_000_000,
                cached_input_credits_micro_per_million_tokens=100_000,
                cache_write_credits_micro_per_million_tokens=1_000_000,
                output_credits_micro_per_million_tokens=2_000_000,
                created_by="existing-admin",
            )
        )
    invocation = ModelInvocation(
        operation_id=str(uuid4()),
        identity=ModelIdentity(
            provider="deepseek",
            provider_model="test",
            preset="test",
            pricing_key="deepseek/test",
        ),
        attribution=UsageAttributionContext(
            request_id=turn,
            user_id="a",
            workspace_id="w",
            turn_id=turn,
            reservation_id=result.reservation_id,
            purpose="worker",
        ),
        attempt=1,
        fallback_index=0,
        started_at=NOW,
    )
    usage = CanonicalTokenUsage(
        input_tokens=3, output_tokens=2, total_tokens=5, source="provider"
    )
    outcome = InvocationOutcome(status="succeeded", completed_at=NOW)
    reporter = DurableModelUsageReporter(quota.engine, quota_service=quota)
    await reporter.report(invocation, usage, outcome)
    await reporter.report(invocation, usage, outcome)
    with quota.engine.begin() as c:
        assert all(
            r.consumed_micro == 0 for r in c.execute(select(models.QuotaBucketModel))
        )
        quota.finish_fixed_in_transaction(
            c, turn_id=turn, status="completed", has_body=True, now=NOW
        )
        quota.finish_fixed_in_transaction(
            c, turn_id=turn, status="failed", has_body=True, now=NOW
        )
        event = c.execute(select(models.UsageEventModel)).mappings().one()
        assert event["total_tokens"] == 5 and event["credits_micro"] == 7
        assert all(
            r.consumed_micro == 0 for r in c.execute(select(models.QuotaBucketModel))
        )


def test_partial_body_excludes_reasoning_tools_background_and_whitespace(quota):
    import json
    from sqlalchemy import text
    from server.quota.turns import has_partial_body

    with quota.engine.begin() as c:
        c.execute(
            text(
                "CREATE TABLE nlp_turn_events(turn_id TEXT,event_type TEXT,payload_json TEXT)"
            )
        )
        for payload in [
            {"delta": "reason", "channel": "reasoning"},
            {"delta": "tool args", "channel": "tool"},
            {"delta": "background answer", "background": True},
            {"delta": "   "},
        ]:
            c.execute(
                text("INSERT INTO nlp_turn_events VALUES('t','message.delta',:p)"),
                {"p": json.dumps(payload)},
            )
        assert not has_partial_body(c, "t")
        c.execute(
            text("INSERT INTO nlp_turn_events VALUES('t','message.delta',:p)"),
            {"p": json.dumps({"delta": "answer"})},
        )
        assert has_partial_body(c, "t")


def test_recovery_uses_the_same_terminal_transaction_as_normal_execution():
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    from gateway.mysql_repository import MySQLGatewayRepository
    from gateway.contracts import TurnStatus

    repo = object.__new__(MySQLGatewayRepository)
    repo._engine = MagicMock()
    repo._engine.connect.return_value.__enter__.return_value.execute.return_value.scalars.return_value.all.return_value = [
        "t"
    ]
    repo.update_turn = MagicMock(
        return_value=SimpleNamespace(status=TurnStatus.INTERRUPTED)
    )
    assert len(repo.recover_interrupted()) == 1
    assert repo.update_turn.call_args.args == ("t", TurnStatus.INTERRUPTED)


def test_chat_quota_errors_never_include_cost_or_balance():
    from server.quota.errors import QuotaErrorCode, QuotaRejectedError
    from server.quota.contracts import QuotaProblem
    from server.quota.transport import quota_error
    from server.web.websocket import _command_error

    error = QuotaRejectedError(
        QuotaProblem(
            code=QuotaErrorCode.DAILY_EXHAUSTED,
            reason="secret cost 987654",
            remaining_micro=123456,
            retryable=True,
        )
    )
    result = quota_error(error)
    assert (
        result[2] == 429 and "987654" not in str(result) and "123456" not in str(result)
    )
    assert _command_error(error) == (*result[:2], {})


def test_unlimited_concurrency_turn_cannot_release_another_turn_slot(quota):
    from sqlalchemy import update

    with quota.engine.begin() as c:
        c.execute(update(models.QuotaPolicyModel).values(concurrency_limit=None))
    first, _ = admit(quota)
    with quota.engine.begin() as c:
        c.execute(update(models.QuotaPolicyModel).values(concurrency_limit=1))
    admit(quota)
    with quota.engine.begin() as c:
        quota.finish_fixed_in_transaction(
            c, turn_id=first, status="completed", has_body=True, now=NOW
        )
        assert (
            c.execute(
                select(models.QuotaConcurrencyLockModel.active_units)
            ).scalar_one()
            == 1
        )
    with pytest.raises(QuotaRejectedError):
        admit(quota)


def test_schema_bridge_is_idempotent_and_preserves_existing_allocations(
    quota, monkeypatch
):
    admit(quota)
    with quota.engine.begin() as c:
        before = {
            table.name: c.execute(select(table)).mappings().all()
            for table in [
                models.QuotaPolicyModel.__table__,
                models.PolicyBindingModel.__table__,
                models.QuotaBucketModel.__table__,
                models.QuotaReservationModel.__table__,
                models.QuotaLedgerEntryModel.__table__,
            ]
        }
        quota.verify_schema()
        quota.verify_schema()
        for name, rows in before.items():
            assert (
                c.execute(select(models.Base.metadata.tables[name])).mappings().all()
                == rows
            )


def test_schema_bridge_creates_empty_tables_without_issuing_credits(monkeypatch):
    from sqlalchemy import func

    engine = create_engine("sqlite://")
    try:
        with engine.begin() as c:
            for value in vars(models).values():
                if isinstance(value, type) and value.__module__ == models.__name__ and hasattr(value, "__table__"):
                    value.__table__.create(c)
            FixedTurnQuota(engine).verify_schema()
            for model in [
                models.QuotaPolicyModel,
                models.PolicyBindingModel,
                models.QuotaBucketModel,
                models.QuotaLedgerEntryModel,
            ]:
                assert (
                    c.execute(select(func.count()).select_from(model)).scalar_one() == 0
                )
    finally:
        engine.dispose()
