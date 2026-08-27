from __future__ import annotations

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
