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


class FileDistributionRequest(BaseModel):
    source_file_id: str = Field(..., min_length=1, max_length=64, description="源文件ID")
    target_type: str = Field(..., description="分发目标类型: all_students, all_users, role, classroom, user")
    target_value: str | None = Field(default=None, max_length=128, description="目标具体标识，如角色名/班级ID/用户身份ID")
    direct_delivery: bool = Field(default=True, description="是否直接存入用户文件空间（无需对方手动同意）")
    workspace_id: str | None = Field(default=None, max_length=64)
