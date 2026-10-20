from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select

from core.identity import AuthenticatedPrincipal
from server.infrastructure.mysql import DatabaseConfig, create_engine, create_session_factory
from server.infrastructure.mysql.models import (
    SandboxEnvironmentModel,
    SandboxLeaseModel,
    SessionModel,
    UserModel,
    WorkspaceMemberModel,
)
from server.rbac.service import rbac_service
from server.sandbox.contracts import SandboxScope
from server.sandbox.service import sandbox_lifecycle_service
from server.user.schemas import UserCreate
from server.user.service import UserService
from server.web.database_auth import DatabaseSessionClaims


def _claims(*, user_id: str, workspace_id: str = "shared-workspace") -> DatabaseSessionClaims:
    return DatabaseSessionClaims(
        user_id=user_id,
        workspace_id=workspace_id,
        session_id=f"session-{user_id}",
        token_hash_value="token-hash",
        csrf_hash_value="csrf-hash",
        expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1),
        authorization_version=3,
    )


def test_sandbox_scope_is_bound_to_database_session_identity() -> None:
    from server.sandbox.contracts import SandboxScope

    claims = _claims(user_id="user-a")
    principal = AuthenticatedPrincipal(
        user_id="user-a", workspace_ids=frozenset({"shared-workspace"})
    )

    scope = SandboxScope.from_authenticated_request(principal, claims)

    assert scope.owner_user_id == "user-a"
    assert scope.auth_session_id == "session-user-a"
    assert scope.workspace_id == "shared-workspace"
    assert scope.generation == 3


def test_shared_workspace_never_becomes_a_shared_sandbox_owner() -> None:
    from server.sandbox.contracts import SandboxScope

    scope_a = SandboxScope.from_authenticated_request(
        AuthenticatedPrincipal(user_id="user-a", workspace_ids=frozenset({"shared-workspace"})),
        _claims(user_id="user-a"),
    )
    scope_b = SandboxScope.from_authenticated_request(
        AuthenticatedPrincipal(user_id="user-b", workspace_ids=frozenset({"shared-workspace"})),
        _claims(user_id="user-b"),
    )

    assert scope_a.workspace_id == scope_b.workspace_id
    assert scope_a.owner_user_id != scope_b.owner_user_id
    assert scope_a.environment_owner_key != scope_b.environment_owner_key


def test_sandbox_scope_rejects_a_principal_or_workspace_mismatch() -> None:
    from server.sandbox.contracts import SandboxScope, SandboxScopeError

    with pytest.raises(SandboxScopeError, match="user"):
        SandboxScope.from_authenticated_request(
            AuthenticatedPrincipal(user_id="user-b", workspace_ids=frozenset({"shared-workspace"})),
            _claims(user_id="user-a"),
        )

    with pytest.raises(SandboxScopeError, match="workspace"):
        SandboxScope.from_authenticated_request(
            AuthenticatedPrincipal(user_id="user-a", workspace_ids=frozenset({"other-workspace"})),
            _claims(user_id="user-a"),
        )


def test_lease_expiry_never_outlives_the_authenticated_session() -> None:
    from server.sandbox.contracts import SandboxScope

    claims = _claims(user_id="user-a")
    scope = SandboxScope.from_authenticated_request(
        AuthenticatedPrincipal(user_id="user-a", workspace_ids=frozenset({"shared-workspace"})),
        claims,
    )

    assert scope.lease_expires_at == claims.expires_at


def test_environment_contract_enforces_one_owner_and_session_bound_leases() -> None:
    from server.infrastructure.mysql.models import (
        SandboxArtifactModel,
        SandboxEnvironmentModel,
        SandboxExecutionModel,
        SandboxLeaseModel,
    )

    owner_unique = [
        constraint
        for constraint in SandboxEnvironmentModel.__table__.constraints
        if getattr(constraint, "name", None) == "uq_nlp_sandbox_environments_owner"
    ]
    assert len(owner_unique) == 1
    assert {column.name for column in owner_unique[0].columns} == {"owner_user_id"}
    assert "auth_session_id" in SandboxLeaseModel.__table__.c
    assert "generation" in SandboxLeaseModel.__table__.c
    owner_link = [
        constraint
        for constraint in SandboxLeaseModel.__table__.foreign_key_constraints
        if constraint.name == "fk_nlp_sandbox_leases_environment_owner"
    ]
    assert len(owner_link) == 1
    assert {column.name for column in owner_link[0].columns} == {"environment_id", "user_id"}
    execution_owner_link = [
        constraint
        for constraint in SandboxExecutionModel.__table__.foreign_key_constraints
        if constraint.name == "fk_nlp_sandbox_executions_environment_owner"
    ]
    assert len(execution_owner_link) == 1
    artifact_owner_link = [
        constraint
        for constraint in SandboxArtifactModel.__table__.foreign_key_constraints
        if constraint.name == "fk_nlp_sandbox_artifacts_execution_owner"
    ]
    assert len(artifact_owner_link) == 1


@pytest.fixture
async def mysql_session_factory():
    database_url = os.getenv("NLP_AGENT_DATABASE_URL")
    if not database_url:
        pytest.skip("MySQL integration database is not configured")
    engine = create_engine(DatabaseConfig(database_url))
    try:
        yield create_session_factory(engine)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_auth_revocation_releases_the_same_session_lease_atomically(mysql_session_factory) -> None:
    from server.web.database_auth import DatabaseSessionAuth

    async with mysql_session_factory() as session:
        user = await UserService(session).create_user(
            data=UserCreate(
                username=f"sandbox{uuid4().hex[:10]}",
                display_name="Sandbox user",
                password="InitialPw0rd1",
            )
        )
        await session.commit()

    auth = DatabaseSessionAuth(allowed_origins=["http://testserver"])
    token, claims = await auth.login(
        mysql_session_factory, user.username, "InitialPw0rd1", client_key="sandbox-test"
    )
    async with mysql_session_factory.begin() as session:
        principal = await rbac_service.principal_for_user_id(session, user.id)
        await sandbox_lifecycle_service.ensure_current_lease(
            session, SandboxScope.from_authenticated_request(principal, claims)
        )

    await auth.revoke_token(mysql_session_factory, auth.token_hash(token))

    async with mysql_session_factory() as session:
        lease = await session.scalar(
            select(SandboxLeaseModel).where(SandboxLeaseModel.auth_session_id == claims.session_id)
        )
    assert lease is not None
    assert lease.state == "released"
    assert lease.reason == "auth.session.logged_out"


@pytest.mark.asyncio
async def test_idle_session_expiry_releases_its_sandbox_lease(mysql_session_factory) -> None:
    from server.web.auth import AuthenticationError
    from server.web.database_auth import DatabaseSessionAuth

    async with mysql_session_factory() as session:
        user = await UserService(session).create_user(
            data=UserCreate(
                username=f"idle{uuid4().hex[:10]}",
                display_name="Idle user",
                password="InitialPw0rd1",
            )
        )
        await session.commit()

    auth = DatabaseSessionAuth(allowed_origins=["http://testserver"], idle_timeout_s=60)
    token, claims = await auth.login(
        mysql_session_factory, user.username, "InitialPw0rd1", client_key="sandbox-idle"
    )
    async with mysql_session_factory.begin() as session:
        principal = await rbac_service.principal_for_user_id(session, user.id)
        await sandbox_lifecycle_service.ensure_current_lease(
            session, SandboxScope.from_authenticated_request(principal, claims)
        )
        auth_session = await session.scalar(
            select(SessionModel).where(SessionModel.id == claims.session_id).with_for_update()
        )
        assert auth_session is not None
        auth_session.last_seen_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=2)

    with pytest.raises(AuthenticationError, match="expired"):
        await auth.authenticate(mysql_session_factory, token)

    async with mysql_session_factory() as session:
        lease = await session.scalar(
            select(SandboxLeaseModel).where(SandboxLeaseModel.auth_session_id == claims.session_id)
        )
    assert lease is not None
    assert lease.state == "released"
    assert lease.reason == "auth.session.expired"


@pytest.mark.asyncio
async def test_reconciler_marks_expired_leases_without_a_browser_request(mysql_session_factory) -> None:
    from server.web.database_auth import DatabaseSessionAuth

    async with mysql_session_factory() as session:
        user = await UserService(session).create_user(
            data=UserCreate(
                username=f"expiry{uuid4().hex[:10]}",
                display_name="Expiry user",
                password="InitialPw0rd1",
            )
        )
        await session.commit()
    auth = DatabaseSessionAuth(allowed_origins=["http://testserver"])
    _, claims = await auth.login(
        mysql_session_factory, user.username, "InitialPw0rd1", client_key="sandbox-expiry"
    )
    async with mysql_session_factory.begin() as session:
        principal = await rbac_service.principal_for_user_id(session, user.id)
        await sandbox_lifecycle_service.ensure_current_lease(
            session, SandboxScope.from_authenticated_request(principal, claims)
        )
        lease = await session.scalar(
            select(SandboxLeaseModel).where(SandboxLeaseModel.auth_session_id == claims.session_id)
        )
        assert lease is not None
        lease.expires_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=1)

    await sandbox_lifecycle_service.reconcile_expired_leases(mysql_session_factory)

    async with mysql_session_factory() as session:
        lease = await session.scalar(
            select(SandboxLeaseModel).where(SandboxLeaseModel.auth_session_id == claims.session_id)
        )
    assert lease is not None
    assert lease.state == "expired"


@pytest.mark.asyncio
async def test_users_sharing_a_workspace_receive_distinct_environments(mysql_session_factory) -> None:
    from server.web.database_auth import DatabaseSessionAuth

    async with mysql_session_factory() as session:
        service = UserService(session)
        user_a = await service.create_user(data=UserCreate(username=f"owner{uuid4().hex[:10]}", display_name="Owner", password="InitialPw0rd1"))
        user_b = await service.create_user(data=UserCreate(username=f"member{uuid4().hex[:10]}", display_name="Member", password="InitialPw0rd1"))
        workspace_id = await session.scalar(select(WorkspaceMemberModel.workspace_id).where(WorkspaceMemberModel.user_id == user_a.id))
        session.add(WorkspaceMemberModel(workspace_id=workspace_id, user_id=user_b.id, status="active"))
        await session.commit()

    auth = DatabaseSessionAuth(allowed_origins=["http://testserver"])
    _, claims_a = await auth.login(mysql_session_factory, user_a.username, "InitialPw0rd1", client_key="sandbox-a", workspace_id=workspace_id)
    _, claims_b = await auth.login(mysql_session_factory, user_b.username, "InitialPw0rd1", client_key="sandbox-b", workspace_id=workspace_id)
    async with mysql_session_factory.begin() as session:
        principal_a = await rbac_service.principal_for_user_id(session, user_a.id)
        await sandbox_lifecycle_service.ensure_current_lease(session, SandboxScope.from_authenticated_request(principal_a, claims_a))
    async with mysql_session_factory.begin() as session:
        principal_b = await rbac_service.principal_for_user_id(session, user_b.id)
        await sandbox_lifecycle_service.ensure_current_lease(session, SandboxScope.from_authenticated_request(principal_b, claims_b))
    async with mysql_session_factory() as session:
        environments = list((await session.scalars(select(SandboxEnvironmentModel).where(SandboxEnvironmentModel.owner_user_id.in_([user_a.id, user_b.id])))).all())
    assert len(environments) == 2
    assert {environment.owner_user_id for environment in environments} == {user_a.id, user_b.id}


@pytest.mark.asyncio
async def test_disabled_user_revokes_active_sandbox_leases(mysql_session_factory) -> None:
    from server.web.database_auth import DatabaseSessionAuth

    async with mysql_session_factory() as session:
        service = UserService(session)
        user = await service.create_user(data=UserCreate(username=f"disabled{uuid4().hex[:10]}", display_name="Disabled", password="InitialPw0rd1"))
        await session.commit()
    auth = DatabaseSessionAuth(allowed_origins=["http://testserver"])
    _, claims = await auth.login(mysql_session_factory, user.username, "InitialPw0rd1", client_key="sandbox-disabled")
    async with mysql_session_factory.begin() as session:
        principal = await rbac_service.principal_for_user_id(session, user.id)
        await sandbox_lifecycle_service.ensure_current_lease(session, SandboxScope.from_authenticated_request(principal, claims))
    async with mysql_session_factory.begin() as session:
        await UserService(session).update_user_status(user.id, "disabled", actor_user_id="system")
    async with mysql_session_factory() as session:
        lease = await session.scalar(select(SandboxLeaseModel).where(SandboxLeaseModel.auth_session_id == claims.session_id))
    assert lease is not None
    assert lease.state == "revoked"


@pytest.mark.asyncio
async def test_concurrent_lease_claims_create_one_environment(mysql_session_factory) -> None:
    from server.web.database_auth import DatabaseSessionAuth

    async with mysql_session_factory() as session:
        user = await UserService(session).create_user(data=UserCreate(username=f"parallel{uuid4().hex[:10]}", display_name="Parallel", password="InitialPw0rd1"))
        await session.commit()
    auth = DatabaseSessionAuth(allowed_origins=["http://testserver"])
    _, claims = await auth.login(mysql_session_factory, user.username, "InitialPw0rd1", client_key="sandbox-parallel")

    async def claim() -> None:
        async with mysql_session_factory.begin() as session:
            principal = await rbac_service.principal_for_user_id(session, user.id)
            await sandbox_lifecycle_service.ensure_current_lease(session, SandboxScope.from_authenticated_request(principal, claims))

    await asyncio.gather(claim(), claim())
    async with mysql_session_factory() as session:
        environments = list((await session.scalars(select(SandboxEnvironmentModel).where(SandboxEnvironmentModel.owner_user_id == user.id))).all())
    assert len(environments) == 1


async def _two_owners_with_sandbox_leases(factory, *, create_leases=True) -> list[SandboxScope]:
    from server.web.database_auth import DatabaseSessionAuth

    async with factory.begin() as session:
        service = UserService(session)
        users = [
            await service.create_user(data=UserCreate(
                username=f"leaseowner{uuid4().hex[:10]}",
                display_name="Lease regression", password="InitialPw0rd1",
            ))
            for _ in range(2)
        ]
    auth = DatabaseSessionAuth(allowed_origins=["http://testserver"])
    scopes = []
    for user in users:
        _, claims = await auth.login(
            factory, user.username, "InitialPw0rd1", client_key=f"lease-{user.id}"
        )
        async with factory.begin() as session:
            principal = await rbac_service.principal_for_user_id(session, user.id)
            scope = SandboxScope.from_authenticated_request(principal, claims)
            if create_leases:
                await sandbox_lifecycle_service.ensure_current_lease(session, scope)
            scopes.append(scope)
    return scopes


@pytest.mark.asyncio
async def test_first_leases_for_two_new_owners_do_not_gap_lock_each_other(mysql_session_factory, monkeypatch):
    """Both owners must be able to insert after finding no existing lease."""
    scopes = await _two_owners_with_sandbox_leases(mysql_session_factory, create_leases=False)
    original = sandbox_lifecycle_service.revoke_expired_leases
    both_cleaned = asyncio.Event()
    owners = set()

    async def synchronized_cleanup(session, *, owner_user_id, now=None):
        await original(session, owner_user_id=owner_user_id, now=now)
        owners.add(owner_user_id)
        if len(owners) == 2:
            both_cleaned.set()
        await asyncio.wait_for(both_cleaned.wait(), timeout=5)

    monkeypatch.setattr(sandbox_lifecycle_service, "revoke_expired_leases", synchronized_cleanup)

    async def first_claim(scope):
        async with mysql_session_factory.begin() as session:
            return await sandbox_lifecycle_service.ensure_current_lease(session, scope)

    results = await asyncio.wait_for(asyncio.gather(*(first_claim(scope) for scope in scopes)), timeout=10)
    assert len({result["lease"]["id"] for result in results}) == 2


@pytest.mark.asyncio
async def test_different_owners_can_renew_leases_while_both_user_rows_are_locked(
    mysql_session_factory, monkeypatch,
) -> None:
    """Force the interleaving that deadlocked the live two-user claim wave."""
    scopes = await _two_owners_with_sandbox_leases(mysql_session_factory)
    original = sandbox_lifecycle_service.ensure_environment
    both_locked = asyncio.Event()
    locked_owners: set[str] = set()

    async def synchronized_environment(session, scope):
        environment = await original(session, scope)
        locked_owners.add(scope.owner_user_id)
        if len(locked_owners) == 2:
            both_locked.set()
        await asyncio.wait_for(both_locked.wait(), timeout=5)
        return environment

    monkeypatch.setattr(sandbox_lifecycle_service, "ensure_environment", synchronized_environment)

    async def renew(scope):
        async with mysql_session_factory.begin() as session:
            return await sandbox_lifecycle_service.ensure_current_lease(session, scope)

    results = await asyncio.wait_for(
        asyncio.gather(*(renew(scope) for scope in scopes)), timeout=10
    )
    assert len({result["lease"]["id"] for result in results}) == 2
    assert all(result["lease"]["state"] == "active" for result in results)


@pytest.mark.asyncio
async def test_describe_does_not_lock_or_expire_another_owner_and_sweep_still_expires(
    mysql_session_factory,
) -> None:
    scopes = await _two_owners_with_sandbox_leases(mysql_session_factory)
    async with mysql_session_factory.begin() as session:
        other_lease = await session.scalar(select(SandboxLeaseModel).where(
            SandboxLeaseModel.auth_session_id == scopes[1].auth_session_id
        ))
        other_lease.expires_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=1)
        other_lease_id = other_lease.id

    async def describe_first_owner():
        async with mysql_session_factory.begin() as session:
            return await sandbox_lifecycle_service.describe(session, scopes[0])

    # A request for A must finish while another transaction owns B's user row.
    # The old global expiry UPDATE waited on B and made unrelated users contend.
    async with mysql_session_factory.begin() as held:
        await held.get(UserModel, scopes[1].owner_user_id, with_for_update=True)
        result = await asyncio.wait_for(describe_first_owner(), timeout=3)
        assert result["lease"]["state"] == "active"
        async with mysql_session_factory() as read:
            assert (await read.get(SandboxLeaseModel, other_lease_id)).state == "active"

    await sandbox_lifecycle_service.reconcile_expired_leases(mysql_session_factory)
    async with mysql_session_factory() as read:
        assert (await read.get(SandboxLeaseModel, other_lease_id)).state == "expired"
        first_lease = await read.scalar(select(SandboxLeaseModel).where(
            SandboxLeaseModel.auth_session_id == scopes[0].auth_session_id
        ))
        assert first_lease.state == "active"


@pytest.mark.asyncio
async def test_concurrent_model_scratch_creates_one_environment(mysql_session_factory) -> None:
    """Scratch may create the logical environment, but creation must be idempotent."""
    from server.sandbox.model_tools import SandboxModelToolService
    from server.web.database_auth import DatabaseSessionAuth

    async with mysql_session_factory() as session:
        user = await UserService(session).create_user(
            data=UserCreate(
                username=f"scratchparallel{uuid4().hex[:10]}",
                display_name="Scratch parallel",
                password="InitialPw0rd1",
            )
        )
        await session.commit()
    auth = DatabaseSessionAuth(allowed_origins=["http://testserver"])
    _, claims = await auth.login(
        mysql_session_factory, user.username, "InitialPw0rd1", client_key="scratch-parallel"
    )
    config = {
        "configurable": {
            "thread_id": claims.session_id,
            "user_id": str(user.id),
            "workspace_id": str(claims.workspace_id),
        }
    }
    service = SandboxModelToolService(mode="inmemory", session_factory=mysql_session_factory)
    results = await asyncio.gather(
        service.run_scratch(source="print(1)", config=config),
        service.run_scratch(source="print(2)", config=config),
    )
    await service.close()
    assert all(result["ok"] for result in results)
    async with mysql_session_factory() as session:
        environments = list(
            (
                await session.scalars(
                    select(SandboxEnvironmentModel).where(
                        SandboxEnvironmentModel.owner_user_id == user.id
                    )
                )
            ).all()
        )
    assert len(environments) == 1
