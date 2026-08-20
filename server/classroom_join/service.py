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

from server.infrastructure.mysql.models import ClassJoinRequestModel


async def submit_join_request(
    db: AsyncSession, class_id: str, user_id: str, student_number: str | None = None
) -> ClassJoinRequestModel:
    """创建一条 pending 申请。

    唯一约束 ``uq_nlp_class_join_requests_cls_usr_sts`` 在数据库层阻止同一
    (班级, 用户, pending) 重复申请；调用方仍可先用 ``get_user_pending_request``
    做友好前置校验。
    """
    req = ClassJoinRequestModel(
        id=str(uuid.uuid4()),
        class_id=class_id,
        user_id=user_id,
        student_number=student_number,
        status="pending",
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
    req.reviewed_at = datetime.now(timezone.utc)
    req.reviewed_by = reviewed_by
    await db.flush()
    return req


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
