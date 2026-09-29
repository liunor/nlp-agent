"""Contract for the intentionally reserved teacher resource route."""

from __future__ import annotations

import pytest

from ..support.environment import SeededUser
from ..support.http import json_response, problem_response


pytestmark = pytest.mark.api_full


def test_reserved_teacher_resource_is_explicit_and_role_scoped(
    authenticated_client_for,
    teacher_user: SeededUser,
    student_user: SeededUser,
) -> None:
    teacher = authenticated_client_for(teacher_user)
    student = authenticated_client_for(student_user)
    reserved = json_response(
        teacher.get(
            f"/api/v1/teacher/courses?workspace_id={teacher_user.workspace_id}"
        ),
        200,
    )
    assert reserved["resource"] == "courses"
    assert reserved["status"] == "interface_reserved"
    assert reserved["workspace_id"] == teacher_user.workspace_id

    denied = student.get(
        f"/api/v1/teacher/courses?workspace_id={teacher_user.workspace_id}"
    )
    assert denied.status_code == 403
    assert problem_response(denied, 403)["code"] == "forbidden"

    unknown = teacher.get(
        f"/api/v1/teacher/not-a-live-resource?workspace_id={teacher_user.workspace_id}"
    )
    assert unknown.status_code == 404
