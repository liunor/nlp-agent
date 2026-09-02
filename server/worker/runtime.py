"""Redis-backed independent Worker lifecycle."""

from __future__ import annotations

import asyncio
import socket
from functools import partial
from typing import Any, Mapping

from configs.settings import settings
from gateway.contracts import GatewayEventType, TurnStatus
from gateway.engine import LangGraphAgentEngine
from gateway.redis_transport import (
    RedisEventPublisher,
    RedisTransportConfig,
    RedisWorkerRuntime,
)
from gateway.state_factory import build_turn_execution_state
from gateway.turn_execution import InProcessTurnExecutor
from server.application.turn_reliability import OutboxRelay, TurnReliabilityService
from server.infrastructure.mysql import MySQLRuntime
from server.session.summary import schedule_summary, summary_sweep_loop
from server.worker.fencing import FencedTurnExecutor
from server.quota.notifications import QuotaSnapshotRedisPublisher
from server.quota.operations import QuotaOperationsService
from server.quota.reaper import QuotaReservationReaper
from server.sandbox.manager_rpc import create_sandbox_manager_rpc_client
from server.sandbox.model_tools import configure_model_sandbox_service


def configure_worker_sandbox_service(session_factory):
    """Bind model tools in the Worker; Web-only setup is insufficient remotely."""
    mode = settings.NLP_AGENT_SANDBOX_RUNTIME_MODE.strip().lower()
    manager = create_sandbox_manager_rpc_client() if mode == "docker" else None
    service = configure_model_sandbox_service(
        mode=mode,
        session_factory=session_factory,
        manager=manager,
    )
    return service, manager


def redis_config() -> RedisTransportConfig:
    config = settings.gateway_runtime
    return RedisTransportConfig(
        url=str(config.get("redis_url", "redis://127.0.0.1:6379/0")),
        task_stream=str(config.get("redis_turn_stream", "nlp-agent:turns")),
        task_group=str(config.get("redis_turn_group", "nlp-agent-workers")),
        event_channel=str(config.get("redis_event_channel", "nlp-agent:events")),
        control_channel=str(config.get("redis_control_channel", "nlp-agent:control")),
        authorization_channel=str(config.get("redis_authorization_channel", "nlp-agent:authorization")),
        quota_snapshot_channel=str(
            config.get("redis_quota_snapshot_channel", "nlp-agent:quota-snapshot")
        ),
        reclaim_idle_ms=int(config.get("redis_reclaim_idle_ms", 60_000)),
        cancel_key_prefix=str(
            config.get("redis_cancel_key_prefix", "nlp-agent:cancel:")
        ),
        cancel_ttl_s=int(config.get("redis_cancel_ttl_s", 604_800)),
        dead_letter_stream=str(
            config.get("redis_dead_letter_stream", "nlp-agent:turns:dead")
        ),
    )

def create_worker_quota_reaper(
    repository: Any, gateway_config: Mapping[str, Any]
) -> QuotaReservationReaper | None:
    """Build the same expiry/maintenance loop for a standalone Worker."""
    quota_service = getattr(repository, "quota_service", None)
    if quota_service is None:
        return None
    return QuotaReservationReaper(
        quota_service,
        interval_seconds=max(
            1.0,
            float(gateway_config.get("quota_reap_interval_s", 30)),
        ),
        operations_service=QuotaOperationsService(quota_service.engine),
        operations_interval_seconds=max(
            1.0,
            float(gateway_config.get("quota_operations_interval_s", 3_600)),
        ),
    )


async def run_worker() -> None:
    from redis.asyncio import Redis
    from server.quota.bootstrap import (
        configure_usage_reporter,
        shutdown_usage_reporter,
    )

    database_runtime = MySQLRuntime.from_runtime(settings.database_runtime)
    await database_runtime.start()
    usage_reporter = configure_usage_reporter(
        settings.NLP_AGENT_DATABASE_URL.strip(),
        required=True,
        quota_enforcement=settings.quota_enforcement_enabled,
    )
    sandbox_model_service, sandbox_manager = configure_worker_sandbox_service(
        database_runtime.session_factory
    )
    config = redis_config()
    redis = Redis.from_url(config.url, decode_responses=True)
    quota_snapshot_publisher = QuotaSnapshotRedisPublisher(
        config.url, channel=config.quota_snapshot_channel
    )
    usage_reporter.set_snapshot_notifier(quota_snapshot_publisher)
    gateway_config = settings.gateway_runtime
    repository = build_turn_execution_state(gateway_config)
    if getattr(repository, "quota_service", None) is not None:
        repository.quota_service.set_snapshot_notifier(quota_snapshot_publisher)
        await asyncio.to_thread(repository.quota_service.verify_schema)
    engine = LangGraphAgentEngine()
    publisher = RedisEventPublisher(redis, config)
    reliability = TurnReliabilityService()
    worker_id = f"{socket.gethostname()}-{id(redis)}"

    async def emit(turn_id: str, session_id: str, event_type: GatewayEventType, payload: dict) -> None:
        event = await asyncio.to_thread(
            repository.append_event,
            turn_id=turn_id,
            session_id=session_id,
            event_type=event_type,
            payload=payload,
        )
        await publisher.publish(event)

    async def cancel_pending(task) -> None:
        turn = await asyncio.to_thread(repository.get_turn, task.turn_id)
        if turn is None:
            raise LookupError(f"turn state is unavailable: {task.turn_id}")
        if turn.status != TurnStatus.CANCELLED:
            await asyncio.to_thread(
                repository.update_turn, task.turn_id, TurnStatus.CANCELLED
            )
        event = await asyncio.to_thread(
            repository.ensure_event,
            turn_id=task.turn_id,
            session_id=task.context.session_id,
            event_type=GatewayEventType.TURN_CANCELLED,
            payload={"status": TurnStatus.CANCELLED.value},
        )
        await publisher.publish(event)
        quota_service = getattr(repository, "quota_service", None)
        if quota_service is not None and task.reservation_id is not None:
            await asyncio.to_thread(
                quota_service.release_reservation,
                task.reservation_id,
                turn_id=task.turn_id,
                idempotency_key=f"worker-cancelled:{task.turn_id}",
            )

    async def is_terminal(task) -> bool:
        turn = await asyncio.to_thread(repository.get_turn, task.turn_id)
        if turn is None:
            raise LookupError(f"turn state is unavailable: {task.turn_id}")
        if turn.status == TurnStatus.COMPLETED:
            terminal_events = (
                (GatewayEventType.MESSAGE_COMPLETED, {"content": turn.final_text or ""}),
                (
                    GatewayEventType.TURN_COMPLETED,
                    {
                        "status": TurnStatus.COMPLETED.value,
                        "content": turn.final_text or "",
                    },
                ),
            )
            # The turn is already durable as completed, but the original worker
            # may have died after the DB write and before on_turn_completed
            # re-armed the summarizer.  schedule_summary is idempotent, so
            # re-arm it here on the retry path instead of losing the title.
            schedule_summary(database_runtime.session_factory, task.context.session_id)
        elif turn.status == TurnStatus.CANCELLED:
            terminal_events = ((
                GatewayEventType.TURN_CANCELLED,
                {"status": TurnStatus.CANCELLED.value},
            ),)
        elif turn.status in {TurnStatus.FAILED, TurnStatus.INTERRUPTED}:
            terminal_events = ((
                GatewayEventType.TURN_FAILED,
                {
                    "status": turn.status.value,
                    "error_kind": turn.error_kind or "worker_interrupted",
                    "message": turn.error_message or "",
                },
            ),)
        else:
            return False
        for event_type, payload in terminal_events:
            event = await asyncio.to_thread(
                repository.ensure_event,
                turn_id=task.turn_id,
                session_id=task.context.session_id,
                event_type=event_type,
                payload=payload,
            )
            await publisher.publish(event)
        return True

    await engine.start(emit)
    executor = InProcessTurnExecutor(
        engine,
        repository,
        emit,
        on_turn_completed=partial(
            schedule_summary, database_runtime.session_factory
        ),
    )
    fenced_executor = FencedTurnExecutor(
        database_runtime.uow,
        reliability,
        executor.run,
        worker_id=worker_id,
        lease_s=int(gateway_config.get("mysql_turn_lease_s", 60)),
    )
    worker = RedisWorkerRuntime(
        redis,
        config,
        fenced_executor,
        consumer_name=worker_id,
        inject=engine.inject,
        cancel_pending=cancel_pending,
        is_terminal=is_terminal,
        reclaim_pending=False,
    )
    quota_reaper = create_worker_quota_reaper(repository, gateway_config)
    if quota_reaper is not None:
        quota_reaper.start()

    async def relay_forever() -> None:
        relay = OutboxRelay(redis, stream=config.task_stream, relay_id=worker_id, authorization_channel=config.authorization_channel)
        while True:
            async with database_runtime.uow.begin() as unit_of_work:
                await reliability.recover_stuck_turns(unit_of_work.session)
                await relay.publish_batch(unit_of_work.session)
                await unit_of_work.commit()
            await asyncio.sleep(0.5)

    relay_task = asyncio.create_task(relay_forever(), name="mysql-outbox-relay")
    summary_sweep_task = asyncio.create_task(
        summary_sweep_loop(database_runtime.session_factory),
        name="session-summary-sweep",
    )
    try:
        await worker.run_forever()
    finally:
        relay_task.cancel()
        summary_sweep_task.cancel()
        await asyncio.gather(relay_task, summary_sweep_task, return_exceptions=True)
        if quota_reaper is not None:
            await quota_reaper.stop()
        await worker.close()
        await engine.close()
        await sandbox_model_service.close()
        if sandbox_manager is not None:
            await sandbox_manager.close()
        repository.close()
        quota_snapshot_publisher.close()
        await redis.aclose()
        shutdown_usage_reporter(usage_reporter)
        await database_runtime.close()
