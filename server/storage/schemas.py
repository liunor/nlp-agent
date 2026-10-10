"""HTTP contracts for account-owned storage."""

from __future__ import annotations

from pydantic import BaseModel, Field


class StorageRenameRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)


class StorageFolderRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    parent_id: str | None = None


class StorageQuotaUpdateRequest(BaseModel):
    core_quota_bytes: int | None = Field(default=None, ge=0)
    files_quota_bytes: int | None = Field(default=None, ge=0)
    max_file_bytes: int | None = Field(default=None, ge=0)
    max_items: int | None = Field(default=None, ge=0)
    reason: str = Field(default="", max_length=500)


class FileTransferRequest(BaseModel):
    source_file_id: str = Field(..., min_length=1, max_length=36)
    recipient_identity_id: str = Field(..., min_length=3, max_length=32)
    workspace_id: str | None = Field(default=None, max_length=36)
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=64)
