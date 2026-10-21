"""Mixed scratch/interactive load must obey the same shared host budget."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


@asynccontextmanager
async def _session():
    yield SimpleNamespace()


def _scratch_manager(monkeypatch, *, count=0, headroom=True, work=None):
    from configs.settings import settings
    from server.sandbox.manager import WarmPoolManager

    monkeypatch.setattr(settings, "NLP_AGENT_SANDBOX_HOST_LOCK_FILE", "")
    docker = SimpleNamespace(runtime_kind="docker", run_scratch=AsyncMock(side_effect=work))
    manager = WarmPoolManager(session_factory=_session, docker=docker,
                              resource_profile_id="python-base", ready_target=1)
    manager._counts = AsyncMock(return_value=SimpleNamespace(total_count=count))
    manager._host_allows_create = AsyncMock(return_value=headroom)
    return manager, docker


@pytest.mark.parametrize("reason", ["namespace_full", "host_full", "missing_lock"])
def test_scratch_cannot_bypass_capacity_or_required_host_lock(monkeypatch, reason):
    from configs.settings import settings

    manager, docker = _scratch_manager(monkeypatch,
        count=settings.NLP_AGENT_SANDBOX_RUNTIME_TOTAL_MAX if reason == "namespace_full" else 0,
        headroom=reason != "host_full")
    if reason == "missing_lock":
        manager._require_host_lock = True
    with pytest.raises(RuntimeError):
        asyncio.run(manager.run_scratch(source="print(1)"))
    docker.run_scratch.assert_not_awaited()


def test_scratch_and_interactive_commands_share_execution_slots(monkeypatch):
    from configs.settings import settings

    monkeypatch.setattr(settings, "NLP_AGENT_SANDBOX_EXECUTION_CONCURRENCY_MAX", 2)
    async def exercise():
        active = 0
        peak = 0
        calls = 0
        async def work(*args, **kwargs):
            nonlocal active, peak, calls
            active += 1
            peak = max(peak, active)
            calls += 1
            await asyncio.sleep(0.02)
            active -= 1
            return {"status": "completed"}
        manager, _ = _scratch_manager(monkeypatch, work=work)
        manager._execute_claimed_admitted = work
        await asyncio.gather(*[
            manager.run_scratch(source="print(1)") if i % 2 else
            manager.execute_claimed(None, lease_id="lease", runtime_id="runtime", generation=1,
                                    nonce=None, source="print(1)")
            for i in range(8)
        ])
        assert calls == 8 and peak == 2
    asyncio.run(exercise())


def test_scratch_queue_deadline_stops_before_starting_a_runtime(monkeypatch):
    import server.sandbox.manager as module

    manager, docker = _scratch_manager(monkeypatch)
    monkeypatch.setattr(module, "SCRATCH_ADMISSION_TIMEOUT_SECONDS", 0.02)
    @asynccontextmanager
    async def blocked_lock(path):
        await asyncio.Event().wait()
        yield
    monkeypatch.setattr(module, "host_budget_lock", blocked_lock)
    with pytest.raises(RuntimeError, match="capacity is busy"):
        asyncio.run(manager.run_scratch(source="print(1)"))
    docker.run_scratch.assert_not_awaited()


def test_reconcile_does_not_remove_an_active_scratch_as_an_orphan(monkeypatch):
    import server.sandbox.manager as module

    async def exercise():
        gate = asyncio.Lock()
        running = asyncio.Event()
        finish = asyncio.Event()
        reconciled = asyncio.Event()
        @asynccontextmanager
        async def lock(path):
            async with gate:
                yield
        async def work(**kwargs):
            running.set()
            await finish.wait()
            return {"status": "completed"}
        manager, _ = _scratch_manager(monkeypatch, work=work)
        monkeypatch.setattr(module, "host_budget_lock", lock)
        async def reconcile_containers():
            assert finish.is_set()
            reconciled.set()
            return SimpleNamespace()
        manager._reconcile_containers_locked = reconcile_containers
        manager._retry_draining_runtimes = AsyncMock()
        manager._destroy_unleased_assigned_runtimes = AsyncMock()
        manager.refill = AsyncMock()
        scratch = asyncio.create_task(manager.run_scratch(source="print(1)"))
        await running.wait()
        reconcile = asyncio.create_task(manager.reconcile())
        await asyncio.sleep(0.02)
        assert not reconciled.is_set()
        finish.set()
        await asyncio.gather(scratch, reconcile)
        assert reconciled.is_set() and not gate.locked()
    asyncio.run(exercise())


def test_logout_while_waiting_for_a_slot_is_checked_before_execution(monkeypatch):
    from server.infrastructure.mysql.models import (
        SandboxLeaseModel, SandboxRuntimeInstanceModel, SessionModel, UserModel,
    )
    from server.sandbox.contracts import SandboxScope
    from server.sandbox.manager import WarmPoolManager

    async def exercise():
        now = datetime.now(UTC).replace(tzinfo=None)
        lease = SimpleNamespace(user_id="owner", auth_session_id="login", workspace_id="workspace", state="active",
                                generation=3, environment_id="env", expires_at=now + timedelta(hours=1))
        runtime = SimpleNamespace(state="assigned", environment_id="env", generation=3,
                                  claim_nonce_hash=None, external_runtime_id="kernel")
        auth = SimpleNamespace(user_id="owner", workspace_id="workspace", revoked_at=None,
                               expires_at=now + timedelta(hours=1), authorization_version=3)
        user = SimpleNamespace(status="active", deleted_at=None, authorization_version=3)
        rows = {SandboxLeaseModel: lease, SandboxRuntimeInstanceModel: runtime, SessionModel: auth, UserModel: user}
        class Session:
            async def get(self, model, key, **kwargs):
                return rows[model]
        @asynccontextmanager
        async def begin():
            yield Session()
        docker = SimpleNamespace(execute=AsyncMock())
        manager = WarmPoolManager(session_factory=SimpleNamespace(begin=begin), docker=docker,
                                  resource_profile_id="python-base", ready_target=1)
        manager._execution_semaphore = asyncio.Semaphore(0)
        scope = SandboxScope("owner", "login", "workspace", 3, now + timedelta(hours=1))
        command = asyncio.create_task(manager.execute_claimed(scope, lease_id="lease", runtime_id="runtime",
                                      generation=3, nonce=None, source="print(1)"))
        await asyncio.sleep(0)
        auth.revoked_at = now
        manager._execution_semaphore.release()
        with pytest.raises(PermissionError, match="lifecycle"):
            await command
        docker.execute.assert_not_awaited()
    asyncio.run(exercise())


def test_cancelled_scratch_removes_only_its_named_container_before_returning(monkeypatch):
    from server.sandbox.docker_runtime import DockerRuntimeAdapter, DockerRuntimeConfig

    async def exercise():
        started = asyncio.Event()
        commands = []
        run = SimpleNamespace(kill=lambda: None, wait=AsyncMock())
        async def communicate(**kwargs):
            started.set()
            await asyncio.Event().wait()
        run.communicate = communicate
        cleanup = SimpleNamespace(returncode=0, communicate=AsyncMock(return_value=(b"", b"")))
        async def spawn(*command, **kwargs):
            commands.append(command)
            return cleanup if command[1] == "rm" else run
        monkeypatch.setattr("server.sandbox.docker_runtime.asyncio.create_subprocess_exec", spawn)
        adapter = DockerRuntimeAdapter(DockerRuntimeConfig(image="example/runtime@sha256:" + "a" * 64))
        task = asyncio.create_task(adapter.run_scratch(source="print(1)"))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert len(commands) == 2 and commands[1][:3] == ("docker", "rm", "--force")
        assert commands[1][3] == commands[0][commands[0].index("--name") + 1]
        cleanup.communicate.assert_awaited_once()
    asyncio.run(exercise())


def test_cancelled_lock_waiter_cannot_acquire_later_or_close_an_owner_lock(monkeypatch, tmp_path):
    import server.sandbox.host_budget as module

    holder = None
    waiting = asyncio.Event()
    def flock(descriptor, operation):
        nonlocal holder
        if operation == 8:
            assert holder == descriptor
            holder = None
        elif holder is not None:
            waiting.set()
            raise BlockingIOError()
        else:
            assert operation == 1 | 4
            holder = descriptor
    monkeypatch.setattr(module, "fcntl", SimpleNamespace(LOCK_EX=1, LOCK_NB=4, LOCK_UN=8, flock=flock))
    async def no_worker(*args, **kwargs):
        pytest.fail("a lock waiter must not outlive cancellation in a thread")
    monkeypatch.setattr(module.asyncio, "to_thread", no_worker)
    async def exercise():
        path = str(tmp_path / "capacity.lock")
        async def wait():
            async with module.host_budget_lock(path):
                pytest.fail("cancelled waiter must never become an owner")
        async with module.host_budget_lock(path):
            owner = holder
            waiter = asyncio.create_task(wait())
            await waiting.wait()
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter
            assert holder == owner
        assert holder is None
        async with module.host_budget_lock(path):
            assert holder is not None
        assert holder is None
    asyncio.run(exercise())
