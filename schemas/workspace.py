"""Workspace-related Pydantic schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class WorkspaceBase(BaseModel):
    """Base workspace schema."""

    name: str = Field(min_length=1, max_length=128)
    type: str = Field(pattern="^(personal|organization|classroom)$")


class WorkspaceCreate(WorkspaceBase):
    """Schema for creating a new workspace."""

    pass


class WorkspaceUpdate(BaseModel):
    """Schema for updating workspace."""

    name: Optional[str] = Field(default=None, min_length=1, max_length=128)
    status: Optional[str] = Field(default=None, pattern="^(active|suspended)$")


class WorkspaceResponse(WorkspaceBase):
    """Schema for workspace response."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    status: str
    created_by_user_id: str
    created_at: datetime
    updated_at: datetime


class WorkspaceListResponse(BaseModel):
    """Schema for workspace list."""

    items: list[WorkspaceResponse]


class WorkspaceMemberAdd(BaseModel):
    """Schema for adding a member to workspace."""

    user_id: str
    role_code: str = Field(min_length=1, max_length=64)


class WorkspaceMemberResponse(BaseModel):
    """Schema for workspace member response."""

    model_config = ConfigDict(from_attributes=True)

    workspace_id: str
    user_id: str
    role_id: str
    role_code: Optional[str] = None
    status: str
    joined_at: datetime


class WorkspaceMemberListResponse(BaseModel):
    """Schema for workspace member list."""

    items: list[WorkspaceMemberResponse]
