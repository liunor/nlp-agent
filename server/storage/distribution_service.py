"""Teacher file distribution service for broadcasting and targeted file sending."""

from __future__ import annotations

from contextlib import asynccontextmanager
import hashlib
import json
import logging
import shutil
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.exc import DBAPIError, OperationalError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from core.identity import AuthenticatedPrincipal
from server.infrastructure.mysql.models import (
    ClassroomMemberModel,
    ClassroomModel,
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
from .quota import AsyncStorageQuota
from .service import (
    StorageError,
    StorageScope,
    StorageValidationError,
    storage_path_for,
    storage_root,
    utc_isoformat,
)
from .transfer_domain import next_available_file_name

logger = logging.getLogger(__name__)

@asynccontextmanager
async def _safe_nested(session: AsyncSession):
    """Safely enter a nested transaction savepoint if supported by the session."""
    if hasattr(session, "begin_nested"):
        try:
            nested = session.begin_nested()
            if hasattr(nested, "__aenter__"):
                async with nested:
                    yield
                return
        except (TypeError, AttributeError):
            pass
    yield


TRANSFER_TTL_DAYS = 7


class DistributionError(StorageError):
    pass


class DistributionPermissionError(DistributionError):
    pass


class DistributionTargetError(DistributionError):
    pass


class FileDistributionService:
    def __init__(
        self,
        db: AsyncSession,
        principal: AuthenticatedPrincipal,
        workspace_id: str | None = None,
    ):
        self.db = db
        self.principal = principal
        available = sorted(value for value in principal.workspace_ids if value != "*")
        self.workspace_id = workspace_id or (available[0] if available else "default")
        self._ensure_teacher_role()

    def _ensure_teacher_role(self) -> None:
        teacher_roles = {"teacher", "developer", "admin"}
        if not any(role in teacher_roles for role in self.principal.roles):
            raise DistributionPermissionError("只有教师或管理员用户才能使用文件分发功能")

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
            # Fallback: check across workspaces owned by the teacher
            item = await self.db.scalar(
                select(UserFileModel).where(
                    UserFileModel.id == file_id,
                    UserFileModel.owner_user_id == self.principal.user_id,
                    UserFileModel.kind == "file",
                    UserFileModel.status == "active",
                )
            )
        if item is None:
            raise DistributionError("待发送的文件不存在或已被删除")
        return item

    async def _recipient_workspace(self, user_id: str) -> str:
        workspace_id = await self.db.scalar(
            select(WorkspaceMemberModel.workspace_id)
            .where(
                WorkspaceMemberModel.user_id == user_id,
                WorkspaceMemberModel.status == "active",
            )
            .order_by(
                (WorkspaceMemberModel.member_type == "owner").desc(),
                WorkspaceMemberModel.created_at,
            )
            .limit(1)
        )
        return str(workspace_id) if workspace_id else "default"

    async def _recipient_quota(self, user_id: str) -> AsyncStorageQuota:
        roles = set(
            (
                await self.db.scalars(
                    select(RoleModel.code)
                    .join(UserRoleModel, UserRoleModel.role_id == RoleModel.id)
                    .where(
                        UserRoleModel.user_id == user_id,
                        RoleModel.status == "active",
                        (
                            UserRoleModel.expires_at.is_(None)
                            | (UserRoleModel.expires_at > func.utc_timestamp(6))
                        ),
                    )
                )
            ).all()
        )
        return AsyncStorageQuota(self.db, owner_user_id=user_id, roles=roles)

    async def _bump_notification_version(self, recipient_user_id: str) -> None:
        account = await self.db.scalar(
            select(StorageAccountModel)
            .where(StorageAccountModel.owner_user_id == recipient_user_id)
            .with_for_update()
        )
        if account is not None:
            account.file_transfer_notification_version = (
                int(account.file_transfer_notification_version or 0) + 1
            )

    async def get_distribution_targets(self) -> dict[str, Any]:
        """Return available target options for the teacher UI."""
        # 1. Classrooms
        classrooms = list(
            (
                await self.db.scalars(
                    select(ClassroomModel).where(ClassroomModel.status == "active")
                )
            ).all()
        )
        classroom_items = []
        for c in classrooms:
            member_count = int(
                await self.db.scalar(
                    select(func.count())
                    .select_from(ClassroomMemberModel)
                    .where(
                        ClassroomMemberModel.classroom_id == c.id,
                        ClassroomMemberModel.status == "active",
                        ClassroomMemberModel.user_id != self.principal.user_id,
                    )
                )
                or 0
            )
            classroom_items.append(
                {
                    "id": c.id,
                    "name": c.name,
                    "workspace_id": c.workspace_id,
                    "student_count": member_count,
                }
            )

        # 2. Roles
        roles = [
            {"code": "student", "name": "全体学生", "description": "系统内所有已注册学生用户"},
            {"code": "guest", "name": "全体游客", "description": "系统内所有试用/游客用户"},
            {"code": "admin", "name": "全体管理员", "description": "系统管理人员与平台开发者"},
            {"code": "teacher", "name": "全体教师", "description": "系统内所有教学教研人员"},
        ]

        # 3. Overall counts
        student_count = int(
            await self.db.scalar(
                select(func.count(UserModel.id.distinct()))
                .select_from(UserModel)
                .join(UserRoleModel, UserRoleModel.user_id == UserModel.id)
                .join(RoleModel, RoleModel.id == UserRoleModel.role_id)
                .where(
                    RoleModel.code == "student",
                    UserModel.status == "active",
                    UserModel.deleted_at.is_(None),
                    UserModel.id != self.principal.user_id,
                )
            )
            or 0
        )
        guest_count = int(
            await self.db.scalar(
                select(func.count(UserModel.id.distinct()))
                .select_from(UserModel)
                .join(UserRoleModel, UserRoleModel.user_id == UserModel.id)
                .join(RoleModel, RoleModel.id == UserRoleModel.role_id)
                .where(
                    RoleModel.code == "guest",
                    UserModel.status == "active",
                    UserModel.deleted_at.is_(None),
                    UserModel.id != self.principal.user_id,
                )
            )
            or 0
        )
        admin_count = int(
            await self.db.scalar(
                select(func.count(UserModel.id.distinct()))
                .select_from(UserModel)
                .join(UserRoleModel, UserRoleModel.user_id == UserModel.id)
                .join(RoleModel, RoleModel.id == UserRoleModel.role_id)
                .where(
                    RoleModel.code.in_(["developer", "admin"]),
                    UserModel.status == "active",
                    UserModel.deleted_at.is_(None),
                    UserModel.id != self.principal.user_id,
                )
            )
            or 0
        )
        total_user_count = int(
            await self.db.scalar(
                select(func.count())
                .select_from(UserModel)
                .where(
                    UserModel.status == "active",
                    UserModel.deleted_at.is_(None),
                    UserModel.id != self.principal.user_id,
                )
            )
            or 0
        )

        return {
            "summary": {
                "student_count": student_count,
                "guest_count": guest_count,
                "admin_count": admin_count,
                "total_user_count": total_user_count,
            },
            "roles": roles,
            "classrooms": classroom_items,
        }

    async def _resolve_recipients(
        self, target_type: str, target_value: str | None = None
    ) -> tuple[list[UserModel], str]:
        """Resolve recipients and return (user_list, target_label)."""
        target_type_clean = (target_type or "").strip().lower()

        if target_type_clean in {"all_students", "students"}:
            users = list(
                (
                    await self.db.scalars(
                        select(UserModel)
                        .join(UserRoleModel, UserRoleModel.user_id == UserModel.id)
                        .join(RoleModel, RoleModel.id == UserRoleModel.role_id)
                        .where(
                            RoleModel.code == "student",
                            UserModel.status == "active",
                            UserModel.deleted_at.is_(None),
                            UserModel.id != self.principal.user_id,
                        )
                        .distinct()
                    )
                ).all()
            )
            return users, "全体学生"

        elif target_type_clean in {"all_guests", "guests"}:
            users = list(
                (
                    await self.db.scalars(
                        select(UserModel)
                        .join(UserRoleModel, UserRoleModel.user_id == UserModel.id)
                        .join(RoleModel, RoleModel.id == UserRoleModel.role_id)
                        .where(
                            RoleModel.code == "guest",
                            UserModel.status == "active",
                            UserModel.deleted_at.is_(None),
                            UserModel.id != self.principal.user_id,
                        )
                        .distinct()
                    )
                ).all()
            )
            return users, "全体游客"

        elif target_type_clean in {"all_admins", "admins", "developers"}:
            users = list(
                (
                    await self.db.scalars(
                        select(UserModel)
                        .join(UserRoleModel, UserRoleModel.user_id == UserModel.id)
                        .join(RoleModel, RoleModel.id == UserRoleModel.role_id)
                        .where(
                            RoleModel.code.in_(["developer", "admin"]),
                            UserModel.status == "active",
                            UserModel.deleted_at.is_(None),
                            UserModel.id != self.principal.user_id,
                        )
                        .distinct()
                    )
                ).all()
            )
            return users, "全体管理员"

        elif target_type_clean in {"all_teachers", "teachers"}:
            users = list(
                (
                    await self.db.scalars(
                        select(UserModel)
                        .join(UserRoleModel, UserRoleModel.user_id == UserModel.id)
                        .join(RoleModel, RoleModel.id == UserRoleModel.role_id)
                        .where(
                            RoleModel.code == "teacher",
                            UserModel.status == "active",
                            UserModel.deleted_at.is_(None),
                            UserModel.id != self.principal.user_id,
                        )
                        .distinct()
                    )
                ).all()
            )
            return users, "全体教师"

        elif target_type_clean == "all_users":
            users = list(
                (
                    await self.db.scalars(
                        select(UserModel).where(
                            UserModel.status == "active",
                            UserModel.deleted_at.is_(None),
                            UserModel.id != self.principal.user_id,
                        )
                    )
                ).all()
            )
            return users, "全体用户"

        elif target_type_clean == "role":
            role_code = (target_value or "student").strip().lower()
            if role_code in {"admin", "developer"}:
                query_codes = ["developer", "admin"]
                label_name = "全体管理员"
            elif role_code == "guest":
                query_codes = ["guest"]
                label_name = "全体游客"
            elif role_code == "teacher":
                query_codes = ["teacher"]
                label_name = "全体教师"
            elif role_code == "student":
                query_codes = ["student"]
                label_name = "全体学生"
            else:
                query_codes = [role_code]
                label_name = f"角色：{role_code}"

            users = list(
                (
                    await self.db.scalars(
                        select(UserModel)
                        .join(UserRoleModel, UserRoleModel.user_id == UserModel.id)
                        .join(RoleModel, RoleModel.id == UserRoleModel.role_id)
                        .where(
                            RoleModel.code.in_(query_codes),
                            UserModel.status == "active",
                            UserModel.deleted_at.is_(None),
                            UserModel.id != self.principal.user_id,
                        )
                        .distinct()
                    )
                ).all()
            )
            return users, label_name

        elif target_type_clean == "classroom":
            classroom_id = (target_value or "").strip()
            if not classroom_id:
                raise DistributionTargetError("未指定班级")
            classroom = await self.db.scalar(
                select(ClassroomModel).where(ClassroomModel.id == classroom_id)
            )
            classroom_name = classroom.name if classroom else classroom_id
            users = list(
                (
                    await self.db.scalars(
                        select(UserModel)
                        .join(ClassroomMemberModel, ClassroomMemberModel.user_id == UserModel.id)
                        .where(
                            ClassroomMemberModel.classroom_id == classroom_id,
                            ClassroomMemberModel.status == "active",
                            UserModel.status == "active",
                            UserModel.deleted_at.is_(None),
                            UserModel.id != self.principal.user_id,
                        )
                        .distinct()
                    )
                ).all()
            )
            return users, f"班级：{classroom_name}"

        elif target_type_clean == "user":
            user_val = (target_value or "").strip()
            if not user_val:
                raise DistributionTargetError("未指定接收用户")
            normalized = normalize_public_identity_id(user_val)
            user = await self.db.scalar(
                select(UserModel).where(
                    or_(
                        UserModel.identity_id == normalized,
                        UserModel.username == user_val,
                        UserModel.id == user_val,
                    ),
                    UserModel.status == "active",
                    UserModel.deleted_at.is_(None),
                    UserModel.id != self.principal.user_id,
                )
            )
            if user is None:
                raise DistributionTargetError(f"未找到目标用户 '{user_val}'")
            return [user], f"用户：{user.display_name} ({user.identity_id})"

        else:
            raise DistributionTargetError(f"不支持的分发目标类型: {target_type}")

    async def distribute_file(
        self,
        source_file_id: str,
        target_type: str,
        target_value: str | None = None,
        direct_delivery: bool = True,
    ) -> dict[str, Any]:
        """Distribute a file to the chosen recipients."""
        source = await self._source(source_file_id)
        recipients, target_label = await self._resolve_recipients(target_type, target_value)

        if not recipients:
            return {
                "distribution_id": str(uuid.uuid4()),
                "source_file_id": source.id,
                "file_name": source.display_name,
                "size_bytes": source.size_bytes,
                "target_type": target_type,
                "target_value": target_value,
                "target_label": target_label,
                "direct_delivery": direct_delivery,
                "total_targets": 0,
                "success_count": 0,
                "failed_count": 0,
                "failures": [],
                "created_at": datetime.now(timezone.utc).isoformat(),
            }

        distribution_id = str(uuid.uuid4())
        success_count = 0
        failures: list[dict[str, str]] = []

        # Find physical source file
        source_quota = await self._recipient_quota(self.principal.user_id)
        source_scope = StorageScope(self.principal.user_id, self.workspace_id, source_quota.policy)
        source_path = storage_path_for(source_scope, source.storage_key)
        if not source_path.is_file():
            raise DistributionError("源物理文件已丢失，无法发送")
        file_bytes = source_path.read_bytes()
        file_digest = hashlib.sha256(file_bytes).hexdigest()

        for recipient in recipients:
            try:
                async with _safe_nested(self.db):
                    recipient_workspace_id = await self._recipient_workspace(recipient.id)
                    quota = await self._recipient_quota(recipient.id)
                    await quota.reconcile()

                    account = await self.db.scalar(
                        select(StorageAccountModel).where(
                            StorageAccountModel.owner_user_id == recipient.id
                        )
                    )
                    if account is None:
                        failures.append(
                            {
                                "user_id": recipient.id,
                                "display_name": recipient.display_name,
                                "identity_id": recipient.identity_id,
                                "reason": "目标用户未初始化存储账户",
                            }
                        )
                        continue

                    active_items = int(
                        await self.db.scalar(
                            select(func.count())
                            .select_from(UserFileModel)
                            .where(
                                UserFileModel.owner_user_id == recipient.id,
                                UserFileModel.kind == "file",
                                UserFileModel.status == "active",
                            )
                        )
                        or 0
                    )

                    if source.size_bytes > quota.policy.max_file_bytes:
                        failures.append(
                            {
                                "user_id": recipient.id,
                                "display_name": recipient.display_name,
                                "identity_id": recipient.identity_id,
                                "reason": "文件超过接收方的单文件大小限制",
                            }
                        )
                        continue

                    if not fits_quota(
                        account.files_used_bytes,
                        account.files_reserved_bytes,
                        source.size_bytes,
                        quota.policy.files_quota_bytes,
                    ):
                        failures.append(
                            {
                                "user_id": recipient.id,
                                "display_name": recipient.display_name,
                                "identity_id": recipient.identity_id,
                                "reason": "接收方存储空间不足",
                            }
                        )
                        continue

                    if not fits_item_quota(
                        active_items=active_items,
                        reserved_items=account.files_reserved_items,
                        incoming_items=1,
                        max_items=quota.policy.max_items,
                    ):
                        failures.append(
                            {
                                "user_id": recipient.id,
                                "display_name": recipient.display_name,
                                "identity_id": recipient.identity_id,
                                "reason": "接收方文件数量已达到上限",
                            }
                        )
                        continue

                    transfer_id = str(uuid.uuid4())
                    reservation = await quota.reserve(
                        StorageBucket.FILES,
                        int(source.size_bytes),
                        resource_type="file_transfer",
                        resource_key=transfer_id,
                        amount_items=1,
                    )
                    if reservation is None:
                        failures.append(
                            {
                                "user_id": recipient.id,
                                "display_name": recipient.display_name,
                                "identity_id": recipient.identity_id,
                                "reason": "无法建立空间预留",
                            }
                        )
                        continue

                    staging_key = f".transfers/{transfer_id}"
                    staging_path = storage_root() / staging_key
                    staging_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source_path, staging_path)

                    now = datetime.now(timezone.utc).replace(tzinfo=None)

                    if direct_delivery:
                        # Direct delivery: write directly into recipient's files and mark transfer as accepted
                        existing_names = set(
                            (
                                await self.db.scalars(
                                    select(UserFileModel.display_name).where(
                                        UserFileModel.owner_user_id == recipient.id,
                                        UserFileModel.workspace_id == recipient_workspace_id,
                                        UserFileModel.parent_id.is_(None),
                                        UserFileModel.status == "active",
                                    )
                                )
                            ).all()
                        )
                        dest_file_name = next_available_file_name(source.display_name, existing_names)
                        target_file_id = str(uuid.uuid4())
                        target_storage_key = f"{recipient_workspace_id}/{recipient.id}/{target_file_id}"
                        target_path = storage_root() / target_storage_key
                        target_path.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(source_path, target_path)

                        target_file_row = UserFileModel(
                            id=target_file_id,
                            owner_user_id=recipient.id,
                            workspace_id=recipient_workspace_id,
                            parent_id=None,
                            kind="file",
                            display_name=dest_file_name,
                            storage_key=target_storage_key,
                            mime_type=source.mime_type,
                            size_bytes=source.size_bytes,
                            sha256=file_digest,
                        )
                        self.db.add(target_file_row)
                        await self.db.flush()

                        await quota.finalize(
                            self._reservation_object(reservation.id, recipient.id, source.size_bytes),
                            reconcile=True,
                        )

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
                            sha256=file_digest,
                            staging_key=staging_key,
                            quota_reservation_id=reservation.id,
                            status="accepted",
                            accepted_file_id=target_file_id,
                            responded_at=func.utc_timestamp(6),
                            expires_at=now,
                            idempotency_key=f"dist_{distribution_id}_{recipient.id}",
                        )
                    else:
                        # Consent request mode
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
                            sha256=file_digest,
                            staging_key=staging_key,
                            quota_reservation_id=reservation.id,
                            status="pending",
                            expires_at=now + datetime.resolution * (TRANSFER_TTL_DAYS * 86400 * 1_000_000),
                            idempotency_key=f"dist_{distribution_id}_{recipient.id}",
                        )

                    self.db.add(transfer)
                    await self.db.flush()
                    await self._bump_notification_version(recipient.id)
                    success_count += 1

            except Exception as err:
                logger.warning(
                    "Failed to distribute file to user %s: %s", recipient.id, err, exc_info=True
                )
                failures.append(
                    {
                        "user_id": recipient.id,
                        "display_name": recipient.display_name,
                        "identity_id": recipient.identity_id,
                        "reason": str(err),
                    }
                )

        result = {
            "distribution_id": distribution_id,
            "source_file_id": source.id,
            "file_name": source.display_name,
            "size_bytes": int(source.size_bytes),
            "target_type": target_type,
            "target_value": target_value,
            "target_label": target_label,
            "direct_delivery": direct_delivery,
            "total_targets": len(recipients),
            "success_count": success_count,
            "failed_count": len(failures),
            "failures": failures,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        # Try to persist in distribution records
        await self._persist_distribution_record(result)

        return result

    def _reservation_object(self, reservation_id: str, owner_user_id: str, size_bytes: int):
        from server.storage.quota import QuotaReservation
        return QuotaReservation(
            id=reservation_id,
            owner_user_id=owner_user_id,
            bucket=StorageBucket.FILES,
            amount_bytes=int(size_bytes),
            resource_type="file_transfer",
            amount_items=1,
        )

    async def _persist_distribution_record(self, record: dict[str, Any]) -> None:
        """Persist distribution record defensively without breaking the outer transaction."""
        try:
            async with _safe_nested(self.db):
                from server.infrastructure.mysql.models import FileDistributionModel
                model = FileDistributionModel(
                    id=record["distribution_id"],
                    teacher_user_id=self.principal.user_id,
                    source_file_id=record["source_file_id"],
                    file_name=record["file_name"],
                    size_bytes=record["size_bytes"],
                    target_type=record["target_type"],
                    target_value=record["target_value"],
                    target_label=record["target_label"],
                    direct_delivery=record["direct_delivery"],
                    total_count=record["total_targets"],
                    success_count=record["success_count"],
                    failed_count=record["failed_count"],
                    status="completed" if record["failed_count"] == 0 else "partial_failure",
                    details_json=json.dumps(record.get("failures", []), ensure_ascii=False),
                )
                self.db.add(model)
                await self.db.flush()
        except Exception as err:
            logger.info("FileDistributionModel table not ready yet, skipping record table insertion: %s", err)

    async def list_distribution_history(self, limit: int = 50) -> list[dict[str, Any]]:
        """List distribution history for current teacher."""
        try:
            async with _safe_nested(self.db):
                from server.infrastructure.mysql.models import FileDistributionModel
                rows = list(
                    (
                        await self.db.scalars(
                            select(FileDistributionModel)
                            .where(FileDistributionModel.teacher_user_id == self.principal.user_id)
                            .order_by(FileDistributionModel.created_at.desc())
                            .limit(limit)
                        )
                    ).all()
                )
                return [
                    {
                        "id": r.id,
                        "file_name": r.file_name,
                        "size_bytes": int(r.size_bytes),
                        "target_type": r.target_type,
                        "target_value": r.target_value,
                        "target_label": r.target_label,
                        "direct_delivery": r.direct_delivery,
                        "total_count": r.total_count,
                        "success_count": r.success_count,
                        "failed_count": r.failed_count,
                        "status": r.status,
                        "details": json.loads(r.details_json) if r.details_json else [],
                        "created_at": utc_isoformat(r.created_at),
                    }
                    for r in rows
                ]
        except Exception:
            # Fallback to aggregations from FileTransferModel
            transfers = list(
                (
                    await self.db.scalars(
                        select(FileTransferModel)
                        .where(
                            FileTransferModel.sender_user_id == self.principal.user_id,
                            FileTransferModel.idempotency_key.like("dist_%"),
                        )
                        .order_by(FileTransferModel.created_at.desc())
                        .limit(limit * 5)
                    )
                ).all()
            )
            # Group by distribution id extracted from idempotency_key
            groups: dict[str, list[FileTransferModel]] = {}
            for t in transfers:
                parts = t.idempotency_key.split("_")
                dist_id = parts[1] if len(parts) >= 2 else t.id
                groups.setdefault(dist_id, []).append(t)

            history = []
            for dist_id, items in groups.items():
                first = items[0]
                success = sum(1 for item in items if item.status == "accepted")
                history.append(
                    {
                        "id": dist_id,
                        "file_name": first.original_name,
                        "size_bytes": int(first.size_bytes),
                        "target_type": "batch",
                        "target_value": None,
                        "target_label": f"向 {len(items)} 位用户发送",
                        "direct_delivery": first.status == "accepted",
                        "total_count": len(items),
                        "success_count": success,
                        "failed_count": len(items) - success,
                        "status": "completed" if success == len(items) else "partial_failure",
                        "details": [],
                        "created_at": utc_isoformat(first.created_at),
                    }
                )
            return history[:limit]
