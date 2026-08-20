"""Workspace management API controller."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.identity import AuthenticatedPrincipal
from core.rbac import Permission, authorization_service
from server.rbac.service import rbac_service

from server.auth.dependencies import Principal, WriteClaims, get_db_session

from .schemas import (
    WorkspaceCreate,
    WorkspaceListResponse,
    WorkspaceMemberAdd,
    WorkspaceMemberResponse,
    WorkspaceResponse,
)
from .service import (
    WorkspaceService,
    WorkspaceNotFoundError,
    WorkspaceAlreadyExistsError,
    WorkspaceServiceError,
)

router = APIRouter(prefix="/api/v1/workspaces", tags=["workspaces"])


DbSession = Annotated[AsyncSession, Depends(get_db_session)]


@router.get("", response_model=WorkspaceListResponse)
async def list_workspaces(
    db: DbSession,
    principal: Principal,
):
    """List workspaces accessible to the current user."""
    service = WorkspaceService(db)
    workspaces = await service.list_user_workspaces(principal.user_id)

    return WorkspaceListResponse(
        workspaces=[WorkspaceResponse.model_validate(w) for w in workspaces],
        total=len(workspaces),
    )


@router.post("", response_model=WorkspaceResponse, status_code=status.HTTP_201_CREATED)
async def create_workspace(
    data: WorkspaceCreate,
    db: DbSession,
    _write: WriteClaims,
    principal: Principal,
):
    """Create a new workspace."""
    # Check permission (classroom creation requires specific permission)
    if data.type == "classroom":
        authorization_service.require(principal, Permission.CLASSROOM_CREATE)

    service = WorkspaceService(db)
    try:
        workspace = await service.create_workspace(
            data, created_by_user_id=principal.user_id
        )
        return WorkspaceResponse.model_validate(workspace)
    except WorkspaceAlreadyExistsError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.get("/{workspace_id}", response_model=WorkspaceResponse)
async def get_workspace(
    workspace_id: str,
    db: DbSession,
    principal: Principal,
):
    """Get workspace details."""
    service = WorkspaceService(db)
    try:
        workspace = await service.get_workspace(workspace_id)
        # Verify access
        if not await service.is_member(workspace_id, principal.user_id):
            if not principal.is_admin:
                raise HTTPException(status_code=403, detail="Access denied")
        return WorkspaceResponse.model_validate(workspace)
    except WorkspaceNotFoundError:
        raise HTTPException(status_code=404, detail="Workspace not found")


@router.get("/{workspace_id}/members", response_model=list[WorkspaceMemberResponse])
async def list_members(
    workspace_id: str,
    db: DbSession,
    principal: Principal,
):
    """List workspace members."""
    service = WorkspaceService(db)

    # Verify access
    if not await service.is_member(workspace_id, principal.user_id):
        if not principal.is_admin:
            raise HTTPException(status_code=403, detail="Access denied")

    try:
        members = await service.list_members(workspace_id)
        return [WorkspaceMemberResponse.model_validate(m) for m in members]
    except WorkspaceNotFoundError:
        raise HTTPException(status_code=404, detail="Workspace not found")


@router.post(
    "/{workspace_id}/members",
    response_model=WorkspaceMemberResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_member(
    workspace_id: str,
    data: WorkspaceMemberAdd,
    db: DbSession,
    _write: WriteClaims,
    principal: Principal,
):
    """Add a member to the workspace."""
    # Check permission
    authorization_service.require(
        principal,
        Permission.CLASSROOM_MEMBER_MANAGE,
        workspace_id=workspace_id,
    )

    service = WorkspaceService(db)
    try:
        member = await service.add_member(
            workspace_id,
            data.user_id,
            member_type=data.member_type,
            actor_user_id=principal.user_id,
        )
        # P0-4：成员变更写入审计事件（对象级动作可溯源）
        await rbac_service.audit(
            db,
            actor_user_id=principal.user_id,
            target_user_id=data.user_id,
            decision="allow",
            reason_code="workspace_member_added",
            permission_code="classroom:member:manage",
            resource_type="workspace",
            resource_id=workspace_id,
            detail={"member_type": data.member_type},
        )
        return WorkspaceMemberResponse.model_validate(member)
    except WorkspaceNotFoundError:
        raise HTTPException(status_code=404, detail="Workspace not found")
    except WorkspaceServiceError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete(
    "/{workspace_id}/members/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_member(
    workspace_id: str,
    user_id: str,
    db: DbSession,
    _write: WriteClaims,
    principal: Principal,
):
    """Remove a member from the workspace."""
    # Check permission
    authorization_service.require(
        principal,
        Permission.CLASSROOM_MEMBER_MANAGE,
        workspace_id=workspace_id,
    )

    service = WorkspaceService(db)
    try:
        removed = await service.remove_member(
            workspace_id, user_id, actor_user_id=principal.user_id
        )
    except WorkspaceServiceError as error:
        raise HTTPException(status_code=409, detail=str(error))
    if not removed:
        raise HTTPException(status_code=404, detail="Member not found")
    # P0-4：成员变更写入审计事件（对象级动作可溯源）
    await rbac_service.audit(
        db,
        actor_user_id=principal.user_id,
        target_user_id=user_id,
        decision="allow",
        reason_code="workspace_member_removed",
        permission_code="classroom:member:manage",
        resource_type="workspace",
        resource_id=workspace_id,
    )
