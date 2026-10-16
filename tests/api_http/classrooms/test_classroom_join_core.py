"""Classroom join-request workflow and resource binding tests."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import httpx
import pytest

from ..support.database import MySqlProbe
from ..support.environment import SeededUser
from ..support.resources import create_classroom, create_user, create_workspace


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

    # Classroom approval is additive: the current student session remains valid.
    profile = student_client.get("/api/v1/users/me")
    assert profile.status_code == 200, profile.text
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


def test_rejected_request_can_be_submitted_and_rejected_again(
    authenticated_client_for,
    student_user: SeededUser,
    teacher_user: SeededUser,
) -> None:
    teacher_client = authenticated_client_for(teacher_user)
    student_client = authenticated_client_for(student_user)
    workspace = create_workspace(teacher_client, name="T06 Reapply Workspace")
    classroom = create_classroom(
        teacher_client,
        workspace_id=workspace["id"],
        name="T06 Reapply Classroom",
    )
    endpoint = f"/api/v1/classrooms/{classroom['id']}/join-requests"

    first = student_client.post(endpoint, json={})
    assert first.status_code == 201, first.text
    rejected = teacher_client.post(
        f"{endpoint}/{first.json()['id']}/reject"
    )
    assert rejected.status_code == 200, rejected.text

    second = student_client.post(endpoint, json={"student_number": "RETRY-002"})
    assert second.status_code == 201, second.text
    assert second.json()["status"] == "pending"

    rejected_again = teacher_client.post(
        f"{endpoint}/{second.json()['id']}/reject"
    )
    assert rejected_again.status_code == 200, rejected_again.text
    assert rejected_again.json()["status"] == "rejected"


def test_approved_member_cannot_submit_a_second_join_request(
    authenticated_client_for,
    student_user: SeededUser,
    teacher_user: SeededUser,
) -> None:
    teacher_client = authenticated_client_for(teacher_user)
    student_client = authenticated_client_for(student_user)
    workspace = create_workspace(teacher_client, name="T06 Member Reapply Workspace")
    classroom = create_classroom(
        teacher_client,
        workspace_id=workspace["id"],
        name="T06 Member Reapply Classroom",
    )
    endpoint = f"/api/v1/classrooms/{classroom['id']}/join-requests"

    first = student_client.post(endpoint, json={})
    assert first.status_code == 201, first.text
    approved = teacher_client.post(
        f"{endpoint}/{first.json()['id']}/approve",
        json={},
    )
    assert approved.status_code == 200, approved.text

    duplicate_after_approval = student_client.post(endpoint, json={})
    assert duplicate_after_approval.status_code == 409, duplicate_after_approval.text
    assert duplicate_after_approval.json()["code"] == "class_join_member_exists"


def test_guest_rejection_keeps_guest_role(
    authenticated_client_for,
    developer_user: SeededUser,
    teacher_user: SeededUser,
) -> None:
    developer_client = authenticated_client_for(developer_user)
    guest = create_user(developer_client, role="guest", prefix="t06guestreject")
    guest_client = authenticated_client_for(guest)
    teacher_client = authenticated_client_for(teacher_user)
    workspace = create_workspace(teacher_client, name="T06 Guest Rejection Workspace")
    classroom = create_classroom(
        teacher_client,
        workspace_id=workspace["id"],
        name="T06 Guest Rejection Classroom",
    )

    submitted = guest_client.post(
        "/api/v1/classrooms/join-requests",
        json={"join_code": classroom["join_code"]},
    )
    assert submitted.status_code == 201, submitted.text
    request_id = submitted.json()["id"]

    rejected = teacher_client.post(
        f"/api/v1/classrooms/{classroom['id']}/join-requests/{request_id}/reject"
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["status"] == "rejected"

    session = guest_client.get("/api/v1/auth/session")
    assert session.status_code == 200, session.text
    assert session.json()["roles"] == ["guest"]


def test_concurrent_pending_requests_have_one_winner(
    authenticated_client_for,
    developer_user: SeededUser,
    teacher_user: SeededUser,
) -> None:
    developer_client = authenticated_client_for(developer_user)
    guest = create_user(developer_client, role="guest", prefix="t06guestconcurrent")
    guest_a = authenticated_client_for(guest)
    guest_b = authenticated_client_for(guest)
    teacher_client = authenticated_client_for(teacher_user)
    workspace = create_workspace(teacher_client, name="T06 Concurrent Join Workspace")
    classroom = create_classroom(
        teacher_client,
        workspace_id=workspace["id"],
        name="T06 Concurrent Join Classroom",
    )
    endpoint = "/api/v1/classrooms/join-requests"
    payload = {"join_code": classroom["join_code"]}
    barrier = Barrier(2)

    def submit(client: httpx.Client) -> httpx.Response:
        barrier.wait(timeout=10)
        return client.post(endpoint, json=payload)

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(submit, (guest_a, guest_b)))

    assert sorted(response.status_code for response in responses) == [201, 409]
    conflict = next(response for response in responses if response.status_code == 409)
    assert conflict.json()["code"] == "class_join_request_pending"


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


def test_guest_can_join_by_case_insensitive_code_and_student_is_not_required(
    authenticated_client_for,
    student_user: SeededUser,
    guest_user: SeededUser,
    teacher_user: SeededUser,
) -> None:
    teacher_client = authenticated_client_for(teacher_user)
    student_client = authenticated_client_for(student_user)
    guest_client = authenticated_client_for(guest_user)
    workspace = create_workspace(teacher_client, name="T06 Join Code Workspace")
    classroom = create_classroom(
        teacher_client,
        workspace_id=workspace["id"],
        name="T06 Join Code Classroom",
    )

    join_code = teacher_client.get(
        f"/api/v1/classrooms/{classroom['id']}/join-code"
    )
    assert join_code.status_code == 200, join_code.text
    code_payload = join_code.json()
    assert code_payload["join_code"].isupper()
    assert len(code_payload["join_code"]) == 8

    submitted = student_client.post(
        "/api/v1/classrooms/join-requests",
        json={"join_code": code_payload["join_code"].lower(), "student_number": "T06-001"},
    )
    assert submitted.status_code == 201, submitted.text
    assert submitted.json()["status"] == "pending"
    assert submitted.json()["class_id"] == classroom["id"]

    duplicate = student_client.post(
        "/api/v1/classrooms/join-requests",
        json={"join_code": code_payload["join_code"]},
    )
    assert duplicate.status_code == 409, duplicate.text
    assert duplicate.json()["code"] == "class_join_request_pending"

    guest_submitted = guest_client.post(
        "/api/v1/classrooms/join-requests",
        json={"join_code": code_payload["join_code"]},
    )
    assert guest_submitted.status_code == 201, guest_submitted.text
    assert guest_submitted.json()["status"] == "pending"
    own_requests = guest_client.get("/api/v1/classrooms/my-join-requests")
    assert own_requests.status_code == 200, own_requests.text
    assert guest_submitted.json()["id"] in {item["id"] for item in own_requests.json()["items"]}



def test_approving_guest_promotes_role_and_preserves_session(
    authenticated_client_for,
    developer_user: SeededUser,
    teacher_user: SeededUser,
) -> None:
    developer_client = authenticated_client_for(developer_user)
    guest = create_user(developer_client, role="guest", prefix="t06guestpromote")
    guest_client = authenticated_client_for(guest)
    teacher_client = authenticated_client_for(teacher_user)
    workspace = create_workspace(teacher_client, name="T06 Guest Promotion Workspace")
    classroom = create_classroom(
        teacher_client,
        workspace_id=workspace["id"],
        name="T06 Guest Promotion Classroom",
    )
    code = teacher_client.get(f"/api/v1/classrooms/{classroom['id']}/join-code").json()["join_code"]

    submitted = guest_client.post("/api/v1/classrooms/join-requests", json={"join_code": code})
    assert submitted.status_code == 201, submitted.text
    approved = teacher_client.post(
        f"/api/v1/classrooms/{classroom['id']}/join-requests/{submitted.json()['id']}/approve",
        json={},
    )
    assert approved.status_code == 200, approved.text

    session = guest_client.get("/api/v1/auth/session")
    assert session.status_code == 200, session.text
    assert session.json()["roles"] == ["student"]
    assert "learning:progress:read_self" in session.json()["permissions"]


def test_guest_approval_rolls_back_when_role_catalog_is_temporarily_unavailable(
    authenticated_client_for,
    developer_user: SeededUser,
    teacher_user: SeededUser,
    mysql_probe: MySqlProbe,
) -> None:
    developer_client = authenticated_client_for(developer_user)
    guest = create_user(developer_client, role="guest", prefix="t06guestrollback")
    guest_client = authenticated_client_for(guest)
    teacher_client = authenticated_client_for(teacher_user)
    workspace = create_workspace(teacher_client, name="T06 Approval Rollback Workspace")
    classroom = create_classroom(
        teacher_client,
        workspace_id=workspace["id"],
        name="T06 Approval Rollback Classroom",
    )
    code = classroom["join_code"]
    submitted = guest_client.post("/api/v1/classrooms/join-requests", json={"join_code": code})
    assert submitted.status_code == 201, submitted.text
    request_id = submitted.json()["id"]

    mysql_probe.execute("UPDATE nlp_roles SET status='disabled' WHERE code='student'")
    try:
        failed = teacher_client.post(
            f"/api/v1/classrooms/{classroom['id']}/join-requests/{request_id}/approve",
            json={},
        )
        assert failed.status_code == 500, failed.text
        pending = guest_client.get("/api/v1/classrooms/my-join-requests")
        assert pending.status_code == 200, pending.text
        row = next(item for item in pending.json()["items"] if item["id"] == request_id)
        assert row["status"] == "pending"
        assert mysql_probe.scalar(
            "SELECT COUNT(*) FROM nlp_classroom_members "
            "WHERE classroom_id=:classroom_id AND user_id=:user_id",
            classroom_id=classroom["id"],
            user_id=guest.user_id,
        ) == 0
    finally:
        mysql_probe.execute("UPDATE nlp_roles SET status='active' WHERE code='student'")


def test_invalid_or_inactive_join_code_has_stable_error(
    authenticated_client_for,
    student_user: SeededUser,
    teacher_user: SeededUser,
    mysql_probe: MySqlProbe,
) -> None:
    client = authenticated_client_for(student_user)
    response = client.post(
        "/api/v1/classrooms/join-requests",
        json={"join_code": "ZZZZZZZZ"},
    )
    assert response.status_code == 404, response.text
    assert response.json()["code"] == "class_join_code_not_found"

    teacher_client = authenticated_client_for(teacher_user)
    workspace = create_workspace(teacher_client, name="T06 Disabled Join Code Workspace")
    classroom = create_classroom(
        teacher_client,
        workspace_id=workspace["id"],
        name="T06 Disabled Join Code Classroom",
    )
    mysql_probe.execute(
        "UPDATE nlp_classrooms SET status='disabled' WHERE id=:classroom_id",
        classroom_id=classroom["id"],
    )
    disabled = client.post(
        "/api/v1/classrooms/join-requests",
        json={"join_code": classroom["join_code"]},
    )
    assert disabled.status_code == 404, disabled.text
    assert disabled.json()["code"] == "class_join_code_not_found"
