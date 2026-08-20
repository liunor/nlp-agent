"""班级加入申请 API — 仅申请/审批/拒绝/列表，对接 V3 RBAC。

设计要点（满足 review 文档）：
- 不重复实现类 CRUD / 成员管理（由 ``server/web/app.py`` 的 ``/api/v1/classrooms`` 提供）。
- 所有写接口统一过 ``WriteClaims``（Origin + CSRF + 身份），满足 P0-3。
- 教师审批/拒绝接口过资源权限 ``CLASSROOM_MEMBER_MANAGE``（capability 驱动，非角色字符串），满足第6.2。
- 审批/拒绝在 service 层对 ``request_id + class_id + status='pending'`` 三重绑定，满足 P0-5。
- 路由前缀 ``/api/v1/classrooms``，由 ``app.py`` 在 SPA 静态 mount 之前注册，满足 P0-1。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from core.rbac import Permission, ResourceRef, authorization_service
from server.auth.dependencies import Principal, WriteClaims, get_db_session
from server.classroom_join import service
from server.classroom_join.schemas import (
    ApproveJoinRequest,
    JoinRequestCreate,
    JoinRequestListResponse,
    JoinRequestResponse,
)
from server.infrastructure.mysql.models import ClassJoinRequestModel
from server.rbac.service import rbac_service

router = APIRouter(prefix="/api/v1/classrooms", tags=["classroom-join-requests"])

DbSession = Annotated[AsyncSession, Depends(get_db_session)]


def _to_response(req: ClassJoinRequestModel) -> JoinRequestResponse:
    return JoinRequestResponse(
        id=req.id,
        class_id=req.class_id,
        class_name=req.cls.name if req.cls else "",
        user_id=req.user_id,
        user_name=req.user_.username if req.user_ else "",
        display_name=(req.user_.display_name or req.user_.username) if req.user_ else "",
        student_number=req.student_number,
        status=req.status,
        requested_at=req.requested_at,
        reviewed_at=req.reviewed_at,
        reviewed_by=req.reviewed_by,
    )


async def _require_classroom_manager(principal: Principal, db: AsyncSession, classroom_id: str) -> None:
    """资源级权限：当前用户须对该班级拥有 CLASSROOM_MEMBER_MANAGE。"""
    classroom = await rbac_service.classroom(db, classroom_id)
    if classroom is None:
        raise HTTPException(status_code=404, detail="班级不存在")
    authorization_service.require_resource(
        principal,
        Permission.CLASSROOM_MEMBER_MANAGE,
        ResourceRef("classroom", workspace_id=classroom.workspace_id, classroom_id=classroom.id),
    )


@router.post(
    "/{classroom_id}/join-requests",
    response_model=JoinRequestResponse,
    status_code=status.HTTP_201_CREATED,
)
async def submit_join_request(
    classroom_id: str,
    body: JoinRequestCreate,
    principal: Principal,
    db: DbSession,
    _write: WriteClaims,
):
    """学生提交加入班级申请（pending）。"""
    authorization_service.require(principal, Permission.LEARNING_PROGRESS_READ_SELF)
    await rbac_service.classroom(db, classroom_id)
    existing = await service.get_user_pending_request(db, classroom_id, principal.user_id)
    if existing is not None:
        raise HTTPException(status_code=409, detail="您已提交过该班级的加入申请，请等待审核")
    req = await service.submit_join_request(db, classroom_id, principal.user_id, body.student_number)
    await db.flush()
    result = await db.execute(
        select(ClassJoinRequestModel)
        .where(ClassJoinRequestModel.id == req.id)
        .options(selectinload(ClassJoinRequestModel.user_), selectinload(ClassJoinRequestModel.cls))
    )
    req = result.scalar_one()
    return _to_response(req)


@router.get("/{classroom_id}/join-requests", response_model=JoinRequestListResponse)
async def list_join_requests(classroom_id: str, principal: Principal, db: DbSession):
    """该班教师/管理员查看待审核申请。"""
    await _require_classroom_manager(principal, db, classroom_id)
    requests = await service.list_pending_requests(db, classroom_id)
    return JoinRequestListResponse(items=[_to_response(r) for r in requests], total=len(requests))


@router.post("/{classroom_id}/join-requests/{request_id}/approve", response_model=JoinRequestResponse)
async def approve_join_request(
    classroom_id: str,
    request_id: str,
    body: ApproveJoinRequest,
    principal: Principal,
    db: DbSession,
    _write: WriteClaims,
):
    """审批通过：request_id 与 class_id 必须一致（P0-5），并把申请人加入班级名册。"""
    await _require_classroom_manager(principal, db, classroom_id)
    req = await service.approve_join_request(
        db, request_id, classroom_id, principal.user_id, body.student_number
    )
    if req is None:
        raise HTTPException(status_code=404, detail="申请不存在或已处理")
    # 审批通过 → 将申请人加入班级名册（student / active），与请求状态变更同一事务
    await rbac_service.replace_classroom_member(
        db,
        classroom_id=classroom_id,
        user_id=req.user_id,
        member_role="student",
        status="active",
        actor_user_id=principal.user_id,
    )
    # P0-5：审批动作写入审计事件
    await rbac_service.audit(
        db,
        actor_user_id=principal.user_id,
        target_user_id=req.user_id,
        decision="allow",
        reason_code="classroom_join_approved",
        permission_code="classroom:member:manage",
        resource_type="classroom",
        resource_id=classroom_id,
        detail={"request_id": request_id},
    )
    await db.flush()
    result = await db.execute(
        select(ClassJoinRequestModel)
        .where(ClassJoinRequestModel.id == req.id)
        .options(selectinload(ClassJoinRequestModel.user_), selectinload(ClassJoinRequestModel.cls))
    )
    req = result.scalar_one()
    return _to_response(req)


@router.post("/{classroom_id}/join-requests/{request_id}/reject", response_model=JoinRequestResponse)
async def reject_join_request(
    classroom_id: str,
    request_id: str,
    principal: Principal,
    db: DbSession,
    _write: WriteClaims,
):
    """拒绝申请：request_id 与 class_id 必须一致（P0-5）。"""
    await _require_classroom_manager(principal, db, classroom_id)
    req = await service.reject_join_request(db, request_id, classroom_id, principal.user_id)
    if req is None:
        raise HTTPException(status_code=404, detail="申请不存在或已处理")
    # P0-5：拒绝动作写入审计事件
    await rbac_service.audit(
        db,
        actor_user_id=principal.user_id,
        target_user_id=req.user_id,
        decision="allow",
        reason_code="classroom_join_rejected",
        permission_code="classroom:member:manage",
        resource_type="classroom",
        resource_id=classroom_id,
        detail={"request_id": request_id},
    )
    await db.flush()
    result = await db.execute(
        select(ClassJoinRequestModel)
        .where(ClassJoinRequestModel.id == req.id)
        .options(selectinload(ClassJoinRequestModel.user_), selectinload(ClassJoinRequestModel.cls))
    )
    req = result.scalar_one()
    return _to_response(req)


@router.get("/my-join-requests", response_model=JoinRequestListResponse)
async def my_join_requests(principal: Principal, db: DbSession):
    """当前用户查看自己的全部加入申请。"""
    requests = await service.get_user_join_requests(db, principal.user_id)
    return JoinRequestListResponse(items=[_to_response(r) for r in requests], total=len(requests))
