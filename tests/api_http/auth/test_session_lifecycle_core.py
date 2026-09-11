"""Database-backed session lifecycle at the real HTTP boundary."""

from __future__ import annotations

from dataclasses import replace

import httpx
import pytest

from ..support.auth import session_id_for_client
from ..support.database import MySqlProbe
from ..support.http import json_response
from ..support.resources import create_user


pytestmark = pytest.mark.api_core


def test_user_can_list_and_revoke_another_device_session(
    authenticated_client_for,
    mysql_probe: MySqlProbe,
    student_user,
) -> None:
    first = authenticated_client_for(student_user)
    second = authenticated_client_for(student_user)
    first_session_id = session_id_for_client(first, mysql_probe)
    second_session_id = session_id_for_client(second, mysql_probe)
    assert first_session_id != second_session_id

    listed = json_response(first.get("/api/v1/auth/sessions"), 200)
    assert isinstance(listed, dict)
    rows = {str(item["session_id"]): item for item in listed["items"]}
    assert rows[first_session_id]["current"] is True
    assert rows[second_session_id]["active"] is True

    revoked = first.delete(f"/api/v1/auth/sessions/{second_session_id}")
    assert revoked.status_code == 204, revoked.text
    assert second.get("/api/v1/users/me").status_code == 401
    assert first.get("/api/v1/users/me").status_code == 200


def test_revoke_current_session_invalidates_cookie(
    authenticated_client_for,
    mysql_probe: MySqlProbe,
    student_user,
) -> None:
    client = authenticated_client_for(student_user)
    session_id = session_id_for_client(client, mysql_probe)

    response = client.delete(f"/api/v1/auth/sessions/{session_id}")
    assert response.status_code == 204
    assert "nlp_session" not in client.cookies
    assert client.get("/api/v1/users/me").status_code == 401


def test_expired_session_is_rejected_without_waiting(
    authenticated_client_for,
    mysql_probe: MySqlProbe,
    student_user,
) -> None:
    client = authenticated_client_for(student_user)
    session_id = session_id_for_client(client, mysql_probe)
    mysql_probe.execute(
        "UPDATE nlp_sessions SET expires_at=UTC_TIMESTAMP(6)-INTERVAL 1 SECOND "
        "WHERE id=:session_id",
        session_id=session_id,
    )

    response = client.get("/api/v1/users/me")
    assert response.status_code == 401


def test_idle_session_is_rejected_without_waiting(
    authenticated_client_for,
    mysql_probe: MySqlProbe,
    student_user,
) -> None:
    client = authenticated_client_for(student_user)
    session_id = session_id_for_client(client, mysql_probe)
    mysql_probe.execute(
        "UPDATE nlp_sessions SET last_seen_at=UTC_TIMESTAMP(6)-INTERVAL 1 DAY "
        "WHERE id=:session_id",
        session_id=session_id,
    )

    response = client.get("/api/v1/users/me")
    assert response.status_code == 401


def test_authorization_version_change_fences_existing_session(
    authenticated_client_for,
    developer_user,
) -> None:
    developer_client = authenticated_client_for(developer_user)
    target = create_user(developer_client, role="student", prefix="phase4authversion")
    target_client = authenticated_client_for(target)

    changed = developer_client.put(
        f"/api/v1/users/{target.user_id}/roles",
        json={"role_codes": ["teacher"]},
    )
    assert changed.status_code == 200, changed.text
    assert target_client.get("/api/v1/users/me").status_code == 401

    # The role change is authoritative, so a new session sees the new role.
    new_client = authenticated_client_for(replace(target, role="teacher"))
    payload = json_response(new_client.get("/api/v1/auth/session"), 200)
    assert isinstance(payload, dict)
    assert "teacher" in payload["roles"]
