"""User management service."""

from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from crud.role_crud import RoleCRUD
from crud.user_crud import UserCRUD
from crud.workspace_crud import WorkspaceCRUD
from models.identity import User


class UserService:
    """Service for user management operations."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.user_crud = UserCRUD(session)
        self.role_crud = RoleCRUD(session)
        self.workspace_crud = WorkspaceCRUD(session)

    def create_user(
        self,
        username: str,
        password: str,
        *,
        display_name: str,
        email: Optional[str] = None,
    ) -> User:
        """Create a new user with password hashing."""
        from application.identity.auth_service import AuthService
        auth_service = AuthService(self.session)
        return auth_service.create_user(
            username=username,
            password=password,
            display_name=display_name,
            email=email,
        )

    def get_user(self, user_id: str) -> Optional[User]:
        """Get user by ID."""
        return self.user_crud.get_by_id(user_id)

    def get_user_by_username(self, username: str) -> Optional[User]:
        """Get user by username."""
        return self.user_crud.get_by_username(username)

    def list_users(
        self, *, offset: int = 0, limit: int = 100
    ) -> tuple[list[User], int]:
        """List users with pagination."""
        return self.user_crud.list_users(offset=offset, limit=limit)

    def update_user(
        self,
        user_id: str,
        *,
        display_name: Optional[str] = None,
        email: Optional[str] = None,
    ) -> Optional[User]:
        """Update user profile."""
        return self.user_crud.update(
            user_id, display_name=display_name, email=email
        )

    def disable_user(self, user_id: str) -> Optional[User]:
        """Disable a user account."""
        from crud.auth_session_crud import AuthSessionCRUD

        user = self.user_crud.update_status(user_id, "disabled")
        if user is not None:
            auth_session_crud = AuthSessionCRUD(self.session)
            auth_session_crud.revoke_all_for_user(
                user_id, reason="account_disabled"
            )
        return user

    def enable_user(self, user_id: str) -> Optional[User]:
        """Enable a disabled user account."""
        return self.user_crud.update_status(user_id, "active")

    def delete_user(self, user_id: str) -> bool:
        """Soft delete a user account."""
        from crud.auth_session_crud import AuthSessionCRUD

        result = self.user_crud.soft_delete(user_id)
        if result:
            auth_session_crud = AuthSessionCRUD(self.session)
            auth_session_crud.revoke_all_for_user(
                user_id, reason="account_deleted"
            )
        return result

    def get_user_roles(self, user_id: str) -> list[str]:
        """Get user's role codes."""
        return list(self.role_crud.get_user_role_codes(user_id))

    def assign_role(self, user_id: str, role_code: str) -> bool:
        """Assign a role to a user."""
        role = self.role_crud.get_by_code(role_code)
        if role is None:
            return False

        user = self.user_crud.get_by_id(user_id)
        if user is None:
            return False

        self.role_crud.assign_role(user_id, role.id)
        return True

    def remove_role(self, user_id: str, role_code: str) -> bool:
        """Remove a role from a user."""
        role = self.role_crud.get_by_code(role_code)
        if role is None:
            return False

        return self.role_crud.remove_role(user_id, role.id)

    def get_user_workspaces(self, user_id: str) -> list[dict]:
        """Get user's workspaces with role info."""
        workspaces = self.workspace_crud.list_workspaces_for_user(user_id)
        result = []
        for ws in workspaces:
            role_code = self.workspace_crud.get_user_workspace_role(ws.id, user_id)
            result.append(
                {
                    "id": ws.id,
                    "name": ws.name,
                    "type": ws.type,
                    "status": ws.status,
                    "role": role_code,
                }
            )
        return result

    def ensure_default_roles(self) -> dict[str, str]:
        """Ensure default roles exist and return code->id mapping."""
        roles = self.role_crud.ensure_default_roles()
        return {code: role.id for code, role in roles.items()}
