"""Teacher content, import and permission contracts over real HTTP."""

from __future__ import annotations

import base64
import io
import json
import zipfile

import pytest

from ..support.http import json_response
from ..support.resources import (
    add_workspace_member,
    create_workspace,
    create_session,
    create_user,
    seed_catalog,
    seed_teacher_ai_evidence,
)


pytestmark = pytest.mark.api_core


def _teacher_context(authenticated_client_for, teacher_user, student_user):
    teacher = authenticated_client_for(teacher_user)
    workspace = create_workspace(teacher, name="Phase 4 Teacher Workspace")
    add_workspace_member(
        teacher,
        workspace_id=workspace["id"],
        user_id=student_user.user_id,
    )
    student = authenticated_client_for(student_user)
    catalog = seed_catalog(
        teacher,
        workspace_id=workspace["id"],
        prefix="phase4teacher",
        include_blueprints=True,
    )
    return teacher, student, workspace, catalog


def _archive(catalog, *, traversal: bool = False) -> str:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "manifest.json",
            json.dumps(
                {
                    "format_version": 1,
                    "title": "Phase 4 Book",
                    "topics": [
                        {
                            "id": catalog.topic_id,
                            "name": catalog.topic_name,
                            "sort_order": 0,
                            "knowledge_points": [
                                {
                                    "id": catalog.knowledge_point_id,
                                    "name": catalog.knowledge_point_name,
                                    "file": "page.md" if not traversal else "../page.md",
                                    "sort_order": 0,
                                }
                            ],
                        }
                    ],
                },
                ensure_ascii=False,
            ),
        )
        archive.writestr("page.md", "# Imported Page\n\nArchive content")
    return base64.b64encode(stream.getvalue()).decode("ascii")


def test_teacher_overview_analytics_and_goals_are_workspace_scoped(
    authenticated_client_for,
    teacher_user,
    student_user,
) -> None:
    teacher, student, workspace, _catalog = _teacher_context(
        authenticated_client_for,
        teacher_user,
        student_user,
    )

    overview = json_response(
        teacher.get(f"/api/v1/teacher/overview?workspace_id={workspace['id']}"),
        200,
    )
    assert "goals" in overview
    assert overview["learning_analysis"]["scope"]["period_days"] == 30
    analytics = json_response(
        teacher.get(f"/api/v1/teacher/analytics?workspace_id={workspace['id']}"),
        200,
    )
    assert analytics["workspace_id"] == workspace["id"]
    assert analytics["learning_analysis"]["scope"]["period_days"] == 30

    updated = json_response(
        teacher.put(
            f"/api/v1/teacher/goals/{workspace['id']}",
            json={
                "course_title": "Phase 4 NLP",
                "description": "Real HTTP goals",
                "objectives": ["Understand attention"],
                "focus_topics": ["attention"],
                "target_level": "intermediate",
            },
        ),
        200,
    )
    assert updated["goals"]["course_title"] == "Phase 4 NLP"
    reread = json_response(
        teacher.get(f"/api/v1/teacher/goals/{workspace['id']}"),
        200,
    )
    assert reread["goals"]["target_level"] == "intermediate"

    assert student.get(f"/api/v1/teacher/goals/{workspace['id']}").status_code == 403
    denied = student.put(
        f"/api/v1/teacher/goals/{workspace['id']}",
        json={"course_title": "not allowed"},
    )
    assert denied.status_code == 403


def test_teacher_can_update_blueprints_and_student_cannot(
    authenticated_client_for,
    teacher_user,
    student_user,
) -> None:
    teacher, student, workspace, catalog = _teacher_context(
        authenticated_client_for,
        teacher_user,
        student_user,
    )
    assert teacher.get(f"/api/v1/teacher/catalog/{workspace['id']}").status_code == 200
    assert student.get(f"/api/v1/teacher/catalog/{workspace['id']}").status_code == 403

    exercise_id = catalog.exercise_blueprint_id
    assert exercise_id
    exercise = json_response(
        teacher.put(
            f"/api/v1/teacher/catalog/{workspace['id']}/exercise-blueprints/{exercise_id}",
            json={
                "id": exercise_id,
                "name": "Updated Exercise",
                "topic_id": catalog.topic_id,
                "knowledge_point_id": catalog.knowledge_point_id,
                "instructions": "Updated instructions",
                "question_type": "简答",
                "status": "draft",
                "rubric": [{"criterion": "concept", "weight": 100}],
            },
        ),
        200,
    )
    assert any(
        item["id"] == exercise_id
        for item in exercise["catalog"]["exercise_blueprints"]
    )

    denied = student.put(
        f"/api/v1/teacher/catalog/{workspace['id']}/guided-blueprints/{catalog.guided_blueprint_id}",
        json={
            "id": catalog.guided_blueprint_id,
            "name": "Denied",
            "topic_id": catalog.topic_id,
            "knowledge_point_id": catalog.knowledge_point_id,
            "guidance": "Denied",
            "status": "draft",
        },
    )
    assert denied.status_code == 403

    deleted = teacher.delete(
        f"/api/v1/teacher/catalog/{workspace['id']}/exercise-blueprints/{exercise_id}"
    )
    assert deleted.status_code == 204
    catalog_after = json_response(
        teacher.get(f"/api/v1/teacher/catalog/{workspace['id']}"),
        200,
    )
    assert all(item["id"] != exercise_id for item in catalog_after["catalog"]["exercise_blueprints"])


def test_markdown_import_preview_and_apply_require_teacher_scope(
    authenticated_client_for,
    teacher_user,
    student_user,
) -> None:
    teacher, student, workspace, catalog = _teacher_context(
        authenticated_client_for,
        teacher_user,
        student_user,
    )
    preview = json_response(
        teacher.post(
            f"/api/v1/teacher/book/{workspace['id']}/imports/preview",
            json={
                "file_name": "lesson.md",
                "content_markdown": "# Lesson\n\n```python\n#@tab tensorflow\ntf(x)\n#@tab pytorch\ntorch(x)\n```",
            },
        ),
        200,
    )
    assert "torch(x)" in preview["content_markdown"]
    assert "tf(x)" not in preview["content_markdown"]
    assert preview["removed_frameworks"] == ["tensorflow"]

    invalid = teacher.post(
        f"/api/v1/teacher/book/{workspace['id']}/imports/preview",
        json={"file_name": "../unsafe.md", "content_markdown": "# unsafe"},
    )
    assert invalid.status_code == 422

    applied = json_response(
        teacher.post(
            f"/api/v1/teacher/book/{workspace['id']}/imports/apply",
            json={
                "knowledge_point_id": catalog.knowledge_point_id,
                "file_name": "lesson.md",
                "content_markdown": "# Lesson\n\nApplied content",
                "expected_revision": 0,
            },
        ),
        200,
    )
    assert applied["page"]["revision"] == 1
    assert applied["page"]["draft_markdown"] == "# Lesson\n\nApplied content"

    denied = student.post(
        f"/api/v1/teacher/book/{workspace['id']}/imports/apply",
        json={
            "knowledge_point_id": catalog.knowledge_point_id,
            "file_name": "lesson.md",
            "content_markdown": "denied",
            "expected_revision": 1,
        },
    )
    assert denied.status_code == 403


def test_archive_import_preview_apply_and_corrupt_archive_semantics(
    authenticated_client_for,
    teacher_user,
    student_user,
) -> None:
    teacher, student, workspace, catalog = _teacher_context(
        authenticated_client_for,
        teacher_user,
        student_user,
    )
    archive_base64 = _archive(catalog)
    preview = json_response(
        teacher.post(
            f"/api/v1/teacher/book/{workspace['id']}/imports/archive/preview",
            json={"file_name": "phase4.zip", "archive_base64": archive_base64},
        ),
        200,
    )
    assert preview["items"][0]["knowledge_point_id"] == catalog.knowledge_point_id
    assert preview["items"][0]["action"] == "create"

    applied = json_response(
        teacher.post(
            f"/api/v1/teacher/book/{workspace['id']}/imports/archive/apply",
            json={
                "file_name": "phase4.zip",
                "archive_base64": archive_base64,
                "expected_revisions": {catalog.knowledge_point_id: 0},
            },
        ),
        200,
    )
    assert applied["applied_count"] == 1

    corrupt = teacher.post(
        f"/api/v1/teacher/book/{workspace['id']}/imports/archive/preview",
        json={"file_name": "phase4.zip", "archive_base64": "not-base64"},
    )
    assert corrupt.status_code == 422

    traversal = teacher.post(
        f"/api/v1/teacher/book/{workspace['id']}/imports/archive/preview",
        json={"file_name": "phase4.zip", "archive_base64": _archive(catalog, traversal=True)},
    )
    assert traversal.status_code == 422

    denied = student.post(
        f"/api/v1/teacher/book/{workspace['id']}/imports/archive/preview",
        json={"file_name": "phase4.zip", "archive_base64": archive_base64},
    )
    assert denied.status_code == 403


def test_teacher_ai_analysis_uses_deterministic_stub_and_fallbacks(
    authenticated_client_for,
    teacher_user,
    student_user,
    developer_user,
    mysql_probe,
) -> None:
    teacher, student, workspace, catalog = _teacher_context(
        authenticated_client_for,
        teacher_user,
        student_user,
    )
    developer = authenticated_client_for(developer_user)
    second_student = create_user(developer, role="student", prefix="phase4ai")
    add_workspace_member(
        teacher,
        workspace_id=workspace["id"],
        user_id=second_student.user_id,
    )
    second_student_client = authenticated_client_for(second_student)
    first_session = create_session(student, workspace_id=workspace["id"])
    second_session = create_session(
        second_student_client,
        workspace_id=workspace["id"],
    )
    seed_teacher_ai_evidence(
        mysql_probe,
        workspace_id=workspace["id"],
        student_user_ids=[student_user.user_id, second_student.user_id],
        session_ids=[first_session["session_id"], second_session["session_id"]],
        topic_id=catalog.topic_id,
        knowledge_point_id=catalog.knowledge_point_id,
    )

    completed = json_response(
        teacher.post(
            "/api/v1/teacher/reports/ai-analysis",
            json={"workspace_id": workspace["id"], "force_refresh": True},
        ),
        200,
    )
    assert completed["source"] == "deepseek"
    assert completed["status"] == "completed"
    assert completed["diagnoses"][0]["knowledge_point_id"] == catalog.knowledge_point_id

    timeout = json_response(
        teacher.post(
            "/api/v1/teacher/reports/ai-analysis",
            json={"workspace_id": workspace["id"], "force_refresh": True},
        ),
        200,
    )
    assert timeout["source"] == "rules"
    assert timeout["status"] == "failed"
    assert "DeepSeek" in timeout["message"]

    error = json_response(
        teacher.post(
            "/api/v1/teacher/reports/ai-analysis",
            json={"workspace_id": workspace["id"], "force_refresh": True},
        ),
        200,
    )
    assert error["source"] == "rules"
    assert error["status"] == "failed"
    assert "DeepSeek" in error["message"]

    denied = student.post(
        "/api/v1/teacher/reports/ai-analysis",
        json={"workspace_id": workspace["id"]},
    )
    assert denied.status_code == 403

    invalid = teacher.post(
        "/api/v1/teacher/reports/ai-analysis",
        json={
            "workspace_id": workspace["id"],
            "start_date": "2026-09-02",
        },
    )
    assert invalid.status_code == 422
