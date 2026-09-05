from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import select
from server.auth import code_store
from server.infrastructure.mysql import DatabaseConfig, create_engine, create_session_factory

from server.infrastructure.mysql.models import SessionModel, UserModel, WorkspaceMemberModel
from server.rbac.service import rbac_service
from core.rbac import Permission
from server.user.controller import _user_response_with_roles
from server.user.schemas import UserCreate, UserCreateWithRole, UserRegister
from server.user.service import UserService
from server.web.auth import AuthenticationError, OriginRejectedError
from server.web.database_auth import DatabaseSessionAuth


def test_database_session_credentials_are_stored_as_one_way_digests() -> None:
    auth = DatabaseSessionAuth(cookie_name="nlp_session", ttl_s=3600)
    token = "opaque-session-token"
    csrf = "csrf-token"

    assert auth.token_hash(token) != token
    assert auth.csrf_hash(csrf) != csrf
    assert auth.token_hash(token) == auth.token_hash(token)
    assert auth.csrf_hash(csrf) == auth.csrf_hash(csrf)


@pytest.mark.asyncio
async def test_malformed_password_hash_fails_closed() -> None:
    service = UserService(None)  # password verification does not require a DB session
    user = UserModel(
        id="user-1",
        username="user1",
        password_hash="not-an-argon2-hash",
        display_name="User",
    )

    assert await service.verify_password(user, "anything") is False


def test_new_user_default_role_is_guest() -> None:
    from server.user.service import DEFAULT_USER_ROLE

    assert DEFAULT_USER_ROLE == "guest"


def test_builtin_rbac_and_developer_menu_catalog_are_stable() -> None:
    from server.rbac.catalog import MENU_CATALOG, ROLE_NAMES, menu_id, role_id, role_menu_rows

    assert set(ROLE_NAMES) == {"guest", "student", "teacher", "developer"}
    assert len(MENU_CATALOG) == len({item[0] for item in MENU_CATALOG})
    assert len({menu_id(item[0]) for item in MENU_CATALOG}) == len(MENU_CATALOG)
    assert {row["role_id"] for row in role_menu_rows()} == {role_id("developer")}


def test_agent_session_management_is_not_in_the_developer_menu_catalog() -> None:
    from server.rbac.catalog import MENU_CATALOG

    assert not any(item[0] == "developer.sessions" for item in MENU_CATALOG)
    assert not any(item[2] == "/developer/sessions" for item in MENU_CATALOG)


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
async def test_new_user_is_persisted_with_guest_role(mysql_session_factory) -> None:
    async with mysql_session_factory() as session:
        service = UserService(session)
        user = await service.create_user(
            data=UserCreate(
                username=f"guest{uuid4().hex[:10]}",
                display_name="Guest user",
                password="InitialPw0rd1",
            )
        )
        await session.commit()
        assert await rbac_service.roles_for(session, user.id) == frozenset({"guest"})


@pytest.mark.asyncio
async def test_phone_registration_uses_unified_service_transaction(
    mysql_session_factory, monkeypatch
) -> None:
    consume_code = AsyncMock(side_effect=[True, True])
    monkeypatch.setattr(code_store, "consume_code", consume_code)
    phone = f"+86139{uuid4().int % 10**8:08d}"
    data = UserRegister(
        phone_number=phone,
        sms_code="123456",
        password="InitialPw0rd1",
        display_name="Phone user",
        captcha_id="captcha-test",
        captcha_code="ABCD",
    )

    async with mysql_session_factory() as session:
        async with session.begin():
            user = await UserService(session).register_user(data)
            assert user.username == "".join(ch for ch in phone if ch.isdigit())
            assert user.phone_number == phone
            assert user.registration_source == "phone"
            assert await rbac_service.roles_for(session, user.id) == frozenset({"guest"})

    assert consume_code.await_count == 2
    assert [call.kwargs["kind"] for call in consume_code.await_args_list] == [
        "captcha",
        "sms",
    ]


@pytest.mark.asyncio
async def test_admin_create_response_can_serialize_after_role_assignment(
    mysql_session_factory,
) -> None:
    """Role assignment refreshes server-managed user timestamps before serialization."""
    async with mysql_session_factory() as session:
        data = UserCreateWithRole(
            username=f"rolecreate{uuid4().hex[:10]}",
            display_name="Role-created user",
            password="InitialPw0rd1",
            role_codes=["student"],
        )
        service = UserService(session)
        user = await service.create_user(data, actor_user_id=None)
        await rbac_service.replace_user_roles(
            session,
            user_id=user.id,
            role_codes=set(data.role_codes),
            assigned_by_user_id=None,
        )

        response = await _user_response_with_roles(service, user)

        assert response.username == data.username
        assert response.roles == ["student"]


@pytest.mark.asyncio
async def test_new_user_can_login_case_insensitively(mysql_session_factory) -> None:
    auth = DatabaseSessionAuth(allowed_origins=["http://testserver"])
    async with mysql_session_factory() as session:
        service = UserService(session)
        user = await service.create_user(
            data=UserCreate(
                username=f"Login{uuid4().hex[:10]}",
                display_name="Login user",
                password="InitialPw0rd1",
            )
        )
        await session.commit()

    token, claims = await auth.login(
        mysql_session_factory,
        user.username.upper(),
        "InitialPw0rd1",
        client_key="test-new-user-login",
    )
    assert claims.user_id == user.id
    authenticated = await auth.authenticate(mysql_session_factory, token)
    assert authenticated.user_id == user.id


@pytest.mark.asyncio
async def test_database_developer_role_does_not_include_sensitive_data_by_default(
    mysql_session_factory,
) -> None:
    async with mysql_session_factory() as session:
        user = await UserService(session).create_user(
            data=UserCreate(
                username=f"developer{uuid4().hex[:10]}",
                display_name="Developer user",
                password="InitialPw0rd1",
            )
        )
        await rbac_service.replace_user_roles(
            session,
            user_id=user.id,
            role_codes={"developer"},
            assigned_by_user_id=None,
        )
        principal = await rbac_service.principal_for_user_id(session, user.id)

    assert Permission.SYSTEM_SENSITIVE_DATA_READ.value not in principal.permissions


@pytest.mark.asyncio
async def test_password_change_revokes_database_sessions(mysql_session_factory) -> None:
    auth = DatabaseSessionAuth(allowed_origins=["http://testserver"])
    async with mysql_session_factory() as session:
        service = UserService(session)
        user = await service.create_user(
            data=UserCreate(
                username=f"revoke{uuid4().hex[:10]}",
                display_name="Revocation user",
                password="InitialPw0rd1",
            )
        )
        workspace_id = await session.scalar(
            select(WorkspaceMemberModel.workspace_id).where(WorkspaceMemberModel.user_id == user.id)
        )
        session.add(SessionModel(
            id=str(uuid4()), user_id=user.id, workspace_id=workspace_id,
            token_hash=f"token-{uuid4()}", csrf_hash=f"csrf-{uuid4()}",
            expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=1),
        ))
        await session.flush()
        await session.commit()
        token, claims = await auth.login(
            mysql_session_factory,
            user.username,
            "InitialPw0rd1",
            client_key="test",
        )
        assert (await auth.authenticate(mysql_session_factory, token)).session_id == claims.session_id
        await service.change_password(user.id, "NewPassw0rd2")
        # UserService participates in the request transaction; the controller
        # owns the commit boundary. Commit before validating from a separate
        # authentication session so MySQL does not retain the update lock.
        await session.commit()
        remaining = await session.scalar(
            select(SessionModel.revoked_at).where(SessionModel.user_id == user.id)
        )
        assert remaining is not None
        with pytest.raises(AuthenticationError, match="invalid|expired"):
            await auth.authenticate(mysql_session_factory, token)


@pytest.mark.asyncio
async def test_websocket_ticket_is_origin_bound_and_single_use(mysql_session_factory) -> None:
    auth = DatabaseSessionAuth(allowed_origins=["http://testserver"])
    async with mysql_session_factory() as session:
        service = UserService(session)
        user = await service.create_user(
            data=UserCreate(
                username=f"ws{uuid4().hex[:10]}",
                display_name="WebSocket user",
                password="InitialPw0rd1",
            )
        )
        await session.commit()

    token, claims = await auth.login(
        mysql_session_factory,
        user.username,
        "InitialPw0rd1",
        client_key="test-ws",
    )
    ticket = await auth.issue_ws_ticket(
        mysql_session_factory,
        claims,
        origin="http://testserver",
        host="testserver",
    )
    consumed = await auth.consume_ws_ticket(
        mysql_session_factory,
        ticket,
        origin="http://testserver",
        host="testserver",
    )
    assert consumed.session_id == claims.session_id
    assert consumed.user_id == claims.user_id

    with pytest.raises(AuthenticationError, match="invalid|expired"):
        await auth.consume_ws_ticket(
            mysql_session_factory,
            ticket,
            origin="http://testserver",
            host="testserver",
        )

    fresh_claims = await auth.authenticate(mysql_session_factory, token)
    fresh_ticket = await auth.issue_ws_ticket(
        mysql_session_factory,
        fresh_claims,
        origin="http://testserver",
        host="testserver",
    )
    with pytest.raises(OriginRejectedError):
        await auth.consume_ws_ticket(
            mysql_session_factory,
            fresh_ticket,
            origin="https://evil.example",
            host="testserver",
        )


@pytest.mark.asyncio
async def test_touch_extends_database_session_expiry(mysql_session_factory) -> None:
    auth = DatabaseSessionAuth(allowed_origins=["http://testserver"], ttl_s=300)
    async with mysql_session_factory() as session:
        user = await UserService(session).create_user(
            data=UserCreate(
                username=f"slide{uuid4().hex[:10]}",
                display_name="Sliding user",
                password="InitialPw0rd1",
            )
        )
        await session.commit()

    token, claims = await auth.login(
        mysql_session_factory,
        user.username,
        "InitialPw0rd1",
        client_key="test-slide",
    )
    original_expiry = claims.expires_at

    # Simulate a TTL increase after the session was already issued: the next
    # authenticated request should extend the absolute expiry accordingly.
    auth.ttl_s = 900
    refreshed = await auth.authenticate(mysql_session_factory, token)

    assert refreshed.expires_at >= original_expiry + timedelta(seconds=600)
