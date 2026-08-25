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


# The developer control plane is also represented in the database menu
# projection.  The React shell may keep route components statically bundled,
# but visibility and role binding must come from this catalog so a menu change
# has an observable effect without changing application code.
MENU_CATALOG = (
    ("developer.overview", "工作台", "/developer", "overview", Permission.SYSTEM_RUNTIME_MONITOR, 10),
    ("developer.agents", "Agent 与 Worker", "/developer/agents", "agents", Permission.SYSTEM_RUNTIME_MONITOR, 20),
    ("developer.tools", "工具", "/developer/tools", "tools", Permission.SYSTEM_TOOL_CONFIG_MANAGE, 30),
    ("developer.models", "模型与 Provider", "/developer/models", "models", Permission.SYSTEM_MODEL_PROFILE_MANAGE, 40),
    ("developer.mcp", "MCP", "/developer/mcp", "mcp", Permission.SYSTEM_TOOL_CONFIG_MANAGE, 50),
    ("developer.skills", "Skills", "/developer/skills", "skills", Permission.SYSTEM_TOOL_CONFIG_MANAGE, 60),
    ("developer.release-notes", "发布说明", "/developer/release-notes", "release-notes", Permission.SYSTEM_RELEASE_NOTES_MANAGE, 70),
    ("developer.automations", "Apps 与自动化", "/developer/automations", "automations", Permission.SYSTEM_RUNTIME_MONITOR, 80),
    ("developer.feedback", "意见反馈", "/developer/feedback", "feedback", Permission.LEARNING_FEEDBACK_READ, 95),
    ("developer.settings", "运行时设置", "/developer/settings", "settings", Permission.SYSTEM_RUNTIME_INSPECT, 90),
    ("developer.users", "用户管理", "/developer/users", "users", Permission.SYSTEM_USER_MANAGE, 100),
    ("developer.roles", "角色权限", "/developer/roles", "roles", Permission.SYSTEM_ROLE_MANAGE, 110),
    ("developer.menus", "菜单管理", "/developer/menus", "menus", Permission.SYSTEM_ROLE_MANAGE, 120),
    ("developer.audit", "审计日志", "/developer/audit", "audit", Permission.SYSTEM_AUDIT_READ, 130),
    ("developer.sessions", "Agent 会话", "/developer/sessions", "sessions", Permission.AGENT_SESSION_READ, 140),
)


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


def menu_id(key: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"pro-nlp/rbac/menu/{key}"))


def menu_row(item: tuple[str, str, str, str, Permission, int]) -> dict[str, str | int | bool | None]:
    key, name, route_path, component_key, permission, sort_order = item
    return {
        "id": menu_id(key),
        "parent_id": None,
        "menu_type": "page",
        "name": name,
        "route_path": route_path,
        "component_key": component_key,
        "permission_id": permission_id(permission),
        "client_scope": "developer",
        "sort_order": sort_order,
        "visible": True,
        "status": "active",
    }


def role_menu_rows() -> list[dict[str, str]]:
    return [
        {"role_id": role_id("developer"), "menu_id": menu_id(item[0])}
        for item in MENU_CATALOG
    ]
