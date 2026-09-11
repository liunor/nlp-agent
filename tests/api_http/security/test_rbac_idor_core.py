"""Focused permission-boundary and IDOR checks for the core resources."""

from __future__ import annotations

import pytest

from ..support.environment import SeededUser
from ..support.resources import create_classroom, create_workspace


pytestmark = pytest.mark.api_core


def test_role_catalog_uses_the_four_canonical_roles(
    authenticated_client_for,
    developer_user: SeededUser,
) -> None:
    developer_client = authenticated_client_for(developer_user)
    response = developer_client.get("/api/v1/roles")

    assert response.status_code == 200, response.text
    role_codes = {item["code"] for item in response.json()["items"]}
    assert role_codes == {"guest", "student", "teacher", "developer"}
    assert "admin" not in role_codes


def test_system_rbac_operations_are_developer_only(
    authenticated_client_for,
    guest_user: SeededUser,
    student_user: SeededUser,
    teacher_user: SeededUser,
    developer_user: SeededUser,
) -> None:
    for user in (guest_user, student_user, teacher_user):
        response = authenticated_client_for(user).get("/api/v1/roles")
        assert response.status_code == 403, (user.role, response.text)

    developer_response = authenticated_client_for(developer_user).get(
        "/api/v1/roles"
    )
    assert developer_response.status_code == 200, developer_response.text


def test_user_idor_cannot_reach_another_user_management_resource(
    authenticated_client_for,
    student_user: SeededUser,
    developer_user: SeededUser,
) -> None:
    student_client = authenticated_client_for(student_user)
    target = developer_user.user_id

    assert student_client.get(f"/api/v1/users/{target}").status_code == 403
    assert student_client.patch(
        f"/api/v1/users/{target}",
        json={"display_name": "should-not-change"},
    ).status_code == 403
    assert student_client.post(f"/api/v1/users/{target}/disable").status_code == 403
    assert student_client.delete(f"/api/v1/users/{target}").status_code == 403


def test_classroom_member_mutation_is_not_available_outside_the_classroom_scope(
    authenticated_client_for,
    guest_user: SeededUser,
    student_user: SeededUser,
    teacher_user: SeededUser,
) -> None:
    teacher_client = authenticated_client_for(teacher_user)
    student_client = authenticated_client_for(student_user)
    workspace = create_workspace(teacher_client, name="Phase 3 Classroom Scope")
    classroom = create_classroom(
        teacher_client,
        workspace_id=workspace["id"],
        name="Phase 3 Classroom Scope",
    )

    list_response = student_client.get(f"/api/v1/classrooms/{classroom['id']}/join-requests")
    assert list_response.status_code == 403

    mutation = student_client.put(
        f"/api/v1/classrooms/{classroom['id']}/members/{guest_user.user_id}",
        json={"member_role": "student", "status": "active"},
    )
    assert mutation.status_code == 403


def test_session_creation_is_workspace_scoped(
    authenticated_client_for,
    student_user: SeededUser,
    teacher_user: SeededUser,
) -> None:
    teacher_client = authenticated_client_for(teacher_user)
    student_client = authenticated_client_for(student_user)
    workspace = create_workspace(teacher_client, name="Phase 3 Session Scope")

    response = student_client.post(
        "/api/v1/sessions",
        json={
            "workspace_id": workspace["id"],
        },
    )
    assert response.status_code == 403
