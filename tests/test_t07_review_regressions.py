from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.learning import TeachingMaterials
from core.session_context import SessionContext
from gateway.contracts import GatewayEventType, TurnStatus
from gateway.dispatch import TurnTask
from gateway.mysql_repository import MySQLGatewayRepository
from gateway.turn_execution import InProcessTurnExecutor


def _task(
    turn_id: str = "turn-1", quota_reservation_id: str | None = None
) -> TurnTask:
    return TurnTask(
        context=SessionContext(session_id="session-1"), turn_id=turn_id,
        content="hello", learning_context=None, learning_progress=None,
        exercise_state=None, teaching_materials=TeachingMaterials(),
        guided_session_id=None, exercise_session_id=None,
        quota_reservation_id=quota_reservation_id,
    )


def test_legacy_task_requires_and_uses_its_durable_quota_reservation():
    from gateway.dispatch import require_durable_quota_reservation
    from gateway.redis_transport import TurnTaskCodec

    legacy_payload = json.loads(TurnTaskCodec.dumps(_task()))
    legacy_payload.pop("quota_reservation_id")
    legacy = TurnTaskCodec.loads(json.dumps(legacy_payload))
    recovered = require_durable_quota_reservation(legacy, "reservation-1")

    assert recovered.quota_reservation_id == "reservation-1"
    assert legacy.quota_reservation_id is None
    with pytest.raises(ValueError, match="no durable model quota reservation"):
        require_durable_quota_reservation(legacy, None)
    with pytest.raises(ValueError, match="does not match durable Turn"):
        require_durable_quota_reservation(
            _task(quota_reservation_id="reservation-stale"), "reservation-1"
        )


@pytest.mark.parametrize("write", ["status", "event", "learning"])
def test_old_worker_cannot_mutate_turn_or_learning_state(write):
    from gateway.execution_fence import TurnWriteFence, bind_turn_write_fence
    from server.application.turn_reliability import LostTurnClaimError

    repository = object.__new__(MySQLGatewayRepository)
    repository._engine = MagicMock()
    connection = repository._engine.begin.return_value.__enter__.return_value
    connection.execute.return_value.mappings.return_value.first.return_value = None

    with bind_turn_write_fence(TurnWriteFence("turn-1", 2, "old-worker")):
        with pytest.raises(LostTurnClaimError):
            if write == "status":
                repository.update_turn("turn-1", TurnStatus.COMPLETED, final_text="stale")
            elif write == "event":
                repository.append_event(turn_id="turn-1", session_id="session-1", event_type=GatewayEventType.MESSAGE_DELTA)
            else:
                repository.advance_guided_session("guided-1", tutor_message="stale")

    statements = [str(call.args[0]) for call in connection.execute.call_args_list]
    assert any(all(part in statement for part in ("claim_generation", "claimed_by", "lease_expires_at", "FOR UPDATE")) for statement in statements)
    assert not any(statement.lstrip().startswith(("UPDATE", "INSERT")) for statement in statements)


@pytest.mark.asyncio
async def test_fenced_cancellation_does_not_finalize_cancelled_without_durable_command():
    from gateway.execution_fence import TurnWriteFence, bind_turn_write_fence

    started = asyncio.Event()

    class Engine:
        async def run_turn(self, *_args):
            started.set()
            await asyncio.Event().wait()

        async def cancel_turn(self, *_args):
            return None

    repository = MagicMock()
    events = AsyncMock()
    executor = InProcessTurnExecutor(Engine(), repository, events)
    fence = TurnWriteFence("turn-1", 2, "old-worker")
    with bind_turn_write_fence(fence):
        execution = asyncio.create_task(executor.run(_task()))
    await started.wait()
    fence.revoke()
    execution.cancel()
    with pytest.raises(asyncio.CancelledError):
        await execution
    assert [call.args[1] for call in repository.update_turn.call_args_list] == [TurnStatus.RUNNING]
    assert not any(call.args[2] == GatewayEventType.TURN_CANCELLED for call in events.await_args_list)


def test_handover_event_is_replayable_after_recovery():
    repository = object.__new__(MySQLGatewayRepository)
    repository._engine = MagicMock()
    connection = repository._engine.connect.return_value.__enter__.return_value
    connection.execute.return_value.mappings.return_value.all.return_value = [{
        "id": "event-1", "conversation_id": "session-1", "sequence": 1,
        "event_type": "turn.handover", "created_at": datetime.now(timezone.utc),
        "payload_json": {"reason": "lease_expired"},
    }]
    assert repository.events_after("turn-1")[0].type.value == "turn.handover"


def test_sync_repository_applies_explicit_pool_and_timeout_configuration():
    from server.infrastructure.mysql.config import DatabaseConfig

    config = DatabaseConfig("mysql+aiomysql://user:password@localhost/test", pool_size=4, max_overflow=2, pool_timeout_s=5)
    with patch("gateway.mysql_repository.create_engine") as create:
        MySQLGatewayRepository(config.url, database_config=config)
    assert create.call_args.kwargs["pool_size"] == 4
    assert create.call_args.kwargs["max_overflow"] == 2
    assert create.call_args.kwargs["pool_timeout"] == 5


@pytest.mark.asyncio
async def test_sms_consumption_preserves_send_history():
    from server.auth import code_store
    from server.infrastructure.mysql.models import AuthCodeModel

    row = AuthCodeModel(id="sms-1", kind="sms", subject="13800138000", code_hash=code_store._code_hash("123456"), expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(seconds=120))
    session = AsyncMock()
    session.scalar.return_value = row
    assert await code_store.consume_code(session, kind="sms", subject=row.subject, code="123456")
    assert row.kind == "sms_history"
    assert not any(str(call.args[0]).lstrip().startswith("DELETE") for call in session.execute.await_args_list)


@pytest.mark.parametrize("state,error_kind,terminal_count", [
    ("timeout", "upstream_auth_failed", 1),
    ("failed", "AuthenticationError", 1),
    ("failed", "upstream_auth_failed", 0),
    ("completed", None, 1),
])
def test_provider_load_case_rejects_wrong_terminal_or_error(state, error_kind, terminal_count):
    from tests.load.t07_classroom_load import provider_case_failures

    result = {"submit_status": 202, "state": state, "error_kind": error_kind, "terminal_event_count": terminal_count, "session_after_provider_error_status": 200}
    assert provider_case_failures("http_401", result)


@pytest.mark.asyncio
async def test_fast_slot_is_refilled_while_sibling_is_still_running():
    from gateway.redis_transport import RedisTransportConfig, RedisWorkerRuntime, TurnTaskCodec
    from tests.test_redis_transport import FakeRedis

    redis = FakeRedis()
    redis.reads.append([("turns", [(str(index), {"payload": TurnTaskCodec.dumps(_task(name))}) for index, name in enumerate(("slow", "fast", "next"))])])
    release_slow = asyncio.Event()
    next_started = asyncio.Event()

    async def execute(task):
        if task.turn_id == "slow":
            await release_slow.wait()
        elif task.turn_id == "next":
            next_started.set()

    worker = RedisWorkerRuntime(redis, RedisTransportConfig(task_stream="turns", worker_concurrency=2), execute, consumer_name="worker")
    try:
        assert await asyncio.wait_for(worker.run_once(block_ms=0), 1) == 1
        assert worker.active_count == 1
        assert await asyncio.wait_for(worker.run_once(block_ms=0), 1) == 1
        assert next_started.is_set()
        assert not release_slow.is_set()
        assert redis.read_counts == [2, 1]
    finally:
        release_slow.set()
        await worker.close(grace_s=1)


@pytest.mark.asyncio
async def test_duplicate_message_preserves_active_owner_and_cancellation():
    from gateway.redis_transport import RedisTransportConfig, RedisWorkerRuntime, TurnTaskCodec
    from tests.test_redis_transport import FakeRedis

    redis = FakeRedis()
    entered = asyncio.Event()
    terminal: set[str] = set()
    executions = []

    async def execute(task):
        executions.append(task.turn_id)
        entered.set()
        await asyncio.Event().wait()

    async def cancel(task):
        terminal.add(task.turn_id)

    worker = RedisWorkerRuntime(redis, RedisTransportConfig(task_stream="turns"), execute, consumer_name="worker", cancel_pending=cancel, is_terminal=lambda task: task.turn_id in terminal)
    fields = {"payload": TurnTaskCodec.dumps(_task())}
    owner = asyncio.create_task(worker._process_message("1-0", fields))
    await entered.wait()
    duplicate = asyncio.create_task(worker._process_message("2-0", fields))
    await asyncio.sleep(0)
    assert executions == ["turn-1"]
    assert worker.active_count == 1
    assert worker.cancel_active("turn-1")
    assert await asyncio.wait_for(asyncio.gather(owner, duplicate), 1) == [1, 1]
    assert worker.active_count == 0
    assert {ack[2] for ack in redis.acks} == {"1-0", "2-0"}


def test_gateway_and_auxiliary_stores_share_one_bounded_sync_pool():
    from configs.settings import settings
    from server.infrastructure.mysql.sync_engine import configured_engine, release_engine
    from server.infrastructure.mysql.config import DatabaseConfig

    url = "mysql+aiomysql://test:test@localhost/shared_pool_regression"
    runtime = {**settings.database_runtime, "url": url, "sync_pool_size": 4, "sync_max_overflow": 2}
    with patch("server.infrastructure.mysql.sync_engine.sqlalchemy_create_engine") as create:
        shared = configured_engine(url, runtime)
        repository = MySQLGatewayRepository(url, database_config=DatabaseConfig.from_runtime(runtime, synchronous=True))
        assert shared is repository._engine
        create.assert_called_once()
        assert create.call_args.kwargs["pool_timeout"] == 5
        release_engine(shared)
        create.return_value.dispose.assert_not_called()
        repository.close()
        create.return_value.dispose.assert_called_once()


def test_compose_accounts_for_every_async_and_shared_sync_pool():
    from pathlib import Path
    import yaml

    services = yaml.safe_load((Path(__file__).resolve().parents[1] / "compose.yaml").read_text(encoding="utf-8"))["services"]
    maxima = {}
    for name, async_engines in (("nova-web", 1), ("nova-worker", 2), ("nova-monitor", 1), ("nova-sandbox-manager", 1)):
        env = services[name]["environment"]
        async_maximum = int(env["NLP_AGENT_DB_POOL_SIZE"]) + int(env["NLP_AGENT_DB_MAX_OVERFLOW"])
        sync_maximum = int(env["NLP_AGENT_DB_SYNC_POOL_SIZE"]) + int(env["NLP_AGENT_DB_SYNC_MAX_OVERFLOW"])
        maxima[name] = async_engines * async_maximum + sync_maximum
        assert env["NLP_AGENT_DB_POOL_TIMEOUT_S"] == "5"
    assert maxima == {"nova-web": 20, "nova-worker": 12, "nova-monitor": 5, "nova-sandbox-manager": 10}
    assert maxima["nova-web"] + 2 * maxima["nova-worker"] + maxima["nova-monitor"] + maxima["nova-sandbox-manager"] + 10 == 69


@pytest.mark.asyncio
async def test_sms_resend_expires_old_codes_without_deleting_send_history():
    from server.auth import code_store
    from server.infrastructure.mysql.models import AuthCodeModel

    attempt = AuthCodeModel(id="new-send", kind="sms_attempt", subject="13800138000", code_hash="hash", created_at=datetime.now(timezone.utc).replace(tzinfo=None), expires_at=datetime.now(timezone.utc).replace(tzinfo=None))
    session = AsyncMock()
    session.scalar.side_effect = [attempt, None]
    await code_store.complete_sms_attempt(session, attempt_id=attempt.id, code="654321", ttl_s=120)
    assert attempt.kind == "sms"
    assert attempt.code_hash == code_store._code_hash("654321")
    invalidate = session.execute.await_args.args[0]
    assert str(invalidate).startswith("UPDATE nlp_auth_codes")
    assert "sms_history" in invalidate.compile().params.values()
    assert not any(str(call.args[0]).startswith("DELETE") for call in session.execute.await_args_list)


@pytest.mark.asyncio
async def test_late_old_sms_delivery_cannot_replace_newer_delivered_code():
    from server.auth import code_store
    from server.infrastructure.mysql.models import AuthCodeModel

    old_attempt = AuthCodeModel(id="old-send", kind="sms_attempt", subject="13800138000", code_hash="hash", created_at=datetime.now(timezone.utc).replace(tzinfo=None), expires_at=datetime.now(timezone.utc).replace(tzinfo=None))
    session = AsyncMock()
    session.scalar.side_effect = [old_attempt, "newer-send"]
    await code_store.complete_sms_attempt(session, attempt_id=old_attempt.id, code="123456", ttl_s=120)
    assert old_attempt.kind == "sms_history"
    session.execute.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("response_status,payload", [(403, {"mode": "http_401"}), (200, {"mode": "success"})])
async def test_provider_mode_switch_must_succeed_and_match_requested_mode(response_status, payload):
    import httpx
    from tests.load.t07_classroom_load import LoadTestError, set_fake_provider_mode

    control = AsyncMock()
    control.post.return_value = httpx.Response(response_status, json=payload)
    with pytest.raises(LoadTestError):
        await set_fake_provider_mode(control, "http://127.0.0.1:8767", "test-token", "http_401")


@pytest.mark.parametrize("write", ["status", "event"])
def test_lease_expiring_between_lock_and_write_rejects_actual_sql_write(write):
    from gateway.execution_fence import TurnWriteFence, bind_turn_write_fence, LostTurnClaimError

    repository = object.__new__(MySQLGatewayRepository)
    repository._engine = MagicMock()
    connection = repository._engine.begin.return_value.__enter__.return_value

    def execute(statement, _parameters=None):
        result = MagicMock()
        result.mappings.return_value.first.return_value = {
            "id": "turn-1", "user_id": "user-1", "result_text": None,
            "error_message": None, "status": "running", "workspace_id": "ws-1",
        }
        result.scalar_one.return_value = 1
        result.rowcount = 0
        return result

    connection.execute.side_effect = execute
    with patch("gateway.mysql_repository.SyncStorageQuota"):
        with bind_turn_write_fence(TurnWriteFence("turn-1", 2, "worker")):
            with pytest.raises(LostTurnClaimError):
                if write == "status":
                    repository.update_turn("turn-1", TurnStatus.COMPLETED)
                else:
                    repository.append_event(turn_id="turn-1", session_id="session-1", event_type=GatewayEventType.MESSAGE_DELTA)
    statement, parameters = connection.execute.call_args.args
    assert all(part in str(statement) for part in ("claim_generation=:fence_generation", "claimed_by=:fence_owner", "lease_expires_at>UTC_TIMESTAMP(6)"))
    assert parameters["fence_generation"] == 2
    assert parameters["fence_owner"] == "worker"


def test_lease_expiring_during_learning_update_rolls_back_transaction():
    from gateway.execution_fence import TurnWriteFence, bind_turn_write_fence, LostTurnClaimError

    repository = object.__new__(MySQLGatewayRepository)
    repository._engine = MagicMock()
    transaction = repository._engine.begin.return_value
    connection = transaction.__enter__.return_value
    checks = 0

    def execute(statement, _parameters=None):
        nonlocal checks
        result = MagicMock()
        if "fence_turn" in str(statement):
            checks += 1
            result.mappings.return_value.first.return_value = {"id": "turn-1"} if checks == 1 else None
        else:
            result.mappings.return_value.first.return_value = {"state_json": {}}
        return result

    connection.execute.side_effect = execute
    with bind_turn_write_fence(TurnWriteFence("turn-1", 2, "worker")):
        with pytest.raises(LostTurnClaimError):
            repository.advance_guided_session("guided-1", tutor_message="must roll back")
    assert checks == 2
    assert transaction.__exit__.call_args.args[0] is LostTurnClaimError


@pytest.mark.asyncio
async def test_replay_drains_full_pages_and_waits_for_late_terminal():
    import httpx
    from tests.load.t07_classroom_load import collect_terminal_events

    client = AsyncMock()
    client.get.side_effect = [
        httpx.Response(200, json={"items": [{"sequence": index, "type": "message.delta"} for index in range(1, 501)]}),
        httpx.Response(200, json={"items": []}),
        httpx.Response(200, json={"items": [{"sequence": 501, "type": "turn.completed"}]}),
    ]
    events, status = await collect_terminal_events(client, "turn-1")
    assert status == 200
    assert len(events) == 501
    assert events[-1]["type"] == "turn.completed"
    assert [call.kwargs["params"]["after_sequence"] for call in client.get.await_args_list] == [0, 500, 500]


@pytest.mark.asyncio
async def test_load_login_uses_rotated_csrf_for_conversation_creation():
    import httpx
    from tests.load.t07_classroom_load import _login_user

    seen = []

    def handler(request):
        if request.url.path.endswith("/login"):
            return httpx.Response(200, json={"csrf_token": "stale", "workspace_ids": ["workspace-1"]})
        if request.url.path.endswith("/session"):
            return httpx.Response(200, json={"csrf_token": "rotated", "user_id": "user-1"})
        seen.append(request.headers.get("X-CSRF-Token"))
        return httpx.Response(201, json={"session_id": "session-1"})

    client = httpx.AsyncClient(base_url="http://127.0.0.1:8765", transport=httpx.MockTransport(handler))
    with patch("tests.load.t07_classroom_load.httpx.AsyncClient", return_value=client):
        result = await _login_user("http://127.0.0.1:8765", "http://127.0.0.1:8765", {"username": "test", "password": "test"}, 0, 0, 0)
    try:
        assert seen == ["rotated"]
        assert result["csrf"] == "rotated"
        assert result["conversation_status"] == 201
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_expired_claim_heartbeat_cannot_resurrect_lease():
    from server.application.turn_reliability import TurnReliabilityService, utc_now
    from server.infrastructure.mysql.models import TurnModel
    from gateway.execution_fence import LostTurnClaimError

    expired = utc_now() - timedelta(seconds=1)
    turn = TurnModel(id="turn-1", status="running", claim_generation=2, claimed_by="old-worker", lease_expires_at=expired)
    session = AsyncMock()
    session.scalar.return_value = turn
    with pytest.raises(LostTurnClaimError):
        await TurnReliabilityService().heartbeat(session, turn_id="turn-1", generation=2, worker_id="old-worker", lease_s=30)
    assert turn.lease_expires_at == expired
    session.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_late_cancellation_cannot_rewrite_completed_turn_in_heartbeat():
    from server.application.turn_reliability import TurnReliabilityService, utc_now
    from server.infrastructure.mysql.models import TurnModel, TurnCancellationModel

    turn = TurnModel(id="turn-1", status="completed", claim_generation=2, claimed_by="worker", lease_expires_at=utc_now() + timedelta(seconds=30))
    session = AsyncMock()
    session.scalar.side_effect = [turn, TurnCancellationModel(turn_id="turn-1", requested_by="user")]
    assert await TurnReliabilityService().heartbeat(session, turn_id="turn-1", generation=2, worker_id="worker", lease_s=30) is True
    assert turn.status == "completed"
    session.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_heartbeat_stops_execution_for_already_durable_cancellation():
    from server.application.turn_reliability import TurnReliabilityService, utc_now
    from server.infrastructure.mysql.models import TurnModel

    turn = TurnModel(id="turn-1", status="cancelled", claim_generation=2, claimed_by="worker", lease_expires_at=utc_now() + timedelta(seconds=30))
    session = AsyncMock()
    session.scalar.return_value = turn
    assert await TurnReliabilityService().heartbeat(session, turn_id="turn-1", generation=2, worker_id="worker", lease_s=30) is False
    assert turn.status == "cancelled"
    session.flush.assert_not_awaited()


def test_late_cancel_request_does_not_create_cancellation_after_terminal_commit():
    repository = object.__new__(MySQLGatewayRepository)
    repository._engine = MagicMock()
    connection = repository._engine.begin.return_value.__enter__.return_value
    connection.execute.return_value.mappings.return_value.first.return_value = {"status": "completed"}
    repository.get_turn = MagicMock(return_value=object())
    repository._record = MagicMock(return_value=object())
    repository.request_turn_cancellation(turn_id="turn-1", requested_by="user")
    assert not any(str(call.args[0]).startswith(("INSERT", "UPDATE")) for call in connection.execute.call_args_list)
