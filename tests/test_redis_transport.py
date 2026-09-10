import asyncio
from unittest.mock import AsyncMock, MagicMock
import pytest

from core.session_context import SessionContext
from core.learning import TeachingMaterials
from gateway.dispatch import ExecutionAuthorizationContext, TurnTask
from gateway.contracts import GatewayEvent, GatewayEventType
from gateway.redis_transport import RedisEventPublisher, RedisTransportConfig, RedisTurnDispatcher, RedisWorkerRuntime, TurnTaskCodec
from server.application.turn_reliability import CancelledTurnClaim
from server.worker.fencing import FencedTurnExecutor


class FakeRedis:
    def __init__(self):
        self.streams = []
        self.messages = []
        self.acks = []
        self.claims = []
        self.reads = []
        self.values = {}
        self.publish_error = None
        self.claim_failures = 0
        self.get_results = []
        self.read_blocks = []
        self.autoclaimed = []
        self.autoclaim_requests = []

    async def xadd(self, stream, fields):
        self.streams.append((stream, fields))

    async def publish(self, channel, payload):
        if self.publish_error is not None:
            raise self.publish_error
        self.messages.append((channel, payload))

    async def set(self, key, value, **options):
        self.values[key] = value
        return True

    async def get(self, key):
        if self.get_results:
            return self.get_results.pop(0)
        return self.values.get(key)

    async def delete(self, key):
        self.values.pop(key, None)

    async def xclaim(self, stream, group, consumer, min_idle_time, message_ids, **options):
        if self.claim_failures:
            self.claim_failures -= 1
            raise ConnectionError("temporary heartbeat failure")
        self.claims.append((stream, group, consumer, tuple(message_ids)))
        return message_ids

    async def aclose(self):
        pass

    async def xgroup_create(self, stream, group, id, mkstream):
        return True

    async def xreadgroup(self, group, consumer, streams, count, block):
        self.read_blocks.append(block)
        return self.reads.pop(0) if self.reads else []

    async def xautoclaim(
        self, stream, group, consumer, min_idle_time, start_id, **options
    ):
        self.autoclaim_requests.append(
            (stream, group, consumer, min_idle_time, start_id, options)
        )
        messages = self.autoclaimed.pop(0) if self.autoclaimed else []
        return ("0-0", messages)

    async def xack(self, stream, group, message_id):
        self.acks.append((stream, group, message_id))


def test_turn_task_codec_preserves_worker_payload():
    task = TurnTask(
        context=SessionContext(session_id="session-1", user_id="alice", workspace_id="w1"),
        turn_id="turn-1",
        content="Explain attention",
        learning_context=None,
        learning_progress=None,
        exercise_state=None,
        teaching_materials=TeachingMaterials(),
        guided_session_id=None,
        exercise_session_id=None,
        model_profile="qwen",
        authorization=ExecutionAuthorizationContext(
            submitter_user_id="alice", workspace_id="w1", authorization_version=7
        ),
    )

    restored = TurnTaskCodec.loads(TurnTaskCodec.dumps(task))

    assert restored == task
    assert restored.model_profile == "qwen"


@pytest.mark.asyncio
async def test_worker_uses_configured_poll_block_and_preserves_explicit_zero():
    redis = FakeRedis()
    config = RedisTransportConfig(
        task_stream="turns", task_group="workers", poll_block_ms=1_234
    )
    worker = RedisWorkerRuntime(
        redis, config, lambda _task: None,
        consumer_name="worker-1", reclaim_pending=False,
    )

    assert await worker.run_once() == 0
    assert await worker.run_once(block_ms=0) == 0
    assert redis.read_blocks == [1_234, 0]


@pytest.mark.parametrize("poll_block_ms", [0, 4_001, 5_000])
def test_redis_transport_rejects_unsafe_poll_block_ms(poll_block_ms):
    with pytest.raises(ValueError, match="between 1 and 4000 milliseconds"):
        RedisTransportConfig(poll_block_ms=poll_block_ms)


def test_redis_transport_accepts_maximum_safe_poll_block_ms():
    assert RedisTransportConfig(poll_block_ms=4_000).poll_block_ms == 4_000


def test_turn_task_codec_rejects_unknown_protocol_version():
    with pytest.raises(ValueError, match="unsupported turn task version"):
        TurnTaskCodec.loads('{"version":2}')


def test_turn_task_codec_rejects_non_object_json():
    with pytest.raises(ValueError, match="must be a JSON object"):
        TurnTaskCodec.loads("[]")


async def test_redis_dispatcher_submits_and_cancels_through_transport():
    redis = FakeRedis()
    config = RedisTransportConfig(url="redis://example", task_stream="turns", control_channel="control")
    dispatcher = RedisTurnDispatcher(redis, config)
    task = TurnTask(
        context=SessionContext(session_id="session-1"), turn_id="turn-1", content="hello",
        learning_context=None, learning_progress=None, exercise_state=None,
        teaching_materials=TeachingMaterials(), guided_session_id=None, exercise_session_id=None,
    )

    await dispatcher.submit(task)
    await dispatcher.cancel("turn-1")

    assert redis.streams[0][0] == "turns"
    assert TurnTaskCodec.loads(redis.streams[0][1]["payload"]) == task
    assert redis.messages == [("control", '{"type":"turn.cancel","turn_id":"turn-1"}')]


async def test_worker_executes_stream_task_and_acknowledges_it():
    redis = FakeRedis()
    config = RedisTransportConfig(task_stream="turns", task_group="workers")
    task = TurnTask(
        context=SessionContext(session_id="session-1"), turn_id="turn-1", content="hello",
        learning_context=None, learning_progress=None, exercise_state=None,
        teaching_materials=TeachingMaterials(), guided_session_id=None, exercise_session_id=None,
    )
    redis.reads.append([("turns", [("1-0", {"payload": TurnTaskCodec.dumps(task)})])])
    executed = []
    worker = RedisWorkerRuntime(redis, config, executed.append, consumer_name="worker-1")

    processed = await worker.run_once(block_ms=0)

    assert processed == 1
    assert executed == [task]
    assert redis.acks == [("turns", "workers", "1-0")]


async def test_mysql_fenced_worker_reclaims_and_acks_stale_terminal_delivery():
    redis = FakeRedis()
    config = RedisTransportConfig(
        task_stream="turns", task_group="workers", reclaim_idle_ms=30
    )
    task = TurnTask(
        context=SessionContext(session_id="session-1"),
        turn_id="turn-stale",
        content="hello",
        learning_context=None,
        learning_progress=None,
        exercise_state=None,
        teaching_materials=TeachingMaterials(),
        guided_session_id=None,
        exercise_session_id=None,
    )
    redis.autoclaimed.append(
        [("9-0", {"payload": TurnTaskCodec.dumps(task)})]
    )
    executed = []
    worker = RedisWorkerRuntime.for_fenced_mysql(
        redis,
        config,
        executed.append,
        consumer_name="worker-new",
        is_terminal=lambda _task: True,
    )

    assert await worker.run_once(block_ms=0) == 1
    assert executed == []
    assert redis.acks == [("turns", "workers", "9-0")]
    assert redis.autoclaim_requests[0][:5] == (
        "turns",
        "workers",
        "worker-new",
        30,
        "0-0",
    )


@pytest.mark.asyncio
async def test_worker_can_disable_redis_pending_reclaim_when_mysql_owns_leases():
    redis = FakeRedis()
    config = RedisTransportConfig(task_stream="turns", task_group="workers")
    task = TurnTask(
        context=SessionContext(session_id="session-1"), turn_id="turn-1", content="hello",
        learning_context=None, learning_progress=None, exercise_state=None,
        teaching_materials=TeachingMaterials(), guided_session_id=None, exercise_session_id=None,
    )
    redis.reads.append([("turns", [("1-0", {"payload": TurnTaskCodec.dumps(task)})])])
    worker = RedisWorkerRuntime(
        redis, config, lambda _task: None, consumer_name="worker-1", reclaim_pending=False
    )

    assert await worker.run_once(block_ms=0) == 1
    assert redis.claims == []


async def test_event_publisher_sends_gateway_event_to_pubsub():
    redis = FakeRedis()
    config = RedisTransportConfig(event_channel="events")
    publisher = RedisEventPublisher(redis, config)
    event = GatewayEvent(
        event_id="event-1", turn_id="turn-1", session_id="session-1", sequence=1,
        type=GatewayEventType.TURN_STARTED,
    )

    await publisher.publish(event)

    assert redis.messages[0][0] == "events"
    assert GatewayEvent.model_validate_json(redis.messages[0][1]) == event


async def test_dispatcher_records_durable_cancellation_marker():
    redis = FakeRedis()
    config = RedisTransportConfig(control_channel="control", cancel_key_prefix="cancel:")
    dispatcher = RedisTurnDispatcher(redis, config)

    await dispatcher.cancel("turn-1")

    assert redis.values["cancel:turn-1"] == "1"


async def test_worker_acknowledges_user_cancel_without_stopping_consumer():
    redis = FakeRedis()
    config = RedisTransportConfig(task_stream="turns", task_group="workers")
    task = TurnTask(
        context=SessionContext(session_id="session-1"), turn_id="turn-1", content="hello",
        learning_context=None, learning_progress=None, exercise_state=None,
        teaching_materials=TeachingMaterials(), guided_session_id=None, exercise_session_id=None,
    )
    redis.reads.append([("turns", [("1-0", {"payload": TurnTaskCodec.dumps(task)})])])
    started = asyncio.Event()

    async def execute(_task):
        started.set()
        await asyncio.Event().wait()

    worker = RedisWorkerRuntime(redis, config, execute, consumer_name="worker-1")
    run = asyncio.create_task(worker.run_once(block_ms=0))
    await started.wait()

    assert worker.cancel_active("turn-1") is True
    assert await run == 1
    assert redis.acks == [("turns", "workers", "1-0")]


async def test_worker_renews_pending_message_while_turn_is_running():
    redis = FakeRedis()
    config = RedisTransportConfig(
        task_stream="turns", task_group="workers", reclaim_idle_ms=30
    )
    task = TurnTask(
        context=SessionContext(session_id="session-1"), turn_id="turn-1", content="hello",
        learning_context=None, learning_progress=None, exercise_state=None,
        teaching_materials=TeachingMaterials(), guided_session_id=None, exercise_session_id=None,
    )
    redis.reads.append([("turns", [("1-0", {"payload": TurnTaskCodec.dumps(task)})])])
    release = asyncio.Event()

    async def execute(_task):
        await release.wait()

    worker = RedisWorkerRuntime(redis, config, execute, consumer_name="worker-1")
    run = asyncio.create_task(worker.run_once(block_ms=0))
    await asyncio.sleep(0.03)
    release.set()

    assert await run == 1
    assert redis.claims


async def test_worker_retries_heartbeat_after_transient_redis_failure():
    redis = FakeRedis()
    redis.claim_failures = 1
    config = RedisTransportConfig(
        task_stream="turns", task_group="workers", reclaim_idle_ms=30
    )
    task = TurnTask(
        context=SessionContext(session_id="session-1"), turn_id="turn-1", content="hello",
        learning_context=None, learning_progress=None, exercise_state=None,
        teaching_materials=TeachingMaterials(), guided_session_id=None, exercise_session_id=None,
    )
    redis.reads.append([("turns", [("1-0", {"payload": TurnTaskCodec.dumps(task)})])])
    release = asyncio.Event()

    async def execute(_task):
        await release.wait()

    worker = RedisWorkerRuntime(redis, config, execute, consumer_name="worker-1")
    run = asyncio.create_task(worker.run_once(block_ms=0))

    async def wait_for_retry() -> None:
        while not redis.claims:
            await asyncio.sleep(0.005)

    await asyncio.wait_for(wait_for_retry(), timeout=1)
    release.set()

    assert await run == 1
    assert redis.claims


async def test_worker_heartbeat_observes_durable_cancel_after_execution_starts():
    redis = FakeRedis()
    config = RedisTransportConfig(
        task_stream="turns", task_group="workers", reclaim_idle_ms=30
    )
    task = TurnTask(
        context=SessionContext(session_id="session-1"), turn_id="turn-1", content="hello",
        learning_context=None, learning_progress=None, exercise_state=None,
        teaching_materials=TeachingMaterials(), guided_session_id=None, exercise_session_id=None,
    )
    redis.reads.append([("turns", [("1-0", {"payload": TurnTaskCodec.dumps(task)})])])
    started = asyncio.Event()
    cancelled = []

    async def execute(_task):
        started.set()
        await asyncio.Event().wait()

    worker = RedisWorkerRuntime(
        redis,
        config,
        execute,
        consumer_name="worker-1",
        cancel_pending=cancelled.append,
    )
    run = asyncio.create_task(worker.run_once(block_ms=0))
    await started.wait()
    redis.values[f"{config.cancel_key_prefix}turn-1"] = "1"

    assert await asyncio.wait_for(run, timeout=0.2) == 1
    assert cancelled == [task]
    assert redis.acks == [("turns", "workers", "1-0")]


async def test_worker_acknowledges_durable_cancel_before_execution():
    redis = FakeRedis()
    config = RedisTransportConfig(
        task_stream="turns", task_group="workers", cancel_key_prefix="cancel:"
    )
    task = TurnTask(
        context=SessionContext(session_id="session-1"), turn_id="turn-1", content="hello",
        learning_context=None, learning_progress=None, exercise_state=None,
        teaching_materials=TeachingMaterials(), guided_session_id=None, exercise_session_id=None,
    )
    redis.values["cancel:turn-1"] = "1"
    redis.reads.append([("turns", [("1-0", {"payload": TurnTaskCodec.dumps(task)})])])
    executed = []
    cancelled = []
    worker = RedisWorkerRuntime(
        redis, config, executed.append, consumer_name="worker-1", cancel_pending=cancelled.append
    )

    assert await worker.run_once(block_ms=0) == 1
    assert executed == []
    assert cancelled == [task]
    assert redis.acks == [("turns", "workers", "1-0")]


async def test_worker_closes_cancel_race_before_execution_becomes_active():
    redis = FakeRedis()
    redis.get_results = [None, "1"]
    config = RedisTransportConfig(task_stream="turns", task_group="workers")
    task = TurnTask(
        context=SessionContext(session_id="session-1"), turn_id="turn-1", content="hello",
        learning_context=None, learning_progress=None, exercise_state=None,
        teaching_materials=TeachingMaterials(), guided_session_id=None, exercise_session_id=None,
    )
    redis.reads.append([("turns", [("1-0", {"payload": TurnTaskCodec.dumps(task)})])])
    executed = []
    worker = RedisWorkerRuntime(redis, config, executed.append, consumer_name="worker-1")

    assert await worker.run_once(block_ms=0) == 1
    assert executed == []
    assert redis.acks == [("turns", "workers", "1-0")]


@pytest.mark.asyncio
async def test_worker_cancel_returns_before_slow_execution_cleanup_finishes():
    redis = FakeRedis()
    config = RedisTransportConfig(task_stream="turns", task_group="workers")
    task = TurnTask(
        context=SessionContext(session_id="session-1"), turn_id="turn-1", content="hello",
        learning_context=None, learning_progress=None, exercise_state=None,
        teaching_materials=TeachingMaterials(), guided_session_id=None, exercise_session_id=None,
    )
    redis.reads.append([("turns", [("1-0", {"payload": TurnTaskCodec.dumps(task)})])])
    started = asyncio.Event()
    release_cleanup = asyncio.Event()
    cancelled = []

    async def execute(_task):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            await release_cleanup.wait()

    worker = RedisWorkerRuntime(
        redis,
        config,
        execute,
        consumer_name="worker-1",
        cancel_pending=cancelled.append,
    )
    run = asyncio.create_task(worker.run_once(block_ms=0))
    await started.wait()

    assert worker.cancel_active(task.turn_id) is True
    assert await asyncio.wait_for(run, timeout=0.5) == 1
    assert cancelled == [task]
    assert redis.acks == [("turns", "workers", "1-0")]
    release_cleanup.set()
    await asyncio.sleep(0)


async def test_worker_runs_cancellation_finalizer_before_terminal_ack():
    redis = FakeRedis()
    config = RedisTransportConfig(
        task_stream="turns", task_group="workers", cancel_key_prefix="cancel:"
    )
    task = TurnTask(
        context=SessionContext(session_id="session-1"), turn_id="turn-1", content="hello",
        learning_context=None, learning_progress=None, exercise_state=None,
        teaching_materials=TeachingMaterials(), guided_session_id=None, exercise_session_id=None,
    )
    redis.values["cancel:turn-1"] = "1"
    redis.reads.append([("turns", [("1-0", {"payload": TurnTaskCodec.dumps(task)})])])
    cancelled = []
    worker = RedisWorkerRuntime(
        redis,
        config,
        lambda _task: pytest.fail("cancelled delivery must not execute"),
        consumer_name="worker-1",
        cancel_pending=cancelled.append,
        is_terminal=lambda _task: True,
    )

    assert await worker.run_once(block_ms=0) == 1
    assert cancelled == [task]
    assert redis.acks == [("turns", "workers", "1-0")]


@pytest.mark.asyncio
async def test_worker_finalizes_cancellation_when_database_claim_is_already_cancelled():
    redis = FakeRedis()
    config = RedisTransportConfig(task_stream="turns", task_group="workers")
    task = TurnTask(
        context=SessionContext(session_id="session-1"), turn_id="turn-1", content="hello",
        learning_context=None, learning_progress=None, exercise_state=None,
        teaching_materials=TeachingMaterials(), guided_session_id=None, exercise_session_id=None,
    )
    redis.reads.append([("turns", [("1-0", {"payload": TurnTaskCodec.dumps(task)})])])
    unit_of_work = AsyncMock()
    unit_of_work.session = AsyncMock()
    unit_of_work.__aenter__.return_value = unit_of_work
    factory = MagicMock()
    factory.begin.return_value = unit_of_work
    reliability = AsyncMock()
    reliability.claim_turn.return_value = CancelledTurnClaim()
    cancelled = []
    worker = RedisWorkerRuntime(
        redis,
        config,
        FencedTurnExecutor(factory, reliability, lambda *_args: None, worker_id="worker-1", lease_s=30),
        consumer_name="worker-1",
        cancel_pending=cancelled.append,
    )

    assert await worker.run_once(block_ms=0) == 1
    assert cancelled == [task]
    assert redis.acks == [("turns", "workers", "1-0")]


async def test_worker_acknowledges_terminal_redelivery_without_reexecution():
    redis = FakeRedis()
    config = RedisTransportConfig(task_stream="turns", task_group="workers")
    task = TurnTask(
        context=SessionContext(session_id="session-1"), turn_id="turn-1", content="hello",
        learning_context=None, learning_progress=None, exercise_state=None,
        teaching_materials=TeachingMaterials(), guided_session_id=None, exercise_session_id=None,
    )
    redis.reads.append([("turns", [("1-0", {"payload": TurnTaskCodec.dumps(task)})])])
    executed = []
    worker = RedisWorkerRuntime(
        redis,
        config,
        executed.append,
        consumer_name="worker-1",
        is_terminal=lambda _task: True,
    )

    assert await worker.run_once(block_ms=0) == 1
    assert executed == []
    assert redis.acks == [("turns", "workers", "1-0")]


async def test_pubsub_failure_is_best_effort_after_durable_event_write():
    redis = FakeRedis()
    redis.publish_error = ConnectionError("redis unavailable")
    publisher = RedisEventPublisher(redis, RedisTransportConfig(event_channel="events"))
    event = GatewayEvent(
        event_id="event-1", turn_id="turn-1", session_id="session-1", sequence=1,
        type=GatewayEventType.TURN_COMPLETED,
    )

    assert await publisher.publish(event) is False


async def test_worker_dead_letters_poison_message_before_ack():
    redis = FakeRedis()
    config = RedisTransportConfig(
        task_stream="turns", task_group="workers", dead_letter_stream="turns:dead"
    )
    redis.reads.append([("turns", [("1-0", {"payload": '{"version":2}'})])])
    worker = RedisWorkerRuntime(redis, config, lambda _task: None, consumer_name="worker-1")

    assert await worker.run_once(block_ms=0) == 1
    assert redis.streams[0][0] == "turns:dead"
    assert redis.streams[0][1]["source_message_id"] == "1-0"
    assert redis.acks == [("turns", "workers", "1-0")]


@pytest.mark.parametrize("cancelled", [False, True])
async def test_worker_dead_letters_delivery_whose_mysql_turn_is_missing(cancelled):
    redis = FakeRedis()
    config = RedisTransportConfig(
        task_stream="turns",
        task_group="workers",
        dead_letter_stream="turns:dead",
        cancel_key_prefix="cancel:",
    )
    task = TurnTask(
        context=SessionContext(session_id="session-1"),
        turn_id="missing-turn",
        content="hello",
        learning_context=None,
        learning_progress=None,
        exercise_state=None,
        teaching_materials=TeachingMaterials(),
        guided_session_id=None,
        exercise_session_id=None,
    )
    redis.values["cancel:missing-turn"] = "1" if cancelled else None
    redis.autoclaimed.append(
        [("9-0", {"payload": TurnTaskCodec.dumps(task)})]
    )

    def missing(_task):
        raise LookupError("turn state is unavailable: missing-turn")

    worker = RedisWorkerRuntime.for_fenced_mysql(
        redis,
        config,
        lambda _task: pytest.fail("missing turn must not execute"),
        consumer_name="worker-1",
        cancel_pending=missing,
        is_terminal=missing,
    )

    assert await worker.run_once(block_ms=0) == 1
    assert redis.streams[0][0] == "turns:dead"
    assert redis.streams[0][1]["source_message_id"] == "9-0"
    assert redis.streams[0][1]["error_kind"] == "LookupError"
    assert redis.acks == [("turns", "workers", "9-0")]
