"""Stable IDs and seed data for built-in Pro_NLP RBAC records."""

from __future__ import annotations

from uuid import NAMESPACE_URL, uuid5

from core.rbac import Permission, ROLE_PERMISSIONS


ROLE_NAMES = {
    "guest": "游客",
    "student": "学生",
    "teacher": "教师",
    "developer": "开发者",
}


def role_id(code: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"pro-nlp/rbac/role/{code}"))


def permission_id(permission: Permission | str) -> str:
    return str(uuid5(NAMESPACE_URL, f"pro-nlp/rbac/permission/{permission}"))


def permission_scope(permission: Permission) -> str:
    if permission is Permission.LEARNING_CONTENT_READ_PUBLIC:
        return "public"
    if permission.name.startswith("SYSTEM_") or permission is Permission.LEARNING_FEEDBACK_READ:
        return "system"
    if permission in {
        Permission.LEARNING_FEEDBACK_SUBMIT,
    }:
        return "own"
    if permission in {
        Permission.LEARNING_CONTENT_MANAGE,
        Permission.LEARNING_PROGRESS_READ_CLASSROOM,
        Permission.LEARNING_FEEDBACK_CREATE,
        Permission.CLASSROOM_CREATE,
        Permission.CLASSROOM_MEMBER_MANAGE,
    }:
        return "classroom"
    if permission in {
        Permission.LEARNING_CONTENT_READ_WORKSPACE,
        Permission.AGENT_SESSION_READ,
        Permission.AGENT_TURN_SUBMIT,
        Permission.AGENT_EVENT_REPLAY,
    }:
        return "workspace"
    return "own"


def permission_row(permission: Permission) -> dict[str, str | bool]:
    domain_name, resource_name, action_name = permission.value.split(":", 2)
    return {
        "id": permission_id(permission),
        "code": permission.value,
        "domain_name": domain_name,
        "resource_name": resource_name,
        "action_name": action_name,
        "name": permission.value,
        "description": "",
        "status": "active",
        "is_builtin": True,
    }


def role_row(code: str) -> dict[str, str | bool]:
    return {
        "id": role_id(code),
        "code": code,
        "name": ROLE_NAMES[code],
        "description": "",
        "status": "active",
        "is_builtin": True,
    }


def role_permission_rows() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for code in ROLE_NAMES:
        for permission in sorted(ROLE_PERMISSIONS[code], key=str):
            rows.append(
                {
                    "role_id": role_id(code),
                    "permission_id": permission_id(permission),
                }
            )
    return rows


def role_permission_scope_rows() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for code in ROLE_NAMES:
        for permission in sorted(ROLE_PERMISSIONS[code], key=str):
            rows.append(
                {
                    "role_id": role_id(code),
                    "permission_id": permission_id(permission),
                    "scope_type": permission_scope(permission),
                }
            )
    return rows
