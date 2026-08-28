from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace


def _auth_fixture(**overrides: object) -> tuple[SimpleNamespace, SimpleNamespace, SimpleNamespace]:
    now = datetime.now(UTC).replace(tzinfo=None)
    lease = SimpleNamespace(
        user_id="user-1",
        auth_session_id="session-1",
        workspace_id="workspace-1",
        expires_at=now + timedelta(minutes=5),
    )
    auth_session = SimpleNamespace(
        user_id="user-1",
        workspace_id="workspace-1",
        revoked_at=None,
        expires_at=now + timedelta(minutes=5),
        authorization_version=3,
    )
    user = SimpleNamespace(status="active", deleted_at=None, authorization_version=3)
    for target, values in ((lease, overrides), (auth_session, overrides), (user, overrides)):
        for key, value in values.items():
            if hasattr(target, key):
                setattr(target, key, value)
    return lease, auth_session, user


def test_refill_plan_counts_only_pristine_ready_slots() -> None:
    from server.sandbox.manager import refill_deficit

    assert refill_deficit(target=3, ready_count=1, creating_count=1) == 1
    assert refill_deficit(target=2, ready_count=3, creating_count=0) == 0


def test_kernel_ready_finalization_promotes_cached_image_slots() -> None:
    from server.sandbox.manager import ready_state_after_kernel_check

    assert ready_state_after_kernel_check("creating") == "ready_unbound"
    assert ready_state_after_kernel_check("created") == "ready_unbound"


def test_kernel_ready_wait_retries_until_startup_completes() -> None:
    """A slow guest must not be destroyed after the first health probe."""
    from server.sandbox.manager import wait_for_kernel_ready

    class DelayedKernel:
        attempts = 0

        async def kernel_ready(self, _external_id: str) -> bool:
            self.attempts += 1
            return self.attempts >= 3

    adapter = DelayedKernel()
    result = asyncio.run(
        wait_for_kernel_ready(
            adapter,
            "runtime-1",
            timeout_seconds=0.5,
            poll_interval_seconds=0.001,
        )
    )
    assert result.ready
    assert result.failure_reason is None
    assert adapter.attempts == 3


def test_kernel_ready_wait_preserves_last_probe_error_on_timeout() -> None:
    from server.sandbox.manager import wait_for_kernel_ready

    class BrokenKernel:
        async def kernel_ready(self, _external_id: str) -> bool:
            raise RuntimeError("guest has not started")

    result = asyncio.run(
        wait_for_kernel_ready(
            BrokenKernel(),
            "runtime-2",
            timeout_seconds=0.01,
            poll_interval_seconds=0.001,
        )
    )

    assert not result.ready
    assert result.failure_reason == "RuntimeError: guest has not started"


def test_create_one_keeps_delayed_kernel_alive_until_ready() -> None:
    from server.sandbox.manager import WarmPoolManager

    runtime = SimpleNamespace(
        state="creating",
        external_runtime_id=None,
        last_heartbeat_at=None,
    )

    class DelayedDocker:
        runtime_kind = "docker"
        image_digest = "registry.example/nova@sha256:" + "a" * 64

        def __init__(self) -> None:
            self.attempts = 0
            self.destroyed: list[str] = []

        async def image_cached(self) -> bool:
            return False

        async def create_ready(self, *, name: str, claim_nonce: str) -> str:
            assert name.startswith("nova-runtime-")
            assert claim_nonce == ""
            return "external-runtime-1"

        async def kernel_ready(self, _external_id: str) -> bool:
            self.attempts += 1
            return self.attempts >= 3

        async def destroy(self, external_id: str) -> None:
            self.destroyed.append(external_id)

    class Session:
        async def get(self, _model: object, _runtime_id: str, **_kwargs: object) -> object:
            return runtime

    class Begin:
        async def __aenter__(self) -> Session:
            return Session()

        async def __aexit__(self, *_args: object) -> None:
            return None

    class SessionFactory:
        def begin(self) -> Begin:
            return Begin()

    docker = DelayedDocker()
    manager = WarmPoolManager(
        session_factory=SessionFactory(),
        docker=docker,
        resource_profile_id="python-base",
        ready_target=1,
    )

    asyncio.run(manager._create_one(runtime_id="runtime-row-1"))

    assert docker.attempts == 3
    assert docker.destroyed == []
    assert runtime.external_runtime_id == "external-runtime-1"
    assert runtime.state == "ready_unbound"


def test_create_one_starts_cached_l1_before_retrying_kernel_readiness() -> None:
    from server.sandbox.manager import WarmPoolManager

    runtime = SimpleNamespace(
        state="creating",
        external_runtime_id=None,
        last_heartbeat_at=None,
    )

    class CachedDocker:
        runtime_kind = "docker"
        image_digest = "registry.example/nova@sha256:" + "b" * 64

        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []
            self.health_attempts = 0

        async def image_cached(self) -> bool:
            self.calls.append(("image_cached", ""))
            return True

        async def create_l1(self, *, name: str) -> str:
            self.calls.append(("create_l1", name))
            return "external-runtime-l1"

        async def start_l1(self, external_runtime_id: str) -> None:
            self.calls.append(("start_l1", external_runtime_id))

        async def kernel_ready(self, external_runtime_id: str) -> bool:
            assert external_runtime_id == "external-runtime-l1"
            self.health_attempts += 1
            self.calls.append(("kernel_ready", external_runtime_id))
            return self.health_attempts >= 3

        async def destroy(self, external_runtime_id: str) -> None:
            self.calls.append(("destroy", external_runtime_id))

    class Session:
        async def get(self, _model: object, _runtime_id: str, **_kwargs: object) -> object:
            return runtime

    class Begin:
        async def __aenter__(self) -> Session:
            return Session()

        async def __aexit__(self, *_args: object) -> None:
            return None

    class SessionFactory:
        def begin(self) -> Begin:
            return Begin()

    docker = CachedDocker()
    manager = WarmPoolManager(
        session_factory=SessionFactory(),
        docker=docker,
        resource_profile_id="python-base",
        ready_target=1,
    )

    asyncio.run(manager._create_one(runtime_id="runtime-row-l1"))

    assert [kind for kind, _value in docker.calls] == [
        "image_cached",
        "create_l1",
        "start_l1",
        "kernel_ready",
        "kernel_ready",
        "kernel_ready",
    ]
    assert docker.health_attempts == 3
    assert runtime.external_runtime_id == "external-runtime-l1"
    assert runtime.state == "ready_unbound"


def test_manager_reconcile_never_adopts_an_orphaned_container() -> None:
    from server.sandbox.manager import reconcile_actions

    actions = reconcile_actions(database_ids={"known"}, docker_ids={"known", "orphan"})

    assert actions.mark_missing_failed == set()
    assert actions.destroy_orphans == {"orphan"}


def test_auth_lifecycle_allows_only_current_active_session() -> None:
    from server.sandbox.manager import auth_lifecycle_allows_execution

    lease, auth_session, user = _auth_fixture()
    now = datetime.now(UTC).replace(tzinfo=None)

    assert auth_lifecycle_allows_execution(
        lease=lease, auth_session=auth_session, user=user, scope_generation=3, now=now
    )

    for overrides in (
        {"revoked_at": now},
        {"expires_at": now - timedelta(seconds=1)},
        {"status": "disabled"},
        {"deleted_at": now},
        {"authorization_version": 4},
    ):
        lease, auth_session, user = _auth_fixture(**overrides)
        assert not auth_lifecycle_allows_execution(
            lease=lease, auth_session=auth_session, user=user, scope_generation=3, now=now
        )


def test_runtime_control_requires_scope_lease_and_generation_binding() -> None:
    from server.sandbox.contracts import SandboxScope
    from server.sandbox.manager import runtime_control_allows

    now = datetime.now(UTC)
    scope = SandboxScope(
        owner_user_id="user-1",
        auth_session_id="session-1",
        workspace_id="workspace-1",
        generation=3,
        lease_expires_at=now + timedelta(minutes=5),
    )
    lease = SimpleNamespace(
        id="lease-1",
        user_id="user-1",
        auth_session_id="session-1",
        workspace_id="workspace-1",
        environment_id="environment-1",
        runtime_instance_id="runtime-1",
        generation=7,
        state="active",
        expires_at=now.replace(tzinfo=None) + timedelta(minutes=5),
    )
    runtime = SimpleNamespace(
        id="runtime-1",
        environment_id="environment-1",
        generation=7,
        state="assigned",
    )
    assert runtime_control_allows(
        scope=scope, lease=lease, runtime=runtime, expected_generation=7, now=now.replace(tzinfo=None)
    )
    for overrides in (
        {"user_id": "other-user"},
        {"auth_session_id": "other-session"},
        {"runtime_instance_id": "other-runtime"},
        {"workspace_id": "other-workspace"},
        {"generation": 8},
        {"state": "released"},
    ):
        changed = SimpleNamespace(**lease.__dict__)
        for key, value in overrides.items():
            setattr(changed, key, value)
        assert not runtime_control_allows(
            scope=scope, lease=changed, runtime=runtime, expected_generation=7, now=now.replace(tzinfo=None)
        )
