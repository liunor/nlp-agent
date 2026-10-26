import asyncio
from unittest.mock import AsyncMock, MagicMock
import json
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
        self.read_counts = []
        self.values = {}
        self.publish_error = None
        self.claim_failures = 0
        self.get_results = []
        self.read_blocks = []
        self.autoclaimed = []
        self.autoclaim_requests = []
        self.heartbeat_written = asyncio.Event()
        self.ping_error = None

    async def xadd(self, stream, fields):
        self.streams.append((stream, fields))

    async def publish(self, channel, payload):
        if self.publish_error is not None:
            raise self.publish_error
        self.messages.append((channel, payload))

    async def set(self, key, value, **options):
        self.values[key] = value
        if "workers:" in key:
            self.heartbeat_written.set()
        return True

    async def ping(self):
        if self.ping_error is not None:
            raise self.ping_error
        return True

    async def scan_iter(self, *, match):
        prefix = match.removesuffix("*")
        for key in self.values:
            if key.startswith(prefix):
                yield key

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
        self.read_counts.append(count)
        batches = self.reads.pop(0) if self.reads else []
        if not batches:
            return []
        stream, messages = batches[0]
        selected, remaining = messages[:count], messages[count:]
        if remaining:
            self.reads.insert(0, [(stream, remaining)])
        return [(stream, selected)] if selected else []

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
        thinking_enabled=False,
        authorization=ExecutionAuthorizationContext(
            submitter_user_id="alice", workspace_id="w1", authorization_version=7
        ),
    )

    restored = TurnTaskCodec.loads(TurnTaskCodec.dumps(task))

    assert restored == task
    assert restored.model_profile == "qwen"
    assert restored.thinking_enabled is False


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


@pytest.mark.parametrize("concurrency", [1, 4, 16, 24, 32])
def test_worker_concurrency_accepts_bounded_tuning_values(concurrency):
    assert RedisTransportConfig(worker_concurrency=concurrency).worker_concurrency == concurrency


@pytest.mark.parametrize("concurrency", [-1, 0, 33])
def test_worker_concurrency_rejects_values_outside_the_bound(concurrency):
    with pytest.raises(ValueError, match="worker_concurrency must be between 1 and 32"):
        RedisTransportConfig(worker_concurrency=concurrency)


def test_worker_concurrency_default_is_still_four():
    assert RedisTransportConfig().worker_concurrency == 4


def test_turn_task_codec_rejects_unknown_protocol_version():
    with pytest.raises(ValueError, match="unsupported turn task version"):
        TurnTaskCodec.loads('{"version":999}')


def test_turn_task_codec_rejects_non_object_json():
    with pytest.raises(ValueError, match="must be a JSON object"):
        TurnTaskCodec.loads("[]")


def test_worker_heartbeat_config_requires_ttl_to_exceed_interval():
    with pytest.raises(ValueError, match="ttl_s must exceed"):
        RedisTransportConfig(worker_heartbeat_interval_s=5, worker_heartbeat_ttl_s=5)


@pytest.mark.asyncio
async def test_dispatcher_counts_live_worker_heartbeats_and_preserves_unknown_state():
    redis = FakeRedis()
    config = RedisTransportConfig(worker_heartbeat_prefix="test:workers:")
    dispatcher = RedisTurnDispatcher(redis, config)
    redis.values["test:workers:one"] = json.dumps({"worker_id": "one", "concurrency": 1, "active_slots": 0})
    redis.values["test:workers:two"] = json.dumps({"worker_id": "two", "concurrency": 4, "active_slots": 2})
    redis.values["test:workers:invalid"] = "{}"

    assert await dispatcher.healthy_worker_count() == 2
    redis.ping_error = ConnectionError("redis unavailable")
    assert await dispatcher.healthy_worker_count() is None


@pytest.mark.asyncio
async def test_worker_publishes_heartbeat_with_capacity_metadata():
    redis = FakeRedis()
    config = RedisTransportConfig(
        worker_heartbeat_prefix="test:workers:",
        worker_heartbeat_interval_s=0.01,
        worker_heartbeat_ttl_s=1,
        worker_concurrency=3,
    )
    worker = RedisWorkerRuntime(redis, config, lambda _task: None, consumer_name="worker-a")
    publishing = asyncio.create_task(worker._publish_worker_heartbeat())
    await asyncio.wait_for(redis.heartbeat_written.wait(), timeout=0.2)
    heartbeat = json.loads(redis.values["test:workers:worker-a"])
    publishing.cancel()
    await asyncio.gather(publishing, return_exceptions=True)

    assert heartbeat["worker_id"] == "worker-a"
    assert heartbeat["concurrency"] == 3
    assert heartbeat["active_slots"] == 0


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


async def test_worker_runs_only_available_slots_concurrently_without_overfetch():
    redis = FakeRedis()
    config = RedisTransportConfig(
        task_stream="turns", task_group="workers", worker_concurrency=2
    )
    tasks = [
        TurnTask(
            context=SessionContext(session_id=f"session-{index}"),
            turn_id=f"turn-{index}",
            content="hello",
            learning_context=None,
            learning_progress=None,
            exercise_state=None,
            teaching_materials=TeachingMaterials(),
            guided_session_id=None,
            exercise_session_id=None,
        )
        for index in range(3)
    ]
    redis.reads.append([("turns", [
        (f"{index}-0", {"payload": TurnTaskCodec.dumps(task)})
        for index, task in enumerate(tasks, start=1)
    ])])
    started: list[str] = []
    both_started = asyncio.Event()
    release = asyncio.Event()

    async def execute(task):
        started.append(task.turn_id)
        if len(started) == 2:
            both_started.set()
        await release.wait()

    worker = RedisWorkerRuntime(redis, config, execute, consumer_name="worker-1")
    run = asyncio.create_task(worker.run_once(block_ms=0))
    await asyncio.wait_for(both_started.wait(), timeout=1)

    assert set(started) == {"turn-0", "turn-1"}
    assert redis.read_counts == [2]
    assert redis.acks == []

    release.set()
    assert await run == 2
    assert len(redis.acks) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("concurrency", [2, 4, 24, 32])
async def test_worker_forever_fills_free_slots_before_first_turn_finishes(monkeypatch, concurrency):
    """Stream COUNT is a maximum: later arrivals must use the remaining slots."""
    redis = FakeRedis()
    config = RedisTransportConfig(
        task_stream="turns", task_group="workers", worker_concurrency=concurrency,
        poll_block_ms=1,
    )
    tasks = [TurnTask(
        context=SessionContext(session_id=f"incremental-session-{index}"),
        turn_id=f"incremental-turn-{index}", content="hello",
        learning_context=None, learning_progress=None, exercise_state=None,
        teaching_materials=TeachingMaterials(), guided_session_id=None, exercise_session_id=None,
    ) for index in range(concurrency + 1)]
    started = []
    first_started, full, replacement_started = asyncio.Event(), asyncio.Event(), asyncio.Event()
    releases = {task.turn_id: asyncio.Event() for task in tasks}
    live, peak = 0, 0

    async def execute(task):
        nonlocal live, peak
        live += 1
        peak = max(peak, live)
        started.append(task.turn_id)
        if len(started) == 1:
            first_started.set()
        if len(started) == concurrency:
            full.set()
        if task.turn_id == tasks[-1].turn_id:
            replacement_started.set()
        try:
            await releases[task.turn_id].wait()
        finally:
            live -= 1

    async def parked():
        await asyncio.Event().wait()

    # Model Redis's empty blocking reads without a CPU-spinning fake.
    original_read = redis.xreadgroup
    async def read(*args, **kwargs):
        if not redis.reads:
            await asyncio.sleep(0.001)
        return await original_read(*args, **kwargs)

    monkeypatch.setattr(redis, "xreadgroup", read)
    worker = RedisWorkerRuntime(redis, config, execute, consumer_name="incremental-worker")
    monkeypatch.setattr(worker, "_listen_control", parked)
    redis.reads.append([("turns", [("1-0", {"payload": TurnTaskCodec.dumps(tasks[0])})])])
    running = asyncio.create_task(worker.run_forever())
    try:
        await asyncio.wait_for(first_started.wait(), timeout=1)
        # Several empty polls must not make an idle slot wait for this turn.
        await asyncio.sleep(0.01)
        for index, task in enumerate(tasks[1:], start=2):
            redis.reads.append([("turns", [(f"{index}-0", {"payload": TurnTaskCodec.dumps(task)})])])
        await asyncio.wait_for(full.wait(), timeout=0.5)
        assert len(started) == concurrency
        assert not releases[tasks[0].turn_id].is_set()
        assert redis.acks == []
        assert not replacement_started.is_set()
        assert peak == concurrency

        releases[tasks[0].turn_id].set()
        await asyncio.wait_for(replacement_started.wait(), timeout=0.5)
        assert peak == concurrency
        assert tasks[-1].turn_id in started
    finally:
        for release in releases.values():
            release.set()
        await worker.close(grace_s=1)
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)

    assert len(redis.acks) == len(tasks)
    assert len(set(redis.acks)) == len(tasks)


@pytest.mark.asyncio
async def test_worker_shutdown_does_not_start_delivery_from_pending_read(monkeypatch):
    redis = FakeRedis()
    reading, deliver = asyncio.Event(), asyncio.Event()
    task = TurnTask(
        context=SessionContext(session_id="shutdown-session"), turn_id="shutdown-turn",
        content="hello", learning_context=None, learning_progress=None, exercise_state=None,
        teaching_materials=TeachingMaterials(), guided_session_id=None, exercise_session_id=None,
    )

    async def blocked_read(*args, **kwargs):
        reading.set()
        await deliver.wait()
        return [("turns", [("1-0", {"payload": TurnTaskCodec.dumps(task)})])]

    monkeypatch.setattr(redis, "xreadgroup", blocked_read)
    executed = []
    worker = RedisWorkerRuntime(
        redis, RedisTransportConfig(task_stream="turns", task_group="workers"),
        executed.append, consumer_name="shutdown-worker",
    )
    read = asyncio.create_task(worker.run_once(wait_for_completion=False))
    try:
        await asyncio.wait_for(reading.wait(), timeout=1)
        await worker.close(grace_s=0)
        deliver.set()
        assert await asyncio.wait_for(read, timeout=1) == 0
        assert executed == []
        assert redis.acks == []
        assert worker.active_count == 0
    finally:
        deliver.set()
        read.cancel()
        await asyncio.gather(read, return_exceptions=True)
        await worker.close(grace_s=0)


async def test_worker_failure_does_not_cancel_sibling_or_ack_failed_turn():
    redis = FakeRedis()
    config = RedisTransportConfig(task_stream="turns", task_group="workers", worker_concurrency=2)
    tasks = [
        TurnTask(
            context=SessionContext(session_id=f"session-{index}"),
            turn_id=f"turn-{index}",
            content="hello",
            learning_context=None,
            learning_progress=None,
            exercise_state=None,
            teaching_materials=TeachingMaterials(),
            guided_session_id=None,
            exercise_session_id=None,
        )
        for index in range(2)
    ]
    redis.reads.append([("turns", [
        ("1-0", {"payload": TurnTaskCodec.dumps(tasks[0])}),
        ("2-0", {"payload": TurnTaskCodec.dumps(tasks[1])}),
    ])])
    sibling_started = asyncio.Event()
    release_sibling = asyncio.Event()

    async def execute(task):
        if task.turn_id == "turn-0":
            await sibling_started.wait()
            raise RuntimeError("isolated turn failure")
        sibling_started.set()
        await release_sibling.wait()

    worker = RedisWorkerRuntime(redis, config, execute, consumer_name="worker-1")
    run = asyncio.create_task(worker.run_once(block_ms=0))
    await asyncio.wait_for(sibling_started.wait(), timeout=1)
    release_sibling.set()

    # FIRST_COMPLETED can observe the failed delivery before its sibling
    # commits. Drain the sibling rather than depending on scheduler order.
    assert await run in (0, 1)
    await worker.close(grace_s=1)
    assert redis.acks == [("turns", "workers", "2-0")]


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
