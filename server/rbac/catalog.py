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

# Stable Chinese labels shown in the administration UI. Codes remain the
# machine-facing identifiers returned alongside these labels.
PERMISSION_LABELS: dict[Permission, tuple[str, str]] = {
    Permission.IDENTITY_PROFILE_READ_SELF: ("查看个人资料", "查看自己的个人资料"),
    Permission.IDENTITY_PROFILE_UPDATE_SELF: ("编辑个人资料", "修改自己的个人资料"),
    Permission.LEARNING_CONTENT_READ_PUBLIC: ("查看公开学习内容", "查看公开的课程和知识内容"),
    Permission.LEARNING_CONTENT_READ_WORKSPACE: ("查看工作区学习内容", "查看所属工作区的学习内容"),
    Permission.LEARNING_EXERCISE_SUBMIT: ("提交练习", "提交学习练习并查看提交结果"),
    Permission.LEARNING_PROGRESS_READ_SELF: ("查看个人学习进度", "查看自己的学习进度"),
    Permission.LEARNING_CONTENT_MANAGE: ("管理学习内容", "创建、编辑和发布学习内容"),
    Permission.LEARNING_PROGRESS_READ_CLASSROOM: ("查看班级学习进度", "查看班级成员的学习进度"),
    Permission.LEARNING_FEEDBACK_SUBMIT: ("提交学习反馈", "提交对课程和学习体验的反馈"),
    Permission.LEARNING_FEEDBACK_READ: ("查看学习反馈", "查看平台收到的学习反馈"),
    Permission.LEARNING_FEEDBACK_CREATE: ("处理学习反馈", "创建和处理反馈回复"),
    Permission.CLASSROOM_CREATE: ("创建班级", "创建和管理自己负责的班级"),
    Permission.CLASSROOM_MEMBER_MANAGE: ("管理班级成员", "添加、移除或调整班级成员"),
    Permission.AGENT_SESSION_CREATE: ("创建智能体会话", "创建新的 Agent 会话"),
    Permission.AGENT_SESSION_READ: ("查看智能体会话", "查看可访问的 Agent 会话"),
    Permission.AGENT_SESSION_UPDATE: ("编辑智能体会话", "更新可访问的 Agent 会话"),
    Permission.AGENT_SESSION_DELETE: ("删除智能体会话", "删除可访问的 Agent 会话"),
    Permission.AGENT_TURN_SUBMIT: ("提交智能体消息", "向 Agent 会话提交消息"),
    Permission.AGENT_TURN_CANCEL: ("取消智能体任务", "取消正在执行的 Agent 任务"),
    Permission.AGENT_EVENT_REPLAY: ("回放智能体事件", "查看和回放 Agent 会话事件"),
    Permission.AGENT_CHECKPOINT_RESTORE: ("恢复会话检查点", "从检查点恢复 Agent 会话"),
    Permission.SYSTEM_MODEL_PROFILE_MANAGE: ("管理模型配置", "管理模型和 Provider 配置"),
    Permission.SYSTEM_PROMPT_TEMPLATE_MANAGE: ("管理提示词模板", "管理系统提示词模板"),
    Permission.SYSTEM_TOOL_CONFIG_MANAGE: ("管理工具配置", "管理工具和 MCP 配置"),
    Permission.SYSTEM_RUNTIME_MONITOR: ("监控运行状态", "查看运行时和服务状态"),
    Permission.SYSTEM_RUNTIME_INSPECT: ("查看运行时详情", "查看运行时诊断信息"),
    Permission.SYSTEM_USER_MANAGE: ("管理用户", "创建、编辑、禁用和恢复用户"),
    Permission.SYSTEM_ROLE_MANAGE: ("管理角色权限", "为固定角色分配权限和作用域"),
    Permission.SYSTEM_RELEASE_NOTES_MANAGE: ("管理发布说明", "创建和维护发布说明"),
    Permission.SYSTEM_PERMISSION_READ: ("查看权限目录", "查看系统权限定义"),
    Permission.SYSTEM_AUDIT_READ: ("查看审计日志", "查看授权和安全审计记录"),
    Permission.SYSTEM_SENSITIVE_DATA_READ: ("查看敏感数据", "查看受保护的敏感数据"),
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
    name, description = PERMISSION_LABELS.get(permission, (permission.value, ""))
    return {
        "id": permission_id(permission),
        "code": permission.value,
        "domain_name": domain_name,
        "resource_name": resource_name,
        "action_name": action_name,
        "name": name,
        "description": description,
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
