"""One deterministic real-HTTP baseline for every Phase 7 operation gap."""

from __future__ import annotations

import base64
import hashlib
import uuid

import httpx
import pytest

from ..support.auth import same_origin_headers
from ..support.chat import submit_turn
from ..support.environment import SeededUser
from ..support.http import json_response, problem_response
from ..support.resources import create_classroom, create_session, create_workspace, seed_catalog


pytestmark = pytest.mark.api_full


@pytest.fixture
def temporary_role_code(mysql_probe) -> str:
    role_code = f"phase7_{uuid.uuid4().hex[:12]}"
    yield role_code
    mysql_probe.execute(
        "DELETE FROM nlp_role_permission_scopes "
        "WHERE role_id = (SELECT id FROM nlp_roles WHERE code=:role_code)",
        role_code=role_code,
    )
    mysql_probe.execute(
        "DELETE FROM nlp_role_permissions "
        "WHERE role_id = (SELECT id FROM nlp_roles WHERE code=:role_code)",
        role_code=role_code,
    )
    mysql_probe.execute(
        "DELETE FROM nlp_role_menus "
        "WHERE role_id = (SELECT id FROM nlp_roles WHERE code=:role_code)",
        role_code=role_code,
    )
    mysql_probe.execute(
        "DELETE FROM nlp_roles WHERE code=:role_code",
        role_code=role_code,
    )


def test_health_auth_guest_and_protocol_baselines(
    http_client: httpx.Client,
    authenticated_client: httpx.Client,
    monitor_http_client: httpx.Client,
    monitor_base_url: str,
    api_http_environment,
) -> None:
    monitor_live = json_response(monitor_http_client.get("/health/live"), 200)
    assert monitor_live["status"] == "ok"
    assert monitor_live["plane"] == "observability"

    monitor_session = monitor_http_client.post(
        "/api/v1/auth/session",
        headers=same_origin_headers(monitor_base_url),
    )
    problem = problem_response(monitor_session, 401)
    assert problem["code"] == "authentication_required"

    guest = http_client.post(
        "/api/v1/auth/guest",
        headers=same_origin_headers(api_http_environment.web_origin),
    )
    problem = problem_response(guest, 401)
    assert problem["code"] == "authentication_required"

    protocol = json_response(authenticated_client.get("/api/v1/protocol"), 200)
    assert protocol["version"] == "1"
    assert protocol["websocket_path"] == "/ws/v1"
    assert "chat.send" in protocol["commands"]
    assert "chat.completed" in protocol["events"]
    assert "limits" in protocol


def test_session_listing_stats_delete_and_settings_are_persisted(
    authenticated_client: httpx.Client,
    student_user: SeededUser,
) -> None:
    session = create_session(
        authenticated_client,
        workspace_id=student_user.workspace_id,
    )
    session_id = str(session["session_id"])

    listed = json_response(authenticated_client.get("/api/v1/sessions?limit=10"), 200)
    assert {"items", "total", "offset", "limit", "has_more"} <= listed.keys()
    assert any(item["session_id"] == session_id for item in listed["items"])

    stats = json_response(authenticated_client.get("/api/v1/sessions/stats"), 200)
    assert {"sessions_total", "sessions_active", "turns_total"} <= stats.keys()
    assert stats["sessions_total"] >= 1

    settings = json_response(authenticated_client.get("/api/v1/settings"), 200)
    assert {"preferences", "runtime"} <= settings.keys()
    updated = json_response(
        authenticated_client.patch(
            "/api/v1/settings",
            json={"theme": "dark", "locale": "zh-CN"},
        ),
        200,
    )
    assert updated["settings"]["theme"] == "dark"
    assert updated["settings"]["locale"] == "zh-CN"
    assert updated["revision"] >= 1

    deleted = authenticated_client.delete(f"/api/v1/sessions/{session_id}")
    assert deleted.status_code == 204, deleted.text
    problem_response(authenticated_client.get(f"/api/v1/sessions/{session_id}"), 404)


def test_chat_injection_and_tool_approval_use_real_session_state(
    authenticated_client: httpx.Client,
    authenticated_client_for,
    student_user: SeededUser,
    teacher_user: SeededUser,
) -> None:
    session = create_session(authenticated_client, workspace_id=student_user.workspace_id)
    accepted = submit_turn(
        authenticated_client,
        session_id=session["session_id"],
        content="__api_http_slow__ Phase 7 injection target",
    )
    injected = authenticated_client.post(
        "/api/v1/chat/injections",
        json={
            "session_id": session["session_id"],
            "content": "deterministic injected context",
        },
    )
    assert injected.status_code == 202, injected.text
    injected_payload = json_response(injected, 202)
    assert injected_payload["turn_id"] == accepted["turn_id"]
    assert injected_payload["session_id"] == session["session_id"]

    approval = authenticated_client.post(
        "/api/v1/tool-approvals",
        json={
            "session_id": session["session_id"],
            "tool_name": "sandbox_reset",
            "reason": "Phase 7 deterministic approval baseline",
            "ttl_s": 60,
        },
    )
    approval_payload = json_response(approval, 201)
    assert approval_payload["session_id"] == session["session_id"]
    assert approval_payload["tool_name"] == "sandbox_reset"
    assert approval_payload["expires_at"] > 0

    foreign = authenticated_client_for(teacher_user)
    denied = foreign.post(
        "/api/v1/chat/injections",
        json={
            "session_id": session["session_id"],
            "content": "foreign injection",
        },
    )
    assert denied.status_code in {403, 404}, denied.text


def test_system_rbac_catalog_mutations_and_audit_baselines(
    authenticated_client_for,
    student_user: SeededUser,
    developer_user: SeededUser,
    temporary_role_code: str,
) -> None:
    student = authenticated_client_for(student_user)
    denied = problem_response(student.get("/api/v1/permissions"), 403)
    assert denied["code"] == "forbidden"
    assert problem_response(student.get("/api/v1/system/menus"), 403)["code"] == "forbidden"

    developer = authenticated_client_for(developer_user)
    permissions = json_response(developer.get("/api/v1/permissions"), 200)
    permission_codes = {item["code"] for item in permissions["items"]}
    assert "system:permission:read" in permission_codes

    menus = json_response(developer.get("/api/v1/system/menus"), 200)
    assert isinstance(menus["items"], list)
    visible = json_response(developer.get("/api/v1/system/menus/visible"), 200)
    assert isinstance(visible["items"], list)

    created = json_response(
        developer.post(
            "/api/v1/system/roles",
            json={
                "code": temporary_role_code,
                "name": "Phase 7 test role",
                "description": "Disposed with the isolated database",
            },
        ),
        201,
    )
    assert created["code"] == temporary_role_code
    assert created["is_builtin"] is False

    role_permissions = developer.put(
        f"/api/v1/system/roles/{temporary_role_code}/permissions",
        json={
            "permission_codes": ["system:permission:read"],
            "scopes": {"system:permission:read": ["system"]},
        },
    )
    permissions_payload = json_response(role_permissions, 200)
    assert permissions_payload["role_code"] == temporary_role_code
    assert permissions_payload["permission_codes"] == ["system:permission:read"]
    read_permissions = json_response(
        developer.get(f"/api/v1/system/roles/{temporary_role_code}/permissions"),
        200,
    )
    assert "system:permission:read" in read_permissions["permissions"]

    role_menus = developer.put(
        f"/api/v1/system/roles/{temporary_role_code}/menus",
        json={"menu_ids": []},
    )
    menus_payload = json_response(role_menus, 200)
    assert menus_payload == {"role_code": temporary_role_code, "menu_ids": []}
    read_menus = json_response(
        developer.get(f"/api/v1/system/roles/{temporary_role_code}/menus"),
        200,
    )
    assert read_menus == {"role_code": temporary_role_code, "menu_ids": []}

    role_status = json_response(
        developer.patch(
            f"/api/v1/system/roles/{temporary_role_code}/status",
            json={"status": "disabled"},
        ),
        200,
    )
    assert role_status == {"role_code": temporary_role_code, "status": "disabled"}

    user_roles = json_response(
        developer.get(f"/api/v1/users/{developer_user.user_id}/roles"),
        200,
    )
    assert user_roles["user_id"] == developer_user.user_id
    assert "developer" in user_roles["role_codes"]

    audit = json_response(
        developer.get("/api/v1/audit/authorization?limit=10&decision=allow"),
        200,
    )
    assert {"items", "total", "offset", "limit", "has_more"} <= audit.keys()
    assert all(item["decision"] == "allow" for item in audit["items"])
    audit_stats = json_response(
        developer.get("/api/v1/audit/authorization/stats?days=30"),
        200,
    )
    assert audit_stats["period_days"] == 30
    assert {"total", "by_decision", "top_reasons"} <= audit_stats.keys()

    feedback = json_response(
        authenticated_client_for(student_user).post(
            "/api/v1/feedback",
            json={"body": "Phase 7 bulk feedback baseline", "category": "ux"},
        ),
        201,
    )
    thread_id = str(feedback["thread_id"])
    bulk_read = json_response(
        developer.post("/api/v1/developer/feedback/bulk-read", json={"thread_ids": [thread_id]}),
        200,
    )
    assert bulk_read == {"ok": True, "updated": 1}
    bulk_delete = json_response(
        developer.post("/api/v1/developer/feedback/bulk-delete", json={"thread_ids": [thread_id]}),
        200,
    )
    assert bulk_delete == {"ok": True, "deleted": 1}
    problem_response(developer.get(f"/api/v1/developer/feedback/{thread_id}"), 404)

    note = json_response(
        developer.post(
            "/api/v1/developer/release-notes",
            json={
                "version": f"7.0.{uuid.uuid4().int % 1000000}",
                "released_at": "2026-09-09T00:00:00Z",
                "notes": ["Phase 7 update baseline"],
                "status": "draft",
            },
        ),
        201,
    )
    updated_note = json_response(
        developer.put(
            f"/api/v1/developer/release-notes/{note['id']}",
            json={
                "version": note["version"],
                "released_at": "2026-09-09T00:00:00Z",
                "notes": ["Phase 7 updated baseline"],
                "status": "published",
            },
        ),
        200,
    )
    assert updated_note["id"] == note["id"]
    assert updated_note["status"] == "published"
    assert updated_note["notes"] == ["Phase 7 updated baseline"]
    assert developer.delete(f"/api/v1/developer/release-notes/{note['id']}").status_code == 204

    # The live canonical roles intentionally do not include the highly
    # sensitive checkpoint-read permission.  Exercise the route with the
    # strongest ordinary HTTP role and assert its fail-closed contract.
    checkpoint = developer.get(
        f"/api/v1/system/sessions/{uuid.uuid4()}/checkpoints/{uuid.uuid4()}"
    )
    problem = problem_response(checkpoint, 403)
    assert problem["code"] == "forbidden"


def test_classroom_list_and_learning_asset_baselines(
    authenticated_client_for,
    student_user: SeededUser,
    teacher_user: SeededUser,
) -> None:
    teacher = authenticated_client_for(teacher_user)
    workspace = create_workspace(teacher, name="Phase 7 learning asset workspace")
    workspace_id = workspace["id"]
    classroom = create_classroom(
        teacher,
        workspace_id=workspace_id,
        name="Phase 7 full coverage classroom",
    )
    classrooms = json_response(teacher.get("/api/v1/classrooms"), 200)
    assert any(item["id"] == classroom["id"] for item in classrooms["items"])

    seeded = seed_catalog(teacher, workspace_id=workspace_id, prefix="phase7")
    source_path = "assets/phase7.png"
    stored_path = (
        "assets/pages/"
        + hashlib.sha256(seeded.knowledge_point_id.encode("utf-8")).hexdigest()[:24]
        + "/phase7.png"
    )
    updated = teacher.put(
        f"/api/v1/teacher/book/{workspace_id}/pages/{seeded.knowledge_point_id}",
        json={
            "content_markdown": f"Phase 7 page ![asset]({source_path})",
            "expected_revision": 0,
            "assets": [
                {
                    "asset_path": source_path,
                    "media_type": "image/png",
                    "content_base64": base64.b64encode(b"phase7-image").decode(),
                }
            ],
        },
    )
    assert updated.status_code == 200, updated.text
    published = teacher.post(
        f"/api/v1/teacher/book/{workspace_id}/pages/{seeded.knowledge_point_id}/publish",
        json={"expected_revision": 1},
    )
    assert published.status_code == 200, published.text

    student = authenticated_client_for(student_user)
    forbidden = student.get(
        f"/api/v1/learning/book/{workspace_id}/assets/{stored_path}"
    )
    assert forbidden.status_code in {403, 404}, forbidden.text

    teacher_asset = teacher.get(
        f"/api/v1/learning/book/{workspace_id}/assets/{stored_path}"
    )
    assert teacher_asset.status_code == 200, teacher_asset.text
    assert teacher_asset.content == b"phase7-image"
    assert teacher_asset.headers["content-type"].startswith("image/png")
    assert teacher_asset.headers["etag"]

    missing = teacher.get(
        f"/api/v1/learning/book/{workspace_id}/assets/assets/pages/missing.png"
    )
    problem = problem_response(missing, 404)
    assert problem["code"] == "book_asset_not_found"


def test_teacher_page_and_review_blueprint_baselines(
    authenticated_client_for,
    student_user: SeededUser,
    teacher_user: SeededUser,
) -> None:
    teacher = authenticated_client_for(teacher_user)
    seeded = seed_catalog(
        teacher,
        workspace_id=teacher_user.workspace_id,
        prefix="phase7-review",
        include_blueprints=True,
    )
    page = json_response(
        teacher.get(
            f"/api/v1/teacher/book/{teacher_user.workspace_id}/pages/{seeded.knowledge_point_id}"
        ),
        200,
    )
    page_payload = page["page"]
    assert page_payload["workspace_id"] == teacher_user.workspace_id
    assert page_payload["knowledge_point_id"] == seeded.knowledge_point_id
    assert page_payload["draft_markdown"] == ""

    review_id = str(seeded.review_blueprint_id)
    review = {
        "id": review_id,
        "name": "Phase 7 review baseline",
        "topic_id": seeded.topic_id,
        "knowledge_point_id": seeded.knowledge_point_id,
        "instructions": "Review the attention inputs.",
        "exercise_blueprint_id": seeded.exercise_blueprint_id,
        "status": "draft",
        "question_type": "简答",
        "rubric": [{"criterion": "概念准确", "weight": 100}],
    }
    updated = json_response(
        teacher.put(
            f"/api/v1/teacher/catalog/{teacher_user.workspace_id}/review-blueprints/{review_id}",
            json=review,
        ),
        200,
    )
    updated_review = next(
        item for item in updated["catalog"]["review_blueprints"] if item["id"] == review_id
    )
    assert updated_review["name"] == review["name"]

    student = authenticated_client_for(student_user)
    denied = student.get(
        f"/api/v1/teacher/book/{teacher_user.workspace_id}/pages/{seeded.knowledge_point_id}"
    )
    assert denied.status_code in {403, 404}, denied.text
