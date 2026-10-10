"""Account storage policy, usage accounting, and file-system operations."""

from __future__ import annotations

import hashlib
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import cast, delete, exists, func, select
from sqlalchemy.dialects.mysql import insert
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
    RoleModel,
    StorageAccountModel,
    StorageQuotaAuditModel,
    StorageReservationModel,
    ToolCallModel,
    TurnEventModel,
    TurnModel,
    UserModel,
    UserRoleModel,
    UserFileModel,
)
from server.tools.vision.input_resolver import DEFAULT_UPLOADS_ROOT

from .policy import StorageBucket, StoragePolicy, policy_for_roles, policy_with_overrides, usage_ratio, usage_state
from .quota import AsyncStorageQuota, StorageError, StorageQuotaExceeded


class StorageNameConflict(StorageError):
    """Raised when a sibling already has the requested name."""


class StorageValidationError(StorageError):
    """Raised for invalid file/folder names or ownership references."""


class StoragePreviewUnsupported(StorageValidationError):
    """Raised when a stored file cannot be safely rendered as text."""


def utc_isoformat(value: datetime | None) -> str | None:
    """Serialize MySQL UTC ``DATETIME`` values with an explicit timezone."""
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    return value.isoformat().replace("+00:00", "Z")


MAX_STORAGE_PREVIEW_BYTES = 800_000
_TEXT_PREVIEW_MIME_TYPES = {
    "application/javascript",
    "application/json",
    "application/sql",
    "application/toml",
    "application/xml",
    "application/x-httpd-php",
    "application/x-sh",
    "application/x-yaml",
}
_TEXT_PREVIEW_EXTENSIONS = {
    "bash", "c", "cc", "cjs", "cpp", "cs", "css", "csv", "dockerfile", "env", "go", "h", "hpp",
    "htm", "html", "ini", "java", "js", "json", "jsx", "kt", "log", "makefile", "markdown",
    "md", "mdown", "mkd", "mjs", "php", "py", "rb", "readme", "rs", "scss", "sh", "sql", "svelte",
    "swift", "toml", "ts", "tsx", "txt", "vue", "xml", "yaml", "yml", "zsh",
}


def build_text_preview(
    path: Path,
    *,
    display_name: str,
    mime_type: str | None,
    max_bytes: int = MAX_STORAGE_PREVIEW_BYTES,
) -> dict:
    """Read a bounded UTF-8 prefix for formats the document viewer supports."""

    extension = Path(display_name).suffix.lower().lstrip(".") or Path(display_name).name.lower()
    normalized_mime = (mime_type or "").split(";", 1)[0].strip().lower()
    supported = (
        normalized_mime.startswith("text/")
        or normalized_mime in _TEXT_PREVIEW_MIME_TYPES
        or extension in _TEXT_PREVIEW_EXTENSIONS
    )
    if not supported:
        raise StoragePreviewUnsupported("该文件格式暂不支持在线预览，请下载到本地查看")
    limit = max(1, int(max_bytes))
    with path.open("rb") as stream:
        payload = stream.read(limit + 1)
    prefix = payload[:limit]
    if b"\x00" in prefix:
        raise StoragePreviewUnsupported("该文件格式暂不支持在线预览，请下载到本地查看")
    try:
        content = prefix.decode("utf-8")
    except UnicodeDecodeError as error:
        if len(payload) > limit and error.start >= max(0, len(prefix) - 3):
            content = prefix[:error.start].decode("utf-8")
        else:
            raise StoragePreviewUnsupported("该文件编码暂不支持在线预览，请下载到本地查看") from error
    return {
        "content": content,
        "truncated": len(payload) > limit,
        "mime_type": mime_type,
        "bytes_read": len(prefix),
    }


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


def storage_path_for(scope: StorageScope, storage_key: str | None) -> Path:
    if not storage_key:
        raise StorageValidationError("文件存储位置不存在")
    relative = Path(storage_key)
    if (
        relative.is_absolute()
        or len(relative.parts) < 2
        or relative.parts[0] != scope.workspace_id
        or relative.parts[1] != scope.owner_user_id
    ):
        raise StorageValidationError("文件存储位置不属于当前工作空间")
    root = storage_root().resolve()
    scope_root = _workspace_path(scope).resolve()
    path = (storage_root() / storage_key).resolve()
    if path != root and root not in path.parents:
        raise StorageValidationError("文件存储位置无效")
    if path != scope_root and scope_root not in path.parents:
        raise StorageValidationError("文件存储位置不属于当前工作空间")
    return path


_safe_file_path = storage_path_for


def _bytes_expr(column):
    return func.coalesce(func.length(cast(column, SqlText)), 0)


async def purge_expired_storage_trash(db: AsyncSession, *, retention_days: int | None = None) -> int:
    """Delete trashed file rows and bytes after the retention window."""

    days = settings.NLP_AGENT_STORAGE_TRASH_RETENTION_DAYS if retention_days is None else max(0, retention_days)
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
    items = (
        await db.scalars(
            select(UserFileModel).where(
                UserFileModel.status == "trashed",
                UserFileModel.deleted_at < cutoff,
            )
        )
    ).all()
    root = storage_root().resolve()
    for item in list(items):
        if item.storage_key:
            path = (storage_root() / item.storage_key).resolve()
            if root in path.parents:
                if item.kind == "folder" and path.is_dir():
                    shutil.rmtree(path)
                elif item.kind == "file":
                    path.unlink(missing_ok=True)
        await db.delete(item)
    if items:
        await db.flush()
    return len(items)


async def purge_expired_guest_data(db: AsyncSession) -> int:
    """Remove durable data for users that have remained guest-only too long."""

    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
        days=max(1, settings.NLP_AGENT_GUEST_STORAGE_RETENTION_DAYS)
    )
    guest_ids = list(
        (
            await db.scalars(
                select(UserModel.id)
                .join(UserRoleModel, UserRoleModel.user_id == UserModel.id)
                .join(RoleModel, RoleModel.id == UserRoleModel.role_id)
                .where(
                    UserModel.status == "active",
                    UserModel.deleted_at.is_(None),
                    UserModel.created_at < cutoff,
                    RoleModel.code == "guest",
                    RoleModel.status == "active",
                    (UserRoleModel.expires_at.is_(None) | (UserRoleModel.expires_at > func.utc_timestamp())),
                    ~exists(
                        select(1)
                        .select_from(UserRoleModel)
                        .join(RoleModel, RoleModel.id == UserRoleModel.role_id)
                        .where(
                            UserRoleModel.user_id == UserModel.id,
                            RoleModel.code != "guest",
                            RoleModel.status == "active",
                            (UserRoleModel.expires_at.is_(None) | (UserRoleModel.expires_at > func.utc_timestamp())),
                        )
                    ),
                )
            )
        ).all()
    )
    removed = 0
    for user_id in guest_ids:
        session_ids = select(ConversationModel.id).where(ConversationModel.owner_user_id == user_id)
        for statement in (
            delete(ConversationTranscriptModel).where(ConversationTranscriptModel.session_id.in_(session_ids)),
            delete(AgentCheckpointModel).where(AgentCheckpointModel.owner_user_id == user_id),
            delete(LangGraphCheckpointModel).where(LangGraphCheckpointModel.owner_user_id == user_id),
            delete(LangGraphCheckpointBlobModel).where(LangGraphCheckpointBlobModel.owner_user_id == user_id),
            delete(LangGraphCheckpointWriteModel).where(LangGraphCheckpointWriteModel.owner_user_id == user_id),
            delete(MemoryDocumentModel).where(MemoryDocumentModel.user_id == user_id),
            delete(MemoryArchiveModel).where(MemoryArchiveModel.user_id == user_id),
            delete(UserFileModel).where(UserFileModel.owner_user_id == user_id),
            delete(StorageReservationModel).where(StorageReservationModel.owner_user_id == user_id),
            delete(StorageAccountModel).where(StorageAccountModel.owner_user_id == user_id),
            delete(ConversationModel).where(ConversationModel.owner_user_id == user_id),
        ):
            await db.execute(statement)
        for workspace_root in DEFAULT_UPLOADS_ROOT.glob("*") if DEFAULT_UPLOADS_ROOT.is_dir() else ():
            path = workspace_root / str(user_id)
            if path.is_dir():
                shutil.rmtree(path)
        personal_root = storage_root().resolve()
        if personal_root.is_dir():
            for workspace_root in personal_root.iterdir():
                if not workspace_root.is_dir():
                    continue
                path = (workspace_root / str(user_id)).resolve()
                if personal_root in path.parents and path.is_dir():
                    shutil.rmtree(path)
        removed += 1
    if guest_ids:
        await db.flush()
    return removed


async def _sum_bytes(db: AsyncSession, model, columns: tuple, filters: tuple) -> int:
    if not columns:
        return 0
    expression = sum((_bytes_expr(column) for column in columns), 0)
    value = await db.scalar(select(func.coalesce(func.sum(expression), 0)).where(*filters))
    return int(value or 0)


async def reconcile_all_storage_accounts(db: AsyncSession) -> int:
    """Rebuild every account ledger once after deployment/startup.

    This is intentionally account-wide rather than request-triggered: legacy
    rows must count before the first write by a user, including users who are
    currently inactive and therefore would never hit a request path.
    """

    user_ids = list((await db.scalars(select(UserModel.id))).all())
    for user_id in user_ids:
        await AsyncStorageQuota(db, owner_user_id=str(user_id), roles=None).reconcile()
    return len(user_ids)


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
        self.quota = AsyncStorageQuota(
            db,
            owner_user_id=principal.user_id,
            roles=principal.roles,
        )

    async def _file_used(self) -> int:
        value = await self.db.scalar(
            select(func.coalesce(func.sum(UserFileModel.size_bytes), 0)).where(
                UserFileModel.owner_user_id == self.scope.owner_user_id,
                UserFileModel.kind == "file",
                UserFileModel.status.in_(("active", "trashed")),
            )
        )
        return int(value or 0)

    async def _effective_policy(self) -> StoragePolicy:
        account = await self.db.scalar(
            select(StorageAccountModel).where(StorageAccountModel.owner_user_id == self.scope.owner_user_id)
        )
        if account is None:
            await self.quota.reconcile()
            account = await self.db.scalar(
                select(StorageAccountModel).where(StorageAccountModel.owner_user_id == self.scope.owner_user_id)
            )
        if account is None:
            raise StorageError("storage account ledger row is unavailable")
        return policy_with_overrides(
            self.scope.policy,
            {
                "core_quota_bytes": account.core_quota_override_bytes,
                "files_quota_bytes": account.files_quota_override_bytes,
                "max_file_bytes": account.max_file_override_bytes,
                "max_items": account.max_items_override,
            },
        )

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
        total += await _sum_bytes(
            self.db,
            ToolCallModel,
            (ToolCallModel.request_json, ToolCallModel.result_json),
            (ToolCallModel.turn_id.in_(select(TurnModel.id).where(TurnModel.user_id == owner)),),
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
    def _bucket(used: int, quota: int, reserved: int = 0) -> dict[str, int | float | str]:
        effective = max(0, used) + max(0, reserved)
        return {
            "used_bytes": effective,
            "committed_used_bytes": max(0, used),
            "reserved_bytes": max(0, reserved),
            "quota_bytes": quota,
            "used_ratio": round(usage_ratio(effective, quota), 4),
            "state": usage_state(effective, quota),
            "over_quota": effective > quota,
        }

    async def usage(self) -> dict:
        await self.quota.reconcile()
        account = await self.db.scalar(
            select(StorageAccountModel).where(StorageAccountModel.owner_user_id == self.scope.owner_user_id)
        )
        if account is None:
            raise StorageError("storage account ledger row is unavailable")
        policy = policy_with_overrides(
            self.scope.policy,
            {
                "core_quota_bytes": account.core_quota_override_bytes,
                "files_quota_bytes": account.files_quota_override_bytes,
                "max_file_bytes": account.max_file_override_bytes,
                "max_items": account.max_items_override,
            },
        )
        files_count = int(
            await self.db.scalar(
                select(func.count()).select_from(UserFileModel).where(
                    UserFileModel.owner_user_id == self.scope.owner_user_id,
                    UserFileModel.kind == "file",
                    UserFileModel.status == "active",
                )
            )
            or 0
        )
        return {
            "role": self.role,
            "core": self._bucket(account.core_used_bytes, policy.core_quota_bytes, account.core_reserved_bytes),
            "files": self._bucket(account.files_used_bytes, policy.files_quota_bytes, account.files_reserved_bytes),
            "files_count": files_count,
            "max_file_bytes": policy.max_file_bytes,
            "max_items": policy.max_items,
        }

    async def _sibling_named(self, name: str, parent_id: str | None, *, exclude_id: str | None = None):
        query = select(UserFileModel).where(
            UserFileModel.owner_user_id == self.scope.owner_user_id,
            UserFileModel.workspace_id == self.scope.workspace_id,
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
                UserFileModel.workspace_id == self.scope.workspace_id,
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
            "created_at": utc_isoformat(item.created_at),
            "updated_at": utc_isoformat(item.updated_at),
        }

    async def list_files(self, parent_id: str | None = None) -> list[dict]:
        await self._parent(parent_id)
        items = (
            await self.db.scalars(
                select(UserFileModel)
                .where(
                    UserFileModel.owner_user_id == self.scope.owner_user_id,
                    UserFileModel.workspace_id == self.scope.workspace_id,
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
            # MySQL fills ``status`` and the timestamp columns from server
            # defaults during the flush.  SQLAlchemy expires those attributes;
            # reading them from an async session while serializing the response
            # would then attempt an implicit lazy load and raise
            # ``MissingGreenlet`` (HTTP 500).  Refresh explicitly while we are
            # still inside the async greenlet.
            await self.db.refresh(item)
        except Exception:
            _safe_file_path(self.scope, item.storage_key).rmdir()
            raise
        return self.serialize(item)

    async def create_file(self, upload, parent_id: str | None = None) -> dict:
        name = _safe_name(upload.filename or "未命名文件")
        policy = await self._effective_policy()
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
        if count >= policy.max_items:
            raise StorageQuotaExceeded("文件数量已达到配额")
        data = await upload.read(policy.max_file_bytes + 1)
        if len(data) > policy.max_file_bytes:
            raise StorageQuotaExceeded("文件大小已达到当前角色的单文件配额")
        current = await self._file_used()
        if current + len(data) > policy.files_quota_bytes:
            raise StorageQuotaExceeded("个人文件空间已达到配额")
        self._check_global_capacity(len(data))

        item_id = str(uuid.uuid4())
        reservation = await self.quota.reserve(
            StorageBucket.FILES,
            len(data),
            resource_type="personal_file",
            resource_key=item_id,
            amount_items=1,
        )
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
            await self.quota.finalize(reservation, reconcile=True)
            # See the folder path above: server-generated fields must be
            # loaded explicitly before ``serialize`` touches them.
            await self.db.refresh(item)
        except Exception:
            await self.quota.release(reservation)
            target.unlink(missing_ok=True)
            raise
        return self.serialize(item)

    async def get_file(self, file_id: str) -> UserFileModel:
        item = await self.db.scalar(
            select(UserFileModel).where(
                UserFileModel.id == file_id,
                UserFileModel.owner_user_id == self.scope.owner_user_id,
                UserFileModel.workspace_id == self.scope.workspace_id,
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
                        UserFileModel.workspace_id == self.scope.workspace_id,
                        UserFileModel.status == "active",
                    )
                )
                or 0
            )
            if child_count:
                raise StorageValidationError("文件夹不为空，请先删除其中的项目")
        item.status = "trashed"
        item.deleted_at = func.utc_timestamp(6)
        await self.db.flush()

    async def list_trash(self) -> list[dict]:
        items = (
            await self.db.scalars(
                select(UserFileModel)
                .where(
                    UserFileModel.owner_user_id == self.scope.owner_user_id,
                    UserFileModel.workspace_id == self.scope.workspace_id,
                    UserFileModel.status == "trashed",
                )
                .order_by(UserFileModel.deleted_at.desc(), UserFileModel.display_name)
            )
        ).all()
        return [
            self.serialize(item)
            | {"deleted_at": utc_isoformat(item.deleted_at)}
            for item in items
        ]

    async def _trashed(self, file_id: str) -> UserFileModel:
        item = await self.db.scalar(
            select(UserFileModel).where(
                UserFileModel.id == file_id,
                UserFileModel.owner_user_id == self.scope.owner_user_id,
                UserFileModel.workspace_id == self.scope.workspace_id,
                UserFileModel.status == "trashed",
            )
        )
        if item is None:
            raise StorageValidationError("回收站中不存在该项目")
        return item

    async def restore(self, file_id: str) -> dict:
        item = await self._trashed(file_id)
        await self._parent(item.parent_id)
        if await self._sibling_named(item.display_name, item.parent_id, exclude_id=item.id):
            raise StorageNameConflict("原位置已有同名项目，请先重命名原项目")
        item.status = "active"
        item.deleted_at = None
        await self.db.flush()
        return self.serialize(item)

    async def permanently_delete(self, file_id: str) -> None:
        item = await self._trashed(file_id)
        if item.storage_key:
            target = _safe_file_path(self.scope, item.storage_key)
            if item.kind == "folder" and target.is_dir():
                shutil.rmtree(target)
            elif item.kind == "file":
                target.unlink(missing_ok=True)
        await self.db.delete(item)
        await self.db.flush()
        await self.quota.reconcile()

    async def purge_expired_trash(self, *, retention_days: int | None = None) -> int:
        days = settings.NLP_AGENT_STORAGE_TRASH_RETENTION_DAYS if retention_days is None else max(0, retention_days)
        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
        items = (
            await self.db.scalars(
                select(UserFileModel).where(
                    UserFileModel.owner_user_id == self.scope.owner_user_id,
                    UserFileModel.workspace_id == self.scope.workspace_id,
                    UserFileModel.status == "trashed",
                    UserFileModel.deleted_at < cutoff,
                )
            )
        ).all()
        for item in list(items):
            if item.storage_key:
                target = _safe_file_path(self.scope, item.storage_key)
                if item.kind == "folder" and target.is_dir():
                    shutil.rmtree(target)
                elif item.kind == "file":
                    target.unlink(missing_ok=True)
            await self.db.delete(item)
        if items:
            await self.db.flush()
            await self.quota.reconcile()
        return len(items)

    async def download_path(self, file_id: str) -> tuple[UserFileModel, Path]:
        item = await self.get_file(file_id)
        if item.kind != "file":
            raise StorageValidationError("文件夹不能下载")
        path = _safe_file_path(self.scope, item.storage_key)
        if not path.is_file():
            raise StorageValidationError("文件内容不存在")
        return item, path

    async def preview(self, file_id: str) -> dict:
        item, path = await self.download_path(file_id)
        return build_text_preview(
            path,
            display_name=item.display_name,
            mime_type=item.mime_type,
        )


class StorageAdminService:
    """Low-volume administrator read/write seam for account quotas."""

    def __init__(self, db: AsyncSession, principal: AuthenticatedPrincipal) -> None:
        if not (set(principal.roles) & {"admin", "developer"}):
            raise StorageValidationError("只有管理员或开发者可以管理账户配额")
        self.db = db

    async def usage(self) -> list[dict]:
        users = list(
            (
                await self.db.scalars(
                    select(UserModel)
                    .where(UserModel.status == "active", UserModel.deleted_at.is_(None))
                    .order_by(UserModel.username)
                )
            ).all()
        )
        items: list[dict] = []
        for user in users:
            role_rows = await self.db.scalars(
                select(RoleModel.code)
                .join(UserRoleModel, UserRoleModel.role_id == RoleModel.id)
                .where(
                    UserRoleModel.user_id == user.id,
                    RoleModel.status == "active",
                    (UserRoleModel.expires_at.is_(None) | (UserRoleModel.expires_at > func.utc_timestamp())),
                )
            )
            roles = frozenset(role_rows.all())
            quota = AsyncStorageQuota(self.db, owner_user_id=user.id, roles=roles)
            await quota.reconcile()
            account = await self.db.scalar(
                select(StorageAccountModel).where(StorageAccountModel.owner_user_id == user.id)
            )
            if account is None:
                continue
            policy = policy_with_overrides(
                policy_for_roles(roles),
                {
                    "core_quota_bytes": account.core_quota_override_bytes,
                    "files_quota_bytes": account.files_quota_override_bytes,
                    "max_file_bytes": account.max_file_override_bytes,
                    "max_items": account.max_items_override,
                },
            )
            items.append(
                {
                    "user_id": user.id,
                    "username": user.username,
                    "display_name": user.display_name,
                    "roles": sorted(roles),
                    "core": StorageService._bucket(account.core_used_bytes, policy.core_quota_bytes, account.core_reserved_bytes),
                    "files": StorageService._bucket(account.files_used_bytes, policy.files_quota_bytes, account.files_reserved_bytes),
                    "overrides": {
                        "core_quota_bytes": account.core_quota_override_bytes,
                        "files_quota_bytes": account.files_quota_override_bytes,
                        "max_file_bytes": account.max_file_override_bytes,
                        "max_items": account.max_items_override,
                    },
                }
            )
        return items

    async def update_quota(
        self,
        user_id: str,
        values: dict[str, int | None],
        *,
        actor_user_id: str,
        reason: str = "",
    ) -> dict:
        user = await self.db.scalar(
            select(UserModel).where(UserModel.id == user_id, UserModel.status == "active")
        )
        if user is None:
            raise StorageValidationError("目标用户不存在")
        await self.db.execute(
            insert(StorageAccountModel)
            .values(id=str(uuid.uuid4()), owner_user_id=user_id)
            .on_duplicate_key_update(id=StorageAccountModel.id)
        )
        account = await self.db.scalar(
            select(StorageAccountModel).where(StorageAccountModel.owner_user_id == user_id).with_for_update()
        )
        if account is None:
            raise StorageError("storage account ledger row is unavailable")
        previous = {
            "core_quota_bytes": account.core_quota_override_bytes,
            "files_quota_bytes": account.files_quota_override_bytes,
            "max_file_bytes": account.max_file_override_bytes,
            "max_items": account.max_items_override,
        }
        if "core_quota_bytes" in values:
            account.core_quota_override_bytes = values["core_quota_bytes"]
        if "files_quota_bytes" in values:
            account.files_quota_override_bytes = values["files_quota_bytes"]
        if "max_file_bytes" in values:
            account.max_file_override_bytes = values["max_file_bytes"]
        if "max_items" in values:
            account.max_items_override = values["max_items"]
        current = {
            "core_quota_bytes": account.core_quota_override_bytes,
            "files_quota_bytes": account.files_quota_override_bytes,
            "max_file_bytes": account.max_file_override_bytes,
            "max_items": account.max_items_override,
        }
        self.db.add(
            StorageQuotaAuditModel(
                id=str(uuid.uuid4()),
                actor_user_id=actor_user_id,
                target_user_id=user_id,
                previous_values=previous,
                new_values=current,
                reason=reason.strip() or "未提供原因",
            )
        )
        await self.db.flush()
        return {
            "user_id": user_id,
            "overrides": current,
            "audit_reason": reason.strip() or "未提供原因",
        }

    async def purge_expired_trash(self, *, retention_days: int | None = None) -> int:
        days = settings.NLP_AGENT_STORAGE_TRASH_RETENTION_DAYS if retention_days is None else max(0, retention_days)
        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
        items = (
            await self.db.scalars(
                select(UserFileModel).where(
                    UserFileModel.status == "trashed",
                    UserFileModel.deleted_at < cutoff,
                )
            )
        ).all()
        for item in list(items):
            if item.storage_key:
                path = (storage_root() / item.storage_key).resolve()
                root = storage_root().resolve()
                if root in path.parents:
                    if item.kind == "folder" and path.is_dir():
                        shutil.rmtree(path)
                    elif item.kind == "file":
                        path.unlink(missing_ok=True)
            await self.db.delete(item)
        if items:
            await self.db.flush()
        return len(items)
