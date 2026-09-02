"""Role capability checks shared by HTTP, WebSocket and application services.

The current adapter derives roles from the authenticated principal.  The
database RBAC adapter added with the MySQL foundation will become the source of
those roles, while this module remains the single capability vocabulary and
decision seam.
"""

from __future__ import annotations

from enum import StrEnum
from dataclasses import dataclass
from typing import Final

from core.identity import AccessDeniedError, AuthenticatedPrincipal
from core.authorization_audit import record as record_authorization_decision


class Permission(StrEnum):
    QUOTA_USAGE_READ_SELF = "quota:usage:read_self"
    IDENTITY_PROFILE_READ_SELF = "identity:profile:read_self"
    IDENTITY_PROFILE_UPDATE_SELF = "identity:profile:update_self"
    LEARNING_CONTENT_READ_PUBLIC = "learning:content:read_public"
    LEARNING_CONTENT_READ_WORKSPACE = "learning:content:read_workspace"
    LEARNING_EXERCISE_SUBMIT = "learning:exercise:submit"
    LEARNING_PROGRESS_READ_SELF = "learning:progress:read_self"
    LEARNING_CONTENT_MANAGE = "learning:content:manage"
    LEARNING_PROGRESS_READ_CLASSROOM = "learning:progress:read_classroom"
    LEARNING_FEEDBACK_SUBMIT = "learning:feedback:submit"
    LEARNING_FEEDBACK_READ = "learning:feedback:read"
    LEARNING_FEEDBACK_WRITE = "learning:feedback:write"
    LEARNING_FEEDBACK_CREATE = "learning:feedback:create"
    CLASSROOM_CREATE = "classroom:classroom:create"
    CLASSROOM_MEMBER_MANAGE = "classroom:member:manage"
    AGENT_SESSION_CREATE = "agent:session:create"
    AGENT_SESSION_READ = "agent:session:read"
    AGENT_SESSION_UPDATE = "agent:session:update"
    AGENT_SESSION_DELETE = "agent:session:delete"
    AGENT_TURN_SUBMIT = "agent:turn:submit"
    AGENT_TURN_CANCEL = "agent:turn:cancel"
    AGENT_EVENT_REPLAY = "agent:event:replay"
    AGENT_CHECKPOINT_RESTORE = "agent:checkpoint:restore"
    SYSTEM_MODEL_PROFILE_MANAGE = "system:model_profile:manage"
    SYSTEM_PROMPT_TEMPLATE_MANAGE = "system:prompt_template:manage"
    SYSTEM_TOOL_CONFIG_MANAGE = "system:tool_config:manage"
    SYSTEM_RUNTIME_MONITOR = "system:runtime:monitor"
    SYSTEM_RUNTIME_INSPECT = "system:runtime:inspect"
    SYSTEM_USER_MANAGE = "system:user:manage"
    SYSTEM_ROLE_MANAGE = "system:role:manage"
    SYSTEM_RELEASE_NOTES_MANAGE = "system:release_notes:manage"
    SYSTEM_PERMISSION_READ = "system:permission:read"
    SYSTEM_AUDIT_READ = "system:audit:read"
    SYSTEM_SENSITIVE_DATA_READ = "system:sensitive_data:read"
    SYSTEM_QUOTA_READ = "system:quota:read"
    SYSTEM_QUOTA_MANAGE = "system:quota:manage"


class ScopeType(StrEnum):
    PUBLIC = "public"
    OWN = "own"
    CLASSROOM = "classroom"
    WORKSPACE = "workspace"
    SYSTEM = "system"


@dataclass(frozen=True, slots=True)
class ResourceRef:
    resource_type: str
    owner_user_id: str | None = None
    workspace_id: str | None = None
    is_public: bool = False
    classroom_id: str | None = None


class ResourcePolicy:
    """Compiles an effective permission scope into an object-level decision."""

    def allows(
        self, principal: AuthenticatedPrincipal, scopes: frozenset[str], resource: ResourceRef
    ) -> bool:
        if ScopeType.SYSTEM in scopes:
            return True
        if resource.is_public and ScopeType.PUBLIC in scopes:
            return True
        if resource.owner_user_id == principal.user_id and ScopeType.OWN in scopes:
            return True
        if resource.workspace_id and ScopeType.WORKSPACE in scopes:
            return "*" in principal.workspace_ids or resource.workspace_id in principal.workspace_ids
        if resource.classroom_id and ScopeType.CLASSROOM in scopes:
            return resource.classroom_id in principal.classroom_ids
        return False


_GUEST: Final[frozenset[Permission]] = frozenset(
    {
        Permission.IDENTITY_PROFILE_READ_SELF,
        Permission.IDENTITY_PROFILE_UPDATE_SELF,
        Permission.LEARNING_CONTENT_READ_PUBLIC,
        # 基础 agent 使用权限：guest 是"来试用智能体的人"，必须能创建/读写会话、提交对话。
        Permission.AGENT_SESSION_CREATE,
        Permission.AGENT_SESSION_READ,
        Permission.AGENT_SESSION_UPDATE,
        Permission.AGENT_SESSION_DELETE,
        Permission.AGENT_TURN_SUBMIT,
        Permission.AGENT_TURN_CANCEL,
        Permission.AGENT_EVENT_REPLAY,
        Permission.QUOTA_USAGE_READ_SELF,
    }
)
_STUDENT: Final[frozenset[Permission]] = _GUEST | {
    # 学习/教学增强权限：练习、进度、反馈、检查点恢复、工作区内容。
    Permission.AGENT_CHECKPOINT_RESTORE,
    Permission.LEARNING_CONTENT_READ_WORKSPACE,
    Permission.LEARNING_EXERCISE_SUBMIT,
    Permission.LEARNING_PROGRESS_READ_SELF,
    Permission.LEARNING_FEEDBACK_SUBMIT,
}
_TEACHER: Final[frozenset[Permission]] = _STUDENT | {
    Permission.LEARNING_CONTENT_MANAGE,
    Permission.LEARNING_PROGRESS_READ_CLASSROOM,
    Permission.LEARNING_FEEDBACK_CREATE,
    Permission.CLASSROOM_CREATE,
    Permission.CLASSROOM_MEMBER_MANAGE,
}
_DEVELOPER: Final[frozenset[Permission]] = _TEACHER | {
    Permission.LEARNING_FEEDBACK_READ,
    Permission.LEARNING_FEEDBACK_WRITE,
    Permission.SYSTEM_MODEL_PROFILE_MANAGE,
    Permission.SYSTEM_PROMPT_TEMPLATE_MANAGE,
    Permission.SYSTEM_TOOL_CONFIG_MANAGE,
    Permission.SYSTEM_RUNTIME_MONITOR,
    Permission.SYSTEM_RUNTIME_INSPECT,
    Permission.SYSTEM_USER_MANAGE,
    Permission.SYSTEM_ROLE_MANAGE,
    Permission.SYSTEM_RELEASE_NOTES_MANAGE,
    Permission.SYSTEM_PERMISSION_READ,
    Permission.SYSTEM_AUDIT_READ,
    Permission.SYSTEM_QUOTA_READ,
    Permission.SYSTEM_QUOTA_MANAGE,
}

ROLE_PERMISSIONS: Final[dict[str, frozenset[Permission]]] = {
    "guest": _GUEST,
    "student": _STUDENT,
    "teacher": _TEACHER,
    "developer": _DEVELOPER,
    # Compatibility during the migration from the old fixed role.  New users
    # must be assigned developer through the RBAC tables instead.
    "admin": _DEVELOPER,
}

# Every HIGH/CRITICAL tool must be listed here.  Registration is fail-closed
# so a future side-effecting tool cannot silently inherit a broad runtime grant.
HIGH_RISK_TOOL_PERMISSIONS: Final[dict[str, Permission]] = {
    "checkpoint.restore": Permission.AGENT_CHECKPOINT_RESTORE,
    "classroom.feedback.write": Permission.LEARNING_FEEDBACK_CREATE,
    "runtime.model_profile.write": Permission.SYSTEM_MODEL_PROFILE_MANAGE,
    "runtime.prompt_template.write": Permission.SYSTEM_PROMPT_TEMPLATE_MANAGE,
    "runtime.tool_config.write": Permission.SYSTEM_TOOL_CONFIG_MANAGE,
    # Sandbox model tools are deliberately mapped to existing session-control
    # permissions so the approval path remains fail-closed without widening
    # the public permission vocabulary.
    "sandbox_run_active_kernel": Permission.AGENT_TURN_SUBMIT,
    "sandbox_reset": Permission.AGENT_TURN_CANCEL,
}


def required_permission_for_high_risk_tool(tool_name: str) -> Permission:
    try:
        return HIGH_RISK_TOOL_PERMISSIONS[tool_name]
    except KeyError as error:
        raise PermissionError(
            f"high-risk tool {tool_name!r} has no explicit RBAC permission mapping"
        ) from error


class AuthorizationService:
    """Small, deterministic capability decision module.

    It deliberately keeps object ownership checks out of the role decision.
    State services must additionally validate owner/workspace relationships;
    no role (including developer) bypasses that validation.
    """

    def permissions_for(self, principal: AuthenticatedPrincipal) -> frozenset[Permission]:
        if principal.permissions:
            return frozenset(
                Permission(code)
                for code in principal.permissions
                if code in Permission._value2member_map_
            )
        permissions: set[Permission] = set()
        for role in principal.roles:
            permissions.update(ROLE_PERMISSIONS.get(role, ()))
        return frozenset(permissions)

    def allowed(
        self,
        principal: AuthenticatedPrincipal,
        permission: Permission | str,
        *,
        workspace_id: str | None = None,
    ) -> bool:
        try:
            required = Permission(permission)
        except ValueError:
            return False
        if required not in self.permissions_for(principal):
            return False
        if workspace_id is None:
            return True
        try:
            principal.require_workspace(workspace_id)
        except AccessDeniedError:
            return False
        return True

    def allowed_resource(
        self, principal: AuthenticatedPrincipal, permission: Permission | str, resource: ResourceRef
    ) -> bool:
        try:
            required = Permission(permission)
        except ValueError:
            return False
        if required not in self.permissions_for(principal):
            return False
        scopes = principal.permission_scopes.get(required.value)
        if not scopes:
            # Compatibility identities use the old role packages: agent data
            # is own-scoped, system controls are system-scoped.
            scopes = (
                frozenset({"system"})
                if required.value.startswith("system:")
                or required in {
                    Permission.LEARNING_FEEDBACK_READ,
                    Permission.LEARNING_FEEDBACK_WRITE,
                }
                else frozenset({"own"})
            )
        return ResourcePolicy().allows(principal, frozenset(scopes), resource)

    def require_resource(
        self, principal: AuthenticatedPrincipal, permission: Permission | str, resource: ResourceRef
    ) -> None:
        allowed = self.allowed_resource(principal, permission, resource)
        record_authorization_decision(
            principal,
            decision="allow" if allowed else "deny",
            permission_code=str(permission),
            resource_type=resource.resource_type,
            workspace_id=resource.workspace_id,
        )
        if not allowed:
            raise AccessDeniedError(f"permission {permission!s} is required for {resource.resource_type}")

    def require(
        self,
        principal: AuthenticatedPrincipal,
        permission: Permission | str,
        *,
        workspace_id: str | None = None,
    ) -> None:
        allowed = self.allowed(principal, permission, workspace_id=workspace_id)
        record_authorization_decision(principal, decision="allow" if allowed else "deny", permission_code=str(permission), workspace_id=workspace_id)
        if allowed:
            return
        if workspace_id is not None:
            try:
                principal.require_workspace(workspace_id)
            except AccessDeniedError:
                raise
        raise AccessDeniedError(f"permission {permission!s} is required")


authorization_service = AuthorizationService()
