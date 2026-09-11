"""Workspace lifecycle and isolation at the real HTTP boundary."""

from __future__ import annotations

import httpx
import pytest

from ..support.environment import SeededUser
from ..support.resources import create_workspace


pytestmark = pytest.mark.api_core


def test_workspace_create_get_list_membership_and_remove(
    authenticated_client_for,
    student_user: SeededUser,
    teacher_user: SeededUser,
) -> None:
    teacher_client = authenticated_client_for(teacher_user)
    student_client = authenticated_client_for(student_user)
    workspace = create_workspace(teacher_client, name="Phase 3 Workspace Lifecycle")

    fetched = teacher_client.get(f"/api/v1/workspaces/{workspace['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["id"] == workspace["id"]

    added = teacher_client.post(
        f"/api/v1/workspaces/{workspace['id']}/members",
        json={"user_id": student_user.user_id, "member_type": "member"},
    )
    assert added.status_code == 201, added.text
    assert added.json()["user_id"] == student_user.user_id

    # Membership changes invalidate the target's previous database session.
    assert student_client.get("/api/v1/users/me").status_code == 401
    student_client = authenticated_client_for(student_user)
    listed = student_client.get("/api/v1/workspaces")
    assert listed.status_code == 200
    assert workspace["id"] in {item["id"] for item in listed.json()["workspaces"]}

    members = student_client.get(f"/api/v1/workspaces/{workspace['id']}/members")
    assert members.status_code == 200
    assert {item["user_id"] for item in members.json()} >= {
        teacher_user.user_id,
        student_user.user_id,
    }

    removed = teacher_client.delete(
        f"/api/v1/workspaces/{workspace['id']}/members/{student_user.user_id}"
    )
    assert removed.status_code == 204
    student_client = authenticated_client_for(student_user)
    assert student_client.get(f"/api/v1/workspaces/{workspace['id']}").status_code == 403
    assert (
        student_client.get(f"/api/v1/workspaces/{workspace['id']}/members").status_code
        == 403
    )


def test_workspace_b_is_not_visible_or_mutable_to_user_a(
    authenticated_client_for,
    student_user: SeededUser,
    teacher_user: SeededUser,
    developer_user: SeededUser,
    guest_user: SeededUser,
) -> None:
    teacher_client = authenticated_client_for(teacher_user)
    developer_client = authenticated_client_for(developer_user)
    workspace_a = create_workspace(teacher_client, name="Phase 3 Workspace A")
    workspace_b = create_workspace(developer_client, name="Phase 3 Workspace B")

    added = teacher_client.post(
        f"/api/v1/workspaces/{workspace_a['id']}/members",
        json={"user_id": student_user.user_id, "member_type": "member"},
    )
    assert added.status_code == 201, added.text
    student_client = authenticated_client_for(student_user)

    visible = student_client.get("/api/v1/workspaces")
    assert visible.status_code == 200
    visible_ids = {item["id"] for item in visible.json()["workspaces"]}
    assert workspace_a["id"] in visible_ids
    assert workspace_b["id"] not in visible_ids

    assert student_client.get(f"/api/v1/workspaces/{workspace_b['id']}").status_code == 403
    assert (
        student_client.get(f"/api/v1/workspaces/{workspace_b['id']}/members").status_code
        == 403
    )

    add_foreign = student_client.post(
        f"/api/v1/workspaces/{workspace_b['id']}/members",
        json={"user_id": guest_user.user_id, "member_type": "member"},
    )
    assert add_foreign.status_code == 403

    remove_foreign = student_client.delete(
        f"/api/v1/workspaces/{workspace_b['id']}/members/{developer_user.user_id}"
    )
    assert remove_foreign.status_code == 403

    create_foreign_classroom = student_client.post(
        "/api/v1/classrooms",
        json={"workspace_id": workspace_b["id"], "name": "Foreign Classroom"},
    )
    assert create_foreign_classroom.status_code == 403
