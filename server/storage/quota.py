"""Durable, account-wide storage reservations.

The storage usage endpoint is deliberately a read model.  Writes use the
small interface in this module instead: a row lock on one account serializes
reservations, while the reservation row makes failures and retries visible.
Both the synchronous gateway repository and async application services use
the same database contract.
"""

from __future__ import annotations

import uuid
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import cast, delete, func, select, text, update
from sqlalchemy.dialects.mysql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.sqltypes import Text as SqlText

from configs.settings import settings
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
    StorageAccountModel,
    StorageReservationModel,
    ToolCallModel,
    TurnEventModel,
    TurnModel,
    UserFileModel,
)
from server.tools.vision.input_resolver import DEFAULT_UPLOADS_ROOT

from .policy import StorageBucket, StoragePolicy, fits_quota, policy_for_roles, policy_with_overrides


class StorageError(Exception):
    """Base error for storage operations."""


class StorageQuotaExceeded(StorageError):
    """Raised before a write would exceed the account's logical quota."""


def validate_final_usage(
    *,
    core_used_bytes: int,
    files_used_bytes: int,
    core_quota_bytes: int,
    files_quota_bytes: int,
    global_used_bytes: int,
    global_limit_bytes: int,
) -> None:
    """Enforce limits against measured usage after a write has landed.

    Reservations protect the admission path, but callers may underestimate
    serialized JSON or filesystem bytes.  This function is intentionally
    side-effect free so both async/sync adapters and tests share the same
    final-settlement rule.
    """

    if core_used_bytes > core_quota_bytes:
        raise StorageQuotaExceeded(
            f"core storage quota exceeded after write: {core_used_bytes} > {core_quota_bytes} bytes"
        )
    if files_used_bytes > files_quota_bytes:
        raise StorageQuotaExceeded(
            f"files storage quota exceeded after write: {files_used_bytes} > {files_quota_bytes} bytes"
        )
    if global_used_bytes > global_limit_bytes:
        raise StorageQuotaExceeded("服务器账户数据池已达到系统上限")


@dataclass(frozen=True, slots=True)
class QuotaReservation:
    id: str
    owner_user_id: str
    bucket: StorageBucket
    amount_bytes: int
    resource_type: str


def _bytes_expr(column):
    return func.coalesce(func.length(cast(column, SqlText)), 0)


def _uploaded_image_bytes(owner_user_id: str) -> int:
    if not DEFAULT_UPLOADS_ROOT.is_dir():
        return 0
    total = 0
    for workspace_root in DEFAULT_UPLOADS_ROOT.iterdir():
        root = workspace_root / owner_user_id
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if path.is_file():
                try:
                    total += path.stat().st_size
                except OSError:
                    continue
    return total


def _disk_has_floor(incoming_bytes: int) -> bool:
    try:
        free = shutil.disk_usage(settings.BASE_DIR).free
    except OSError:
        return True
    return free - max(0, incoming_bytes) >= settings.NLP_AGENT_STORAGE_MIN_FREE_BYTES


async def _sum_bytes(db: AsyncSession, model, columns: tuple, filters: tuple) -> int:
    expression = sum((_bytes_expr(column) for column in columns), 0)
    value = await db.scalar(select(func.coalesce(func.sum(expression), 0)).where(*filters))
    return int(value or 0)


async def _async_reconcile(db: AsyncSession, owner: str) -> tuple[int, int]:
    """Rebuild the account totals once, including data written by old code."""

    core = 0
    core += await _sum_bytes(db, ConversationModel, (ConversationModel.title, ConversationModel.id), (ConversationModel.owner_user_id == owner,))
    core += await _sum_bytes(db, TurnModel, (TurnModel.input_text, TurnModel.result_text, TurnModel.error_message, TurnModel.learning_state_json), (TurnModel.user_id == owner,))
    core += await _sum_bytes(
        db,
        ConversationMessageModel,
        (ConversationMessageModel.content,),
        (ConversationMessageModel.conversation_id.in_(select(ConversationModel.id).where(ConversationModel.owner_user_id == owner)),),
    )
    core += await _sum_bytes(
        db,
        TurnEventModel,
        (TurnEventModel.payload_json,),
        (TurnEventModel.turn_id.in_(select(TurnModel.id).where(TurnModel.user_id == owner)),),
    )
    core += await _sum_bytes(
        db,
        ConversationTranscriptModel,
        (ConversationTranscriptModel.content_json, ConversationTranscriptModel.tool_json, ConversationTranscriptModel.usage_json),
        (ConversationTranscriptModel.session_id.in_(select(ConversationModel.id).where(ConversationModel.owner_user_id == owner)),),
    )
    core += await _sum_bytes(db, MemoryDocumentModel, (MemoryDocumentModel.content_json,), (MemoryDocumentModel.user_id == owner,))
    core += await _sum_bytes(db, MemoryArchiveModel, (MemoryArchiveModel.payload_json,), (MemoryArchiveModel.user_id == owner,))
    core += await _sum_bytes(db, AgentCheckpointModel, (AgentCheckpointModel.checkpoint_json, AgentCheckpointModel.metadata_json), (AgentCheckpointModel.owner_user_id == owner,))
    core += await _sum_bytes(db, LangGraphCheckpointModel, (LangGraphCheckpointModel.checkpoint_blob, LangGraphCheckpointModel.metadata_blob), (LangGraphCheckpointModel.owner_user_id == owner,))
    core += await _sum_bytes(db, LangGraphCheckpointBlobModel, (LangGraphCheckpointBlobModel.value_blob,), (LangGraphCheckpointBlobModel.owner_user_id == owner,))
    core += await _sum_bytes(db, LangGraphCheckpointWriteModel, (LangGraphCheckpointWriteModel.value_blob,), (LangGraphCheckpointWriteModel.owner_user_id == owner,))
    core += await _sum_bytes(
        db,
        ToolCallModel,
        (ToolCallModel.request_json, ToolCallModel.result_json),
        (ToolCallModel.turn_id.in_(select(TurnModel.id).where(TurnModel.user_id == owner)),),
    )
    core += _uploaded_image_bytes(owner)
    files = await db.scalar(
        select(func.coalesce(func.sum(UserFileModel.size_bytes), 0)).where(
            UserFileModel.owner_user_id == owner,
            UserFileModel.kind == "file",
            UserFileModel.status.in_(("active", "trashed")),
        )
    )
    return core, int(files or 0)


class AsyncStorageQuota:
    """Reserve and finalize quota inside an existing request transaction."""

    def __init__(self, db: AsyncSession, *, owner_user_id: str, roles: frozenset[str] | set[str] | None) -> None:
        self.db = db
        self.owner_user_id = owner_user_id
        self.roles = roles
        self.policy: StoragePolicy = policy_for_roles(roles or frozenset())

    async def _resolve_policy(self) -> None:
        if self.roles is not None:
            return
        rows = await self.db.execute(
            text(
                "SELECT r.code FROM nlp_user_roles ur JOIN nlp_roles r ON r.id=ur.role_id "
                "WHERE ur.user_id=:owner AND r.status='active' "
                "AND (ur.expires_at IS NULL OR ur.expires_at>UTC_TIMESTAMP())"
            ),
            {"owner": self.owner_user_id},
        )
        self.roles = {str(row[0]) for row in rows.all()}
        self.policy = policy_for_roles(self.roles)

    async def _locked_account(self) -> StorageAccountModel:
        await self.db.execute(
            insert(StorageAccountModel)
            .values(id=str(uuid.uuid4()), owner_user_id=self.owner_user_id)
            .on_duplicate_key_update(id=StorageAccountModel.id)
        )
        account = await self.db.scalar(
            select(StorageAccountModel)
            .where(StorageAccountModel.owner_user_id == self.owner_user_id)
            .with_for_update()
        )
        if account is None:
            raise RuntimeError("storage account ledger row is unavailable")
        if account.last_reconciled_at is None:
            account.core_used_bytes, account.files_used_bytes = await _async_reconcile(self.db, self.owner_user_id)
            account.last_reconciled_at = func.utc_timestamp(6)
            await self.db.flush()
        self.policy = policy_with_overrides(
            self.policy,
            {
                "core_quota_bytes": account.core_quota_override_bytes,
                "files_quota_bytes": account.files_quota_override_bytes,
                "max_file_bytes": account.max_file_override_bytes,
                "max_items": account.max_items_override,
            },
        )
        return account

    @staticmethod
    def _used_and_reserved(account: StorageAccountModel, bucket: StorageBucket) -> tuple[int, int]:
        if bucket is StorageBucket.CORE:
            return int(account.core_used_bytes), int(account.core_reserved_bytes)
        return int(account.files_used_bytes), int(account.files_reserved_bytes)

    async def reserve(
        self,
        bucket: StorageBucket,
        amount_bytes: int,
        *,
        resource_type: str,
        resource_key: str | None = None,
    ) -> QuotaReservation | None:
        amount = max(0, int(amount_bytes))
        if amount == 0:
            return None
        await self._resolve_policy()
        account = await self._locked_account()
        used, reserved = self._used_and_reserved(account, bucket)
        if not fits_quota(used, reserved, amount, self.policy.quota_for(bucket)):
            raise StorageQuotaExceeded(
                f"{bucket.value} storage quota exceeded: {used + reserved + amount} > {self.policy.quota_for(bucket)} bytes"
            )
        global_bytes = await self.db.scalar(
            select(
                func.coalesce(
                    func.sum(
                        StorageAccountModel.core_used_bytes
                        + StorageAccountModel.files_used_bytes
                        + StorageAccountModel.core_reserved_bytes
                        + StorageAccountModel.files_reserved_bytes
                    ),
                    0,
                )
            )
        )
        if int(global_bytes or 0) + amount > settings.NLP_AGENT_STORAGE_GLOBAL_DATA_LIMIT_BYTES:
            raise StorageQuotaExceeded("服务器账户数据池已达到系统上限")
        if not _disk_has_floor(amount):
            raise StorageQuotaExceeded("服务器需要保留的系统空间不足")
        reservation = QuotaReservation(
            id=str(uuid.uuid4()),
            owner_user_id=self.owner_user_id,
            bucket=bucket,
            amount_bytes=amount,
            resource_type=resource_type,
        )
        self.db.add(
            StorageReservationModel(
                id=reservation.id,
                owner_user_id=self.owner_user_id,
                bucket=bucket.value,
                amount_bytes=amount,
                resource_type=resource_type,
                resource_key=resource_key,
                status="reserved",
            )
        )
        if bucket is StorageBucket.CORE:
            account.core_reserved_bytes += amount
        else:
            account.files_reserved_bytes += amount
        await self.db.flush()
        return reservation

    async def finalize(self, reservation: QuotaReservation | None, *, actual_bytes: int | None = None, reconcile: bool = True) -> None:
        if reservation is None:
            return
        row = await self.db.scalar(
            select(StorageReservationModel)
            .where(StorageReservationModel.id == reservation.id, StorageReservationModel.owner_user_id == self.owner_user_id)
            .with_for_update()
        )
        if row is None or row.status != "reserved":
            return
        account = await self.db.scalar(
            select(StorageAccountModel)
            .where(StorageAccountModel.owner_user_id == self.owner_user_id)
            .with_for_update()
        )
        if account is None:
            raise RuntimeError("storage account ledger row is unavailable")
        account_before_total = (
            int(account.core_used_bytes)
            + int(account.files_used_bytes)
            + int(account.core_reserved_bytes)
            + int(account.files_reserved_bytes)
        )
        global_before = await self.db.scalar(
            select(
                func.coalesce(
                    func.sum(
                        StorageAccountModel.core_used_bytes
                        + StorageAccountModel.files_used_bytes
                        + StorageAccountModel.core_reserved_bytes
                        + StorageAccountModel.files_reserved_bytes
                    ),
                    0,
                )
            )
        )
        if reconcile:
            final_core, final_files = await _async_reconcile(self.db, self.owner_user_id)
        else:
            actual = max(0, int(actual_bytes if actual_bytes is not None else row.amount_bytes))
            final_core = int(account.core_used_bytes) + (actual if reservation.bucket is StorageBucket.CORE else 0)
            final_files = int(account.files_used_bytes) + (actual if reservation.bucket is StorageBucket.FILES else 0)
        remaining_reserved = (
            int(account.core_reserved_bytes) + int(account.files_reserved_bytes) - int(row.amount_bytes)
        )
        final_global = int(global_before or 0) - account_before_total + final_core + final_files + max(0, remaining_reserved)
        validate_final_usage(
            core_used_bytes=final_core,
            files_used_bytes=final_files,
            core_quota_bytes=self.policy.core_quota_bytes,
            files_quota_bytes=self.policy.files_quota_bytes,
            global_used_bytes=final_global,
            global_limit_bytes=settings.NLP_AGENT_STORAGE_GLOBAL_DATA_LIMIT_BYTES,
        )
        account.core_used_bytes = final_core
        account.files_used_bytes = final_files
        account.last_reconciled_at = func.utc_timestamp(6)
        if reservation.bucket is StorageBucket.CORE:
            account.core_reserved_bytes = max(0, int(account.core_reserved_bytes) - int(row.amount_bytes))
        else:
            account.files_reserved_bytes = max(0, int(account.files_reserved_bytes) - int(row.amount_bytes))
        row.status = "committed"
        row.finalized_at = func.utc_timestamp(6)
        await self.db.flush()

    async def release(self, reservation: QuotaReservation | None) -> None:
        if reservation is None:
            return
        row = await self.db.scalar(
            select(StorageReservationModel)
            .where(StorageReservationModel.id == reservation.id, StorageReservationModel.owner_user_id == self.owner_user_id)
            .with_for_update()
        )
        if row is None or row.status != "reserved":
            return
        account = await self.db.scalar(
            select(StorageAccountModel)
            .where(StorageAccountModel.owner_user_id == self.owner_user_id)
            .with_for_update()
        )
        if account is None:
            return
        if reservation.bucket is StorageBucket.CORE:
            account.core_reserved_bytes = max(0, int(account.core_reserved_bytes) - int(row.amount_bytes))
        else:
            account.files_reserved_bytes = max(0, int(account.files_reserved_bytes) - int(row.amount_bytes))
        row.status = "released"
        row.finalized_at = func.utc_timestamp(6)
        await self.db.flush()

    async def reconcile(self) -> None:
        account = await self._locked_account()
        account.core_used_bytes, account.files_used_bytes = await _async_reconcile(self.db, self.owner_user_id)
        await self.db.flush()


class SyncStorageQuota:
    """Synchronous adapter for the worker/gateway repositories."""

    def __init__(self, connection, *, owner_user_id: str, roles: set[str] | frozenset[str] | None = None) -> None:
        self.connection = connection
        self.owner_user_id = owner_user_id
        if roles is None:
            rows = connection.execute(
                text(
                    "SELECT r.code FROM nlp_user_roles ur JOIN nlp_roles r ON r.id=ur.role_id "
                    "WHERE ur.user_id=:user_id AND r.status='active' "
                    "AND (ur.expires_at IS NULL OR ur.expires_at>UTC_TIMESTAMP())"
                ),
                {"user_id": owner_user_id},
            ).all()
            roles = {str(row[0]) for row in rows}
        self.policy = policy_for_roles(roles)

    def _account(self):
        self.connection.execute(
            text(
                "INSERT INTO nlp_storage_accounts(id,owner_user_id) VALUES(UUID(),:owner) "
                "ON DUPLICATE KEY UPDATE owner_user_id=VALUES(owner_user_id)"
            ),
            {"owner": self.owner_user_id},
        )
        row = self.connection.execute(
            text("SELECT * FROM nlp_storage_accounts WHERE owner_user_id=:owner FOR UPDATE"),
            {"owner": self.owner_user_id},
        ).mappings().one()
        if row["last_reconciled_at"] is None:
            core, files = self._reconcile()
            self.connection.execute(
                text(
                    "UPDATE nlp_storage_accounts SET core_used_bytes=:core,files_used_bytes=:files,"
                    "last_reconciled_at=UTC_TIMESTAMP(6) WHERE owner_user_id=:owner"
                ),
                {"core": core, "files": files, "owner": self.owner_user_id},
            )
            row = self.connection.execute(
                text("SELECT * FROM nlp_storage_accounts WHERE owner_user_id=:owner FOR UPDATE"),
                {"owner": self.owner_user_id},
            ).mappings().one()
        self.policy = policy_with_overrides(
            self.policy,
            {
                "core_quota_bytes": row["core_quota_override_bytes"],
                "files_quota_bytes": row["files_quota_override_bytes"],
                "max_file_bytes": row["max_file_override_bytes"],
                "max_items": row["max_items_override"],
            },
        )
        return row

    def _sum(self, expression: str, from_sql: str, where_sql: str = "") -> int:
        value = self.connection.execute(text(f"SELECT COALESCE(SUM({expression}),0) FROM {from_sql} {where_sql}"), {"owner": self.owner_user_id}).scalar()
        return int(value or 0)

    def _reconcile(self) -> tuple[int, int]:
        core = self._sum("OCTET_LENGTH(COALESCE(title,''))+OCTET_LENGTH(id)", "nlp_conversations", "WHERE owner_user_id=:owner")
        core += self._sum("OCTET_LENGTH(COALESCE(input_text,''))+OCTET_LENGTH(COALESCE(result_text,''))+OCTET_LENGTH(COALESCE(error_message,''))+OCTET_LENGTH(CAST(COALESCE(learning_state_json,'{}') AS CHAR))", "nlp_turns", "WHERE user_id=:owner")
        core += self._sum("OCTET_LENGTH(COALESCE(m.content,''))", "nlp_conversation_messages m JOIN nlp_conversations c ON c.id=m.conversation_id", "WHERE c.owner_user_id=:owner")
        core += self._sum("OCTET_LENGTH(CAST(COALESCE(e.payload_json,'{}') AS CHAR))", "nlp_turn_events e JOIN nlp_turns t ON t.id=e.turn_id", "WHERE t.user_id=:owner")
        core += self._sum("OCTET_LENGTH(CAST(COALESCE(t.content_json,'{}') AS CHAR))+OCTET_LENGTH(CAST(COALESCE(t.tool_json,'{}') AS CHAR))+OCTET_LENGTH(CAST(COALESCE(t.usage_json,'{}') AS CHAR))", "nlp_conversation_transcripts t JOIN nlp_conversations c ON c.id=t.session_id", "WHERE c.owner_user_id=:owner")
        core += self._sum("OCTET_LENGTH(CAST(COALESCE(content_json,'{}') AS CHAR))", "nlp_memory_documents", "WHERE user_id=:owner")
        core += self._sum("OCTET_LENGTH(CAST(COALESCE(payload_json,'{}') AS CHAR))", "nlp_memory_archives", "WHERE user_id=:owner")
        core += self._sum("OCTET_LENGTH(CAST(COALESCE(checkpoint_json,'{}') AS CHAR))+OCTET_LENGTH(CAST(COALESCE(metadata_json,'{}') AS CHAR))", "nlp_agent_checkpoints", "WHERE owner_user_id=:owner")
        core += self._sum("OCTET_LENGTH(checkpoint_blob)+OCTET_LENGTH(metadata_blob)", "nlp_langgraph_checkpoints", "WHERE owner_user_id=:owner")
        core += self._sum("OCTET_LENGTH(value_blob)", "nlp_langgraph_checkpoint_blobs", "WHERE owner_user_id=:owner")
        core += self._sum("OCTET_LENGTH(value_blob)", "nlp_langgraph_checkpoint_writes", "WHERE owner_user_id=:owner")
        core += self._sum(
            "OCTET_LENGTH(CAST(COALESCE(tc.request_json,'') AS CHAR))"
            "+OCTET_LENGTH(CAST(COALESCE(tc.result_json,'') AS CHAR))",
            "nlp_tool_calls tc JOIN nlp_turns t ON t.id=tc.turn_id",
            "WHERE t.user_id=:owner",
        )
        core += _uploaded_image_bytes(self.owner_user_id)
        files = self.connection.execute(
            text("SELECT COALESCE(SUM(size_bytes),0) FROM nlp_user_files WHERE owner_user_id=:owner AND kind='file' AND status IN ('active','trashed')"),
            {"owner": self.owner_user_id},
        ).scalar()
        return core, int(files or 0)

    def reserve(self, bucket: StorageBucket, amount_bytes: int, *, resource_type: str, resource_key: str | None = None) -> QuotaReservation | None:
        amount = max(0, int(amount_bytes))
        if amount == 0:
            return None
        account = self._account()
        used = int(account["core_used_bytes"] if bucket is StorageBucket.CORE else account["files_used_bytes"])
        reserved = int(account["core_reserved_bytes"] if bucket is StorageBucket.CORE else account["files_reserved_bytes"])
        quota = self.policy.quota_for(bucket)
        if not fits_quota(used, reserved, amount, quota):
            raise StorageQuotaExceeded(f"{bucket.value} storage quota exceeded: {used + reserved + amount} > {quota} bytes")
        global_bytes = self.connection.execute(
            text(
                "SELECT COALESCE(SUM(core_used_bytes+files_used_bytes+"
                "core_reserved_bytes+files_reserved_bytes),0) FROM nlp_storage_accounts"
            )
        ).scalar()
        if int(global_bytes or 0) + amount > settings.NLP_AGENT_STORAGE_GLOBAL_DATA_LIMIT_BYTES:
            raise StorageQuotaExceeded("服务器账户数据池已达到系统上限")
        if not _disk_has_floor(amount):
            raise StorageQuotaExceeded("服务器需要保留的系统空间不足")
        reservation = QuotaReservation(str(uuid.uuid4()), self.owner_user_id, bucket, amount, resource_type)
        self.connection.execute(
            text("INSERT INTO nlp_storage_reservations(id,owner_user_id,bucket,amount_bytes,resource_type,resource_key,status) VALUES(:id,:owner,:bucket,:amount,:type,:key,'reserved')"),
            {"id": reservation.id, "owner": self.owner_user_id, "bucket": bucket.value, "amount": amount, "type": resource_type, "key": resource_key},
        )
        column = "core_reserved_bytes" if bucket is StorageBucket.CORE else "files_reserved_bytes"
        self.connection.execute(text(f"UPDATE nlp_storage_accounts SET {column}={column}+:amount WHERE owner_user_id=:owner"), {"amount": amount, "owner": self.owner_user_id})
        return reservation

    def finalize(self, reservation: QuotaReservation | None, *, actual_bytes: int | None = None, reconcile: bool = True) -> None:
        if reservation is None:
            return
        row = self.connection.execute(text("SELECT * FROM nlp_storage_reservations WHERE id=:id AND owner_user_id=:owner FOR UPDATE"), {"id": reservation.id, "owner": self.owner_user_id}).mappings().first()
        if row is None or row["status"] != "reserved":
            return
        account = self._account()
        account_before_total = (
            int(account["core_used_bytes"])
            + int(account["files_used_bytes"])
            + int(account["core_reserved_bytes"])
            + int(account["files_reserved_bytes"])
        )
        global_before = self.connection.execute(
            text(
                "SELECT COALESCE(SUM(core_used_bytes+files_used_bytes+"
                "core_reserved_bytes+files_reserved_bytes),0) FROM nlp_storage_accounts"
            )
        ).scalar()
        if reconcile:
            core, files = self._reconcile()
        else:
            actual = max(0, int(actual_bytes if actual_bytes is not None else row["amount_bytes"]))
            core = int(account["core_used_bytes"]) + (actual if reservation.bucket is StorageBucket.CORE else 0)
            files = int(account["files_used_bytes"]) + (actual if reservation.bucket is StorageBucket.FILES else 0)
        remaining_reserved = int(account["core_reserved_bytes"]) + int(account["files_reserved_bytes"]) - int(row["amount_bytes"])
        final_global = int(global_before or 0) - account_before_total + int(core) + int(files) + max(0, remaining_reserved)
        validate_final_usage(
            core_used_bytes=int(core),
            files_used_bytes=int(files),
            core_quota_bytes=self.policy.core_quota_bytes,
            files_quota_bytes=self.policy.files_quota_bytes,
            global_used_bytes=final_global,
            global_limit_bytes=settings.NLP_AGENT_STORAGE_GLOBAL_DATA_LIMIT_BYTES,
        )
        self.connection.execute(
            text("UPDATE nlp_storage_accounts SET core_used_bytes=:core,files_used_bytes=:files,last_reconciled_at=UTC_TIMESTAMP(6) WHERE owner_user_id=:owner"),
            {"core": core, "files": files, "owner": self.owner_user_id},
        )
        reserved_column = "core_reserved_bytes" if reservation.bucket is StorageBucket.CORE else "files_reserved_bytes"
        self.connection.execute(text(f"UPDATE nlp_storage_accounts SET {reserved_column}=GREATEST(0,{reserved_column}-:amount) WHERE owner_user_id=:owner"), {"amount": int(row["amount_bytes"]), "owner": self.owner_user_id})
        self.connection.execute(text("UPDATE nlp_storage_reservations SET status='committed',finalized_at=UTC_TIMESTAMP(6) WHERE id=:id"), {"id": reservation.id})

    def reconcile(self) -> None:
        self._account()
        core, files = self._reconcile()
        self.connection.execute(
            text("UPDATE nlp_storage_accounts SET core_used_bytes=:core,files_used_bytes=:files,last_reconciled_at=UTC_TIMESTAMP(6) WHERE owner_user_id=:owner"),
            {"core": core, "files": files, "owner": self.owner_user_id},
        )

    def release(self, reservation: QuotaReservation | None) -> None:
        if reservation is None:
            return
        row = self.connection.execute(text("SELECT * FROM nlp_storage_reservations WHERE id=:id AND owner_user_id=:owner FOR UPDATE"), {"id": reservation.id, "owner": self.owner_user_id}).mappings().first()
        if row is None or row["status"] != "reserved":
            return
        reserved_column = "core_reserved_bytes" if reservation.bucket is StorageBucket.CORE else "files_reserved_bytes"
        self.connection.execute(text(f"UPDATE nlp_storage_accounts SET {reserved_column}=GREATEST(0,{reserved_column}-:amount) WHERE owner_user_id=:owner"), {"amount": int(row["amount_bytes"]), "owner": self.owner_user_id})
        self.connection.execute(text("UPDATE nlp_storage_reservations SET status='released',finalized_at=UTC_TIMESTAMP(6) WHERE id=:id"), {"id": reservation.id})


def quota_error_code(error: StorageQuotaExceeded) -> str:
    """Stable transport code for websocket and HTTP adapters."""

    return "storage_quota_exceeded"
