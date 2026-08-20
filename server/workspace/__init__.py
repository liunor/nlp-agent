"""Workspace management module.

Provides workspace CRUD operations and membership management,
integrated with the existing RBAC infrastructure.
"""

from server.workspace.service import WorkspaceService, WorkspaceServiceError
from server.workspace.schemas import (
    WorkspaceCreate,
    WorkspaceResponse,
    WorkspaceMemberAdd,
)

__all__ = [
    "WorkspaceService",
    "WorkspaceServiceError",
    "WorkspaceCreate",
    "WorkspaceResponse",
    "WorkspaceMemberAdd",
]
