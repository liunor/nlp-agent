"""Classroom join-request workflow and resource binding tests."""

from __future__ import annotations

import httpx
import pytest

from ..support.database import MySqlProbe
from ..support.environment import SeededUser
from ..support.resources import create_classroom, create_workspace


pytestmark = pytest.mark.api_core


def test_student_join_request_approval_creates_classroom_membership(
    authenticated_client_for,
    mysql_probe: MySqlProbe,
    student_user: SeededUser,
    teacher_user: SeededUser,
) -> None:
    teacher_client = authenticated_client_for(teacher_user)
    student_client = authenticated_client_for(student_user)
    classroom = create_classroom(
        teacher_client,
        workspace_id=teacher_user.workspace_id,
        name="Phase 3 Approval Classroom",
    )

    submitted = student_client.post(
        f"/api/v1/classrooms/{classroom['id']}/join-requests",
        json={"student_number": "P3-001"},
    )
    assert submitted.status_code == 201, submitted.text
    request_id = submitted.json()["id"]
    assert submitted.json()["status"] == "pending"

    pending = teacher_client.get(
        f"/api/v1/classrooms/{classroom['id']}/join-requests"
    )
    assert pending.status_code == 200
    assert request_id in {item["id"] for item in pending.json()["items"]}

    approved = teacher_client.post(
        f"/api/v1/classrooms/{classroom['id']}/join-requests/{request_id}/approve",
        json={},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"

    membership = mysql_probe.scalar(
        """
        SELECT COUNT(*)
        FROM nlp_classroom_members
        WHERE classroom_id = :classroom_id
          AND user_id = :user_id
          AND member_role = 'student'
          AND status = 'active'
        """,
        classroom_id=classroom["id"],
        user_id=student_user.user_id,
    )
    assert membership == 1

    # Approval changes authorization_version, so the old student session is fenced.
    assert student_client.get("/api/v1/users/me").status_code == 401
    student_client = authenticated_client_for(student_user)
    requests = student_client.get("/api/v1/classrooms/my-join-requests")
    assert requests.status_code == 200
    matching = [
        item for item in requests.json()["items"] if item["id"] == request_id
    ]
    assert matching and matching[0]["status"] == "approved"


def test_duplicate_pending_request_and_rejection_are_consistent(
    authenticated_client_for,
    student_user: SeededUser,
    teacher_user: SeededUser,
) -> None:
    teacher_client = authenticated_client_for(teacher_user)
    student_client = authenticated_client_for(student_user)
    workspace = create_workspace(teacher_client, name="Phase 3 Rejection Workspace")
    classroom = create_classroom(
        teacher_client,
        workspace_id=workspace["id"],
        name="Phase 3 Rejection Classroom",
    )

    first = student_client.post(
        f"/api/v1/classrooms/{classroom['id']}/join-requests",
        json={},
    )
    assert first.status_code == 201, first.text
    request_id = first.json()["id"]

    duplicate = student_client.post(
        f"/api/v1/classrooms/{classroom['id']}/join-requests",
        json={},
    )
    assert duplicate.status_code == 409

    rejected = teacher_client.post(
        f"/api/v1/classrooms/{classroom['id']}/join-requests/{request_id}/reject"
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["status"] == "rejected"

    processed_again = teacher_client.post(
        f"/api/v1/classrooms/{classroom['id']}/join-requests/{request_id}/approve",
        json={},
    )
    assert processed_again.status_code == 404

    student_client = authenticated_client_for(student_user)
    own_requests = student_client.get("/api/v1/classrooms/my-join-requests")
    assert own_requests.status_code == 200
    matching = [
        item for item in own_requests.json()["items"] if item["id"] == request_id
    ]
    assert matching and matching[0]["status"] == "rejected"


def test_join_request_scope_rejects_nonmember_and_cross_classroom_approval(
    authenticated_client_for,
    student_user: SeededUser,
    teacher_user: SeededUser,
) -> None:
    teacher_client = authenticated_client_for(teacher_user)
    student_client = authenticated_client_for(student_user)
    workspace_a = create_workspace(teacher_client, name="Phase 3 Classroom A")
    workspace_b = create_workspace(teacher_client, name="Phase 3 Classroom B")
    classroom_a = create_classroom(
        teacher_client,
        workspace_id=workspace_a["id"],
        name="Phase 3 Classroom A",
    )
    classroom_b = create_classroom(
        teacher_client,
        workspace_id=workspace_b["id"],
        name="Phase 3 Classroom B",
    )

    submitted = student_client.post(
        f"/api/v1/classrooms/{classroom_a['id']}/join-requests",
        json={},
    )
    assert submitted.status_code == 201, submitted.text
    request_id = submitted.json()["id"]

    nonmember_list = student_client.get(
        f"/api/v1/classrooms/{classroom_a['id']}/join-requests"
    )
    assert nonmember_list.status_code == 403

    mismatch = teacher_client.post(
        f"/api/v1/classrooms/{classroom_b['id']}/join-requests/{request_id}/approve",
        json={},
    )
    assert mismatch.status_code == 404

    pending = teacher_client.get(
        f"/api/v1/classrooms/{classroom_a['id']}/join-requests"
    )
    assert pending.status_code == 200
    assert request_id in {item["id"] for item in pending.json()["items"]}


def test_nonexistent_classroom_has_not_leaked_as_a_success(
    authenticated_client_for,
    student_user: SeededUser,
) -> None:
    student_client = authenticated_client_for(student_user)
    response = student_client.post(
        "/api/v1/classrooms/00000000-0000-0000-0000-000000000000/join-requests",
        json={},
    )
    assert response.status_code == 404
