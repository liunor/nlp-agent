"""Role CRUD operations."""

from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.database import generate_uuid7
from models.identity import Role, UserRole


class RoleCRUD:
    """CRUD operations for Role model."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, *, code: str, name: str, scope: str) -> Role:
        """Create a new role."""
        role = Role(
            id=generate_uuid7(),
            code=code,
            name=name,
            scope=scope,
        )
        self.session.add(role)
        self.session.flush()
        return role

    def get_by_id(self, role_id: str) -> Optional[Role]:
        """Get role by ID."""
        stmt = select(Role).where(Role.id == role_id)
        return self.session.execute(stmt).scalar_one_or_none()

    def get_by_code(self, code: str) -> Optional[Role]:
        """Get role by code."""
        stmt = select(Role).where(Role.code == code)
        return self.session.execute(stmt).scalar_one_or_none()

    def list_roles(self, *, scope: Optional[str] = None) -> list[Role]:
        """List all roles, optionally filtered by scope."""
        stmt = select(Role)
        if scope:
            stmt = stmt.where(Role.scope == scope)
        return list(self.session.execute(stmt).scalars().all())

    def assign_role(self, user_id: str, role_id: str) -> UserRole:
        """Assign a role to a user."""
        user_role = UserRole(user_id=user_id, role_id=role_id)
        self.session.add(user_role)
        self.session.flush()
        return user_role

    def remove_role(self, user_id: str, role_id: str) -> bool:
        """Remove a role from a user."""
        stmt = select(UserRole).where(
            UserRole.user_id == user_id, UserRole.role_id == role_id
        )
        user_role = self.session.execute(stmt).scalar_one_or_none()
        if user_role is None:
            return False
        self.session.delete(user_role)
        self.session.flush()
        return True

    def get_user_roles(self, user_id: str) -> list[Role]:
        """Get all roles for a user."""
        stmt = (
            select(Role)
            .join(UserRole, UserRole.role_id == Role.id)
            .where(UserRole.user_id == user_id)
        )
        return list(self.session.execute(stmt).scalars().all())

    def get_user_role_codes(self, user_id: str) -> set[str]:
        """Get all role codes for a user."""
        roles = self.get_user_roles(user_id)
        return {role.code for role in roles}

    def ensure_default_roles(self) -> dict[str, Role]:
        """Ensure default system roles exist."""
        default_roles = [
            ("admin", "Administrator", "system"),
            ("teacher", "Teacher", "system"),
            ("developer", "Developer", "system"),
            ("operations", "Operations", "system"),
            ("learner", "Learner", "system"),
            ("owner", "Owner", "workspace"),
            ("member", "Member", "workspace"),
            ("viewer", "Viewer", "workspace"),
        ]
        roles = {}
        for code, name, scope in default_roles:
            role = self.get_by_code(code)
            if role is None:
                role = self.create(code=code, name=name, scope=scope)
            roles[code] = role
        return roles
