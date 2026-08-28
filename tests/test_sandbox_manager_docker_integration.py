"""Opt-in MySQL + Docker reconciliation proof used by the Linux CI gate."""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from scripts.benchmark_sandbox_startup import manager_claim_probe_image

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_SANDBOX_DOCKER_MANAGER_INTEGRATION") != "1",
    reason="Docker manager integration is enabled in CI only",
)
DOCKER_COMMAND_TIMEOUT_SECONDS = 120
TEST_IMAGE = manager_claim_probe_image()


@pytest.fixture(autouse=True)
async def _close_global_telemetry_after_test():
    """Stop the Manager's process-wide writer before pytest closes its loop."""
    yield
    from core.observability.runtime import global_telemetry

    await global_telemetry.close()


def _docker_runtime_args() -> list[str]:
    runtime = os.getenv("NLP_AGENT_DOCKER_RUNTIME")
    return [] if not runtime else ["--runtime", runtime]


def _run_docker(args: list[str], *, timeout: int) -> subprocess.CompletedProcess[str]:
    """Run Docker in its own process group so timeout cleanup cannot deadlock pytest."""
    process = subprocess.Popen(
        ["docker", *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=os.name != "nt",
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        if os.name != "nt":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
        try:
            stdout, stderr = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate()
        raise
    return subprocess.CompletedProcess(["docker", *args], process.returncode, stdout, stderr)


def _remove_container(name: str) -> None:
    try:
        _run_docker(["rm", "--force", name], timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        # Cleanup is best effort; the runner is ephemeral and the test result
        # from the operation itself remains authoritative.
        pass


@pytest.mark.asyncio
async def test_manager_destroys_unregistered_managed_container() -> None:
    from server.infrastructure.mysql import DatabaseConfig, create_engine, create_session_factory
    from server.sandbox.docker_runtime import DockerRuntimeAdapter, DockerRuntimeConfig
    from server.sandbox.manager import WarmPoolManager

    name = f"nova-manager-orphan-{uuid4().hex}"
    engine = create_engine(DatabaseConfig(os.environ["NLP_AGENT_DATABASE_URL"], pool_size=1, max_overflow=0))
    try:
        container = _run_docker(
            ["run", "--detach", "--name", name, "--label", "nova.sandbox.managed=true", *_docker_runtime_args(), TEST_IMAGE, "sleep", "300"],
            timeout=DOCKER_COMMAND_TIMEOUT_SECONDS,
        )
        if container.returncode != 0:
            raise subprocess.CalledProcessError(container.returncode, container.args, container.stdout, container.stderr)
        container_id = container.stdout.strip()
        assert container_id
        manager = WarmPoolManager(
            session_factory=create_session_factory(engine),
            docker=DockerRuntimeAdapter(DockerRuntimeConfig(image="alpine@sha256:" + "0" * 64)),
            resource_profile_id="python-base",
            ready_target=0,
        )
        actions = await manager.reconcile()
        assert container_id in actions.destroy_orphans
        remaining = _run_docker(["inspect", name], timeout=30)
        assert remaining.returncode != 0
    finally:
        _remove_container(name)
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "lifecycle_state",
    ("logout", "revoke", "ttl", "disabled", "authorization_changed", "lease_expired", "deleted"),
)
async def test_manager_rejects_and_reclaims_runtime_after_auth_lifecycle_change(lifecycle_state: str) -> None:
    from sqlalchemy import select

    from server.infrastructure.mysql import DatabaseConfig, create_engine, create_session_factory
    from server.infrastructure.mysql.models import (
        SandboxEnvironmentModel, SandboxLeaseModel, SandboxRuntimeInstanceModel, SessionModel, WorkspaceMemberModel,
    )
    from server.sandbox.docker_runtime import DockerRuntimeAdapter, DockerRuntimeConfig
    from server.sandbox.contracts import SandboxScope
    from server.sandbox.manager import WarmPoolManager
    from server.web.database_auth import DatabaseSessionAuth
    from server.user.schemas import UserCreate
    from server.user.service import UserService

    name = f"nova-manager-revoked-{uuid4().hex}"
    engine = create_engine(DatabaseConfig(os.environ["NLP_AGENT_DATABASE_URL"], pool_size=1, max_overflow=0))
    factory = create_session_factory(engine)
    now = datetime.now(UTC).replace(tzinfo=None)
    try:
        container = _run_docker(
            ["run", "--detach", "--name", name, "--label", "nova.sandbox.managed=true", *_docker_runtime_args(), TEST_IMAGE, "sleep", "300"],
            timeout=DOCKER_COMMAND_TIMEOUT_SECONDS,
        )
        if container.returncode != 0:
            raise subprocess.CalledProcessError(container.returncode, container.args, container.stdout, container.stderr)
        container_id = container.stdout.strip()
        assert container_id
        async with factory.begin() as session:
            user = await UserService(session).create_user(
                UserCreate(username=f"reconcile{uuid4().hex[:10]}", display_name="Reconcile", password="InitialPw0rd1")
            )
            workspace_id = await session.scalar(select(WorkspaceMemberModel.workspace_id).where(WorkspaceMemberModel.user_id == user.id))
            session_id = str(uuid4())
            environment_id = str(uuid4())
            runtime_id = str(uuid4())
            lease_id = str(uuid4())
            session_expires_at = now + timedelta(hours=1)
            lease_expires_at = now + timedelta(hours=1)
            session.add(SessionModel(
                id=session_id, user_id=user.id, workspace_id=workspace_id,
                token_hash=f"token-{session_id}", csrf_hash=f"csrf-{session_id}",
                authorization_version=user.authorization_version, expires_at=session_expires_at,
                revoked_at=None,
            ))
            session.add(SandboxEnvironmentModel(id=environment_id, owner_user_id=user.id, generation=1))
            session.add(SandboxRuntimeInstanceModel(
                id=runtime_id, environment_id=environment_id, external_runtime_id=container_id,
                runtime_kind="docker", state="assigned", generation=1,
            ))
            await session.flush()
            session.add(SandboxLeaseModel(
                id=lease_id, environment_id=environment_id, user_id=user.id,
                auth_session_id=session_id, runtime_instance_id=runtime_id, workspace_id=workspace_id,
                generation=1, state="active", expires_at=lease_expires_at,
            ))
        if lifecycle_state == "logout":
            await DatabaseSessionAuth().revoke_session_id(factory, user_id=user.id, session_id=session_id)
        elif lifecycle_state == "revoke":
            async with factory.begin() as session:
                await UserService(session).revoke_user_sessions(user.id, actor_user_id="phase3-test-admin")
        elif lifecycle_state == "disabled":
            async with factory.begin() as session:
                await UserService(session).update_user_status(user.id, "disabled", actor_user_id="phase3-test-admin")
        elif lifecycle_state == "authorization_changed":
            async with factory.begin() as session:
                await UserService(session).change_password(user.id, "ChangedPw0rd2")
        elif lifecycle_state == "deleted":
            async with factory.begin() as session:
                await UserService(session).soft_delete_user(user.id, actor_user_id="phase3-test-admin")
        elif lifecycle_state == "ttl":
            async with factory.begin() as session:
                auth_session = await session.get(SessionModel, session_id, with_for_update=True)
                assert auth_session is not None
                auth_session.expires_at = now - timedelta(seconds=1)
        elif lifecycle_state == "lease_expired":
            async with factory.begin() as session:
                lease = await session.get(SandboxLeaseModel, lease_id, with_for_update=True)
                assert lease is not None
                lease.expires_at = now - timedelta(seconds=1)
        manager = WarmPoolManager(
            session_factory=factory,
            docker=DockerRuntimeAdapter(DockerRuntimeConfig(image="alpine@sha256:" + "0" * 64)),
            resource_profile_id="python-base",
            ready_target=0,
        )
        scope = SandboxScope(
            owner_user_id=user.id,
            auth_session_id=session_id,
            workspace_id=workspace_id,
            generation=1,
            lease_expires_at=now + timedelta(hours=1),
        )
        with pytest.raises(PermissionError):
            await manager.execute_claimed(
                scope,
                lease_id=lease_id,
                runtime_id=runtime_id,
                generation=1,
                nonce=None,
                source="print(1)",
            )
        actions = await manager.reconcile()
        assert name not in actions.destroy_orphans
        remaining = _run_docker(["inspect", name], timeout=30)
        assert remaining.returncode != 0
    finally:
        _remove_container(name)
        await engine.dispose()


@pytest.mark.asyncio
async def test_reset_claim_execute_uses_new_environment_generation() -> None:
    """Regression: reset must fence old tickets without bricking the next claim."""
    from types import SimpleNamespace

    from sqlalchemy import select

    from server.infrastructure.mysql import DatabaseConfig, create_engine, create_session_factory
    from server.infrastructure.mysql.models import (
        SandboxEnvironmentModel,
        SandboxLeaseModel,
        SandboxRuntimeInstanceModel,
        SessionModel,
        WorkspaceMemberModel,
    )
    from server.sandbox.contracts import SandboxScope
    from server.sandbox.manager import WarmPoolManager
    from server.sandbox.warm_pool import RuntimeState
    from server.user.schemas import UserCreate
    from server.user.service import UserService

    class FakeDocker:
        config = SimpleNamespace(image="nova-test@sha256:" + "0" * 64)

        async def destroy(self, external_id: str) -> None:
            assert external_id == "runtime-old"

        async def kernel_ready(self, external_id: str) -> bool:
            return external_id == "runtime-new"

        async def execute(self, external_id: str, *, source: str) -> dict[str, object]:
            assert external_id == "runtime-new"
            assert source == "print(1)"
            return {"status": "completed", "stdout": "1\n", "stderr": ""}

        async def managed_runtime_ids(self) -> set[str]:
            return {"runtime-new"}

        async def image_cached(self) -> bool:
            return True

    engine = create_engine(DatabaseConfig(os.environ["NLP_AGENT_DATABASE_URL"], pool_size=2, max_overflow=0))
    factory = create_session_factory(engine)
    now = datetime.now(UTC).replace(tzinfo=None)
    try:
        async with factory.begin() as session:
            user = await UserService(session).create_user(
                UserCreate(username=f"reset{uuid4().hex[:10]}", display_name="Reset", password="InitialPw0rd1")
            )
            workspace_id = await session.scalar(
                select(WorkspaceMemberModel.workspace_id).where(WorkspaceMemberModel.user_id == user.id)
            )
            session_id = str(uuid4())
            environment_id = str(uuid4())
            old_runtime_id = str(uuid4())
            new_runtime_id = str(uuid4())
            lease_id = str(uuid4())
            session.add(
                SessionModel(
                    id=session_id,
                    user_id=user.id,
                    workspace_id=workspace_id,
                    token_hash=f"token-{session_id}",
                    csrf_hash=f"csrf-{session_id}",
                    authorization_version=1,
                    expires_at=now + timedelta(hours=1),
                )
            )
            session.add(
                SandboxEnvironmentModel(
                    id=environment_id,
                    owner_user_id=user.id,
                    generation=1,
                    active_runtime_id=old_runtime_id,
                )
            )
            session.add(
                SandboxRuntimeInstanceModel(
                    id=old_runtime_id,
                    environment_id=environment_id,
                    external_runtime_id="runtime-old",
                    runtime_kind="docker",
                    state=RuntimeState.ASSIGNED,
                    generation=1,
                )
            )
            session.add(
                SandboxRuntimeInstanceModel(
                    id=new_runtime_id,
                    external_runtime_id="runtime-new",
                    runtime_kind="docker",
                    state=RuntimeState.READY_UNBOUND,
                    generation=1,
                )
            )
            # Lease/runtime are linked by a database FK but intentionally do
            # not have an ORM relationship.  Flush the runtime rows first so
            # SQLAlchemy cannot batch a lease insert ahead of its target.
            await session.flush()
            session.add(
                SandboxLeaseModel(
                    id=lease_id,
                    environment_id=environment_id,
                    user_id=user.id,
                    auth_session_id=session_id,
                    runtime_instance_id=old_runtime_id,
                    workspace_id=workspace_id,
                    generation=1,
                    state="active",
                    expires_at=now + timedelta(hours=1),
                )
            )
        manager = WarmPoolManager(
            session_factory=factory,
            docker=FakeDocker(),
            resource_profile_id="python-base",
            ready_target=0,
        )
        print("reset integration: seeded database", flush=True)
        scope = SandboxScope(
            owner_user_id=str(user.id),
            auth_session_id=session_id,
            workspace_id=str(workspace_id),
            generation=1,
            lease_expires_at=now + timedelta(hours=1),
        )
        print("reset integration: reset start", flush=True)
        await asyncio.wait_for(
            manager.reset_runtime(
                scope,
                lease_id=lease_id,
                runtime_id=old_runtime_id,
                generation=1,
            ),
            timeout=60,
        )
        print("reset integration: reset complete", flush=True)
        claim = await asyncio.wait_for(manager.claim(scope, lease_id=lease_id), timeout=60)
        assert claim is not None
        assert claim.runtime.generation == 2
        print("reset integration: claim complete", flush=True)
        result = await asyncio.wait_for(
            manager.execute_claimed(
                scope,
                lease_id=lease_id,
                runtime_id=str(claim.runtime.id),
                generation=claim.runtime.generation,
                nonce=claim.nonce,
                source="print(1)",
            ),
            timeout=60,
        )
        assert result["status"] == "completed"
    finally:
        await engine.dispose()
