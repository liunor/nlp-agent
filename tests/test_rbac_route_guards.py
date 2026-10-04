from __future__ import annotations

import pytest

from core.identity import AccessDeniedError, AuthenticatedPrincipal
from server.teacher.service import teacher_service
from server.web.authorization import require_admin


def principal(*roles: str, workspaces: tuple[str, ...] = ("class-a",)) -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        user_id="user-1",
        workspace_ids=frozenset(workspaces),
        roles=frozenset(roles),
    )


def test_teacher_guard_requires_teacher_capability_and_workspace_membership() -> None:
    teacher_service.require_teacher(principal("teacher"), "class-a")

    with pytest.raises(AccessDeniedError):
        teacher_service.require_teacher(principal("student"), "class-a")
    with pytest.raises(AccessDeniedError):
        teacher_service.require_teacher(principal("teacher"), "class-b")


@pytest.mark.parametrize("role", ["guest", "student"])
def test_guest_and_student_can_never_enter_teacher_service(role: str) -> None:
    with pytest.raises(AccessDeniedError):
        teacher_service.require_teacher(principal(role), "class-a")


def test_developer_guard_accepts_developer_and_legacy_admin_during_migration() -> None:
    require_admin(principal("developer"))
    require_admin(principal("admin"))

    with pytest.raises(AccessDeniedError):
        require_admin(principal("teacher"))
