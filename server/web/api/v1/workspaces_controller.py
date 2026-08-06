"""Workspaces controller for workspace management operations."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy.orm import Session

from application.identity.authorization import AccessDeniedError
from application.identity.workspace_service import WorkspaceService
from schemas.workspace import (
    WorkspaceCreate,
    WorkspaceListResponse,
    WorkspaceMemberAdd,
    WorkspaceMemberListResponse,
    WorkspaceMemberResponse,
    WorkspaceResponse,
    WorkspaceUpdate,
)
from server.web.dependencies.identity import DbSession, Principal

router = APIRouter(prefix="/api/v1/workspaces", tags=["workspaces"])


@router.get("", response_model=WorkspaceListResponse)
async def list_workspaces(
    db: DbSession,
    principal: Principal,
):
    """List workspaces for the current user."""
    workspace_service = WorkspaceService(db)
    workspaces = workspace_service.list_user_workspaces(principal.user_id)

    return WorkspaceListResponse(
        items=[WorkspaceResponse.model_validate(ws) for ws in workspaces],
    )


@router.post("", response_model=WorkspaceResponse, status_code=status.HTTP_201_CREATED)
async def create_workspace(
    body: WorkspaceCreate,
    db: DbSession,
    principal: Principal,
):
    """Create a new workspace."""
    workspace_service = WorkspaceService(db)

    workspace = workspace_service.create_workspace(
        name=body.name,
        type=body.type,
        created_by_user_id=principal.user_id,
    )

    return WorkspaceResponse.model_validate(workspace)


@router.get("/{workspace_id}", response_model=WorkspaceResponse)
async def get_workspace(
    workspace_id: str,
    db: DbSession,
    principal: Principal,
):
    """Get workspace by ID."""
    if not principal.is_admin:
        workspace_service = WorkspaceService(db)
        if not workspace_service.is_member(workspace_id, principal.user_id):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Workspace not found",
            )

    workspace_service = WorkspaceService(db)
    workspace = workspace_service.get_workspace(workspace_id)

    if workspace is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workspace not found",
        )

    return WorkspaceResponse.model_validate(workspace)


@router.patch("/{workspace_id}", response_model=WorkspaceResponse)
async def update_workspace(
    workspace_id: str,
    body: WorkspaceUpdate,
    db: DbSession,
    principal: Principal,
):
    """Update workspace (owner or admin only)."""
    workspace_service = WorkspaceService(db)

    if not principal.is_admin:
        principal.require_role("owner")

    workspace = workspace_service.update_workspace(
        workspace_id,
        name=body.name,
        status=body.status,
    )

    if workspace is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workspace not found",
        )

    return WorkspaceResponse.model_validate(workspace)


@router.delete("/{workspace_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workspace(
    workspace_id: str,
    db: DbSession,
    principal: Principal,
):
    """Delete workspace (owner or admin only)."""
    workspace_service = WorkspaceService(db)

    if not principal.is_admin:
        principal.require_workspace_owner()

    success = workspace_service.delete_workspace(workspace_id)

    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workspace not found",
        )


@router.get("/{workspace_id}/members", response_model=WorkspaceMemberListResponse)
async def list_members(
    workspace_id: str,
    db: DbSession,
    principal: Principal,
):
    """List workspace members."""
    workspace_service = WorkspaceService(db)

    if not principal.is_admin:
        if not workspace_service.is_member(workspace_id, principal.user_id):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Workspace not found",
            )

    members = workspace_service.list_members(workspace_id)

    return WorkspaceMemberListResponse(
        items=[
            WorkspaceMemberResponse(
                workspace_id=m.workspace_id,
                user_id=m.user_id,
                role_id=m.role_id,
                status=m.status,
                joined_at=m.joined_at,
            )
            for m in members
        ],
    )


@router.post(
    "/{workspace_id}/members",
    response_model=WorkspaceMemberResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_member(
    workspace_id: str,
    body: WorkspaceMemberAdd,
    db: DbSession,
    principal: Principal,
):
    """Add a member to workspace (owner or admin only)."""
    workspace_service = WorkspaceService(db)

    if not principal.is_admin:
        principal.require_role("owner")

    member = workspace_service.add_member(
        workspace_id=workspace_id,
        user_id=body.user_id,
        role_code=body.role_code,
    )

    if member is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Failed to add member. User may already be a member or role not found.",
        )

    return WorkspaceMemberResponse(
        workspace_id=member.workspace_id,
        user_id=member.user_id,
        role_id=member.role_id,
        status=member.status,
        joined_at=member.joined_at,
    )


@router.delete("/{workspace_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(
    workspace_id: str,
    user_id: str,
    db: DbSession,
    principal: Principal,
):
    """Remove a member from workspace (owner or admin only)."""
    workspace_service = WorkspaceService(db)

    if not principal.is_admin:
        principal.require_role("owner")

    success = workspace_service.remove_member(workspace_id, user_id)

    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member not found",
        )
