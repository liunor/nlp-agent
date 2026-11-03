from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from server.infrastructure.mysql.models import (
    SandboxEnvironmentModel, SandboxLeaseModel, SandboxRuntimeInstanceModel, SessionModel, UserModel,
)
from server.sandbox.contracts import SandboxScope
from server.sandbox.manager import WarmPoolManager
from server.sandbox.warm_pool import RuntimeState, validate_claim_nonce


@pytest.fixture
def bridge_claim(monkeypatch, tmp_path):
    monkeypatch.setenv("SANDBOX_ENABLE_FILES_BRIDGE", "true")
    monkeypatch.setenv("SANDBOX_BRIDGE_BASE_DIR", str(tmp_path))
    monkeypatch.setattr("os.chown", lambda *args: None, raising=False)
    monkeypatch.setattr("os.path.ismount", lambda path: True)
    now = datetime.now(UTC).replace(tzinfo=None)
    scope = SandboxScope("user-1", "session-1", "workspace-1", 3, now + timedelta(minutes=5))
    lease = SimpleNamespace(id="lease-1", user_id=scope.owner_user_id,
        auth_session_id=scope.auth_session_id, workspace_id=scope.workspace_id,
        state="active", expires_at=scope.lease_expires_at, environment_id="env-1",
        generation=7, runtime_instance_id=None)
    environment = SimpleNamespace(id="env-1", owner_user_id=scope.owner_user_id,
        workspace_id=scope.workspace_id, generation=7, active_runtime_id=None)
    auth = SimpleNamespace(user_id=scope.owner_user_id, workspace_id=scope.workspace_id,
        revoked_at=None, expires_at=scope.lease_expires_at, authorization_version=3)
    user = SimpleNamespace(status="active", deleted_at=None, authorization_version=3)
    rows = {(SandboxLeaseModel, lease.id): lease, (SandboxEnvironmentModel, environment.id): environment,
            (SessionModel, scope.auth_session_id): auth, (UserModel, scope.owner_user_id): user}

    class Session:
        async def get(self, model, identifier, **kwargs):
            return rows.get((model, identifier))

        def add(self, row):
            rows[(type(row), row.id)] = row

    class Factory:
        @asynccontextmanager
        async def begin(self):
            yield Session()

    manager = WarmPoolManager(session_factory=Factory(),
        docker=SimpleNamespace(runtime_kind="docker", image_digest="image", destroy=AsyncMock(),
            files_mount_matches=AsyncMock(return_value=True)),
        resource_profile_id="python-base", ready_target=0)
    manager._trace = lambda *args, **kwargs: None
    manager._files_mounts = SimpleNamespace(ensure=AsyncMock(return_value="/bridge/user-1/workspace-1"),
        path_for=lambda *args: "/bridge/user-1/workspace-1", owns=lambda *args: True)
    manager._counts = AsyncMock(return_value=SimpleNamespace(total_count=0))
    manager._retire_unbound_runtimes = AsyncMock(return_value=0)
    manager._host_allows_create = AsyncMock(return_value=True)

    async def create(*, runtime_id, files_mount):
        runtime = rows[(SandboxRuntimeInstanceModel, runtime_id)]
        runtime.external_runtime_id = "container-1"
        # Scoped slots must never enter the unbound shared pool.
        runtime.state = "creating"

    manager._create_one = AsyncMock(side_effect=create)
    return SimpleNamespace(manager=manager, scope=scope, lease=lease,
        environment=environment, auth=auth, rows=rows)


async def test_files_mode_refill_retires_unusable_warm_slots_instead_of_replenishing(bridge_claim):
    f = bridge_claim
    f.manager._retire_unbound_runtimes.return_value = 2
    assert await f.manager.refill() == 0
    f.manager._retire_unbound_runtimes.assert_awaited_once()


async def test_files_claim_replaces_existing_container_without_readonly_files_mount(bridge_claim):
    f = bridge_claim
    first = await f.manager.claim(f.scope, lease_id=f.lease.id)
    f.manager._docker.files_mount_matches = AsyncMock(return_value=False)
    f.manager.destroy_runtime = AsyncMock()
    second = await f.manager.claim(f.scope, lease_id=f.lease.id)
    assert second.runtime.id != first.runtime.id
    f.manager.destroy_runtime.assert_awaited_once_with(first.runtime.id, reason="files_bridge.mount.changed", refill=False)


async def test_files_claim_binds_generation_pointer_and_one_time_nonce_and_reuses_runtime(bridge_claim):
    f = bridge_claim
    first = await f.manager.claim(f.scope, lease_id=f.lease.id)
    assert first is not None
    runtime = first.runtime
    assert runtime.state == RuntimeState.ASSIGNED
    assert runtime.environment_id == f.environment.id
    assert runtime.generation == f.lease.generation == 7
    assert f.environment.active_runtime_id == f.lease.runtime_instance_id == runtime.id
    assert first.nonce and validate_claim_nonce(runtime.claim_nonce_hash, first.nonce)
    first_nonce = first.nonce
    second = await f.manager.claim(f.scope, lease_id=f.lease.id)
    assert second.runtime is runtime
    assert second.nonce != first_nonce
    assert not validate_claim_nonce(runtime.claim_nonce_hash, first_nonce)
    assert f.manager._create_one.await_count == 1
    assert f.manager._files_mounts.ensure.await_count == 1


@pytest.mark.parametrize("change", ["expired", "revoked", "session", "generation", "workspace"])
async def test_files_claim_rejects_invalid_lease_before_mount_or_create(bridge_claim, change):
    f = bridge_claim
    if change == "expired":
        f.lease.expires_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=1)
    elif change == "revoked":
        f.auth.revoked_at = datetime.now(UTC)
    elif change == "session":
        f.lease.auth_session_id = "another-session"
    elif change == "generation":
        f.lease.generation -= 1
    else:
        f.environment.workspace_id = "another-workspace"
    assert await f.manager.claim(f.scope, lease_id=f.lease.id) is None
    f.manager._files_mounts.ensure.assert_not_awaited()
    f.manager._create_one.assert_not_awaited()


async def test_files_claim_does_not_create_runtime_when_mount_fails(bridge_claim):
    f = bridge_claim
    f.manager._files_mounts.ensure.side_effect = RuntimeError("mount unavailable")
    assert await f.manager.claim(f.scope, lease_id=f.lease.id) is None
    f.manager._create_one.assert_not_awaited()
    assert not any(model is SandboxRuntimeInstanceModel for model, key in f.rows)


async def test_files_claim_respects_capacity_guard(bridge_claim):
    f = bridge_claim
    f.manager._host_allows_create.return_value = False
    assert await f.manager.claim(f.scope, lease_id=f.lease.id) is None
    f.manager._create_one.assert_not_awaited()


async def test_files_claim_rechecks_revocation_after_slow_provisioning_and_destroys_container(bridge_claim):
    f = bridge_claim
    original = f.manager._create_one.side_effect

    async def revoked_during_create(**kwargs):
        await original(**kwargs)
        f.auth.revoked_at = datetime.now(UTC)

    f.manager._create_one.side_effect = revoked_during_create
    assert await f.manager.claim(f.scope, lease_id=f.lease.id) is None
    f.manager._docker.destroy.assert_awaited_once_with("container-1")
    assert f.environment.active_runtime_id is None
    assert f.lease.runtime_instance_id is None
