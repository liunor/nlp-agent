"""Uploads module API unit and integration tests."""

from __future__ import annotations

import io
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from core.identity import AuthenticatedPrincipal
from core.session_context import SessionContext
from server.uploads.controller import (
    get_current_principal,
    get_write_access,
    router as uploads_router,
)
from server.uploads import controller as uploads_controller
from server.web.database_auth import DatabaseSessionClaims
from server.tools.vision import input_resolver


class FakeSessions:
    def __init__(self, context: SessionContext) -> None:
        self.context = context

    async def resolve(
        self, principal: AuthenticatedPrincipal, session_id: str
    ) -> SessionContext:
        if session_id != self.context.session_id:
            raise FileNotFoundError(session_id)
        principal.require_context(self.context)
        return self.context


class FakeAuthorizationSession:
    async def __aenter__(self) -> "FakeAuthorizationSession":
        return self

    async def __aexit__(self, *args: object) -> None:
        return None


class FakeAuthorizationSessionFactory:
    def __init__(self) -> None:
        self.session = FakeAuthorizationSession()

    def __call__(self) -> FakeAuthorizationSession:
        return self.session


class LegacyAuthThatMustNotBeUsed:
    cookie_name = "nlp_session"

    def authenticate(self, _token: str | None) -> None:
        raise AssertionError("database-backed uploads must not use legacy auth")


class FakeDatabaseAuth:
    def __init__(self, claims: DatabaseSessionClaims) -> None:
        self.claims = claims
        self.token: str | None = None
        self.origin: tuple[str | None, str | None] | None = None
        self.csrf: str | None = None

    async def authenticate(
        self, _factory: FakeAuthorizationSessionFactory, token: str | None
    ) -> DatabaseSessionClaims:
        self.token = token
        return self.claims

    def require_same_origin(self, origin: str | None, host: str | None) -> None:
        self.origin = (origin, host)

    def require_csrf(
        self, _claims: DatabaseSessionClaims, csrf_token: str | None
    ) -> None:
        self.csrf = csrf_token


def _make_test_image_bytes(width: int = 100, height: int = 100, format: str = "PNG") -> bytes:
    img = Image.new("RGB", (width, height), color="red")
    buf = io.BytesIO()
    img.save(buf, format=format)
    return buf.getvalue()


@pytest.fixture
def mock_principal() -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        user_id="test_user",
        workspace_ids=frozenset({"other_ws", "test_ws"}),
        roles=frozenset({"student"}),
    )


@pytest.fixture
def test_app(
    mock_principal: AuthenticatedPrincipal, tmp_path, monkeypatch
) -> FastAPI:
    uploads_root = tmp_path / ".data" / "uploads"
    monkeypatch.setattr(input_resolver, "DEFAULT_UPLOADS_ROOT", uploads_root)
    app = FastAPI()
    app.include_router(uploads_router)
    app.state.gateway = SimpleNamespace(
        sessions=FakeSessions(
            SessionContext(
                session_id="sess_123",
                user_id="test_user",
                workspace_id="test_ws",
            )
        )
    )
    app.state.test_uploads_root = uploads_root
    app.dependency_overrides[get_current_principal] = lambda: mock_principal
    app.dependency_overrides[get_write_access] = lambda: None
    return app


def test_upload_image_success(test_app: FastAPI) -> None:
    client = TestClient(test_app)

    img_data = _make_test_image_bytes(120, 80, "PNG")
    response = client.post(
        "/api/v1/uploads",
        data={"session_id": "sess_123"},
        files={"file": ("my_image.png", img_data, "image/png")},
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["file_name"].endswith(".png")
    assert payload["url"] == f"/api/v1/uploads/sess_123/{payload['file_name']}"
    assert payload["media_type"] == "image/png"
    assert payload["width"] == 120
    assert payload["height"] == 80
    assert payload["size_bytes"] == len(img_data)
    assert len(payload["sha256"]) == 64
    stored = (
        test_app.state.test_uploads_root
        / "test_ws"
        / "test_user"
        / "sess_123"
        / payload["file_name"]
    )
    assert stored.is_file()
    assert not (test_app.state.test_uploads_root / "other_ws").exists()

    # Verify GET returns the file with nosniff header
    get_res = client.get(payload["url"])
    assert get_res.status_code == 200
    assert get_res.headers.get("x-content-type-options") == "nosniff"
    assert get_res.content == img_data


def test_database_authenticated_upload_uses_database_session_dependencies(
    mock_principal: AuthenticatedPrincipal, monkeypatch, tmp_path
) -> None:
    uploads_root = tmp_path / ".data" / "uploads"
    monkeypatch.setattr(input_resolver, "DEFAULT_UPLOADS_ROOT", uploads_root)
    claims = DatabaseSessionClaims(
        user_id="database-user-id",
        workspace_id="test_ws",
        session_id="browser-session-id",
        token_hash_value="token-hash",
        csrf_hash_value="csrf-hash",
        expires_at=datetime.now(timezone.utc),
        authorization_version=1,
    )
    database_auth = FakeDatabaseAuth(claims)
    session_factory = FakeAuthorizationSessionFactory()
    app = FastAPI()
    app.include_router(uploads_router)
    app.state.auth = LegacyAuthThatMustNotBeUsed()
    app.state.auth_injected = False
    app.state.database_auth = database_auth
    app.state.gateway = SimpleNamespace(
        authorization_session_factory=session_factory,
        sessions=FakeSessions(
            SessionContext(
                session_id="sess_123",
                user_id="test_user",
                workspace_id="test_ws",
            )
        ),
    )

    async def principal_for_user_id(
        session: FakeAuthorizationSession, user_id: str
    ) -> AuthenticatedPrincipal:
        assert session is session_factory.session
        assert user_id == "database-user-id"
        return mock_principal

    monkeypatch.setattr(
        uploads_controller.rbac_service,
        "principal_for_user_id",
        principal_for_user_id,
    )

    client = TestClient(app)
    client.cookies.set("nlp_session", "database-token")
    response = client.post(
        "/api/v1/uploads",
        data={"session_id": "sess_123"},
        files={"file": ("image.png", _make_test_image_bytes(), "image/png")},
        headers={"Origin": "http://127.0.0.1:5173", "X-CSRF-Token": "csrf-token"},
    )

    assert response.status_code == 201
    assert database_auth.token == "database-token"
    assert database_auth.origin == ("http://127.0.0.1:5173", "testserver")
    assert database_auth.csrf == "csrf-token"


def test_upload_corrupt_image_rejected(test_app: FastAPI) -> None:
    client = TestClient(test_app)

    response = client.post(
        "/api/v1/uploads",
        data={"session_id": "sess_123"},
        files={"file": ("bad.png", b"not-a-valid-image-data", "image/png")},
    )

    assert response.status_code == 415


def test_get_upload_rejects_path_traversal(test_app: FastAPI) -> None:
    client = TestClient(test_app)

    response = client.get("/api/v1/uploads/sess_123/..%2f..%2fetc%2fpasswd")
    assert response.status_code == 404


def test_upload_rejects_unknown_session_without_creating_namespace(
    test_app: FastAPI,
) -> None:
    response = TestClient(test_app).post(
        "/api/v1/uploads",
        data={"session_id": "not-a-session"},
        files={"file": ("image.png", _make_test_image_bytes(), "image/png")},
    )

    assert response.status_code == 404
    assert not test_app.state.test_uploads_root.exists()


def test_get_upload_hides_session_from_another_user(
    test_app: FastAPI,
) -> None:
    owner_client = TestClient(test_app)
    upload = owner_client.post(
        "/api/v1/uploads",
        data={"session_id": "sess_123"},
        files={"file": ("image.png", _make_test_image_bytes(), "image/png")},
    ).json()
    intruder = AuthenticatedPrincipal(
        user_id="intruder",
        workspace_ids=frozenset({"test_ws"}),
        roles=frozenset({"student"}),
    )
    test_app.dependency_overrides[get_current_principal] = lambda: intruder

    response = TestClient(test_app).get(upload["url"])

    assert response.status_code == 404
