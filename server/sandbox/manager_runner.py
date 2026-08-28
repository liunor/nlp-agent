"""Dedicated Process entry point for Phase 2 Sandbox Manager.

Run this separately from the Web service.  Only this process is granted Docker
Engine access; its runtime image must be pinned to a digest.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import time
from typing import Any

from configs.settings import settings
from server.infrastructure.mysql.config import DatabaseConfig
from server.infrastructure.mysql.engine import create_engine, create_session_factory

from .commands import command_expired, create_sandbox_manager_command_store
from .manager import WarmPoolManager
from .manager_rpc import create_sandbox_manager_rpc_server
from .optimization import AdaptivePoolPolicy
from .metrics import create_sandbox_adaptive_state_store, create_sandbox_metrics_store
from .runtime_factory import create_kubernetes_runtime_client, create_runtime_adapter


async def process_manager_command(
    command: dict[str, str], *, command_store: Any, manager: Any, now: float | None = None
) -> bool:
    """Apply one command and acknowledge it only after its side effect succeeds.

    The Redis stream cursor is deliberately advanced independently from this
    acknowledgement.  If Manager or Docker fails, returning ``False`` keeps
    the command pending so a later loop (or a restarted Manager) can retry it.
    Pool targets are idempotent, so a crash after ``refill`` and before the
    acknowledgement is safe to replay.
    """
    command_id = command.get("id", "")
    if not command_id:
        return False

    def trace_apply_failed(error: Exception) -> None:
        try:
            manager._trace(
                "sandbox.manager.command.apply_failed",
                command_id=command_id,
                error=type(error).__name__,
            )
        except Exception:
            pass

    try:
        if await command_store.is_handled(command_id):
            return True
    except Exception:
        return False

    async def acknowledge() -> bool:
        try:
            accepted = await command_store.mark_handled(command_id)
        except Exception:
            return False
        if accepted:
            return True
        try:
            return await command_store.is_handled(command_id)
        except Exception:
            return False

    if command_expired(command, now=now):
        return await acknowledge()
    if command.get("type") != "pool_target":
        return await acknowledge()
    if command.get("profile_id") != "python-base":
        try:
            manager._trace(
                "sandbox.manager.command.unsupported_profile",
                profile_id=command.get("profile_id"),
            )
        except Exception:
            pass
        return await acknowledge()
    try:
        target = int(command["target"])
    except (KeyError, ValueError):
        return await acknowledge()
    try:
        await manager.request_target(target)
    except ValueError:
        # Invalid operator input cannot become valid on a retry; acknowledge
        # it after refusing the side effect so it cannot poison the queue.
        return await acknowledge()
    except Exception as error:
        trace_apply_failed(error)
        return False
    try:
        # Refill is part of the command's side effect.  Do it before the
        # durable acknowledgement so a failure cannot permanently lose the
        # requested capacity target.
        await manager.refill()
    except Exception as error:
        trace_apply_failed(error)
        return False
    return await acknowledge()


async def run_forever() -> None:
    image = settings.NLP_AGENT_SANDBOX_DOCKER_IMAGE_DIGEST.strip()
    target = settings.NLP_AGENT_SANDBOX_WARM_POOL_READY_TARGET
    if not image or target <= 0:
        raise RuntimeError(
            "Set NLP_AGENT_SANDBOX_DOCKER_IMAGE_DIGEST and a positive "
            "NLP_AGENT_SANDBOX_WARM_POOL_READY_TARGET before starting Sandbox Manager."
        )
    engine = create_engine(DatabaseConfig.from_runtime(settings.database_runtime))
    adaptive_state_store = (
        create_sandbox_adaptive_state_store()
        if settings.NLP_AGENT_SANDBOX_ADAPTIVE_POOL_ENABLED
        else None
    )
    metrics_store = create_sandbox_metrics_store() if settings.NLP_AGENT_SANDBOX_ADAPTIVE_POOL_ENABLED else None
    backend = settings.NLP_AGENT_SANDBOX_RUNTIME_BACKEND.strip().lower()
    kubernetes_client = None
    if backend in {"kubernetes", "k8s"}:
        kubernetes_client = create_kubernetes_runtime_client(
            settings.NLP_AGENT_SANDBOX_KUBERNETES_CLIENT_FACTORY
        )
    runtime = create_runtime_adapter(
        backend=backend,
        image=image,
        kernel_image=settings.NLP_AGENT_SANDBOX_FIRECRACKER_KERNEL_IMAGE.strip() or None,
        rootfs_image=settings.NLP_AGENT_SANDBOX_FIRECRACKER_ROOTFS_IMAGE.strip() or None,
        client=kubernetes_client,
    )
    manager = WarmPoolManager(
        session_factory=create_session_factory(engine),
        docker=runtime,
        resource_profile_id="python-base",
        ready_target=target,
        adaptive_policy=(
            AdaptivePoolPolicy(
                ready_min=settings.NLP_AGENT_SANDBOX_WARM_POOL_READY_MIN,
                ready_max=settings.NLP_AGENT_SANDBOX_WARM_POOL_READY_MAX,
                burst_buffer=settings.NLP_AGENT_SANDBOX_BURST_BUFFER,
            )
            if settings.NLP_AGENT_SANDBOX_ADAPTIVE_POOL_ENABLED
            else None
        ),
        adaptive_state_store=adaptive_state_store,
        metrics_store=metrics_store,
    )
    command_store = create_sandbox_manager_command_store()
    rpc_server = create_sandbox_manager_rpc_server(manager)
    if command_store is not None:
        try:
            command_cursor = await command_store.load_cursor()
        except Exception:
            command_cursor = "0-0"
    else:
        command_cursor = "0-0"
    pending_commands: list[dict[str, str]] = []
    next_reconcile = 0.0
    try:
        while True:
            now_monotonic = time.monotonic()
            if now_monotonic >= next_reconcile:
                try:
                    await manager.reconcile()
                except Exception:
                    # A Docker/DB/Redis fault must not terminate the isolated
                    # control plane.  The next scheduled pass retries it.
                    pass
                next_reconcile = now_monotonic + max(5, settings.NLP_AGENT_SANDBOX_RECONCILE_INTERVAL_S)
            if rpc_server is not None:
                try:
                    await rpc_server.process_once(block_ms=25)
                except Exception:
                    pass
            if command_store is not None:
                try:
                    command_cursor, commands = await command_store.read(after_id=command_cursor, block_ms=1)
                except Exception:
                    # Redis is an optional optimization/control input.  Keep
                    # reconciling Docker and retry the stream on the next pass.
                    commands = []
                pending_commands.extend(commands)
                due: list[dict[str, str]] = []
                remaining: list[dict[str, str]] = []
                now = datetime.now(UTC)
                for command in pending_commands:
                    if command_expired(command, now=now.timestamp()):
                        due.append(command)
                        continue
                    execute_at = command.get("execute_at", "")
                    if execute_at:
                        try:
                            scheduled = datetime.fromisoformat(execute_at.replace("Z", "+00:00"))
                        except ValueError:
                            scheduled = now
                        if scheduled.tzinfo is None:
                            scheduled = scheduled.replace(tzinfo=UTC)
                        if scheduled > now:
                            remaining.append(command)
                            continue
                    due.append(command)
                pending_commands = remaining
                cursor_safe = True
                retry_commands: list[dict[str, str]] = []
                for command in due:
                    handled = await process_manager_command(
                        command,
                        command_store=command_store,
                        manager=manager,
                        now=now.timestamp(),
                    )
                    if not handled:
                        cursor_safe = False
                        retry_commands.append(command)
                pending_commands.extend(retry_commands)
                if due and cursor_safe and not pending_commands and command_store is not None:
                    try:
                        await command_store.save_cursor(command_cursor)
                    except Exception:
                        pass
            await asyncio.sleep(0.05)
    finally:
        if command_store is not None:
            await command_store.close()
        if rpc_server is not None:
            await rpc_server.close()
        if adaptive_state_store is not None:
            await adaptive_state_store.close()
        if metrics_store is not None:
            await metrics_store.close()
        await engine.dispose()


def main() -> None:
    asyncio.run(run_forever())


if __name__ == "__main__":
    main()
