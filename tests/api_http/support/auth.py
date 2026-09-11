"""Production-style browser authentication helpers for HTTP tests."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from typing import Any

import httpx

from .database import MySqlProbe


@dataclass(frozen=True, slots=True)
class LoginResult:
    """Observable result of the real login/session bootstrap."""

    username: str
    user_id: str
    csrf_token: str
    cookie_name: str
    workspace_ids: tuple[str, ...] = ()


def same_origin_headers(origin: str, *, csrf_token: str | None = None) -> dict[str, str]:
    headers = {"Origin": origin}
    if csrf_token:
        headers["X-CSRF-Token"] = csrf_token
    return headers


def login(
    client: httpx.Client,
    *,
    origin: str,
    username: str,
    password: str,
    cookie_name: str = "nlp_session",
) -> LoginResult:
    """Log in through the public HTTP boundary and require Cookie persistence."""

    response = client.post(
        "/api/v1/auth/login",
        headers=same_origin_headers(origin),
        json={"username": username, "password": password},
    )
    response.raise_for_status()
    payload: dict[str, Any] = response.json()
    if cookie_name not in client.cookies:
        raise AssertionError(f"{cookie_name!r} was not persisted by httpx")
    csrf_token = payload.get("csrf_token")
    if not isinstance(csrf_token, str) or not csrf_token:
        raise AssertionError("login response did not contain a CSRF token")
    return LoginResult(
        username=username,
        user_id=str(payload["user_id"]),
        csrf_token=csrf_token,
        cookie_name=cookie_name,
        workspace_ids=tuple(str(item) for item in payload.get("workspace_ids", [])),
    )


def refresh_session(
    client: httpx.Client,
    *,
    origin: str,
    login_result: LoginResult,
) -> LoginResult:
    """Reload the DB-backed session and use its freshly rotated CSRF token."""

    response = client.get(
        "/api/v1/auth/session",
        headers=same_origin_headers(origin),
    )
    response.raise_for_status()
    payload: dict[str, Any] = response.json()
    csrf_token = payload.get("csrf_token")
    if not isinstance(csrf_token, str) or not csrf_token:
        raise AssertionError("session response did not contain a CSRF token")
    if str(payload.get("user_id")) != login_result.user_id:
        raise AssertionError("session identity differs from the login identity")
    return LoginResult(
        username=login_result.username,
        user_id=login_result.user_id,
        csrf_token=csrf_token,
        cookie_name=login_result.cookie_name,
        workspace_ids=login_result.workspace_ids,
    )


def set_test_auth_code(
    mysql_probe: MySqlProbe,
    *,
    kind: str,
    subject: str,
    code: str,
    expired: bool = False,
) -> None:
    """Make a DB-backed CAPTCHA/SMS code deterministic for a black-box test.

    The HTTP route still generates, stores, consumes and deletes the real
    MySQL row.  Only the opaque hash is replaced in the isolated test DB so a
    test does not need OCR or a real SMS delivery channel.
    """
    code_hash = hashlib.sha256(code.strip().casefold().encode("utf-8")).hexdigest()
    expires_expression = (
        "UTC_TIMESTAMP(6) - INTERVAL 1 SECOND"
        if expired
        else "UTC_TIMESTAMP(6) + INTERVAL 120 SECOND"
    )
    mysql_probe.execute(
        "DELETE FROM nlp_auth_codes WHERE kind=:kind AND subject=:subject",
        kind=kind,
        subject=subject,
    )
    mysql_probe.execute(
        "INSERT INTO nlp_auth_codes"
        "(id,kind,subject,code_hash,expires_at,created_at)"
        f" VALUES(:id,:kind,:subject,:code_hash,{expires_expression},UTC_TIMESTAMP(6))",
        id=str(uuid.uuid4()),
        code_hash=code_hash,
        kind=kind,
        subject=subject,
    )


def session_id_for_client(client: httpx.Client, mysql_probe: MySqlProbe) -> str:
    token = client.cookies.get("nlp_session")
    if not token:
        raise AssertionError("client has no nlp_session cookie")
    token_hash = hashlib.sha256(token.encode("ascii")).hexdigest()
    session_id = mysql_probe.scalar(
        "SELECT id FROM nlp_sessions WHERE token_hash=:token_hash",
        token_hash=token_hash,
    )
    if not session_id:
        raise AssertionError("session cookie has no MySQL session row")
    return str(session_id)
