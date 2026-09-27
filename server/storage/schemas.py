"""HTTP contracts for account-owned storage."""

from __future__ import annotations

from pydantic import BaseModel, Field


class StorageRenameRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)


class StorageFolderRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    parent_id: str | None = None
