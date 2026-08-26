"""Interface-level coverage for the feedback HTTP contract.

Drives the real FastAPI routes (auth, CSRF, RBAC guards, validation, problem
mapping) end-to-end without MySQL: the four feedback service functions and the
authorization session factory are monkeypatched, following the established
pattern in ``test_web_api.py`` (see the release-notes route test).
"""

from contextlib import asynccontextmanager

import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient

from core.identity import AuthenticatedPrincipal
from core.rbac import Permission
from gateway.core import BackendGateway
from gateway.repository import GatewayRepository
from server.web.app import create_app
from server.web.auth import SameOriginSessionAuth
from test_web_api import FakeEngine, FakeSessions


SECRET = "test-secret-that-is-long-enough-for-hmac"


class FakeSession:
    def add(self, obj):
        return None

    @asynccontextmanager
    async def begin(self):
        yield


@asynccontextmanager
async def fake_session_factory():
    yield FakeSession()


def _fake_rbac_principal(monkeypatch, role: str) -> None:
    """Keep role resolution in memory: production reloads roles from MySQL on
    every request (app.resolve_principal -> rbac_service); these tests pin the
    feedback contract, not the RBAC tables."""

    import server.web.app as web_app_module

    async def fake_for_username(session, username):
        return AuthenticatedPrincipal(
            user_id=username,
            workspace_ids=frozenset({"default"}),
            roles=frozenset({role}),
        )

    async def fake_for_user_id(session, user_id):
        return AuthenticatedPrincipal(
            user_id=user_id,
            workspace_ids=frozenset({"default"}),
            roles=frozenset({role}),
        )

    monkeypatch.setattr(web_app_module.rbac_service, "principal_for_username", fake_for_username)
    monkeypatch.setattr(web_app_module.rbac_service, "principal_for_user_id", fake_for_user_id)


def _make_app(tmp_path, roles: str):
    engine = FakeEngine()
    sessions = FakeSessions()

    def gateway_factory():
        return BackendGateway(
            engine=engine,
            repository=GatewayRepository(tmp_path / "gateway.sqlite3"),
            sessions=sessions,
        )

    auth = SameOriginSessionAuth(
        secret=SECRET,
        allowed_origins=["http://testserver"],
        username="nova",
        password_hash=PasswordHasher().hash("test-password"),
        roles=frozenset({roles}),
    )
    return create_app(gateway_factory=gateway_factory, auth=auth, allowed_hosts=["testserver"])


@pytest.fixture(autouse=True)
def _fake_authorization_session_factory(monkeypatch):
    """The real gateway has no DB factory in these tests; every feedback route
    resolves its session through this gateway property (see the release-notes
    route test in test_web_api.py), so patch it once for the whole module."""
    monkeypatch.setattr(
        BackendGateway,
        "authorization_session_factory",
        property(lambda self: fake_session_factory),
    )


@pytest.fixture
def student_app(tmp_path, monkeypatch):
    _fake_rbac_principal(monkeypatch, "student")
    return _make_app(tmp_path, "student")


@pytest.fixture
def developer_app(tmp_path, monkeypatch):
    _fake_rbac_principal(monkeypatch, "developer")
    return _make_app(tmp_path, "admin")


def authenticate(client: TestClient) -> str:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "nova", "password": "test-password"},
        headers={"Origin": "http://testserver"},
    )
    assert response.status_code == 200
    return response.json()["csrf_token"]


def write_headers(csrf: str) -> dict[str, str]:
    return {"Origin": "http://testserver", "X-CSRF-Token": csrf}


def guest_login(client: TestClient) -> str:
    response = client.post("/api/v1/auth/guest", headers={"Origin": "http://testserver"})
    assert response.status_code == 200
    return response.json()["csrf_token"]


# --- POST /api/v1/feedback (student submission) -------------------------------


def test_submit_feedback_requires_authentication(student_app):
    with TestClient(student_app) as client:
        response = client.post(
            "/api/v1/feedback", json={"body": "hello"}, headers={"Origin": "http://testserver"}
        )

    assert response.status_code == 401


def test_submit_feedback_rejects_missing_csrf(student_app, monkeypatch):
    import server.web.app as web_app_module

    captured = {}

    async def fake_submit(session, principal, body):
        captured["called"] = True
        return {"thread_id": "t", "message": {}}

    monkeypatch.setattr(web_app_module, "submit_feedback", fake_submit)
    with TestClient(student_app) as client:
        authenticate(client)
        response = client.post("/api/v1/feedback", json={"body": "hello"})

    assert response.status_code == 403
    assert response.json()["code"] == "origin_rejected"
    assert "called" not in captured


def test_guest_cannot_submit_feedback(developer_app):
    with TestClient(developer_app) as client:
        csrf = guest_login(client)

        response = client.post(
            "/api/v1/feedback", json={"body": "hello"}, headers=write_headers(csrf)
        )

        assert response.status_code == 403
        assert response.json()["code"] == "forbidden"


def test_student_submission_is_forwarded_and_serialized(student_app, monkeypatch):
    import server.web.app as web_app_module

    captured = {}
    payload_message = {
        "id": "m-1",
        "sender_type": "student",
        "body": "请增加错题计划",
        "created_at": "2026-08-26T00:00:00+00:00",
    }

    async def fake_submit(session, principal, body):
        captured["principal_user_id"] = principal.user_id
        captured["body"] = body
        return {"thread_id": "thread-9", "message": payload_message}

    monkeypatch.setattr(web_app_module, "submit_feedback", fake_submit)
    with TestClient(student_app) as client:
        csrf = authenticate(client)
        response = client.post(
            "/api/v1/feedback",
            json={"body": "  请增加错题计划  "},
            headers=write_headers(csrf),
        )

    assert response.status_code == 201
    assert response.json() == {"thread_id": "thread-9", "message": payload_message}
    assert captured["principal_user_id"] == "nova"
    assert captured["body"] == "请增加错题计划"


def test_whitespace_only_body_is_rejected_before_the_service(student_app, monkeypatch):
    import server.web.app as web_app_module

    async def fail_submit(session, principal, body):
        raise AssertionError("service must not be called for invalid bodies")

    monkeypatch.setattr(web_app_module, "submit_feedback", fail_submit)
    with TestClient(student_app) as client:
        csrf = authenticate(client)
        response = client.post(
            "/api/v1/feedback",
            json={"body": " \t\n "},
            headers=write_headers(csrf),
        )

    assert response.status_code == 422


# --- GET /api/v1/developer/feedback (developer permission + paging) -----------


def test_feedback_endpoints_require_developer_permission(student_app):
    with TestClient(student_app) as client:
        csrf = authenticate(client)

        listing = client.get("/api/v1/developer/feedback")
        detail = client.get("/api/v1/developer/feedback/thread-1")
        marked = client.post(
            "/api/v1/developer/feedback/thread-1/read",
            json={"read_through_message_id": "m-1"},
            headers=write_headers(csrf),
        )

        assert listing.status_code == 403
        assert listing.json()["code"] == "forbidden"
        assert detail.status_code == 403
        assert marked.status_code == 403


def test_guest_cannot_read_feedback_list(developer_app):
    with TestClient(developer_app) as client:
        guest_login(client)

        assert client.get("/api/v1/developer/feedback").status_code == 403


def test_feedback_endpoints_require_system_scope(developer_app, monkeypatch):
    import server.web.app as web_app_module

    async def fake_for_username(session, username):
        return AuthenticatedPrincipal(
            user_id=username,
            roles=frozenset({"custom-feedback-reader"}),
            permissions=frozenset({Permission.LEARNING_FEEDBACK_READ.value}),
            permission_scopes={Permission.LEARNING_FEEDBACK_READ.value: frozenset({"own"})},
        )

    async def fake_for_user_id(session, user_id):
        return await fake_for_username(session, user_id)

    monkeypatch.setattr(web_app_module.rbac_service, "principal_for_username", fake_for_username)
    monkeypatch.setattr(web_app_module.rbac_service, "principal_for_user_id", fake_for_user_id)

    with TestClient(developer_app) as client:
        csrf = authenticate(client)
        listing = client.get("/api/v1/developer/feedback")
        detail = client.get("/api/v1/developer/feedback/thread-1")
        marked = client.post(
            "/api/v1/developer/feedback/thread-1/read",
            json={"read_through_message_id": "m-1"},
            headers=write_headers(csrf),
        )

    assert listing.status_code == 403
    assert detail.status_code == 403
    assert marked.status_code == 403


def test_list_feedback_defaults_are_forwarded(developer_app, monkeypatch):
    import server.web.app as web_app_module

    captured = {}

    async def fake_list(session, *, limit, offset, search):
        captured.update(limit=limit, offset=offset, search=search)
        return {"items": [], "total": 0}

    monkeypatch.setattr(web_app_module, "list_feedback_threads", fake_list)
    with TestClient(developer_app) as client:
        authenticate(client)
        response = client.get("/api/v1/developer/feedback")

    assert response.status_code == 200
    assert response.json() == {"items": [], "total": 0}
    assert captured == {"limit": 50, "offset": 0, "search": None}


def test_list_feedback_forwards_limit_offset_and_search(developer_app, monkeypatch):
    import server.web.app as web_app_module

    captured = {}

    async def fake_list(session, *, limit, offset, search):
        captured.update(limit=limit, offset=offset, search=search)
        return {"items": [{"thread_id": "t1"}], "total": 41}

    monkeypatch.setattr(web_app_module, "list_feedback_threads", fake_list)
    with TestClient(developer_app) as client:
        authenticate(client)
        response = client.get("/api/v1/developer/feedback?limit=20&offset=40&q=Alice")

    assert response.status_code == 200
    assert response.json()["total"] == 41
    assert captured == {"limit": 20, "offset": 40, "search": "Alice"}


@pytest.mark.parametrize(
    ("query", "bad_param"),
    [
        ("?limit=0", "limit"),
        ("?limit=201", "limit"),
        ("?offset=-1", "offset"),
        (f"?q={'x' * 65}", "q"),
    ],
)
def test_list_feedback_rejects_out_of_range_query_params(developer_app, query, bad_param):
    with TestClient(developer_app) as client:
        authenticate(client)
        response = client.get(f"/api/v1/developer/feedback{query}")

    assert response.status_code == 422
    assert bad_param in str(response.json())


# --- GET /api/v1/developer/feedback/{thread_id} -------------------------------


def test_get_feedback_detail_returns_the_thread_payload(developer_app, monkeypatch):
    import server.web.app as web_app_module

    thread_payload = {
        "thread_id": "thread-1",
        "user_id": "user-1",
        "username": "student",
        "display_name": "Student",
        "messages": [{"id": "m-1", "sender_type": "student", "body": "hi", "created_at": "2026-08-26T00:00:00+00:00"}],
    }
    seen = {}

    async def fake_detail(session, thread_id):
        seen["thread_id"] = thread_id
        return thread_payload

    monkeypatch.setattr(web_app_module, "get_feedback_thread", fake_detail)
    with TestClient(developer_app) as client:
        authenticate(client)
        response = client.get("/api/v1/developer/feedback/thread-1")

    assert response.status_code == 200
    assert response.json() == thread_payload
    assert seen["thread_id"] == "thread-1"


def test_unknown_thread_maps_lookup_error_to_404_problem(developer_app, monkeypatch):
    import server.web.app as web_app_module

    async def raise_detail_lookup(session, thread_id):
        raise LookupError(thread_id)

    async def raise_read_lookup(session, thread_id, read_through_message_id):
        raise LookupError(read_through_message_id)

    monkeypatch.setattr(web_app_module, "get_feedback_thread", raise_detail_lookup)
    monkeypatch.setattr(web_app_module, "mark_feedback_read", raise_read_lookup)
    with TestClient(developer_app) as client:
        csrf = authenticate(client)
        missing_detail = client.get("/api/v1/developer/feedback/thread-missing")
        missing_read = client.post(
            "/api/v1/developer/feedback/thread-missing/read",
            json={"read_through_message_id": "m-x"},
            headers=write_headers(csrf),
        )
    assert missing_detail.status_code == 404
    assert missing_detail.json()["code"] == "feedback_not_found"
    assert missing_read.status_code == 404
    assert missing_read.json()["code"] == "feedback_not_found"


# --- POST /api/v1/developer/feedback/{thread_id}/read -------------------------


def test_mark_feedback_read_forwards_ids_and_returns_ok(developer_app, monkeypatch):
    import server.web.app as web_app_module

    captured = {}

    async def fake_mark(session, thread_id, read_through_message_id):
        captured.update(thread_id=thread_id, message_id=read_through_message_id)

    monkeypatch.setattr(web_app_module, "mark_feedback_read", fake_mark)
    with TestClient(developer_app) as client:
        csrf = authenticate(client)
        rejected = client.post(
            "/api/v1/developer/feedback/thread-1/read",
            json={"read_through_message_id": "m-1"},
        )
        response = client.post(
            "/api/v1/developer/feedback/thread-1/read",
            json={"read_through_message_id": "m-1"},
            headers=write_headers(csrf),
        )

    assert rejected.status_code == 403
    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert captured == {"thread_id": "thread-1", "message_id": "m-1"}
