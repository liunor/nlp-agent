"""Consent-based, offline-capable account file transfers."""

from __future__ import annotations

import hashlib
import shutil
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.identity import AuthenticatedPrincipal
from server.infrastructure.mysql.models import (
    FileTransferModel,
    RoleModel,
    StorageAccountModel,
    StorageReservationModel,
    UserFileModel,
    UserModel,
    UserRoleModel,
    WorkspaceMemberModel,
)
from server.user.identity import normalize_public_identity_id

from .policy import StorageBucket, fits_item_quota, fits_quota
from .quota import AsyncStorageQuota, QuotaReservation, StorageQuotaExceeded
from .service import StorageError, StorageScope, StorageValidationError, storage_path_for, storage_root
from .transfer_domain import InvalidTransferTransition, next_available_file_name, transition_transfer


TRANSFER_TTL_DAYS = 7
MAX_PENDING_FROM_ONE_SENDER = 50


class TransferNotFound(StorageError):
    pass


class TransferConflict(StorageError):
    pass


class FileTransferService:
    def __init__(self, db: AsyncSession, principal: AuthenticatedPrincipal, workspace_id: str | None = None):
        self.db = db
        self.principal = principal
        available = sorted(value for value in principal.workspace_ids if value != "*")
        self.workspace_id = workspace_id or (available[0] if available else "default")
        if self.workspace_id not in principal.workspace_ids and "*" not in principal.workspace_ids:
            raise StorageValidationError("无权访问该工作空间")

    async def lookup_recipient(self, identity_id: str) -> dict:
        normalized = normalize_public_identity_id(identity_id)
        user = await self.db.scalar(
            select(UserModel).where(
                UserModel.identity_id == normalized,
                UserModel.status == "active",
                UserModel.deleted_at.is_(None),
                UserModel.id != self.principal.user_id,
            )
        )
        if user is None:
            raise TransferNotFound("未找到该身份 ID 对应的有效用户")
        return {"identity_id": user.identity_id, "display_name": user.display_name}

    async def _recipient(self, identity_id: str) -> UserModel:
        normalized = normalize_public_identity_id(identity_id)
        user = await self.db.scalar(
            select(UserModel).where(
                UserModel.identity_id == normalized,
                UserModel.status == "active",
                UserModel.deleted_at.is_(None),
            )
        )
        if user is None:
            raise TransferNotFound("未找到该身份 ID 对应的有效用户")
        if user.id == self.principal.user_id:
            raise StorageValidationError("不能给自己发送文件")
        return user

    async def _source(self, file_id: str) -> UserFileModel:
        item = await self.db.scalar(
            select(UserFileModel).where(
                UserFileModel.id == file_id,
                UserFileModel.owner_user_id == self.principal.user_id,
                UserFileModel.workspace_id == self.workspace_id,
                UserFileModel.kind == "file",
                UserFileModel.status == "active",
            )
        )
        if item is None:
            raise TransferNotFound("待发送文件不存在")
        return item

    async def _recipient_workspace(self, user_id: str) -> str:
        workspace_id = await self.db.scalar(
            select(WorkspaceMemberModel.workspace_id)
            .where(WorkspaceMemberModel.user_id == user_id, WorkspaceMemberModel.status == "active")
            .order_by((WorkspaceMemberModel.member_type == "owner").desc(), WorkspaceMemberModel.created_at)
            .limit(1)
        )
        if workspace_id is None:
            raise StorageValidationError("接收方当前没有可用的文件空间")
        return str(workspace_id)

    async def _recipient_quota(self, user_id: str) -> AsyncStorageQuota:
        roles = set(
            (await self.db.scalars(
                select(RoleModel.code)
                .join(UserRoleModel, UserRoleModel.role_id == RoleModel.id)
                .where(
                    UserRoleModel.user_id == user_id,
                    RoleModel.status == "active",
                    (UserRoleModel.expires_at.is_(None) | (UserRoleModel.expires_at > func.utc_timestamp(6))),
                )
            )).all()
        )
        return AsyncStorageQuota(self.db, owner_user_id=user_id, roles=roles)

    async def preflight(self, source_file_id: str, recipient_identity_id: str) -> dict:
        source = await self._source(source_file_id)
        recipient = await self._recipient(recipient_identity_id)
        quota = await self._recipient_quota(recipient.id)
        await quota.reconcile()
        account = await self.db.scalar(select(StorageAccountModel).where(StorageAccountModel.owner_user_id == recipient.id))
        if account is None:
            return {"can_receive": False, "reason": "对方当前无法接收文件"}
        active_items = int(await self.db.scalar(select(func.count()).select_from(UserFileModel).where(UserFileModel.owner_user_id == recipient.id, UserFileModel.kind == "file", UserFileModel.status == "active")) or 0)
        if source.size_bytes > quota.policy.max_file_bytes:
            return {"can_receive": False, "reason": "文件超过对方的单文件大小限制"}
        if not fits_quota(account.files_used_bytes, account.files_reserved_bytes, source.size_bytes, quota.policy.files_quota_bytes):
            return {"can_receive": False, "reason": "对方存储空间不足"}
        if not fits_item_quota(active_items=active_items, reserved_items=account.files_reserved_items, incoming_items=1, max_items=quota.policy.max_items):
            return {"can_receive": False, "reason": "对方文件数量已达到上限"}
        return {"can_receive": True, "reason": None}

    async def create(self, source_file_id: str, recipient_identity_id: str, idempotency_key: str | None = None) -> dict:
        key = idempotency_key or str(uuid.uuid4())
        existing = await self.db.scalar(select(FileTransferModel).where(FileTransferModel.sender_user_id == self.principal.user_id, FileTransferModel.idempotency_key == key))
        if existing is not None:
            return await self.serialize(existing)
        source = await self._source(source_file_id)
        recipient = await self._recipient(recipient_identity_id)
        pending = int(await self.db.scalar(select(func.count()).select_from(FileTransferModel).where(FileTransferModel.sender_user_id == self.principal.user_id, FileTransferModel.recipient_user_id == recipient.id, FileTransferModel.status == "pending")) or 0)
        if pending >= MAX_PENDING_FROM_ONE_SENDER:
            raise TransferConflict("给该用户的待处理发送请求过多，请等待对方处理")
        recipient_workspace_id = await self._recipient_workspace(recipient.id)
        quota = await self._recipient_quota(recipient.id)
        transfer_id = str(uuid.uuid4())
        reservation = await quota.reserve(StorageBucket.FILES, int(source.size_bytes), resource_type="file_transfer", resource_key=transfer_id, amount_items=1)
        if reservation is None:
            raise StorageError("无法建立接收空间预留")
        source_scope = StorageScope(self.principal.user_id, self.workspace_id, quota.policy)
        source_path = storage_path_for(source_scope, source.storage_key)
        staging_key = f".transfers/{transfer_id}"
        staging_path = storage_root() / staging_key
        staging_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(source_path, staging_path)
            digest = hashlib.sha256(staging_path.read_bytes()).hexdigest()
            transfer = FileTransferModel(
                id=transfer_id,
                sender_user_id=self.principal.user_id,
                recipient_user_id=recipient.id,
                source_workspace_id=self.workspace_id,
                recipient_workspace_id=recipient_workspace_id,
                source_file_id=source.id,
                original_name=source.display_name,
                mime_type=source.mime_type,
                size_bytes=source.size_bytes,
                sha256=digest,
                staging_key=staging_key,
                quota_reservation_id=reservation.id,
                status="pending",
                expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=TRANSFER_TTL_DAYS),
                idempotency_key=key,
            )
            try:
                async with self.db.begin_nested():
                    self.db.add(transfer)
                    await self.db.flush()
            except IntegrityError as error:
                await quota.release(reservation)
                staging_path.unlink(missing_ok=True)
                existing = await self.db.scalar(
                    select(FileTransferModel).where(
                        FileTransferModel.sender_user_id == self.principal.user_id,
                        FileTransferModel.idempotency_key == key,
                    ).with_for_update()
                )
                if existing is None:
                    raise error
                return await self.serialize(existing)
            await self.db.refresh(transfer)
            return await self.serialize(transfer)
        except Exception:
            await quota.release(reservation)
            staging_path.unlink(missing_ok=True)
            raise

    def _reservation(self, row: StorageReservationModel) -> QuotaReservation:
        return QuotaReservation(row.id, row.owner_user_id, StorageBucket(row.bucket), int(row.amount_bytes), row.resource_type, int(row.amount_items))

    async def _locked(self, transfer_id: str) -> FileTransferModel:
        transfer = await self.db.scalar(select(FileTransferModel).where(FileTransferModel.id == transfer_id).with_for_update())
        if transfer is None:
            raise TransferNotFound("文件发送请求不存在")
        return transfer

    async def accept(self, transfer_id: str) -> dict:
        transfer = await self._locked(transfer_id)
        if transfer.recipient_user_id != self.principal.user_id:
            raise TransferNotFound("文件发送请求不存在")
        if transfer.status == "accepted":
            return await self.serialize(transfer)
        if transfer.expires_at <= datetime.now(timezone.utc).replace(tzinfo=None):
            return await self._resolve_without_copy(transfer, "expire", "system")
        try:
            next_status = transition_transfer(transfer.status, "accept", actor="recipient")
        except InvalidTransferTransition as error:
            raise TransferConflict(str(error)) from error
        # Serialize accepts for one recipient so concurrent same-name files
        # observe the name chosen by the request that commits first.
        await self.db.scalar(
            select(UserModel.id)
            .where(UserModel.id == transfer.recipient_user_id)
            .with_for_update()
        )
        names = set((await self.db.scalars(select(UserFileModel.display_name).where(UserFileModel.owner_user_id == transfer.recipient_user_id, UserFileModel.workspace_id == transfer.recipient_workspace_id, UserFileModel.parent_id.is_(None), UserFileModel.status == "active"))).all())
        name = next_available_file_name(transfer.original_name, names)
        file_id = str(uuid.uuid4())
        storage_key = f"{transfer.recipient_workspace_id}/{transfer.recipient_user_id}/{file_id}"
        target = storage_root() / storage_key
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_name(f".tmp-{file_id}")
        staging_path = storage_root() / transfer.staging_key
        if not staging_path.is_file() or hashlib.sha256(staging_path.read_bytes()).hexdigest() != transfer.sha256:
            raise StorageError("待接收文件校验失败，请联系发送方重新发送")
        shutil.copy2(staging_path, temp)
        temp.replace(target)
        item = UserFileModel(id=file_id, owner_user_id=transfer.recipient_user_id, workspace_id=transfer.recipient_workspace_id, parent_id=None, kind="file", display_name=name, storage_key=storage_key, mime_type=transfer.mime_type, size_bytes=transfer.size_bytes, sha256=transfer.sha256)
        self.db.add(item)
        try:
            await self.db.flush()
            reservation_row = await self.db.scalar(select(StorageReservationModel).where(StorageReservationModel.id == transfer.quota_reservation_id).with_for_update())
            if reservation_row is None:
                raise StorageError("接收空间预留不存在")
            quota = await self._recipient_quota(transfer.recipient_user_id)
            await quota.finalize(self._reservation(reservation_row), reconcile=True)
            transfer.status = next_status
            transfer.accepted_file_id = file_id
            transfer.responded_at = func.utc_timestamp(6)
            await self.db.flush()
            return await self.serialize(transfer)
        except Exception:
            target.unlink(missing_ok=True)
            temp.unlink(missing_ok=True)
            raise

    async def _resolve_without_copy(self, transfer: FileTransferModel, action: str, actor: str) -> dict:
        try:
            transfer.status = transition_transfer(transfer.status, action, actor=actor)
        except InvalidTransferTransition as error:
            raise TransferConflict(str(error)) from error
        reservation_row = await self.db.scalar(select(StorageReservationModel).where(StorageReservationModel.id == transfer.quota_reservation_id).with_for_update())
        if reservation_row is not None:
            quota = await self._recipient_quota(transfer.recipient_user_id)
            await quota.release(self._reservation(reservation_row))
        transfer.responded_at = func.utc_timestamp(6)
        await self.db.flush()
        return await self.serialize(transfer)

    async def reject(self, transfer_id: str) -> dict:
        transfer = await self._locked(transfer_id)
        if transfer.recipient_user_id != self.principal.user_id:
            raise TransferNotFound("文件发送请求不存在")
        if transfer.status == "rejected":
            return await self.serialize(transfer)
        return await self._resolve_without_copy(transfer, "reject", "recipient")

    async def cancel(self, transfer_id: str) -> dict:
        transfer = await self._locked(transfer_id)
        if transfer.sender_user_id != self.principal.user_id:
            raise TransferNotFound("文件发送请求不存在")
        if transfer.status == "cancelled":
            return await self.serialize(transfer)
        return await self._resolve_without_copy(transfer, "cancel", "sender")

    async def list(self, box: str) -> list[dict]:
        if box not in {"incoming", "outgoing"}:
            raise StorageValidationError("消息箱类型不正确")
        field = FileTransferModel.recipient_user_id if box == "incoming" else FileTransferModel.sender_user_id
        rows = list((await self.db.scalars(select(FileTransferModel).where(field == self.principal.user_id).order_by(FileTransferModel.created_at.desc()).limit(200))).all())
        return [await self.serialize(row) for row in rows]

    async def summary(self) -> dict:
        count = int(await self.db.scalar(select(func.count()).select_from(FileTransferModel).where(FileTransferModel.recipient_user_id == self.principal.user_id, FileTransferModel.status == "pending", FileTransferModel.expires_at > func.utc_timestamp(6))) or 0)
        notification_version = int(
            await self.db.scalar(
                select(func.count())
                .select_from(FileTransferModel)
                .where(FileTransferModel.recipient_user_id == self.principal.user_id)
            )
            or 0
        )
        return {"pending_count": count, "notification_version": notification_version}

    async def _expire_pending(self, *, batch_size: int = 200) -> int:
        transfers = list(
            (
                await self.db.scalars(
                    select(FileTransferModel)
                    .where(
                        FileTransferModel.status == "pending",
                        FileTransferModel.expires_at <= func.utc_timestamp(6),
                    )
                    .order_by(FileTransferModel.expires_at, FileTransferModel.id)
                    .limit(max(1, int(batch_size)))
                    .with_for_update(skip_locked=True)
                )
            ).all()
        )
        expired = 0
        for transfer in transfers:
            if transfer.status == "pending" and transfer.expires_at <= datetime.now(timezone.utc).replace(tzinfo=None):
                await self._resolve_without_copy(transfer, "expire", "system")
                expired += 1
        return expired

    async def _cleanup_resolved_staging(self, *, batch_size: int = 200) -> int:
        """Delete snapshots only after their resolved database state is observable."""
        transfers = list(
            (
                await self.db.scalars(
                    select(FileTransferModel)
                    .where(
                        FileTransferModel.status != "pending",
                        FileTransferModel.staging_deleted_at.is_(None),
                    )
                    .order_by(FileTransferModel.responded_at)
                    .limit(max(1, int(batch_size)))
                    .with_for_update(skip_locked=True)
                )
            ).all()
        )
        for transfer in transfers:
            (storage_root() / transfer.staging_key).unlink(missing_ok=True)
            transfer.staging_deleted_at = func.utc_timestamp(6)
        if transfers:
            await self.db.flush()
        return len(transfers)

    async def serialize(self, transfer: FileTransferModel) -> dict:
        sender = await self.db.scalar(select(UserModel).where(UserModel.id == transfer.sender_user_id))
        recipient = await self.db.scalar(select(UserModel).where(UserModel.id == transfer.recipient_user_id))
        return {
            "id": transfer.id,
            "status": transfer.status,
            "file_name": transfer.original_name,
            "size_bytes": int(transfer.size_bytes),
            "sender": {"identity_id": sender.identity_id, "display_name": sender.display_name} if sender else None,
            "recipient": {"identity_id": recipient.identity_id, "display_name": recipient.display_name} if recipient else None,
            "accepted_file_id": transfer.accepted_file_id,
            "created_at": transfer.created_at.isoformat() if transfer.created_at else None,
            "expires_at": transfer.expires_at.isoformat(),
        }


async def maintain_file_transfers(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    batch_size: int = 200,
) -> dict[str, int]:
    """Expire requests and clean snapshots outside user GET transactions."""

    principal = AuthenticatedPrincipal(
        user_id="file-transfer-maintenance",
        workspace_ids=frozenset({"*"}),
        roles=frozenset({"admin"}),
    )
    async with session_factory() as db:
        async with db.begin():
            expired = await FileTransferService(db, principal)._expire_pending(batch_size=batch_size)
    async with session_factory() as db:
        async with db.begin():
            cleaned = await FileTransferService(db, principal)._cleanup_resolved_staging(batch_size=batch_size)
    return {"expired": expired, "cleaned": cleaned}
