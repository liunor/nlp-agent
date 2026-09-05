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

ROLE_DESCRIPTIONS = {
    "guest": "基础试用用户，可使用自己的智能体会话和公开学习内容，不能访问教学管理或系统管理功能。",
    "student": "学习用户，可在游客权限基础上访问工作区学习内容、提交练习、查看个人进度并提交学习反馈。",
    "teacher": "教学用户，可在学生权限基础上管理学习内容、班级成员、班级进度和学习反馈。",
    "developer": "平台管理用户，可管理模型、提示词、工具、运行状态、用户和四个固定角色的权限。",
}

# Stable Chinese labels shown in the administration UI. Codes remain the
# machine-facing identifiers returned alongside these labels.
PERMISSION_LABELS: dict[Permission, tuple[str, str]] = {
    Permission.IDENTITY_PROFILE_READ_SELF: ("查看个人资料", "查看当前账号自己的昵称、头像和资料信息，不能查看其他用户资料。"),
    Permission.IDENTITY_PROFILE_UPDATE_SELF: ("编辑个人资料", "修改当前账号自己的昵称、头像和资料信息，不会改变账号和角色。"),
    Permission.LEARNING_CONTENT_READ_PUBLIC: ("查看公开学习内容", "查看平台公开发布的课程、知识点和学习材料，不需要加入工作区。"),
    Permission.LEARNING_CONTENT_READ_WORKSPACE: ("查看工作区学习内容", "查看当前用户所属工作区内已发布的课程和知识内容。"),
    Permission.LEARNING_EXERCISE_SUBMIT: ("提交练习", "提交练习答案并查看本次提交结果，不可修改其他用户的提交记录。"),
    Permission.LEARNING_PROGRESS_READ_SELF: ("查看个人学习进度", "查看当前账号自己的学习进度、练习记录和待复习内容。"),
    Permission.LEARNING_CONTENT_MANAGE: ("管理学习内容", "创建、编辑、发布或下架自己负责的课程、知识点和学习材料。"),
    Permission.LEARNING_PROGRESS_READ_CLASSROOM: ("查看班级学习进度", "查看所负责班级成员的学习进度、练习完成情况和学习统计。"),
    Permission.LEARNING_FEEDBACK_SUBMIT: ("提交学习反馈", "提交对课程、练习和学习体验的意见，反馈内容归当前账号所有。"),
    Permission.LEARNING_FEEDBACK_READ: ("查看学习反馈", "查看平台收到的学习反馈，用于了解问题分布和整体学习体验。"),
    Permission.LEARNING_FEEDBACK_CREATE: ("处理学习反馈", "查看所负责班级的反馈并创建、更新处理回复和跟进记录。"),
    Permission.CLASSROOM_CREATE: ("创建班级", "创建和维护自己负责的班级基本信息，不代表可以管理其他教师的班级。"),
    Permission.CLASSROOM_MEMBER_MANAGE: ("管理班级成员", "向所负责的班级添加、移除成员或调整成员状态。"),
    Permission.AGENT_SESSION_CREATE: ("创建智能体会话", "创建属于当前账号或当前工作区的新 Agent 会话。"),
    Permission.AGENT_SESSION_READ: ("查看智能体会话", "查看当前账号拥有或所在工作区允许访问的 Agent 会话内容。"),
    Permission.AGENT_SESSION_UPDATE: ("编辑智能体会话", "修改可访问会话的标题、状态和会话设置，不可编辑无权访问的会话。"),
    Permission.AGENT_SESSION_DELETE: ("删除智能体会话", "删除当前账号有权管理的 Agent 会话及其关联消息记录。"),
    Permission.AGENT_TURN_SUBMIT: ("提交智能体消息", "向可访问的 Agent 会话发送消息并创建一次模型处理任务。"),
    Permission.AGENT_TURN_CANCEL: ("取消智能体任务", "取消当前账号有权访问且仍在执行中的 Agent 任务。"),
    Permission.AGENT_EVENT_REPLAY: ("回放智能体事件", "查看 Agent 会话的事件记录，并按时间顺序回放已产生的执行过程。"),
    Permission.AGENT_CHECKPOINT_RESTORE: ("恢复会话检查点", "将 Agent 会话恢复到已保存的检查点，可能覆盖检查点之后的会话状态。"),
    Permission.SYSTEM_MODEL_PROFILE_MANAGE: ("管理模型配置", "新增、编辑或停用模型和 Provider 配置，影响后续 Agent 请求使用的模型。"),
    Permission.SYSTEM_PROMPT_TEMPLATE_MANAGE: ("管理提示词模板", "创建、编辑、发布或停用系统提示词模板，影响使用该模板的 Agent 会话。"),
    Permission.SYSTEM_TOOL_CONFIG_MANAGE: ("管理工具配置", "管理工具和 MCP 服务配置，包括启用状态、调用策略和安全限制。"),
    Permission.SYSTEM_RUNTIME_MONITOR: ("监控运行状态", "查看服务、Worker、Agent 和任务队列的运行状态及基础指标。"),
    Permission.SYSTEM_RUNTIME_INSPECT: ("查看运行时详情", "查看运行时诊断、配置状态和故障排查信息，不代表可以修改配置。"),
    Permission.SYSTEM_USER_MANAGE: ("管理用户", "创建、编辑、禁用、恢复用户，并管理用户角色和登录会话。"),
    Permission.SYSTEM_ROLE_MANAGE: ("管理角色权限", "为游客、学生、教师、开发者四个固定角色分配权限和数据作用域，不能创建新角色。"),
    Permission.SYSTEM_RELEASE_NOTES_MANAGE: ("管理发布说明", "创建、编辑、发布或下线开发者工作台中的版本说明。"),
    Permission.SYSTEM_PERMISSION_READ: ("查看权限目录", "查看系统提供的权限名称、用途说明和可选数据作用域。"),
    Permission.SYSTEM_AUDIT_READ: ("查看审计日志", "查看用户、角色、权限和安全操作产生的授权审计记录。"),
    Permission.SYSTEM_SENSITIVE_DATA_READ: ("查看敏感数据", "查看受保护的敏感信息，仅在明确授权且符合数据访问范围时使用。"),
}


# The developer control plane is represented in the database menu projection.
# The React shell may keep route components statically bundled, but visibility
# and role binding must come from this catalog so a menu change has an
# observable effect without changing application code. Menu administration is
# deliberately not exposed: the product supports four fixed roles and the
# developer workspace has no live menu-management route.
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
    ("developer.quotas", "额度管理", "/developer/quotas", "quotas", Permission.SYSTEM_QUOTA_MANAGE, 125),
    ("monitor.audit", "审计日志", "/monitor?page=audit", "audit", Permission.SYSTEM_AUDIT_READ, 35, "monitor"),
)


def role_id(code: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"pro-nlp/rbac/role/{code}"))


def permission_id(permission: Permission | str) -> str:
    return str(uuid5(NAMESPACE_URL, f"pro-nlp/rbac/permission/{permission}"))


def permission_scope(permission: Permission) -> str:
    if permission is Permission.LEARNING_CONTENT_READ_PUBLIC:
        return "public"
    if (
        permission.name.startswith("SYSTEM_")
        or permission in {
            Permission.LEARNING_FEEDBACK_READ,
            Permission.LEARNING_FEEDBACK_WRITE,
        }
    ):
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


def permission_display(
    permission_code: str,
    *,
    fallback_name: str = "",
    fallback_description: str = "",
) -> tuple[str, str]:
    """Return the stable display labels for a permission code.

    Permission codes are the durable contract.  Older databases may contain
    an empty, stale, or incorrectly decoded display value, so known built-in
    permissions must use the catalog labels instead of trusting persisted text.
    Unknown codes retain their database values for forward compatibility.
    """
    try:
        permission = Permission(permission_code)
    except ValueError:
        return fallback_name, fallback_description
    return PERMISSION_LABELS.get(permission, (fallback_name, fallback_description))


def role_display(
    role_code: str,
    *,
    fallback_name: str = "",
    fallback_description: str = "",
) -> tuple[str, str]:
    """Return stable display labels for one of the four fixed roles."""
    name = ROLE_NAMES.get(role_code, fallback_name)
    description = ROLE_DESCRIPTIONS.get(role_code, fallback_description)
    return name, description


def role_row(code: str) -> dict[str, str | bool]:
    return {
        "id": role_id(code),
        "code": code,
        "name": ROLE_NAMES[code],
        "description": ROLE_DESCRIPTIONS[code],
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


def menu_row(item: tuple[str, str, str, str, Permission, int] | tuple[str, str, str, str, Permission, int, str]) -> dict[str, str | int | bool | None]:
    key, name, route_path, component_key, permission, sort_order = item[:6]
    client_scope = item[6] if len(item) > 6 else "developer"
    return {
        "id": menu_id(key),
        "parent_id": None,
        "menu_type": "page",
        "name": name,
        "route_path": route_path,
        "component_key": component_key,
        "permission_id": permission_id(permission),
        "client_scope": client_scope,
        "sort_order": sort_order,
        "visible": True,
        "status": "active",
    }


def role_menu_rows() -> list[dict[str, str]]:
    return [
        {"role_id": role_id("developer"), "menu_id": menu_id(item[0])}
        for item in MENU_CATALOG
    ]
