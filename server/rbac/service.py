"""Application service for the MySQL role assignment source of truth."""

from __future__ import annotations

from datetime import datetime, timezone
import inspect

import uuid

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.identity import AuthenticatedPrincipal
from core.rbac import Permission
from server.infrastructure.mysql.models import (
    RoleModel,
    PermissionModel,
    UserModel,
    UserRoleModel,
    WorkspaceMemberModel,
    ClassroomModel,
    ClassroomMemberModel,
    OutboxMessageModel,
    AgentCheckpointModel,
    AuthorizationAuditLogModel,
    RolePermissionModel,
    RolePermissionScopeModel,
    MenuModel,
    RoleMenuModel,
)
from server.sandbox.service import sandbox_lifecycle_service
from server.rbac.catalog import ROLE_NAMES


class UnknownRoleError(ValueError):
    pass


class LastDeveloperForbiddenError(PermissionError):
    """Raised when a role change would remove the last active developer."""


class RbacService:
    async def _lock_developer_role(self, session: AsyncSession) -> RoleModel | None:
        """Serialize every operation that can change developer availability.

        The last-developer check must share a stable lock with both role
        replacement and account-status changes. Locking the role row before
        any user row also keeps the lock order compatible with role-permission
        updates, which lock the role first.
        """
        return await session.scalar(
            select(RoleModel)
            .where(RoleModel.code == "developer")
            .with_for_update()
        )
    async def create_role(self, session: AsyncSession, *, code: str, name: str, description: str, actor_user_id: str) -> RoleModel:
        raise PermissionError("系统仅支持游客、学生、教师、开发者四个固定角色")

    async def update_role_status(self, session: AsyncSession, *, role_code: str, status: str, actor_user_id: str) -> set[str]:
        role = await session.scalar(select(RoleModel).where(RoleModel.code == role_code).with_for_update())
        if role is None or role.code not in ROLE_NAMES or role.is_builtin:
            raise PermissionError("固定角色状态不可修改")
        role.status = status
        await self.audit(session, actor_user_id=actor_user_id, target_user_id=None, decision="allow", reason_code="role_status_changed", permission_code="system:role:manage", resource_type="role", resource_id=role_code, detail={"status": status})
        return await self.invalidate_role_users(session, role.id, reason="role_status_changed")

    async def principal_for_user_id(
        self, session: AsyncSession, user_id: str
    ) -> AuthenticatedPrincipal:
        user = await session.scalar(
            select(UserModel).where(
                UserModel.id == user_id,
                UserModel.status == "active",
                UserModel.deleted_at.is_(None),
            )
        )
        if user is None:
            raise PermissionError("turn submitter is not active in RBAC")
        roles = await self.roles_for(session, user.id)
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        permissions = frozenset(
            (
                await session.scalars(
                    select(PermissionModel.code)
                    .join(RolePermissionModel, RolePermissionModel.permission_id == PermissionModel.id)
                    .join(UserRoleModel, UserRoleModel.role_id == RolePermissionModel.role_id)
                    .join(RoleModel, RoleModel.id == UserRoleModel.role_id)
                    .where(
                        UserRoleModel.user_id == user.id,
                        RoleModel.status == "active",
                        RoleModel.code.in_(ROLE_NAMES),
                        PermissionModel.status == "active",
                        (UserRoleModel.expires_at.is_(None)) | (UserRoleModel.expires_at > now),
                    )
                )
            ).all()
        )
        scope_rows = await session.execute(
            select(PermissionModel.code, RolePermissionScopeModel.scope_type)
            .join(RolePermissionScopeModel, RolePermissionScopeModel.permission_id == PermissionModel.id)
            .join(UserRoleModel, UserRoleModel.role_id == RolePermissionScopeModel.role_id)
            .join(RoleModel, RoleModel.id == UserRoleModel.role_id)
            .where(UserRoleModel.user_id == user.id, RoleModel.status == "active", RoleModel.code.in_(ROLE_NAMES), PermissionModel.status == "active", (UserRoleModel.expires_at.is_(None)) | (UserRoleModel.expires_at > now))
        )
        permission_scopes: dict[str, frozenset[str]] = {}
        for code, scope in scope_rows:
            permission_scopes[code] = permission_scopes.get(code, frozenset()) | {scope}
        workspaces = frozenset(
            (
                await session.scalars(
                    select(WorkspaceMemberModel.workspace_id).where(
                        WorkspaceMemberModel.user_id == user.id,
                        WorkspaceMemberModel.status == "active",
                    )
                )
            ).all()
        )
        classrooms = frozenset((await session.scalars(
            select(ClassroomMemberModel.classroom_id)
            .join(ClassroomModel, ClassroomModel.id == ClassroomMemberModel.classroom_id)
            .where(ClassroomMemberModel.user_id == user.id, ClassroomMemberModel.status == "active", ClassroomModel.status == "active")
        )).all())
        return AuthenticatedPrincipal(
            user_id=user.id,
            workspace_ids=workspaces,
            classroom_ids=classrooms,
            roles=roles,
            permissions=permissions,
            permission_scopes=permission_scopes,
            authorization_version=user.authorization_version,
        )

    async def principal_for_username(
        self, session: AsyncSession, username: str
    ) -> AuthenticatedPrincipal:
        """Resolve the authoritative runtime principal from MySQL.

        The signed browser session proves who authenticated; role and workspace
        membership are deliberately reloaded here so a changed assignment takes
        effect on the next HTTP request (and on WebSocket guard ticks).
        """
        user = await session.scalar(
            select(UserModel).where(UserModel.username_lower == username.casefold())
        )
        if user is None:
            raise PermissionError("authenticated user is not active in RBAC")
        return await self.principal_for_user_id(session, user.id)

    async def role_catalog(self, session: AsyncSession) -> list[RoleModel]:
        return list((await session.scalars(select(RoleModel).where(RoleModel.code.in_(ROLE_NAMES)).order_by(RoleModel.code))).all())

    async def permission_catalog(self, session: AsyncSession) -> list[PermissionModel]:
        return list((await session.scalars(select(PermissionModel).order_by(PermissionModel.code))).all())

    async def role_permissions(self, session: AsyncSession, role_code: str) -> dict[str, frozenset[str]]:
        role = await session.scalar(
            select(RoleModel.id).where(
                RoleModel.code == role_code,
                RoleModel.code.in_(ROLE_NAMES),
                RoleModel.is_builtin.is_(True),
            )
        )
        if role is None:
            raise KeyError(role_code)
        rows = await session.execute(
            select(PermissionModel.code, RolePermissionScopeModel.scope_type)
            .join(RolePermissionModel, RolePermissionModel.permission_id == PermissionModel.id)
            .outerjoin(RolePermissionScopeModel, (RolePermissionScopeModel.role_id == RolePermissionModel.role_id) & (RolePermissionScopeModel.permission_id == RolePermissionModel.permission_id))
            .join(RoleModel, RoleModel.id == RolePermissionModel.role_id)
            .where(RoleModel.code == role_code, RoleModel.code.in_(ROLE_NAMES), RoleModel.is_builtin.is_(True))
        )
        result: dict[str, frozenset[str]] = {}
        for code, scope in rows:
            result[code] = result.get(code, frozenset()) | ({scope} if scope else set())
        return result

    async def replace_role_permissions(self, session: AsyncSession, *, role_code: str, permission_codes: set[str], scopes: dict[str, set[str]], actor_user_id: str) -> set[str]:
        role = await session.scalar(select(RoleModel).where(RoleModel.code == role_code).with_for_update())
        if role is None or role.code not in ROLE_NAMES or not role.is_builtin:
            raise PermissionError("仅支持修改四个固定角色的权限")
        permissions = list((await session.scalars(select(PermissionModel).where(PermissionModel.code.in_(permission_codes), PermissionModel.status == "active"))).all())
        if {item.code for item in permissions} != permission_codes:
            raise UnknownRoleError("unknown permission code")
        valid_scopes = {"public", "own", "classroom", "workspace", "system"}
        if not set(scopes).issubset(permission_codes) or any(not values or not values.issubset(valid_scopes) for values in scopes.values()):
            raise ValueError("each configured permission scope must be valid and non-empty")
        feedback_read_code = Permission.LEARNING_FEEDBACK_READ.value
        if feedback_read_code in permission_codes and scopes.get(feedback_read_code) != {"system"}:
            raise ValueError("learning:feedback:read must use system scope")
        feedback_write_code = Permission.LEARNING_FEEDBACK_WRITE.value
        if feedback_write_code in permission_codes and scopes.get(feedback_write_code) != {"system"}:
            raise ValueError("learning:feedback:write must use system scope")
        if role_code == "developer":
            required = {Permission.SYSTEM_USER_MANAGE.value, Permission.SYSTEM_ROLE_MANAGE.value}
            if required - permission_codes:
                raise PermissionError("developer role must retain user and role management permissions")
            if any(scopes.get(code) != {"system"} for code in required):
                raise ValueError("developer management permissions must use system scope")
        await session.execute(delete(RolePermissionScopeModel).where(RolePermissionScopeModel.role_id == role.id))
        await session.execute(delete(RolePermissionModel).where(RolePermissionModel.role_id == role.id))
        for item in permissions:
            session.add(RolePermissionModel(role_id=role.id, permission_id=item.id, granted_by_user_id=actor_user_id))
            for scope in scopes.get(item.code, set()):
                session.add(RolePermissionScopeModel(role_id=role.id, permission_id=item.id, scope_type=scope))
        await self.audit(session, actor_user_id=actor_user_id, target_user_id=None, decision="allow", reason_code="role_permissions_replaced", permission_code="system:role:manage", resource_type="role", resource_id=role_code)
        return await self.invalidate_role_users(session, role.id, reason="role_permissions_changed")

    async def visible_menus(
        self, session: AsyncSession, principal: AuthenticatedPrincipal
    ) -> list[MenuModel]:
        """Return visible menus granted by the principal's active roles.

        Menu bindings are presentation metadata, never an authorization
        bypass.  The permission check below keeps a stale role-menu binding
        from exposing a control-plane entry after its permission is removed.
        """
        if not principal.roles:
            return []
        rows = list(
            (
                await session.scalars(
                    select(MenuModel)
                    .join(RoleMenuModel, RoleMenuModel.menu_id == MenuModel.id)
                    .join(RoleModel, RoleModel.id == RoleMenuModel.role_id)
                    .outerjoin(PermissionModel, PermissionModel.id == MenuModel.permission_id)
                    .where(
                        RoleModel.code.in_(principal.roles),
                        RoleModel.status == "active",
                        MenuModel.status == "active",
                        MenuModel.visible.is_(True),
                        or_(
                            MenuModel.permission_id.is_(None),
                            PermissionModel.code.in_(principal.permissions),
                        ),
                    )
                    .order_by(MenuModel.sort_order, MenuModel.id)
                )
            ).unique()
        )
        return rows

    async def invalidate_role_users(self, session: AsyncSession, role_id: str, *, reason: str) -> set[str]:
        """Bump all affected principals and durably publish a revocation event."""
        user_ids = set((await session.scalars(select(UserRoleModel.user_id).where(UserRoleModel.role_id == role_id))).all())
        if user_ids:
            await session.execute(UserModel.__table__.update().where(UserModel.id.in_(user_ids)).values(authorization_version=UserModel.authorization_version + 1))
            for user_id in user_ids:
                await sandbox_lifecycle_service.revoke_user_leases(
                    session, user_id=user_id, reason=f"authorization.changed:{reason}"
                )
            session.add_all([OutboxMessageModel(id=str(uuid.uuid4()), topic="authorization.changed", payload_json={"user_id": user_id, "reason": reason}) for user_id in user_ids])
        return user_ids

    async def create_classroom(self, session: AsyncSession, *, workspace_id: str, name: str, actor_user_id: str) -> ClassroomModel:
        if await session.scalar(select(ClassroomModel.id).where(ClassroomModel.workspace_id == workspace_id).with_for_update()):
            raise ValueError("workspace already has a classroom")
        classroom = ClassroomModel(id=str(uuid.uuid4()), workspace_id=workspace_id, name=name, status="active")
        session.add(classroom)
        # The creator is the first classroom teacher; this is the explicit
        # classroom-scope root rather than an implicit workspace shortcut.
        session.add(ClassroomMemberModel(classroom_id=classroom.id, user_id=actor_user_id, member_role="teacher", status="active"))
        await self.audit(session, actor_user_id=actor_user_id, target_user_id=None, decision="allow", reason_code="classroom_created", permission_code="classroom:classroom:create", resource_type="classroom", resource_id=classroom.id)
        return classroom

    async def classroom(self, session: AsyncSession, classroom_id: str) -> ClassroomModel:
        row = await session.scalar(select(ClassroomModel).where(ClassroomModel.id == classroom_id, ClassroomModel.status == "active"))
        if row is None:
            raise KeyError(classroom_id)
        return row

    async def classrooms_for_user(self, session: AsyncSession, user_id: str) -> list[ClassroomModel]:
        return list((await session.scalars(select(ClassroomModel).join(ClassroomMemberModel, ClassroomMemberModel.classroom_id == ClassroomModel.id).where(ClassroomMemberModel.user_id == user_id, ClassroomMemberModel.status == "active", ClassroomModel.status == "active").order_by(ClassroomModel.name))).all())

    async def replace_classroom_member(self, session: AsyncSession, *, classroom_id: str, user_id: str, member_role: str, status: str, actor_user_id: str) -> None:
        if member_role not in {"student", "teacher"} or status not in {"active", "disabled"}:
            raise ValueError("invalid classroom member role or status")
        classroom = await session.scalar(select(ClassroomModel).where(ClassroomModel.id == classroom_id, ClassroomModel.status == "active").with_for_update())
        if classroom is None:
            raise KeyError(classroom_id)
        user = await session.scalar(select(UserModel).where(UserModel.id == user_id).with_for_update())
        if user is None:
            raise KeyError(user_id)
        member = await session.scalar(select(ClassroomMemberModel).where(ClassroomMemberModel.classroom_id == classroom_id, ClassroomMemberModel.user_id == user_id).with_for_update())
        if member is None:
            session.add(ClassroomMemberModel(classroom_id=classroom_id, user_id=user_id, member_role=member_role, status=status))
        else:
            member.member_role, member.status = member_role, status
        user.authorization_version += 1
        await sandbox_lifecycle_service.revoke_user_leases(
            session, user_id=user_id, reason="authorization.changed:classroom_membership_changed"
        )
        session.add(OutboxMessageModel(id=str(uuid.uuid4()), topic="authorization.changed", payload_json={"user_id": user_id, "reason": "classroom_membership_changed"}))
        await self.audit(session, actor_user_id=actor_user_id, target_user_id=user_id, decision="allow", reason_code="classroom_member_replaced", permission_code="classroom:member:manage", resource_type="classroom", resource_id=classroom_id, detail={"member_role": member_role, "status": status})

    async def audit(
        self,
        session: AsyncSession,
        *,
        actor_user_id: str | None,
        target_user_id: str | None,
        decision: str,
        reason_code: str,
        permission_code: str | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        detail: dict | None = None,
    ) -> None:
        result = session.add(
            AuthorizationAuditLogModel(
                id=str(uuid.uuid4()), actor_user_id=actor_user_id,
                target_user_id=target_user_id, decision=decision,
                reason_code=reason_code, permission_code=permission_code,
                resource_type=resource_type, resource_id=resource_id,
                detail_json=detail or {},
            )
        )
        if inspect.isawaitable(result):
            await result

    async def audit_records(
        self,
        session: AsyncSession,
        *,
        limit: int = 100,
        offset: int = 0,
        actor_user_id: str | None = None,
        decision: str | None = None,
        reason_code: str | None = None,
    ) -> list[AuthorizationAuditLogModel]:
        statement = self._audit_statement(
            actor_user_id=actor_user_id,
            decision=decision,
            reason_code=reason_code,
        ).order_by(
            AuthorizationAuditLogModel.created_at.desc(),
            AuthorizationAuditLogModel.id.desc(),
        ).offset(max(0, offset)).limit(max(1, min(limit, 500)))
        return list((await session.scalars(statement)).all())

    @staticmethod
    def _audit_statement(
        *,
        actor_user_id: str | None = None,
        decision: str | None = None,
        reason_code: str | None = None,
    ):
        statement = select(AuthorizationAuditLogModel)
        if actor_user_id is not None:
            statement = statement.where(AuthorizationAuditLogModel.actor_user_id == actor_user_id)
        if decision is not None:
            statement = statement.where(AuthorizationAuditLogModel.decision == decision)
        if reason_code is not None:
            statement = statement.where(AuthorizationAuditLogModel.reason_code == reason_code)
        return statement

    async def audit_page(
        self,
        session: AsyncSession,
        *,
        limit: int = 50,
        offset: int = 0,
        actor_user_id: str | None = None,
        decision: str | None = None,
        reason_code: str | None = None,
    ) -> tuple[list[AuthorizationAuditLogModel], int]:
        """Return one deterministic audit page and its total row count."""
        statement = self._audit_statement(
            actor_user_id=actor_user_id,
            decision=decision,
            reason_code=reason_code,
        )
        total = int(
            await session.scalar(
                select(func.count()).select_from(statement.order_by(None).subquery())
            )
            or 0
        )
        rows = list(
            (
                await session.scalars(
                    statement.order_by(
                        AuthorizationAuditLogModel.created_at.desc(),
                        AuthorizationAuditLogModel.id.desc(),
                    )
                    .offset(max(0, offset))
                    .limit(max(1, min(limit, 500)))
                )
            ).all()
        )
        return rows, total

    async def audit_summary(
        self, session: AsyncSession, *, since: datetime
    ) -> dict[str, object]:
        """Aggregate audit volume without loading detail JSON into memory."""
        base = select(AuthorizationAuditLogModel).where(
            AuthorizationAuditLogModel.created_at >= since
        )
        total = int(
            await session.scalar(
                select(func.count()).select_from(base.order_by(None).subquery())
            )
            or 0
        )
        decision_rows = await session.execute(
            select(
                AuthorizationAuditLogModel.decision,
                func.count().label("count"),
            )
            .where(AuthorizationAuditLogModel.created_at >= since)
            .group_by(AuthorizationAuditLogModel.decision)
        )
        reason_rows = await session.execute(
            select(
                AuthorizationAuditLogModel.reason_code,
                func.count().label("count"),
            )
            .where(AuthorizationAuditLogModel.created_at >= since)
            .group_by(AuthorizationAuditLogModel.reason_code)
            .order_by(func.count().desc(), AuthorizationAuditLogModel.reason_code)
            .limit(10)
        )
        return {
            "total": total,
            "by_decision": {
                str(row._mapping["decision"]): int(row._mapping["count"])
                for row in decision_rows
            },
            "top_reasons": [
                {
                    "reason_code": str(row._mapping["reason_code"]),
                    "count": int(row._mapping["count"]),
                }
                for row in reason_rows
            ],
        }

    async def read_sensitive_checkpoint(
        self,
        session: AsyncSession,
        *,
        session_id: str,
        checkpoint_id: str,
        actor_user_id: str,
        workspace_ids: frozenset[str] = frozenset(),
    ) -> AgentCheckpointModel:
        """Read only an ownership-bound raw checkpoint payload."""
        checkpoint = await session.scalar(select(AgentCheckpointModel).where(
            AgentCheckpointModel.session_id == session_id,
            AgentCheckpointModel.checkpoint_id == checkpoint_id,
            AgentCheckpointModel.owner_user_id == actor_user_id,
            AgentCheckpointModel.workspace_id.in_(workspace_ids),
        ))
        if checkpoint is None:
            raise KeyError(checkpoint_id)
        await self.audit(session, actor_user_id=actor_user_id, target_user_id=None,
            decision="allow", reason_code="sensitive_checkpoint_read",
            permission_code="system:sensitive_data:read", resource_type="checkpoint",
            resource_id=checkpoint_id, detail={"session_id": session_id})
        return checkpoint

    async def user_role_codes(self, session: AsyncSession, user_id: str) -> frozenset[str]:
        user = await session.scalar(select(UserModel.id).where(UserModel.id == user_id))
        if user is None:
            raise KeyError(user_id)
        return await self.roles_for(session, user_id)
    async def roles_for(self, session: AsyncSession, user_id: str) -> frozenset[str]:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        result = await session.execute(
            select(RoleModel.code)
            .join(UserRoleModel, UserRoleModel.role_id == RoleModel.id)
            .where(
                UserRoleModel.user_id == user_id,
                RoleModel.status == "active",
                RoleModel.code.in_(ROLE_NAMES),
                (UserRoleModel.expires_at.is_(None)) | (UserRoleModel.expires_at > now),
            )
        )
        return frozenset(result.scalars())

    async def replace_user_roles(
        self,
        session: AsyncSession,
        *,
        user_id: str,
        role_codes: set[str] | frozenset[str],
        assigned_by_user_id: str | None,
    ) -> frozenset[str]:
        requested_codes = set(role_codes) or {"guest"}
        await self._lock_developer_role(session)
        user = await session.scalar(
            select(UserModel).where(UserModel.id == user_id).with_for_update()
        )
        if user is None:
            raise KeyError(user_id)
        roles = list(
            (
                await session.scalars(
                    select(RoleModel).where(
                        RoleModel.code.in_(requested_codes), RoleModel.code.in_(ROLE_NAMES), RoleModel.status == "active", RoleModel.is_builtin.is_(True)
                    )
                )
            ).all()
        )
        found_codes = {role.code for role in roles}
        if found_codes != requested_codes:
            missing = ", ".join(sorted(requested_codes - found_codes))
            raise UnknownRoleError(f"unknown or inactive role codes: {missing}")
        if user_id == assigned_by_user_id and not requested_codes.issubset(
            await self.roles_for(session, user_id)
        ):
            raise PermissionError("users cannot grant themselves additional roles")
        current_roles = await self.roles_for(session, user_id)
        if "developer" in current_roles and "developer" not in found_codes:
            developer_count = await session.scalar(
                select(func.count(UserRoleModel.user_id))
                .join(RoleModel, RoleModel.id == UserRoleModel.role_id)
                .where(RoleModel.code == "developer", RoleModel.status == "active")
            )
            if int(developer_count or 0) <= 1:
                raise LastDeveloperForbiddenError(
                    "cannot remove the last active developer"
                )
        await session.execute(delete(UserRoleModel).where(UserRoleModel.user_id == user_id))
        session.add_all(
            [
                UserRoleModel(
                    user_id=user_id,
                    role_id=role.id,
                    assigned_by_user_id=assigned_by_user_id,
                )
                for role in roles
            ]
        )
        user.authorization_version += 1
        await sandbox_lifecycle_service.revoke_user_leases(
            session, user_id=user_id, reason="authorization.changed:user_roles_changed"
        )
        session.add(OutboxMessageModel(id=str(uuid.uuid4()), topic="authorization.changed", payload_json={"user_id": user_id, "reason": "user_roles_changed"}))
        await self.audit(
            session,
            actor_user_id=assigned_by_user_id,
            target_user_id=user_id,
            decision="allow",
            reason_code="user_roles_replaced",
            permission_code="system:role:manage",
            resource_type="user",
            resource_id=user_id,
            detail={"before": sorted(current_roles), "after": sorted(found_codes)},
        )
        await session.flush()
        return frozenset(found_codes)


rbac_service = RbacService()
