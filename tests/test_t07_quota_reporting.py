from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
import uuid

import pytest

from core.model_runtime.reporters import UsageEventConflictError
from core.model_runtime.usage import CanonicalTokenUsage, InvocationOutcome, ModelIdentity, ModelInvocation, UsageAttributionContext
from gateway.contracts import AdmissionLimitError
from gateway.execution_fence import LostTurnClaimError, TurnWriteFence, bind_turn_write_fence, current_turn_write_fence
from server.infrastructure.mysql.models import ModelQuotaAccountModel, ModelQuotaReservationModel, ModelUsageEventModel, ModelUsageIntentModel, TurnModel
from server.model_usage.quota import DurableQuotaUsageReporter


def invocation():
    return ModelInvocation(
        operation_id=str(uuid.uuid4()), started_at=datetime.now(timezone.utc),
        identity=ModelIdentity(provider="test", provider_model="model", preset="test", context_window_tokens=1000, max_output_tokens=100),
        attribution=UsageAttributionContext(request_id="request", user_id="user", turn_id="turn", reservation_id="reservation", purpose="coordinator"),
        attempt=1, fallback_index=0,
    )


def outcome():
    return InvocationOutcome(status="succeeded", completed_at=datetime.now(timezone.utc))


def test_credit_snapshot_notifier_survives_token_reporter_wrapping(tmp_path):
    class Credits:
        notifier = None

        def set_snapshot_notifier(self, notifier):
            self.notifier = notifier

    credits = Credits()
    reporter = DurableQuotaUsageReporter(None, journal_directory=tmp_path, downstream_reporter=credits)
    notifier = object()
    reporter.set_snapshot_notifier(notifier)
    assert credits.notifier is notifier
    reporter.set_snapshot_notifier(None)
    assert credits.notifier is None


@pytest.mark.asyncio
async def test_develop_credit_reporter_keeps_its_reservation_identity(tmp_path):
    factory, reservation, account, _, _ = ledger()
    primary = SimpleNamespace(report=AsyncMock(), reserve_feature_usage=AsyncMock())
    reporter = DurableQuotaUsageReporter(factory, journal_directory=tmp_path, downstream_reporter=primary)
    original = invocation()
    call = original.model_copy(update={"attribution": original.attribution.model_copy(update={
        "reservation_id": "credit-reservation", "token_reservation_id": "reservation",
    })})
    await reporter.prepare(call)
    await reporter.reserve_feature_usage(call)
    await reporter.report(call, CanonicalTokenUsage(source="provider", input_tokens=10, total_tokens=10), outcome())
    primary.report.assert_awaited_once()
    assert primary.report.await_args.args[0].attribution.reservation_id == "credit-reservation"
    primary.reserve_feature_usage.assert_awaited_once_with(call)
    assert reservation.pending_invocations == 0
    assert account.used_tokens == 10


@pytest.fixture(autouse=True)
def execution_fence():
    with bind_turn_write_fence(TurnWriteFence("turn", 1, "worker")):
        yield


def ledger(*, terminal=False, settled=False, pending=0, limit=None):
    reservation = ModelQuotaReservationModel(
        id="reservation", user_id="user", turn_id="turn", reserved_tokens=1100,
        used_tokens=0, settled_tokens=0, pending_invocations=pending,
        unknown_usage_events=0, status="settled" if settled else "reserved",
    )
    account = ModelQuotaAccountModel(user_id="user", reserved_tokens=0 if settled else 1100, used_tokens=0, consumed_tokens=0, unknown_usage_events=0, token_limit=limit)
    turn = TurnModel(id="turn", status="cancelled" if terminal else "running",
        claim_generation=1, claimed_by="worker", lease_expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=1))
    intents, events = {}, {}
    session = AsyncMock()
    session.scalar.side_effect = lambda statement: (
        turn if "nlp_turns" in str(statement) else
        reservation if "nlp_model_quota_reservations" in str(statement) else
        datetime.now(timezone.utc).replace(tzinfo=None) if "utc_timestamp" in str(statement) else account
    )
    session.scalars.side_effect = lambda _statement: SimpleNamespace(all=lambda: [item.reserved_tokens for item in intents.values() if item.status in {"pending", "unknown_usage"}])
    session.get.side_effect = lambda model, key, **_kw: (events if model is ModelUsageEventModel else intents).get(key)

    def add(value):
        (events if isinstance(value, ModelUsageEventModel) else intents)[value.operation_id] = value

    session.add = MagicMock(side_effect=add)

    @asynccontextmanager
    async def begin():
        yield session

    return SimpleNamespace(begin=begin), reservation, account, intents, events


@pytest.mark.asyncio
async def test_late_usage_updates_already_settled_cancellation(tmp_path):
    factory, reservation, account, _, events = ledger(terminal=True, settled=True)
    reporter = DurableQuotaUsageReporter(factory, journal_directory=tmp_path)
    call, result = invocation(), outcome()
    usage = CanonicalTokenUsage(source="provider", total_tokens=100, input_tokens=100)
    await reporter.report(call, usage, result)
    await reporter.report(call, usage, result)
    assert reservation.used_tokens == reservation.settled_tokens == 100
    assert account.used_tokens == account.consumed_tokens == 100
    assert len(events) == 1


@pytest.mark.asyncio
async def test_pending_call_retains_budget_until_late_report_is_settled(tmp_path):
    factory, reservation, account, intents, _ = ledger()
    reporter = DurableQuotaUsageReporter(factory, journal_directory=tmp_path)
    call = invocation()
    await reporter.prepare(call)
    assert reservation.pending_invocations == 1
    assert intents[call.operation_id].reserved_tokens == 1100
    # Emulate the durable cancellation (the SQL trigger retains this budget).
    reporter._lock_turn = AsyncMock(return_value=TurnModel(id="turn", status="cancelled"))
    await reporter.report(call, CanonicalTokenUsage(source="provider", input_tokens=100, total_tokens=100), outcome())
    assert reservation.pending_invocations == 0
    assert reservation.status == "settled"
    assert reservation.used_tokens == reservation.settled_tokens == 100
    assert account.reserved_tokens == 0


@pytest.mark.asyncio
async def test_next_provider_call_cannot_exceed_remaining_account_budget(tmp_path):
    factory, reservation, account, intents, _ = ledger(limit=1100)
    reporter = DurableQuotaUsageReporter(factory, journal_directory=tmp_path)
    await reporter.prepare(invocation())
    with pytest.raises(AdmissionLimitError):
        await reporter.prepare(invocation())
    assert len(intents) == 1
    assert account.reserved_tokens == reservation.reserved_tokens == 1100


@pytest.mark.asyncio
async def test_reported_usage_does_not_count_against_both_used_and_reserved(tmp_path):
    factory, reservation, account, _, _ = ledger(limit=1200)
    reporter = DurableQuotaUsageReporter(factory, journal_directory=tmp_path)
    call = invocation()
    await reporter.prepare(call)
    await reporter.report(call, CanonicalTokenUsage(source="provider", input_tokens=100, total_tokens=100), outcome())
    assert account.used_tokens == 100
    assert reservation.reserved_tokens == account.reserved_tokens == 1000
    await reporter.prepare(invocation())
    assert account.used_tokens + account.reserved_tokens == 1200


@pytest.mark.asyncio
async def test_failed_report_is_durable_and_replayed_by_a_new_reporter(tmp_path):
    call, result = invocation(), outcome()
    usage = CanonicalTokenUsage(source="provider", input_tokens=100, total_tokens=100)
    failed = DurableQuotaUsageReporter(None, journal_directory=tmp_path)
    failed._persist_event = AsyncMock(side_effect=OSError("database unavailable"))
    with pytest.raises(OSError, match="database unavailable"):
        await failed.report(call, usage, result)
    assert len(failed.journal.pending()) == 1
    recovered = DurableQuotaUsageReporter(None, journal_directory=tmp_path)
    recovered._persist_event = AsyncMock()
    assert await recovered.replay_pending() == {"replayed": 1, "failed": 0}
    recovered._persist_event.assert_awaited_once_with(call, usage, result)
    assert recovered.journal.pending() == []


@pytest.mark.asyncio
async def test_lost_journal_acknowledgement_replays_without_double_charge(tmp_path):
    factory, reservation, account, _, _ = ledger()
    reporter = DurableQuotaUsageReporter(factory, journal_directory=tmp_path)
    call, result = invocation(), outcome()
    await reporter.prepare(call)
    reporter.journal.acknowledge = MagicMock(side_effect=OSError("disk acknowledgement failed"))
    await reporter.report(call, CanonicalTokenUsage(source="provider", input_tokens=100, total_tokens=100), result)
    recovered = DurableQuotaUsageReporter(factory, journal_directory=tmp_path)
    assert await recovered.replay_pending() == {"replayed": 1, "failed": 0}
    assert account.used_tokens == reservation.used_tokens == 100
    assert reservation.pending_invocations == 0


@pytest.mark.asyncio
async def test_unknown_provider_cost_remains_protected_after_cancellation(tmp_path):
    factory, reservation, account, _, _ = ledger()
    reporter = DurableQuotaUsageReporter(factory, journal_directory=tmp_path)
    call = invocation()
    await reporter.prepare(call)
    reporter._lock_turn = AsyncMock(return_value=TurnModel(id="turn", status="cancelled"))
    await reporter.report(call, CanonicalTokenUsage(source="none"), outcome())
    assert reservation.pending_invocations == 0
    assert reservation.unknown_usage_events == 1
    assert reservation.status == "reserved"
    assert reservation.reserved_tokens == account.reserved_tokens == 1100


def test_journal_rejects_conflicting_usage_without_replacing_first_fact(tmp_path):
    reporter = DurableQuotaUsageReporter(None, journal_directory=tmp_path)
    call, result = invocation(), outcome()
    usage = CanonicalTokenUsage(source="provider", input_tokens=100, total_tokens=100)
    path = reporter.journal.put(call, usage, result)
    with pytest.raises(UsageEventConflictError):
        reporter.journal.put(call, usage.model_copy(update={"total_tokens": 101}), result)
    assert reporter.journal.read(path) == (call, usage, result)


@pytest.mark.asyncio
async def test_worker_shutdown_keeps_reporter_until_execution_and_replay_finish(monkeypatch):
    from server.worker.runtime import close_worker_execution

    order = []
    reporter = SimpleNamespace()
    active = {"reporter": reporter}

    async def finish(name):
        assert active["reporter"] is reporter
        order.append(name)

    reporter.replay_pending = lambda: finish("replay")
    def configure(value):
        active["reporter"] = value
        order.append("unload")
    monkeypatch.setattr("core.model_runtime.reporters.configure_global_model_usage_reporter", configure)
    await close_worker_execution(SimpleNamespace(close=lambda: finish("worker")), SimpleNamespace(close=lambda: finish("engine")), reporter)
    assert order == ["worker", "engine", "replay", "unload"]


@pytest.mark.asyncio
async def test_cancelled_shutdown_caller_does_not_unload_metering_before_drain(monkeypatch):
    from server.worker.runtime import close_worker_execution

    entered, release = asyncio.Event(), asyncio.Event()
    unloaded = []
    monkeypatch.setattr("core.model_runtime.reporters.configure_global_model_usage_reporter", unloaded.append)

    async def drain():
        entered.set()
        await release.wait()

    reporter = SimpleNamespace(replay_pending=AsyncMock())
    task = asyncio.create_task(close_worker_execution(SimpleNamespace(close=drain), SimpleNamespace(close=AsyncMock()), reporter))
    await entered.wait()
    task.cancel()
    await asyncio.sleep(0)
    assert unloaded == []
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    reporter.replay_pending.assert_awaited_once()
    assert unloaded == [None]


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_quota_refusal_happens_before_provider_dispatch(stream):
    from core.model_runtime.reporters import ModelUsageReporterSlot
    from core.model_runtime.runtime import ResilientChatModel
    from core.model_runtime.usage import bind_usage_attribution
    from tests.test_model_usage_reporting import FakeRawModel, _candidate, _sample_attribution

    raw = FakeRawModel([])
    reporter = SimpleNamespace(prepare=AsyncMock(side_effect=AdmissionLimitError(AdmissionLimitError.MODEL_QUOTA_EXCEEDED)), report=AsyncMock())
    model = ResilientChatModel([_candidate("test", raw)], normalize_response=False, reporter_slot=ModelUsageReporterSlot(reporter))
    with bind_usage_attribution(_sample_attribution()), pytest.raises(AdmissionLimitError):
        if stream:
            async for _ in model.astream("a short question with a large unseen history"):
                pass
        else:
            await model.ainvoke("a short question with a large unseen history")
    assert raw.calls == 0
    reporter.report.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("invalid", ["generation", "owner", "expired", "revoked", "missing", "wrong_turn"])
async def test_lost_owner_cannot_prepare_or_dispatch_provider(invalid, stream, tmp_path, monkeypatch):
    from core.model_runtime.reporters import ModelUsageReporterSlot
    from core.model_runtime.runtime import ResilientChatModel
    from core.model_runtime.usage import bind_usage_attribution
    from tests.test_model_usage_reporting import FakeRawModel, _candidate

    factory, reservation, account, intents, _ = ledger()
    reporter = DurableQuotaUsageReporter(factory, journal_directory=tmp_path)
    # The database boundary returns the new owner's locked row, not the stale context.
    turn = TurnModel(id="turn", status="running", claim_generation=2 if invalid == "generation" else 1,
        claimed_by="new-worker" if invalid == "owner" else "worker",
        lease_expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(seconds=-1 if invalid == "expired" else 60))
    reporter._lock_turn = AsyncMock(return_value=turn)
    fence = TurnWriteFence("other" if invalid == "wrong_turn" else "turn", 1, "worker", revoked=invalid == "revoked")
    if invalid == "missing":
        monkeypatch.setattr("gateway.execution_fence.current_turn_write_fence", lambda: None)
    raw = FakeRawModel([])
    model = ResilientChatModel([_candidate("test", raw)], normalize_response=False, reporter_slot=ModelUsageReporterSlot(reporter))
    with bind_turn_write_fence(fence), bind_usage_attribution(invocation().attribution), pytest.raises(LostTurnClaimError):
        if stream:
            async for _ in model.astream("question"):
                pass
        else:
            await model.ainvoke("question")
    assert raw.calls == 0
    assert not intents
    assert reservation.pending_invocations == 0
    assert account.reserved_tokens == 1100


@pytest.mark.asyncio
async def test_revoked_owner_can_still_report_already_incurred_usage(tmp_path):
    factory, reservation, account, _, events = ledger()
    reporter = DurableQuotaUsageReporter(factory, journal_directory=tmp_path)
    call = invocation()
    await reporter.prepare(call)
    with bind_turn_write_fence(TurnWriteFence("turn", 1, "worker", revoked=True)):
        await reporter.report(call, CanonicalTokenUsage(source="provider", input_tokens=100, total_tokens=100), outcome())
    assert account.used_tokens == reservation.used_tokens == 100
    assert len(events) == 1


@pytest.mark.asyncio
async def test_database_preparation_wait_does_not_consume_stream_provider_timeout(tmp_path, monkeypatch):
    from langchain_core.messages import AIMessageChunk
    from core.model_runtime.reporters import ModelUsageReporterSlot
    from core.model_runtime.runtime import ResilientChatModel
    from core.model_runtime.usage import bind_usage_attribution
    from tests.test_model_usage_reporting import FakeRawModel, _candidate
    import core.model_runtime.runtime as runtime

    factory, reservation, account, _, _ = ledger()
    reporter = DurableQuotaUsageReporter(factory, journal_directory=tmp_path)
    clock = [0.0]
    original = reporter._lock_capacity

    async def delayed_lock(session):
        await original(session)
        clock[0] += 3.0  # > the candidate's 2-second Provider deadline

    reporter._lock_capacity = delayed_lock
    monkeypatch.setattr(runtime, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    raw = FakeRawModel([[AIMessageChunk(content="ok", usage_metadata={"input_tokens": 10, "output_tokens": 1, "total_tokens": 11})]])
    candidate = _candidate("test", raw)
    model = ResilientChatModel([candidate], reporter_slot=ModelUsageReporterSlot(reporter))
    with bind_usage_attribution(invocation().attribution):
        chunks = [chunk async for chunk in model.astream("question")]
    assert chunks[0].content == "ok"
    assert raw.calls == 1
    assert candidate.circuit.failures == 0
    assert account.used_tokens == 11
    assert reservation.unknown_usage_events == reservation.pending_invocations == 0


@pytest.mark.asyncio
async def test_preparation_timeout_does_not_dispatch_or_record_unknown_usage(tmp_path):
    from core.model_runtime.reporters import ModelUsageReporterSlot
    from core.model_runtime.runtime import ResilientChatModel
    from core.model_runtime.usage import bind_usage_attribution
    from tests.test_model_usage_reporting import FakeRawModel, _candidate

    factory, reservation, account, intents, events = ledger()
    reporter = DurableQuotaUsageReporter(factory, journal_directory=tmp_path, preparation_timeout_s=0.01)

    async def blocked_lock(_session):
        await asyncio.Event().wait()

    reporter._lock_capacity = blocked_lock
    raw = FakeRawModel([])
    candidate = _candidate("test", raw)
    model = ResilientChatModel([candidate], reporter_slot=ModelUsageReporterSlot(reporter))
    with bind_usage_attribution(invocation().attribution), pytest.raises(TimeoutError) as raised:
        async for _ in model.astream("question"):
            pass
    from core.model_runtime.runtime import classify_model_error
    assert classify_model_error(raised.value).kind == "usage_preparation_timeout"
    assert raw.calls == candidate.circuit.failures == 0
    assert not intents and not events
    assert reservation.pending_invocations == account.unknown_usage_events == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("lost", ["lease", "fence"])
async def test_ownership_is_checked_after_waiting_for_intent_locks(lost, tmp_path):
    factory, reservation, _, intents, _ = ledger()
    reporter = DurableQuotaUsageReporter(factory, journal_directory=tmp_path)
    turn = TurnModel(id="turn", status="running", claim_generation=1, claimed_by="worker",
        lease_expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(seconds=60))
    reporter._lock_turn = AsyncMock(return_value=turn)

    @asynccontextmanager
    async def begin():
        session = AsyncMock()
        session.get.return_value = None
        session.scalar.side_effect = lambda statement: datetime.now(timezone.utc).replace(tzinfo=None) if "utc_timestamp" in str(statement) else ModelQuotaAccountModel(user_id="user", reserved_tokens=1100, used_tokens=0)

        async def wait_for_intents(_statement):
            if lost == "lease":
                turn.lease_expires_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=1)
            else:
                current_turn_write_fence().revoke()
            return SimpleNamespace(all=lambda: [])

        session.scalars.side_effect = wait_for_intents
        yield session

    reporter._session_factory = SimpleNamespace(begin=begin)
    reporter._lock_reservation = AsyncMock(return_value=reservation)
    with pytest.raises(LostTurnClaimError):
        await reporter.prepare(invocation())
    assert reservation.pending_invocations == 0
    assert not intents


@pytest.mark.asyncio
async def test_undispatched_stream_failure_is_known_zero_not_unknown_cost(tmp_path, monkeypatch):
    from core.model_runtime.reporters import ModelUsageReporterSlot
    from core.model_runtime.runtime import ResilientChatModel, ModelRuntimeExhaustedError
    from core.model_runtime.usage import bind_usage_attribution
    from tests.test_model_usage_reporting import FakeRawModel, _candidate
    import core.model_runtime.runtime as runtime

    factory, reservation, account, _, events = ledger()
    reporter = DurableQuotaUsageReporter(factory, journal_directory=tmp_path)
    clock = [0.0]
    monkeypatch.setattr(runtime, "time", SimpleNamespace(monotonic=lambda: clock[0]))

    @asynccontextmanager
    async def delayed_span(*_args):
        clock[0] += 3.0
        yield None

    monkeypatch.setattr(runtime, "_attempt_span", delayed_span)
    raw = FakeRawModel([])
    candidate = _candidate("test", raw)
    model = ResilientChatModel([candidate], reporter_slot=ModelUsageReporterSlot(reporter))
    with bind_usage_attribution(invocation().attribution), pytest.raises(ModelRuntimeExhaustedError):
        async for _ in model.astream("question"):
            pass
    assert raw.calls == 0
    assert candidate.circuit.failures == 0
    assert account.unknown_usage_events == reservation.unknown_usage_events == 0
    assert reservation.pending_invocations == 0
    assert len(events) == 1
    assert next(iter(events.values())).error_kind == "provider_not_dispatched"


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_local_fence_revoked_after_prepare_blocks_provider_and_closes_intent(stream, tmp_path, monkeypatch):
    from core.model_runtime.reporters import ModelUsageReporterSlot
    from core.model_runtime.runtime import ResilientChatModel
    from core.model_runtime.usage import bind_usage_attribution
    from tests.test_model_usage_reporting import FakeRawModel, _candidate
    import core.model_runtime.runtime as runtime

    factory, reservation, account, _, events = ledger()
    reporter = DurableQuotaUsageReporter(factory, journal_directory=tmp_path)

    @asynccontextmanager
    async def span(*_args):
        current_turn_write_fence().revoke()
        yield None

    monkeypatch.setattr(runtime, "_attempt_span", span)
    raw = FakeRawModel([])
    candidate = _candidate("test", raw)
    model = ResilientChatModel([candidate], normalize_response=False, reporter_slot=ModelUsageReporterSlot(reporter))
    with bind_usage_attribution(invocation().attribution), pytest.raises(LostTurnClaimError):
        if stream:
            async for _ in model.astream("question"):
                pass
        else:
            await model.ainvoke("question")
    assert raw.calls == candidate.circuit.failures == 0
    assert reservation.pending_invocations == account.unknown_usage_events == 0
    assert next(iter(events.values())).error_kind == "provider_not_dispatched"


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_commit_wait_crossing_lease_expiry_prevents_old_provider_dispatch(stream, tmp_path, monkeypatch):
    from core.model_runtime.reporters import ModelUsageReporterSlot
    from core.model_runtime.runtime import ResilientChatModel
    from core.model_runtime.usage import bind_usage_attribution
    from tests.test_model_usage_reporting import FakeRawModel, _candidate
    import server.model_usage.quota as quota

    factory, reservation, account, _, events = ledger()
    clock = [0.0]
    monkeypatch.setattr(quota, "time", SimpleNamespace(monotonic=lambda: clock[0]), raising=False)

    @asynccontextmanager
    async def slow_commit():
        async with factory.begin() as session:
            yield session
            # Simulate delayed commit; another generation can claim immediately
            # after release. Heartbeat has not yet revoked this local context.
            clock[0] += 120

    reporter = DurableQuotaUsageReporter(SimpleNamespace(begin=slow_commit), journal_directory=tmp_path)
    raw = FakeRawModel([])
    candidate = _candidate("test", raw)
    model = ResilientChatModel([candidate], normalize_response=False, reporter_slot=ModelUsageReporterSlot(reporter))
    with bind_usage_attribution(invocation().attribution), pytest.raises(LostTurnClaimError):
        if stream:
            async for _ in model.astream("old lease"):
                pass
        else:
            await model.ainvoke("old lease")
    assert raw.calls == candidate.circuit.failures == 0
    assert reservation.pending_invocations == account.unknown_usage_events == 0
    assert next(iter(events.values())).error_kind == "provider_not_dispatched"
