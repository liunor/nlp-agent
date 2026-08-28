from __future__ import annotations

import asyncio

import pytest


def test_fault_injector_has_recovery_stages() -> None:
    from server.sandbox.faults import SandboxFaultInjector, SandboxInjectedFault

    injector = SandboxFaultInjector.from_env(
        "docker.destroy,manager.reconcile,redis.append,redis.read,artifact.cleanup"
    )
    for stage in (
        "docker.destroy",
        "manager.reconcile",
        "redis.append",
        "redis.read",
        "artifact.cleanup",
    ):
        with pytest.raises(SandboxInjectedFault):
            injector.fail_if_configured(stage)


def test_manager_command_stream_fault_is_opt_in() -> None:
    from server.sandbox.commands import RedisSandboxManagerCommandStore
    from server.sandbox.faults import SandboxFaultInjector, SandboxInjectedFault

    class FakeRedis:
        async def xadd(self, *_args, **_kwargs):
            return "1-0"

        async def xread(self, *_args, **_kwargs):
            return []

    async def exercise() -> None:
        store = RedisSandboxManagerCommandStore(
            FakeRedis(), fault_injector=SandboxFaultInjector(frozenset({"redis.xadd"}))
        )
        with pytest.raises(SandboxInjectedFault):
            await store.request_pool_target(
                profile_id="python-base", target=2, reason="test"
            )

    asyncio.run(exercise())


def test_redis_event_fault_can_be_retried_by_reconciler() -> None:
    from server.sandbox.events import RedisSandboxEventStore
    from server.sandbox.faults import SandboxFaultInjector, SandboxInjectedFault

    class FakeRedis:
        async def xadd(self, *_args, **_kwargs):
            return "1-0"

        async def expire(self, *_args, **_kwargs):
            return True

    async def exercise() -> None:
        store = RedisSandboxEventStore(
            FakeRedis(), fault_injector=SandboxFaultInjector(frozenset({"redis.append"}))
        )
        with pytest.raises(SandboxInjectedFault):
            await store.append(
                "execution", user_id="user", event_type="execution.started", payload={}
            )

    asyncio.run(exercise())


def test_artifact_cleanup_fault_rolls_back_before_unlink(tmp_path) -> None:
    from datetime import UTC, datetime, timedelta
    from types import SimpleNamespace

    from server.sandbox.artifact_retention import purge_expired_artifacts
    from server.sandbox.faults import SandboxFaultInjector, SandboxInjectedFault

    class Result:
        def all(self):
            return [
                SimpleNamespace(
                    locator="output.txt",
                    expires_at=datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=1),
                    created_at=datetime.now(UTC).replace(tzinfo=None),
                )
            ]

    class Session:
        async def scalars(self, _statement):
            return Result()

        async def delete(self, _artifact):
            raise AssertionError("metadata must not be deleted after an injected cleanup fault")

    class Transaction:
        async def __aenter__(self):
            return Session()

        async def __aexit__(self, *_args):
            return False

    class Factory:
        def begin(self):
            return Transaction()

    async def exercise() -> None:
        with pytest.raises(SandboxInjectedFault):
            await purge_expired_artifacts(
                Factory(),
                store_root=tmp_path,
                fault_injector=SandboxFaultInjector(frozenset({"artifact.cleanup"})),
            )

    asyncio.run(exercise())
