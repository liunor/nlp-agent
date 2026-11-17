"""Storage failures with stable, safe public explanations."""

from enum import StrEnum


class StorageError(Exception):
    """Base error for storage operations."""


class StorageQuotaReason(StrEnum):
    CORE = "account_core_limit"
    FILES = "account_files_limit"
    FILE_SIZE = "file_size_limit"
    FILE_COUNT = "file_count_limit"
    GLOBAL_DATA = "global_data_limit"
    GLOBAL_FILES = "global_files_limit"
    DISK = "disk_reserve_limit"
    UNKNOWN = "unknown"


_PUBLIC_MESSAGES = {
    StorageQuotaReason.CORE: "账户通用存储额度不足，无法保存本次内容。请查看存储用量或联系管理员。",
    StorageQuotaReason.FILES: "个人文件存储额度不足，请查看文件空间用量或联系管理员。",
    StorageQuotaReason.FILE_SIZE: "文件超过单文件大小限制，请选择较小的文件。",
    StorageQuotaReason.FILE_COUNT: "个人文件数量已达到上限，请查看文件列表或联系管理员。",
    StorageQuotaReason.GLOBAL_DATA: "服务器账户数据空间已达到上限，请联系管理员处理。",
    StorageQuotaReason.GLOBAL_FILES: "服务器个人文件存储空间已达到上限，请联系管理员处理。",
    StorageQuotaReason.DISK: "服务器可用存储空间暂时不足，请稍后重试或联系管理员。",
    StorageQuotaReason.UNKNOWN: "存储操作未完成，请稍后重试或联系管理员。",
}


class StorageQuotaExceeded(StorageError):
    """Keep the private exception text separate from the user-facing reason."""

    def __init__(self, message: str, *, reason: StorageQuotaReason = StorageQuotaReason.UNKNOWN):
        super().__init__(message)
        self.reason = reason

    @property
    def public_message(self) -> str:
        return _PUBLIC_MESSAGES.get(self.reason, _PUBLIC_MESSAGES[StorageQuotaReason.UNKNOWN])

    @property
    def public_details(self) -> dict[str, str]:
        return {"storage_reason": str(self.reason)}
