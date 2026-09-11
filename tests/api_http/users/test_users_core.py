"""Core user profile, lifecycle and system-scope authorization tests."""

from __future__ import annotations

from dataclasses import replace

import httpx
import pytest

from ..support.environment import SeededUser
from ..support.resources import create_user


pytestmark = pytest.mark.api_core


def test_user_can_read_and_update_own_profile(
    authenticated_client: httpx.Client,
    student_user: SeededUser,
) -> None:
    current = authenticated_client.get("/api/v1/users/me")
    assert current.status_code == 200
    assert current.json()["id"] == student_user.user_id
    assert "student" in current.json()["roles"]

    updated = authenticated_client.patch(
        "/api/v1/users/me",
        json={"display_name": "Phase 3 Student Profile"},
    )
    assert updated.status_code == 200
    assert updated.json()["display_name"] == "Phase 3 Student Profile"

    reread = authenticated_client.get("/api/v1/users/me")
    assert reread.status_code == 200
    assert reread.json()["display_name"] == "Phase 3 Student Profile"


def test_student_and_teacher_cannot_manage_users(
    authenticated_client_for,
    student_user: SeededUser,
    teacher_user: SeededUser,
    developer_user: SeededUser,
) -> None:
    for user in (student_user, teacher_user):
        client = authenticated_client_for(user)
        assert client.get("/api/v1/users").status_code == 403
        assert client.get(f"/api/v1/users/{developer_user.user_id}").status_code == 403
        response = client.post(
            "/api/v1/users",
            json={
                "username": "phase3forbiddenuser",
                "display_name": "Forbidden",
                "password": "Phase3-password-123!",
                "role_codes": ["guest"],
            },
        )
        assert response.status_code == 403


def test_developer_can_manage_user_lifecycle_and_revoke_sessions(
    authenticated_client_for,
    developer_user: SeededUser,
) -> None:
    admin_client = authenticated_client_for(developer_user)
    target = create_user(admin_client, role="student", prefix="phase3lifecycle")

    listed = admin_client.get(
        "/api/v1/users",
        params={"keyword": target.username, "limit": 10},
    )
    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    assert listed.json()["users"][0]["id"] == target.user_id

    retrieved = admin_client.get(f"/api/v1/users/{target.user_id}")
    assert retrieved.status_code == 200
    assert retrieved.json()["roles"] == ["student"]

    patched = admin_client.patch(
        f"/api/v1/users/{target.user_id}",
        json={"display_name": "Phase 3 Lifecycle Updated"},
    )
    assert patched.status_code == 200
    assert patched.json()["display_name"] == "Phase 3 Lifecycle Updated"

    target_client = authenticated_client_for(target)
    disabled = admin_client.post(f"/api/v1/users/{target.user_id}/disable")
    assert disabled.status_code == 204
    assert target_client.get("/api/v1/users/me").status_code == 401
    assert admin_client.get(f"/api/v1/users/{target.user_id}").json()["status"] == "disabled"

    enabled = admin_client.post(f"/api/v1/users/{target.user_id}/enable")
    assert enabled.status_code == 204
    enabled_client = authenticated_client_for(target)
    assert enabled_client.get("/api/v1/users/me").status_code == 200

    reset_password = "Phase3-reset-password-789!"
    reset = admin_client.post(
        f"/api/v1/users/{target.user_id}/password",
        json={"new_password": reset_password},
    )
    assert reset.status_code == 204
    assert enabled_client.get("/api/v1/users/me").status_code == 401

    reset_credentials = replace(target, password=reset_password)
    reset_client = authenticated_client_for(reset_credentials)
    assert reset_client.get("/api/v1/users/me").status_code == 200

    revoked = admin_client.post(f"/api/v1/users/{target.user_id}/sessions/revoke")
    assert revoked.status_code == 204
    assert reset_client.get("/api/v1/users/me").status_code == 401

    deleted = admin_client.delete(f"/api/v1/users/{target.user_id}")
    assert deleted.status_code == 204
    assert admin_client.get(f"/api/v1/users/{target.user_id}").status_code == 404

    deleted_list = admin_client.get(
        "/api/v1/users",
        params={"status": "deleted", "include_deleted": "true"},
    )
    assert deleted_list.status_code == 200
    assert target.user_id in {item["id"] for item in deleted_list.json()["users"]}

    restored = admin_client.post(f"/api/v1/users/{target.user_id}/restore")
    assert restored.status_code == 200
    assert restored.json()["status"] == "active"
    assert admin_client.get(f"/api/v1/users/{target.user_id}").status_code == 200
