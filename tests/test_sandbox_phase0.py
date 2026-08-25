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
