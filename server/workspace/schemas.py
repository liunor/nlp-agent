"""Pydantic schemas for workspace management."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class WorkspaceBase(BaseModel):
    """Base workspace schema."""

    name: str = Field(..., min_length=1, max_length=128)


class WorkspaceCreate(WorkspaceBase):
    """Schema for creating a workspace."""

    slug: Optional[str] = Field(None, max_length=64)
    type: str = Field("personal", pattern="^(personal|organization|classroom)$")


class WorkspaceUpdate(BaseModel):
    """Schema for updating a workspace."""

    name: Optional[str] = Field(None, min_length=1, max_length=128)
    status: Optional[str] = Field(None, pattern="^(active|suspended|deleted)$")


class WorkspaceResponse(BaseModel):
    """Workspace response schema."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    slug: str
    name: str
    status: str
    created_at: datetime
    updated_at: datetime


class WorkspaceListResponse(BaseModel):
    """List of workspaces."""

    workspaces: list[WorkspaceResponse]
    total: int


class WorkspaceMemberAdd(BaseModel):
    """Schema for adding a workspace member."""

    user_id: str
    member_type: str = Field("member", pattern="^(owner|member|viewer)$")


class WorkspaceMemberResponse(BaseModel):
    """Workspace member response."""

    workspace_id: str
    user_id: str
    member_type: str
    status: str
    created_at: datetime
