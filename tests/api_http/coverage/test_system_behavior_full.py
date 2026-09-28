"""Real HTTP behavior contracts for system, settings, and shared resources."""

from __future__ import annotations

import uuid

import pytest

from ..support.environment import SeededUser
from ..support.database import MySqlProbe
from ..support.http import json_response
from ..support.resources import create_classroom, create_workspace


pytestmark = pytest.mark.api_full


def test_system_catalogs_and_role_projection_round_trip(
    authenticated_client_for,
    developer_user: SeededUser,
    guest_user: SeededUser,
) -> None:
    client = authenticated_client_for(developer_user)

    roles = json_response(client.get("/api/v1/roles"), 200)
    role_codes = {item["code"] for item in roles["items"]}
    assert role_codes == {"guest", "student", "teacher", "developer"}

    permissions = json_response(client.get("/api/v1/permissions"), 200)
    permission_codes = {item["code"] for item in permissions["items"]}
    assert "system:user:manage" in permission_codes
    assert "system:role:manage" in permission_codes

    role_permissions = json_response(
        client.get("/api/v1/system/roles/student/permissions"),
        200,
    )
    assert role_permissions["role_code"] == "student"
    assert role_permissions["permissions"]
    permission_scope_body = {
        "permission_codes": list(role_permissions["permissions"]),
        "scopes": role_permissions["permissions"],
    }
    replaced_permissions = json_response(
        client.put(
            "/api/v1/system/roles/student/permissions",
            json=permission_scope_body,
        ),
        200,
    )
    assert set(replaced_permissions["permission_codes"]) == set(
        permission_scope_body["permission_codes"]
    )

    menus = json_response(client.get("/api/v1/system/menus"), 200)
    visible_menus = json_response(client.get("/api/v1/system/menus/visible"), 200)
    assert isinstance(menus["items"], list)
    assert isinstance(visible_menus["items"], list)

    student_menus = json_response(
        client.get("/api/v1/system/roles/student/menus"),
        200,
    )
    assert student_menus["role_code"] == "student"
    # Built-in roles are authoritative catalog entries.  The write endpoint
    # accepts them for permission projection but deliberately rejects menu
    # mutation; verify that invariant instead of mutating production metadata.
    menu_write = client.put(
        "/api/v1/system/roles/student/menus",
        json={"menu_ids": student_menus["menu_ids"]},
    )
    menu_error = json_response(menu_write, 404)
    assert menu_error["detail"] == "RBAC resource not found"

    user_roles = json_response(
        client.get(f"/api/v1/users/{guest_user.user_id}/roles"),
        200,
    )
    assert user_roles["role_codes"] == ["guest"]
    role_update = json_response(
        client.put(
            f"/api/v1/users/{guest_user.user_id}/roles",
            json={"role_codes": ["guest"]},
        ),
        200,
    )
    assert role_update["role_codes"] == ["guest"]


def test_classroom_listing_and_membership_are_persisted_and_scoped(
    authenticated_client_for,
    mysql_probe: MySqlProbe,
    teacher_user: SeededUser,
    student_user: SeededUser,
    developer_user: SeededUser,
) -> None:
    teacher = authenticated_client_for(teacher_user)
    developer = authenticated_client_for(developer_user)
    workspace = create_workspace(teacher, name="Full system classroom workspace")
    classroom = create_classroom(
        teacher,
        workspace_id=workspace["id"],
        name="Full system classroom",
    )

    listed = json_response(teacher.get("/api/v1/classrooms"), 200)
    assert any(item["id"] == classroom["id"] for item in listed["items"])
    added = json_response(
        teacher.put(
            f"/api/v1/classrooms/{classroom['id']}/members/{student_user.user_id}",
            json={"member_role": "student", "status": "active"},
        ),
        200,
    )
    assert added == {
        "classroom_id": classroom["id"],
        "user_id": student_user.user_id,
        "member_role": "student",
        "status": "active",
    }
    assert mysql_probe.scalar(
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
    ) == 1

    # The classroom listing is the teacher progress-management surface.  A
    # student membership is persisted, but the student role does not grant
    # LEARNING_PROGRESS_READ_CLASSROOM for this route.
    student = authenticated_client_for(student_user)
    assert student.get("/api/v1/classrooms").status_code == 403

    other_workspace = create_workspace(
        developer,
        name="Full system other workspace",
    )
    assert student.get(
        f"/api/v1/workspaces/{other_workspace['id']}"
    ).status_code in {403, 404}
    assert student.put(
        f"/api/v1/classrooms/{classroom['id']}/members/{developer_user.user_id}",
        json={"member_role": "teacher", "status": "active"},
    ).status_code == 403


def test_settings_protocol_usage_quota_and_teacher_annotations_have_contracts(
    authenticated_client_for,
    teacher_user: SeededUser,
) -> None:
    client = authenticated_client_for(teacher_user)

    settings = json_response(client.get("/api/v1/settings"), 200)
    assert set(("preferences", "runtime")) <= settings.keys()
    updated = json_response(
        client.patch(
            "/api/v1/settings",
            json={"locale": "zh-CN", "theme": "dark", "reduce_motion": True},
        ),
        200,
    )
    assert updated["settings"]["locale"] == "zh-CN"
    assert updated["settings"]["theme"] == "dark"
    reread = json_response(client.get("/api/v1/settings"), 200)
    assert reread["preferences"]["settings"]["reduce_motion"] is True
    assert reread["preferences"]["revision"] >= settings["preferences"]["revision"] + 1

    protocol = json_response(client.get("/api/v1/protocol"), 200)
    assert protocol["version"] == "1"
    assert protocol["websocket_path"] == "/ws/v1"
    assert "chat.send" in protocol["commands"]
    assert "command.error" in protocol["events"]

    usage = json_response(client.get("/api/v1/usage/me?days=1&granularity=day"), 200)
    assert isinstance(usage, dict)
    quota = json_response(client.get("/api/v1/quota/me"), 200)
    assert isinstance(quota, dict)

    workspace = create_workspace(client, name="Full annotations workspace")
    annotations = json_response(
        client.get(f"/api/v1/teacher/analysis-annotations/{workspace['id']}"),
        200,
    )
    assert annotations["annotations"]["workspace_id"] == workspace["id"]
    saved = json_response(
        client.put(
            f"/api/v1/teacher/analysis-annotations/{workspace['id']}",
            json={
                "focused": ["knowledge-point-full"],
                "ignored": [],
                "notes": {"knowledge-point-full": "Follow up next lesson"},
            },
        ),
        200,
    )
    assert saved["annotations"]["focused"] == ["knowledge-point-full"]
    assert saved["annotations"]["notes"]["knowledge-point-full"] == "Follow up next lesson"
    invalid = client.put(
        f"/api/v1/teacher/analysis-annotations/{workspace['id']}",
        json={"focused": [], "ignored": [], "notes": {"x": "a" * 2001}},
    )
    assert invalid.status_code == 422


def test_whiteboard_library_is_shared_read_only_to_students_and_validated(
    authenticated_client_for,
    developer_user: SeededUser,
    teacher_user: SeededUser,
    student_user: SeededUser,
) -> None:
    developer = authenticated_client_for(developer_user)
    teacher = authenticated_client_for(teacher_user)
    student = authenticated_client_for(student_user)
    name = f"full-whiteboard-{uuid.uuid4().hex[:10]}"
    created = json_response(
        teacher.post(
            "/api/v1/whiteboard/library",
            json={
                "name": name,
                "elements": [{"id": "text-1", "type": "text", "text": "Full HTTP"}],
            },
        ),
        201,
    )
    assert created["item"]["name"] == name
    student_items = json_response(student.get("/api/v1/whiteboard/library"), 200)
    assert any(item["id"] == created["item"]["id"] for item in student_items["items"])

    invalid = teacher.post(
        "/api/v1/whiteboard/library",
        json={"name": name, "elements": []},
    )
    assert invalid.status_code == 422
    assert student.post(
        "/api/v1/whiteboard/library",
        json={
            "name": "student-denied",
            "elements": [{"id": "text-2", "type": "text"}],
        },
    ).status_code == 403
    assert developer.get("/api/v1/whiteboard/library").status_code == 200
