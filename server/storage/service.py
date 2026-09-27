"""Account storage policy, usage accounting, and file-system operations."""

from __future__ import annotations

import hashlib
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.sqltypes import Text as SqlText

from configs.settings import settings
from core.identity import AuthenticatedPrincipal
from server.infrastructure.mysql.models import (
    AgentCheckpointModel,
    ConversationMessageModel,
    ConversationModel,
    ConversationTranscriptModel,
    LangGraphCheckpointBlobModel,
    LangGraphCheckpointModel,
    LangGraphCheckpointWriteModel,
    MemoryArchiveModel,
    MemoryDocumentModel,
    TurnEventModel,
    TurnModel,
    UserFileModel,
)
from server.tools.vision.input_resolver import DEFAULT_UPLOADS_ROOT

from .policy import StoragePolicy, policy_for_roles, usage_ratio, usage_state


class StorageError(Exception):
    """Base error for storage operations."""


class StorageQuotaExceeded(StorageError):
    """Raised before a write would cross a user's logical quota."""


class StorageNameConflict(StorageError):
    """Raised when a sibling already has the requested name."""


class StorageValidationError(StorageError):
    """Raised for invalid file/folder names or ownership references."""


@dataclass(frozen=True, slots=True)
class StorageScope:
    owner_user_id: str
    workspace_id: str
    policy: StoragePolicy


def storage_root() -> Path:
    configured = settings.NLP_AGENT_USER_FILES_ROOT.strip()
    return Path(configured).expanduser() if configured else settings.BASE_DIR / ".data" / "user-files"


def _safe_name(name: str) -> str:
    cleaned = name.strip()
    if not cleaned or cleaned in {".", ".."} or "/" in cleaned or "\\" in cleaned:
        raise StorageValidationError("文件名不能包含路径分隔符")
    if len(cleaned) > 255 or any(ord(character) < 32 for character in cleaned):
        raise StorageValidationError("文件名长度或字符不合法")
    return cleaned


def _display_role(roles: frozenset[str] | set[str]) -> str:
    for role in ("admin", "developer", "teacher", "student", "guest"):
        if role in roles:
            return role
    return "guest"


def _workspace_path(scope: StorageScope) -> Path:
    return storage_root() / scope.workspace_id / scope.owner_user_id


def _safe_file_path(scope: StorageScope, storage_key: str | None) -> Path:
    if not storage_key:
        raise StorageValidationError("文件存储位置不存在")
    root = storage_root().resolve()
    path = (storage_root() / storage_key).resolve()
    if path != root and root not in path.parents:
        raise StorageValidationError("文件存储位置无效")
    return path


def _bytes_expr(column):
    return func.coalesce(func.length(cast(column, SqlText)), 0)


async def _sum_bytes(db: AsyncSession, model, columns: tuple, filters: tuple) -> int:
    if not columns:
        return 0
    expression = sum((_bytes_expr(column) for column in columns), 0)
    value = await db.scalar(select(func.coalesce(func.sum(expression), 0)).where(*filters))
    return int(value or 0)


class StorageService:
    """A small deep module for account-wide storage accounting and mutations.

    ``workspace_id`` is retained as the creation context and filesystem
    namespace, but all quota and ownership queries are intentionally keyed by
    ``owner_user_id`` so switching workspaces cannot multiply an account's
    allowance.
    """

    def __init__(self, db: AsyncSession, principal: AuthenticatedPrincipal, workspace_id: str):
        if workspace_id not in principal.workspace_ids and "*" not in principal.workspace_ids:
            raise StorageValidationError("无权访问该工作空间")
        self.db = db
        self.scope = StorageScope(
            owner_user_id=principal.user_id,
            workspace_id=workspace_id,
            policy=policy_for_roles(principal.roles),
        )
        self.role = _display_role(principal.roles)

    async def _file_used(self) -> int:
        value = await self.db.scalar(
            select(func.coalesce(func.sum(UserFileModel.size_bytes), 0)).where(
                UserFileModel.owner_user_id == self.scope.owner_user_id,
                UserFileModel.kind == "file",
                UserFileModel.status == "active",
            )
        )
        return int(value or 0)

    async def _core_used_from_db(self) -> int:
        owner = self.scope.owner_user_id
        total = 0
        total += await _sum_bytes(
            self.db,
            ConversationModel,
            (ConversationModel.title, ConversationModel.id),
            (ConversationModel.owner_user_id == owner,),
        )
        total += await _sum_bytes(
            self.db,
            TurnModel,
            (TurnModel.input_text, TurnModel.result_text, TurnModel.error_message, TurnModel.learning_state_json),
            (TurnModel.user_id == owner,),
        )
        total += await _sum_bytes(
            self.db,
            ConversationMessageModel,
            (ConversationMessageModel.content,),
            (ConversationMessageModel.conversation_id.in_(
                select(ConversationModel.id).where(
                    ConversationModel.owner_user_id == owner,
                )
            ),),
        )
        total += await _sum_bytes(
            self.db,
            TurnEventModel,
            (TurnEventModel.payload_json,),
            (TurnEventModel.turn_id.in_(
                select(TurnModel.id).where(
                    TurnModel.user_id == owner,
                )
            ),),
        )
        total += await _sum_bytes(
            self.db,
            ConversationTranscriptModel,
            (ConversationTranscriptModel.content_json, ConversationTranscriptModel.tool_json, ConversationTranscriptModel.usage_json),
            (ConversationTranscriptModel.session_id.in_(
                select(ConversationModel.id).where(
                    ConversationModel.owner_user_id == owner,
                )
            ),),
        )
        total += await _sum_bytes(
            self.db,
            MemoryDocumentModel,
            (MemoryDocumentModel.content_json,),
            (MemoryDocumentModel.user_id == owner,),
        )
        total += await _sum_bytes(
            self.db,
            MemoryArchiveModel,
            (MemoryArchiveModel.payload_json,),
            (MemoryArchiveModel.user_id == owner,),
        )
        total += await _sum_bytes(
            self.db,
            AgentCheckpointModel,
            (AgentCheckpointModel.checkpoint_json, AgentCheckpointModel.metadata_json),
            (AgentCheckpointModel.owner_user_id == owner,),
        )
        total += await _sum_bytes(
            self.db,
            LangGraphCheckpointModel,
            (LangGraphCheckpointModel.checkpoint_blob, LangGraphCheckpointModel.metadata_blob),
            (LangGraphCheckpointModel.owner_user_id == owner,),
        )
        total += await _sum_bytes(
            self.db,
            LangGraphCheckpointBlobModel,
            (LangGraphCheckpointBlobModel.value_blob,),
            (LangGraphCheckpointBlobModel.owner_user_id == owner,),
        )
        total += await _sum_bytes(
            self.db,
            LangGraphCheckpointWriteModel,
            (LangGraphCheckpointWriteModel.value_blob,),
            (LangGraphCheckpointWriteModel.owner_user_id == owner,),
        )
        return total

    def _uploaded_images_bytes(self) -> int:
        if not DEFAULT_UPLOADS_ROOT.is_dir():
            return 0
        total = 0
        for workspace_root in DEFAULT_UPLOADS_ROOT.iterdir():
            root = workspace_root / self.scope.owner_user_id
            if not root.is_dir():
                continue
            for path in root.rglob("*"):
                if path.is_file():
                    try:
                        total += path.stat().st_size
                    except OSError:
                        continue
        return total

    def _global_file_bytes(self) -> int:
        root = storage_root()
        if not root.is_dir():
            return 0
        total = 0
        for path in root.rglob("*"):
            if path.is_file():
                try:
                    total += path.stat().st_size
                except OSError:
                    continue
        return total

    def _check_global_capacity(self, incoming_bytes: int) -> None:
        if self._global_file_bytes() + incoming_bytes > settings.NLP_AGENT_USER_FILES_GLOBAL_LIMIT_BYTES:
            raise StorageQuotaExceeded("服务器个人文件池已达到系统上限")
        try:
            free_bytes = shutil.disk_usage(storage_root().parent).free
        except OSError:
            return
        if free_bytes - incoming_bytes < settings.NLP_AGENT_STORAGE_MIN_FREE_BYTES:
            raise StorageQuotaExceeded("服务器需要保留的系统空间不足")

    @staticmethod
    def _bucket(used: int, quota: int) -> dict[str, int | float | str]:
        return {
            "used_bytes": used,
            "quota_bytes": quota,
            "used_ratio": round(usage_ratio(used, quota), 4),
            "state": usage_state(used, quota),
        }

    async def usage(self) -> dict:
        core_used = await self._core_used_from_db() + self._uploaded_images_bytes()
        files_used = await self._file_used()
        return {
            "role": self.role,
            "core": self._bucket(core_used, self.scope.policy.core_quota_bytes),
            "files": self._bucket(files_used, self.scope.policy.files_quota_bytes),
            "files_count": int(
                await self.db.scalar(
                    select(func.count()).select_from(UserFileModel).where(
                        UserFileModel.owner_user_id == self.scope.owner_user_id,
                        UserFileModel.kind == "file",
                        UserFileModel.status == "active",
                    )
                )
                or 0
            ),
            "max_file_bytes": self.scope.policy.max_file_bytes,
            "max_items": self.scope.policy.max_items,
        }

    async def _sibling_named(self, name: str, parent_id: str | None, *, exclude_id: str | None = None):
        query = select(UserFileModel).where(
            UserFileModel.owner_user_id == self.scope.owner_user_id,
            UserFileModel.parent_id == parent_id,
            UserFileModel.status == "active",
            func.lower(UserFileModel.display_name) == name.lower(),
        )
        if exclude_id:
            query = query.where(UserFileModel.id != exclude_id)
        return await self.db.scalar(query.limit(1))

    async def _parent(self, parent_id: str | None) -> UserFileModel | None:
        if parent_id is None:
            return None
        parent = await self.db.scalar(
            select(UserFileModel).where(
                UserFileModel.id == parent_id,
                UserFileModel.owner_user_id == self.scope.owner_user_id,
                UserFileModel.kind == "folder",
                UserFileModel.status == "active",
            )
        )
        if parent is None:
            raise StorageValidationError("目标文件夹不存在")
        return parent

    @staticmethod
    def serialize(item: UserFileModel) -> dict:
        return {
            "id": item.id,
            "kind": item.kind,
            "name": item.display_name,
            "mime_type": item.mime_type,
            "size_bytes": int(item.size_bytes or 0),
            "created_at": item.created_at.isoformat() if item.created_at else None,
            "updated_at": item.updated_at.isoformat() if item.updated_at else None,
        }

    async def list_files(self, parent_id: str | None = None) -> list[dict]:
        await self._parent(parent_id)
        items = (
            await self.db.scalars(
                select(UserFileModel)
                .where(
                    UserFileModel.owner_user_id == self.scope.owner_user_id,
                    UserFileModel.parent_id == parent_id,
                    UserFileModel.status == "active",
                )
                .order_by(UserFileModel.kind.desc(), func.lower(UserFileModel.display_name))
            )
        ).all()
        return [self.serialize(item) for item in items]

    async def create_folder(self, name: str, parent_id: str | None = None) -> dict:
        name = _safe_name(name)
        await self._parent(parent_id)
        if await self._sibling_named(name, parent_id):
            raise StorageNameConflict("同一文件夹中已经存在同名项目")
        item = UserFileModel(
            id=str(uuid.uuid4()),
            owner_user_id=self.scope.owner_user_id,
            workspace_id=self.scope.workspace_id,
            parent_id=parent_id,
            kind="folder",
            display_name=name,
            storage_key=str(Path(self.scope.workspace_id) / self.scope.owner_user_id / uuid.uuid4().hex),
            size_bytes=0,
        )
        _workspace_path(self.scope).mkdir(parents=True, exist_ok=True)
        _safe_file_path(self.scope, item.storage_key).mkdir(parents=True, exist_ok=True)
        self.db.add(item)
        try:
            await self.db.flush()
        except Exception:
            _safe_file_path(self.scope, item.storage_key).rmdir()
            raise
        return self.serialize(item)

    async def create_file(self, upload, parent_id: str | None = None) -> dict:
        name = _safe_name(upload.filename or "未命名文件")
        await self._parent(parent_id)
        if await self._sibling_named(name, parent_id):
            raise StorageNameConflict("同一文件夹中已经存在同名项目")
        count = int(
            await self.db.scalar(
                select(func.count()).select_from(UserFileModel).where(
                        UserFileModel.owner_user_id == self.scope.owner_user_id,
                        UserFileModel.kind == "file",
                    UserFileModel.status == "active",
                )
            )
            or 0
        )
        if count >= self.scope.policy.max_items:
            raise StorageQuotaExceeded("文件数量已达到配额")
        data = await upload.read(self.scope.policy.max_file_bytes + 1)
        if len(data) > self.scope.policy.max_file_bytes:
            raise StorageQuotaExceeded("文件大小已达到当前角色的单文件配额")
        current = await self._file_used()
        if current + len(data) > self.scope.policy.files_quota_bytes:
            raise StorageQuotaExceeded("个人文件空间已达到配额")
        self._check_global_capacity(len(data))

        item_id = str(uuid.uuid4())
        storage_key = str(Path(self.scope.workspace_id) / self.scope.owner_user_id / item_id)
        target = _safe_file_path(self.scope, storage_key)
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_name(f".tmp-{item_id}")
        temp.write_bytes(data)
        temp.replace(target)
        item = UserFileModel(
            id=item_id,
            owner_user_id=self.scope.owner_user_id,
            workspace_id=self.scope.workspace_id,
            parent_id=parent_id,
            kind="file",
            display_name=name,
            storage_key=storage_key,
            mime_type=getattr(upload, "content_type", None),
            size_bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
        )
        self.db.add(item)
        try:
            await self.db.flush()
        except Exception:
            target.unlink(missing_ok=True)
            raise
        return self.serialize(item)

    async def get_file(self, file_id: str) -> UserFileModel:
        item = await self.db.scalar(
            select(UserFileModel).where(
                UserFileModel.id == file_id,
                UserFileModel.owner_user_id == self.scope.owner_user_id,
                UserFileModel.status == "active",
            )
        )
        if item is None:
            raise StorageValidationError("文件或文件夹不存在")
        return item

    async def rename(self, file_id: str, name: str) -> dict:
        item = await self.get_file(file_id)
        name = _safe_name(name)
        if await self._sibling_named(name, item.parent_id, exclude_id=item.id):
            raise StorageNameConflict("同一文件夹中已经存在同名项目")
        item.display_name = name
        await self.db.flush()
        return self.serialize(item)

    async def delete(self, file_id: str) -> None:
        item = await self.get_file(file_id)
        if item.kind == "folder":
            child_count = int(
                await self.db.scalar(
                    select(func.count()).select_from(UserFileModel).where(
                        UserFileModel.parent_id == item.id,
                        UserFileModel.owner_user_id == self.scope.owner_user_id,
                        UserFileModel.status == "active",
                    )
                )
                or 0
            )
            if child_count:
                raise StorageValidationError("文件夹不为空，请先删除其中的项目")
        if item.storage_key:
            target = _safe_file_path(self.scope, item.storage_key)
            if item.kind == "folder":
                if target.is_dir():
                    target.rmdir()
            else:
                target.unlink(missing_ok=True)
        await self.db.delete(item)
        await self.db.flush()

    async def download_path(self, file_id: str) -> tuple[UserFileModel, Path]:
        item = await self.get_file(file_id)
        if item.kind != "file":
            raise StorageValidationError("文件夹不能下载")
        path = _safe_file_path(self.scope, item.storage_key)
        if not path.is_file():
            raise StorageValidationError("文件内容不存在")
        return item, path
