"""FastAPI dependencies for identity and workspace principal resolution."""

from __future__ import annotations

from typing import Annotated, Optional

from fastapi import Cookie, Depends, Header, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from application.identity.authorization import AccessDeniedError, WorkspacePrincipal
from application.identity.auth_service import AuthService
from crud.role_crud import RoleCRUD
from crud.workspace_crud import WorkspaceCRUD
from db.database import database_manager


async def get_db_session() -> Session:
    """Get a database session for the request."""
    with database_manager.session() as session:
        yield session


DbSession = Annotated[Session, Depends(get_db_session)]


async def get_workspace_principal(
    request: Request,
    db: DbSession,
    nlp_session: Annotated[Optional[str], Cookie(alias="nlp_session")] = None,
    x_workspace_id: Annotated[Optional[str], Header(alias="X-Workspace-ID")] = None,
) -> WorkspacePrincipal:
    """Resolve the workspace principal from the request.

    This dependency:
    1. Validates the session cookie
    2. Resolves user identity and roles
    3. Validates workspace access
    4. Returns a WorkspacePrincipal for the request

    Raises HTTPException(401) if not authenticated.
    Raises HTTPException(403) if workspace access is denied.
    """
    if not nlp_session:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )

    auth_service = AuthService(db)
    login_result = auth_service.validate_session(session_token=nlp_session)

    if login_result is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired session",
        )

    workspace_id = x_workspace_id
    if not workspace_id and login_result.workspace_ids:
        workspace_id = login_result.workspace_ids[0]

    if not workspace_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No workspace available. Please create or join a workspace.",
        )

    if workspace_id not in login_result.workspace_ids and "admin" not in login_result.system_roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied to the specified workspace",
        )

    workspace_role = None
    if workspace_id in login_result.workspace_ids:
        workspace_crud = WorkspaceCRUD(db)
        workspace_role = workspace_crud.get_user_workspace_role(
            workspace_id, login_result.user.id
        )

    return WorkspacePrincipal(
        user_id=login_result.user.id,
        auth_session_id=login_result.auth_session_id,
        workspace_id=workspace_id,
        system_roles=frozenset(login_result.system_roles),
        workspace_role=workspace_role,
    )


Principal = Annotated[WorkspacePrincipal, Depends(get_workspace_principal)]
