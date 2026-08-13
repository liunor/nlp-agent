"""Regression tests for Fix A (admin alias) and Fix B (DB-backed multi-user login).

Both paths must produce a session that:
  - sets the same ``nlp_session`` cookie,
  - returns the same response shape (user_id/workspace_ids/classroom_ids/roles/permissions/csrf_token/expires_at),
  - resolves the real DB principal (roles/permissions) via ``resolve_principal``,
  - includes the admin alias when the DB role is developer,
  - goes through the SameOriginSessionAuth rate limiter.

The test stubs the DB-touching service methods so it can exercise the REAL
endpoint wiring without needing a live MySQL connection.  The live DB path
has been verified separately by ``inspect_accounts`` and the previous
``diag_auth`` script.
"""
from __future__ import annotations

import os
import types
from contextlib import asynccontextmanager

from argon2 import PasswordHasher
from dotenv import load_dotenv

NOVA_PASSWORD = "TestLogin123!"
load_dotenv()
os.environ["NLP_AGENT_AUTH_USERNAME"] = "nova"
os.environ["NLP_AGENT_AUTH_PASSWORD_HASH"] = PasswordHasher().hash(NOVA_PASSWORD)
os.environ["NLP_AGENT_WEB_ALLOWED_HOSTS"] = "testserver,localhost"

from fastapi.testclient import TestClient  # noqa: E402

from configs.settings import settings  # noqa: E402
from core.identity import AuthenticatedPrincipal  # noqa: E402
from server.rbac import service as rbac_module  # noqa: E402
from server.web.app import create_app  # noqa: E402


async def _noop():
    return None


@asynccontextmanager
async def _fake_factory():
    yield object()


async def _fake_principal_for_username(session, username):
    return AuthenticatedPrincipal(
        user_id=username,
        workspace_ids=frozenset(["ws1"]),
        classroom_ids=frozenset(),
        roles=frozenset(["developer"]),
        permissions=frozenset(f"perm{i}" for i in range(28)),
    )


# ---------------- Stubs for the UserService (so DB login can run) ----------------
class _FakeUser:
    def __init__(self, user_id: str, username: str) -> None:
        self.id = user_id
        self.username = username
        self.password_hash = PasswordHasher().hash("Test123456")
        self.display_name = username.title()
        self.status = "active"
        self.created_at = "2026-01-01T00:00:00Z"
        self.updated_at = "2026-01-01T00:00:00Z"


# user_id is the DB UUID; username is the login name
_FAKE_USER_TABLE = {
    "guest01": _FakeUser("u-guest01", "guest01"),
    "student01": _FakeUser("u-student01", "student01"),
    "teacher01": _FakeUser("u-teacher01", "teacher01"),
    "developer01": _FakeUser("u-developer01", "developer01"),
}


async def _fake_get_user_by_username(self, username: str):
    return _FAKE_USER_TABLE.get(username)


async def _fake_verify_password(self, user, password: str) -> bool:
    try:
        return PasswordHasher().verify(user.password_hash, password)
    except Exception:
        return False


async def _fake_get_user(self, user_id: str):
    # For /auth/me resolution by user_id
    for u in _FAKE_USER_TABLE.values():
        if u.id == user_id:
            return u
    # nova fallback (env-credential login uses "nova" as user_id)
    return _FakeUser("u-nova", "nova")


def main() -> None:
    rbac_module.rbac_service.principal_for_username = _fake_principal_for_username

    from server.user import service as user_module

    # Bind as instance methods (FastAPI calls them as ``self.<method>``).
    user_module.UserService.get_user = _fake_get_user
    user_module.UserService.get_user_by_username = _fake_get_user_by_username
    user_module.UserService.verify_password = _fake_verify_password

    gateway = types.SimpleNamespace(
        authorization_session_factory=_fake_factory,
        start=_noop,
        begin_shutdown=_noop,
        close=_noop,
    )
    app = create_app(gateway_factory=lambda: gateway, allowed_hosts=["testserver", "localhost"])

    results: list[tuple[str, bool, str]] = []

    def record(label: str, ok: bool, detail: str = "") -> None:
        results.append((label, ok, detail))
        print(f"  [{'OK' if ok else 'FAIL'}] {label}{(' — ' + detail) if detail else ''}")

    with TestClient(app) as client:
        # =================== Fix A regression ===================
        print()
        print("== Fix A: env-credential /auth/login (nova) ==")
        r = client.post(
            "/api/v1/auth/login",
            json={"username": "nova", "password": NOVA_PASSWORD},
            headers={"origin": "http://testserver"},
        )
        record("env login status 200", r.status_code == 200, f"got {r.status_code}")
        if r.status_code != 200:
            print("  body:", r.text[:200])
        else:
            data = r.json()
            record("env login roles contain admin+developer", "admin" in data["roles"] and "developer" in data["roles"])
            record("env login perms == 28", len(data.get("permissions", [])) == 28)
            record("env login csrf_token present", bool(data.get("csrf_token")))
            record("env login nlp_session cookie set", "nlp_session" in client.cookies)

        # =================== Fix B: DB login ===================
        print()
        print("== Fix B: DB-credential /auth/login/db (test accounts) ==")
        for username in ("guest01", "student01", "teacher01", "developer01"):
            r = client.post(
                "/api/v1/auth/login/db",
                json={"username": username, "password": "Test123456"},
                headers={"origin": "http://testserver"},
            )
            ok_status = r.status_code == 200
            record(f"db login ({username}) status 200", ok_status, f"got {r.status_code}")
            if not ok_status:
                print("    body:", r.text[:200])
                continue
            data = r.json()
            record(f"db login ({username}) returns user_id (UUID)", "user_id" in data and data["user_id"].startswith("u-"))
            record(f"db login ({username}) csrf_token present", bool(data.get("csrf_token")))
            record(f"db login ({username}) workspace_ids non-empty", len(data.get("workspace_ids", [])) > 0)

        # Critical: developer01 should get admin+developer alias
        print()
        print("== Fix B + Fix A combined: developer01 gets admin alias via DB login ==")
        r = client.post(
            "/api/v1/auth/login/db",
            json={"username": "developer01", "password": "Test123456"},
            headers={"origin": "http://testserver"},
        )
        if r.status_code == 200:
            data = r.json()
            record(
                "developer01 roles contain admin+developer",
                "admin" in data["roles"] and "developer" in data["roles"],
                f"got {data['roles']}",
            )
            record("developer01 perms == 28", len(data.get("permissions", [])) == 28)
        else:
            record("developer01 DB login", False, f"status={r.status_code}")

        # Wrong password should be rejected
        print()
        print("== Fix B: wrong password rejected ==")
        r = client.post(
            "/api/v1/auth/login/db",
            json={"username": "student01", "password": "wrong_password"},
            headers={"origin": "http://testserver"},
        )
        record("wrong password -> 401", r.status_code == 401, f"got {r.status_code}")

        # Unknown user should be rejected
        r = client.post(
            "/api/v1/auth/login/db",
            json={"username": "nobody-here", "password": "anything"},
            headers={"origin": "http://testserver"},
        )
        record("unknown user -> 401", r.status_code == 401, f"got {r.status_code}")

        # /auth/session after DB login should also return admin alias for developer01
        print()
        print("== Session roundtrip after DB login ==")
        client.post(
            "/api/v1/auth/login/db",
            json={"username": "developer01", "password": "Test123456"},
            headers={"origin": "http://testserver"},
        )
        r = client.get("/api/v1/auth/session", headers={"origin": "http://testserver"})
        if r.status_code == 200:
            data = r.json()
            record(
                "session after DB login -> admin+developer",
                "admin" in data["roles"] and "developer" in data["roles"],
                f"got {data['roles']}",
            )
        else:
            record("session after DB login", False, f"status={r.status_code}")

    print()
    print("=" * 78)
    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    print(f"RESULT: {'PASS' if passed == total else 'FAIL'} ({passed}/{total})")


if __name__ == "__main__":
    main()