from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from core.identity import AccessDeniedError, AuthenticatedPrincipal
from core.rbac import Permission
from server.rbac.service import rbac_service
from server.user import controller
from server.user.schemas import PasswordReset, UserAdminUpdate, UserCreateWithRole
from server.auth.dependencies import get_current_principal, get_db_session, get_write_access


def _principal() -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        user_id="admin-user",
        workspace_ids=frozenset({"*"}),
        roles=frozenset({"developer"}),
    )


def _user_manager_principal() -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        user_id="user-manager",
        workspace_ids=frozenset({"*"}),
        roles=frozenset({"custom-user-manager"}),
        permissions=frozenset({Permission.SYSTEM_USER_MANAGE.value}),
    )


def _user(*, user_id: str = "target-user", display_name: str = "Target"):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    return SimpleNamespace(
        id=user_id,
        username=f"user{user_id.replace('-', '')[:8]}",
        display_name=display_name,
        status="active",
        created_at=now,
        updated_at=now,
        deleted_at=None,
        last_login_at=None,
    )


class _FakeService:
    def __init__(self, _session):
        self.session = SimpleNamespace(refresh=AsyncMock())
        self.user = _user()
        self.create_user = AsyncMock(return_value=self.user)
        self.change_password = AsyncMock()

    async def get_user(self, _user_id):
        return self.user

    async def get_roles_for_users(self, _user_ids):
        return {self.user.id: ["student"]}


def _test_app() -> FastAPI:
    app = FastAPI()
    app.include_router(controller.router)

    async def db_session():
        yield SimpleNamespace(flush=AsyncMock())

    async def principal():
        return _principal()

    async def write_access():
        return object()

    app.dependency_overrides[get_db_session] = db_session
    app.dependency_overrides[get_current_principal] = principal
    app.dependency_overrides[get_write_access] = write_access
    return app


@pytest.mark.asyncio
async def test_create_user_handler_assigns_requested_roles_and_returns_safe_response(monkeypatch):
    service = _FakeService(None)
    monkeypatch.setattr(controller, "UserService", lambda session: service)
    replace_roles = AsyncMock(return_value=frozenset({"teacher"}))
    audit = AsyncMock()
    monkeypatch.setattr(rbac_service, "replace_user_roles", replace_roles)
    monkeypatch.setattr(rbac_service, "audit", audit)

    result = await controller.create_user(
        UserCreateWithRole(
            username="newuser",
            display_name="New User",
            password="InitialPw0rd1",
            role_codes=["teacher"],
        ),
        db=object(),
        _write=object(),
        principal=_principal(),
    )

    service.create_user.assert_awaited_once()
    replace_roles.assert_awaited_once()
    assert replace_roles.await_args.kwargs["role_codes"] == {"teacher"}
    audit.assert_awaited_once()
    assert result.username == service.user.username
    assert "password" not in result.model_dump()


@pytest.mark.asyncio
async def test_create_user_with_roles_requires_role_management_permission(monkeypatch):
    service = _FakeService(None)
    monkeypatch.setattr(controller, "UserService", lambda session: service)

    with pytest.raises(AccessDeniedError):
        await controller.create_user(
            UserCreateWithRole(
                username="newuser",
                display_name="New User",
                password="InitialPw0rd1",
                role_codes=["developer"],
            ),
            db=object(),
            _write=object(),
            principal=_user_manager_principal(),
        )

    service.create_user.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_user_handler_persists_display_name(monkeypatch):
    service = _FakeService(None)
    monkeypatch.setattr(controller, "UserService", lambda session: service)
    monkeypatch.setattr(rbac_service, "audit", AsyncMock())

    result = await controller.update_user(
        service.user.id,
        UserAdminUpdate(display_name="Renamed user"),
        db=SimpleNamespace(flush=AsyncMock()),
        _write=object(),
        principal=_principal(),
    )

    assert service.user.display_name == "Renamed user"
    assert result.display_name == "Renamed user"


@pytest.mark.asyncio
async def test_admin_display_name_update_is_audited(monkeypatch):
    service = _FakeService(None)
    monkeypatch.setattr(controller, "UserService", lambda session: service)
    audit = AsyncMock()
    monkeypatch.setattr(rbac_service, "audit", audit)

    await controller.update_user(
        service.user.id,
        UserAdminUpdate(display_name="Renamed user"),
        db=SimpleNamespace(flush=AsyncMock()),
        _write=object(),
        principal=_principal(),
    )

    audit.assert_awaited_once()
    assert audit.await_args.kwargs["reason_code"] == "user_display_name_updated"


@pytest.mark.asyncio
async def test_reset_password_handler_calls_service_and_audits(monkeypatch):
    service = _FakeService(None)
    monkeypatch.setattr(controller, "UserService", lambda session: service)
    audit = AsyncMock()
    monkeypatch.setattr(rbac_service, "audit", audit)

    result = await controller.reset_user_password(
        service.user.id,
        PasswordReset(new_password="ChangedPw0rd2"),
        db=object(),
        _write=object(),
        principal=_principal(),
    )

    assert result is None
    service.change_password.assert_awaited_once_with(
        service.user.id, "ChangedPw0rd2"
    )
    audit.assert_awaited_once()


@pytest.mark.asyncio
async def test_management_http_endpoints_cover_create_edit_and_password_reset(monkeypatch):
    service = _FakeService(None)
    monkeypatch.setattr(controller, "UserService", lambda session: service)
    replace_roles = AsyncMock(return_value=frozenset({"teacher"}))
    audit = AsyncMock()
    monkeypatch.setattr(rbac_service, "replace_user_roles", replace_roles)
    monkeypatch.setattr(rbac_service, "audit", audit)

    async with AsyncClient(
        transport=ASGITransport(app=_test_app()), base_url="http://testserver"
    ) as client:
        created = await client.post(
            "/api/v1/users",
            json={
                "username": "newuser",
                "display_name": "New User",
                "password": "InitialPw0rd1",
                "role_codes": ["teacher"],
            },
        )
        edited = await client.patch(
            f"/api/v1/users/{service.user.id}",
            json={"display_name": "Renamed user"},
        )
        reset = await client.post(
            f"/api/v1/users/{service.user.id}/password",
            json={"new_password": "ChangedPw0rd2"},
        )

    assert created.status_code == 201
    assert created.json()["roles"] == ["student"]
    assert "password" not in created.json()
    assert edited.status_code == 200
    assert edited.json()["display_name"] == "Renamed user"
    assert reset.status_code == 204
    service.change_password.assert_awaited_once_with(
        service.user.id, "ChangedPw0rd2"
    )
