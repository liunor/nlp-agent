"""班级加入申请工作流 — 仅申请/审批/拒绝/列表，对接 V3 的 nlp_classrooms。

不含类 CRUD / 成员管理（V3 的 ``/api/v1/classrooms`` 已提供），避免第二套班级系统。
所有审批/拒绝查询都强制 ``request_id + class_id + status='pending'`` 三重绑定，
满足 review 文档 P0-5（路径级班级权限不能替代资源关联鉴权）。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from server.infrastructure.mysql.models import ClassJoinRequestModel, ClassroomMemberModel, ClassroomModel


class ClassJoinError(Exception):
    """Stable API error for the guest/student classroom-join flow."""

    def __init__(self, *, status_code: int, code: str, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.code = code
        self.detail = detail


class PendingJoinRequestError(ClassJoinError):
    def __init__(self) -> None:
        super().__init__(
            status_code=409,
            code="class_join_request_pending",
            detail="已申请该班级，请等待审核",
        )


class ClassroomMemberExistsError(ClassJoinError):
    def __init__(self) -> None:
        super().__init__(
            status_code=409,
            code="class_join_member_exists",
            detail="你已经加入该班级，无需重复申请",
        )


class JoinCodeNotFoundError(ClassJoinError):
    def __init__(self) -> None:
        super().__init__(
            status_code=404,
            code="class_join_code_not_found",
            detail="班级码不存在或班级不可加入",
        )


def normalize_join_code(join_code: str) -> str:
    return join_code.strip().upper()


async def get_active_classroom_by_join_code(
    db: AsyncSession, join_code: str
) -> ClassroomModel | None:
    normalized = normalize_join_code(join_code)
    return await db.scalar(
        select(ClassroomModel).where(
            ClassroomModel.join_code == normalized,
            ClassroomModel.status == "active",
        )
    )


async def submit_join_request(
    db: AsyncSession, class_id: str, user_id: str, student_number: str | None = None
) -> ClassJoinRequestModel:
    """创建一条 pending 申请。

    ``pending_key`` 在数据库层阻止同一 (班级, 用户) 的并发 pending 重复申请；
    审批或拒绝后会清空该键，因此历史记录不会阻止用户重新申请。
    """
    req = ClassJoinRequestModel(
        id=str(uuid.uuid4()),
        class_id=class_id,
        user_id=user_id,
        student_number=student_number,
        status="pending",
        pending_key=f"{class_id}:{user_id}",
    )
    db.add(req)
    await db.flush()
    return req


async def get_user_pending_request(
    db: AsyncSession, class_id: str, user_id: str
) -> ClassJoinRequestModel | None:
    result = await db.execute(
        select(ClassJoinRequestModel).where(
            ClassJoinRequestModel.class_id == class_id,
            ClassJoinRequestModel.user_id == user_id,
            ClassJoinRequestModel.status == "pending",
        )
    )
    return result.scalar_one_or_none()


async def list_pending_requests(db: AsyncSession, class_id: str) -> list[ClassJoinRequestModel]:
    result = await db.execute(
        select(ClassJoinRequestModel)
        .where(
            ClassJoinRequestModel.class_id == class_id,
            ClassJoinRequestModel.status == "pending",
        )
        .options(
            selectinload(ClassJoinRequestModel.user_),
            selectinload(ClassJoinRequestModel.cls),
        )
        .order_by(ClassJoinRequestModel.requested_at)
    )
    return list(result.scalars().all())


async def approve_join_request(
    db: AsyncSession,
    request_id: str,
    class_id: str,
    reviewed_by: str,
    student_number_override: str | None = None,
) -> ClassJoinRequestModel | None:
    """审批通过：仅在 ``request_id + class_id + status='pending'`` 同时命中时生效。

    返回 None 表示申请不存在或已处理（调用方应回 404）。实际名册写入由 API 层
    在同一事务内通过 ``rbac_service.replace_classroom_member`` 完成。
    """
    result = await db.execute(
        select(ClassJoinRequestModel)
        .where(
            ClassJoinRequestModel.id == request_id,
            ClassJoinRequestModel.class_id == class_id,
            ClassJoinRequestModel.status == "pending",
        )
        .with_for_update()
        .options(
            selectinload(ClassJoinRequestModel.user_),
            selectinload(ClassJoinRequestModel.cls),
        )
    )
    req = result.scalar_one_or_none()
    if req is None:
        return None
    req.status = "approved"
    req.pending_key = None
    req.reviewed_at = datetime.now(timezone.utc)
    req.reviewed_by = reviewed_by
    if student_number_override is not None:
        req.student_number = student_number_override
    await db.flush()
    return req


async def reject_join_request(
    db: AsyncSession,
    request_id: str,
    class_id: str,
    reviewed_by: str,
) -> ClassJoinRequestModel | None:
    """拒绝：同样要求 ``request_id + class_id + status='pending'`` 三重绑定。"""
    result = await db.execute(
        select(ClassJoinRequestModel).where(
            ClassJoinRequestModel.id == request_id,
            ClassJoinRequestModel.class_id == class_id,
            ClassJoinRequestModel.status == "pending",
        ).with_for_update()
    )
    req = result.scalar_one_or_none()
    if req is None:
        return None
    req.status = "rejected"
    req.pending_key = None
    req.reviewed_at = datetime.now(timezone.utc)
    req.reviewed_by = reviewed_by
    await db.flush()
    return req


async def get_active_classroom_membership(
    db: AsyncSession, class_id: str, user_id: str
) -> ClassroomMemberModel | None:
    return await db.scalar(
        select(ClassroomMemberModel).where(
            ClassroomMemberModel.classroom_id == class_id,
            ClassroomMemberModel.user_id == user_id,
            ClassroomMemberModel.status == "active",
        )
    )


async def get_user_join_requests(db: AsyncSession, user_id: str) -> list[ClassJoinRequestModel]:
    result = await db.execute(
        select(ClassJoinRequestModel)
        .where(ClassJoinRequestModel.user_id == user_id)
        .options(
            selectinload(ClassJoinRequestModel.cls),
            selectinload(ClassJoinRequestModel.user_),
        )
        .order_by(ClassJoinRequestModel.requested_at.desc())
    )
    return list(result.scalars().all())
