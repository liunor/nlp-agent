"""班级管理 API 路由 — 复用项目 Principal / WriteClaims 依赖与内联角色校验。

对应「用户管理第二版」中缺失的班级加入申请工作流，独立于 workspace 作用域的 nlp_classes 体系。
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from core.identity import AuthenticatedPrincipal
from server.auth.dependencies import Principal, WriteClaims, get_db_session
from server.infrastructure.mysql.models import (
    ClassEnrollmentModel,
    ClassJoinRequestModel,
    ClassModel,
    ClassTeacherModel,
)
from server.classroom_join import service
from server.classroom_join.schemas import (
    ApproveJoinRequest,
    AvailableClassListResponse,
    AvailableClassResponse,
    ClassAddStudentRequest,
    ClassCreate,
    ClassDetailResponse,
    ClassDetailTeacher,
    ClassListResponse,
    ClassResponse,
    ClassTeacherInfo,
    ClassUpdate,
    JoinRequestCreate,
    JoinRequestListResponse,
    JoinRequestResponse,
    MyClassInfo,
    MyClassesResponse,
    StudentAdd,
    StudentListResponse,
    StudentResponse,
    TeacherAdd,
    TeacherListResponse,
    TeacherResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/classes", tags=["classes"])

DbSession = Annotated[AsyncSession, Depends(get_db_session)]


# ── 权限辅助 ──────────────────────────────────────────────

def _require_admin(principal: AuthenticatedPrincipal) -> None:
    if not principal.is_admin:
        raise HTTPException(status_code=403, detail="需要管理员权限")


def _require_admin_or_teacher(principal: AuthenticatedPrincipal) -> None:
    if not (principal.is_admin or "teacher" in principal.roles):
        raise HTTPException(status_code=403, detail="需要管理员或教师权限")


# ── 响应辅助 ──────────────────────────────────────────────

def _enrollment_to_response(enr: ClassEnrollmentModel) -> StudentResponse:
    return StudentResponse(
        id=enr.id,
        class_id=enr.class_id,
        user_id=enr.user_id,
        username=enr.student.username if enr.student else "",
        display_name=(enr.student.display_name or enr.student.username) if enr.student else "",
        student_number=enr.student_number,
        major=enr.major or "",
        status=enr.status,
        joined_at=enr.joined_at,
    )


def _teacher_to_response(ct: ClassTeacherModel) -> TeacherResponse:
    return TeacherResponse(
        id=ct.id,
        class_id=ct.class_id,
        teacher_id=ct.teacher_id,
        username=ct.teacher.username if ct.teacher else "",
        display_name=(ct.teacher.display_name or ct.teacher.username) if ct.teacher else "",
        subject=ct.subject,
        status=ct.status,
    )


def _class_to_response(cls: ClassModel) -> ClassResponse:
    return ClassResponse(
        id=cls.id,
        name=cls.name,
        grade=cls.grade,
        status=cls.status,
        created_by=cls.created_by,
        student_count=sum(1 for e in (cls.enrollments or []) if e.status == "active"),
        teacher_count=sum(1 for t in (cls.teachers or []) if t.status == "active"),
        created_at=cls.created_at,
        updated_at=cls.updated_at,
    )


def _join_request_to_response(req: ClassJoinRequestModel) -> JoinRequestResponse:
    return JoinRequestResponse(
        id=req.id,
        class_id=req.class_id,
        class_name=req.cls.name if req.cls else "",
        grade=req.cls.grade if req.cls else None,
        user_id=req.user_id,
        user_name=req.user_.username if req.user_ else "",
        display_name=(req.user_.display_name or req.user_.username) if req.user_ else "",
        student_number=req.student_number,
        status=req.status,
        requested_at=req.requested_at,
        reviewed_at=req.reviewed_at,
        reviewed_by=req.reviewed_by,
    )


# ── 班级 CRUD ────────────────────────────────────────────

@router.post("", response_model=ClassResponse, status_code=status.HTTP_201_CREATED)
async def create_class(
    body: ClassCreate,
    principal: Principal,
    db: DbSession,
    _write: WriteClaims,
):
    _require_admin_or_teacher(principal)
    try:
        cls = await service.create_class(db, body.name, body.grade, principal.user_id, body.subject)
        await db.commit()
        cls = await service.get_class(db, cls.id)
        return _class_to_response(cls)
    except HTTPException:
        raise
    except Exception:
        logger.exception("create_class 失败: name=%s grade=%s user_id=%s", body.name, body.grade, principal.user_id)
        await db.rollback()
        raise HTTPException(status_code=500, detail="创建班级失败，请检查服务器日志")


@router.get("", response_model=ClassListResponse)
async def list_classes(
    principal: Principal,
    db: DbSession,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    status_filter: str | None = Query(None, alias="status"),
):
    is_admin = principal.is_admin
    teacher_id: str | None = None
    if not is_admin:
        if "teacher" in principal.roles:
            teacher_id = principal.user_id
        else:
            raise HTTPException(status_code=403, detail="无权限查看班级列表")

    classes, total = await service.list_classes(
        db, skip=skip, limit=limit, status=status_filter, teacher_id=teacher_id
    )
    return ClassListResponse(items=[_class_to_response(c) for c in classes], total=total)


@router.get("/my/classes", response_model=MyClassesResponse)
async def get_my_classes(principal: Principal, db: DbSession):
    """当前登录用户看到的班级：学生→所在班级+老师；老师→任教班级+待审数。"""
    user_id = principal.user_id
    is_admin = principal.is_admin
    has_teacher = is_admin or ("teacher" in principal.roles)
    has_student = "student" in principal.roles

    result_classes: list[MyClassInfo] = []

    if has_student:
        enrollments = await service.get_student_classes(db, user_id)
        for enr in enrollments:
            if enr.cls.status != "active":
                continue
            teachers_info: list[ClassTeacherInfo] = []
            for ct in enr.cls.teachers:
                if ct.status != "active":
                    continue
                teachers_info.append(ClassTeacherInfo(
                    teacher_id=ct.teacher_id,
                    display_name=(ct.teacher.display_name or ct.teacher.username) if ct.teacher else "",
                    username=ct.teacher.username if ct.teacher else "",
                    subject=ct.subject,
                ))
            result_classes.append(MyClassInfo(
                class_id=enr.cls.id,
                class_name=enr.cls.name,
                grade=enr.cls.grade,
                student_number=enr.student_number,
                student_count=sum(1 for e in (enr.cls.enrollments or []) if e.status == "active"),
                teachers=teachers_info,
            ))

    if has_teacher:
        teacher_classes = await service.get_teacher_classes(db, user_id)
        seen_class_ids = {c.class_id for c in result_classes}
        for ct in teacher_classes:
            if ct.cls.status != "active":
                continue
            pending = await service.count_pending_requests(db, ct.class_id)
            if ct.class_id in seen_class_ids:
                for rc in result_classes:
                    if rc.class_id == ct.class_id:
                        rc.subject = ct.subject
                        rc.pending_count = pending
                        break
            else:
                teachers_info = [
                    ClassTeacherInfo(
                        teacher_id=t.teacher_id,
                        display_name=(t.teacher.display_name or t.teacher.username) if t.teacher else "",
                        username=t.teacher.username if t.teacher else "",
                        subject=t.subject,
                    )
                    for t in ct.cls.teachers
                    if t.status == "active"
                ]
                result_classes.append(MyClassInfo(
                    class_id=ct.cls.id,
                    class_name=ct.cls.name,
                    grade=ct.cls.grade,
                    subject=ct.subject,
                    student_count=sum(1 for e in (ct.cls.enrollments or []) if e.status == "active"),
                    pending_count=pending,
                    teachers=teachers_info,
                ))
                seen_class_ids.add(ct.class_id)

    role = "admin" if is_admin else ("teacher" if has_teacher else "student")
    return MyClassesResponse(role=role, classes=result_classes)


@router.get("/available", response_model=AvailableClassListResponse)
async def list_available_classes(
    principal: Principal,
    db: DbSession,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
):
    """学生可加入的班级（排除已加入/已 pending/已归档）。"""
    user_id = principal.user_id
    classes, total = await service.get_available_classes(db, user_id, skip, limit)
    items = [
        AvailableClassResponse(
            class_id=c.id,
            name=c.name,
            grade=c.grade,
            teacher_count=sum(1 for t in (c.teachers or []) if t.status == "active"),
            student_count=sum(1 for e in (c.enrollments or []) if e.status == "active"),
            teachers=[
                {
                    "teacher_id": t.teacher_id,
                    "display_name": (t.teacher.display_name or t.teacher.username) if t.teacher else "",
                    "username": t.teacher.username if t.teacher else "",
                    "subject": t.subject,
                }
                for t in (c.teachers or []) if t.status == "active"
            ],
        )
        for c in classes
    ]
    return AvailableClassListResponse(items=items, total=total)


@router.get("/my/join-requests", response_model=JoinRequestListResponse)
async def get_my_join_requests(principal: Principal, db: DbSession):
    """学生查看自己的所有加入申请。"""
    user_id = principal.user_id
    requests = await service.get_user_join_requests(db, user_id)
    items = [_join_request_to_response(r) for r in requests]
    return JoinRequestListResponse(items=items, total=len(items))


@router.get("/{class_id}/detail", response_model=ClassDetailResponse)
async def get_class_detail(class_id: str, principal: Principal, db: DbSession):
    """教师查看班级详情。"""
    is_admin = principal.is_admin
    if not is_admin:
        is_teacher = await service.is_class_teacher(db, class_id, principal.user_id)
        if not is_teacher:
            raise HTTPException(status_code=403, detail="无权限查看该班级详情")
    detail = await service.get_class_detail(db, class_id)
    if not detail:
        raise HTTPException(status_code=404, detail="班级不存在")
    return ClassDetailResponse(**detail)


@router.get("/{class_id}", response_model=ClassResponse)
async def get_class(class_id: str, principal: Principal, db: DbSession):
    cls = await service.get_class(db, class_id)
    if not cls:
        raise HTTPException(status_code=404, detail="班级不存在")
    is_admin = principal.is_admin
    is_teacher = await service.is_class_teacher(db, class_id, principal.user_id)
    if not is_admin and not is_teacher:
        raise HTTPException(status_code=403, detail="无权限查看该班级")
    return _class_to_response(cls)


@router.patch("/{class_id}", response_model=ClassResponse)
async def update_class(
    class_id: str, body: ClassUpdate, principal: Principal, db: DbSession, _write: WriteClaims,
):
    _require_admin_or_teacher(principal)
    cls = await service.update_class(db, class_id, name=body.name, grade=body.grade)
    if not cls:
        raise HTTPException(status_code=404, detail="班级不存在")
    await db.commit()
    return _class_to_response(cls)


@router.delete("/{class_id}", status_code=status.HTTP_200_OK)
async def delete_class(class_id: str, principal: Principal, db: DbSession, _write: WriteClaims):
    cls = await service.get_class(db, class_id)
    if not cls:
        raise HTTPException(status_code=404, detail="班级不存在")
    is_admin = principal.is_admin
    is_teacher_of_class = await service.is_class_teacher(db, class_id, principal.user_id)
    is_creator = cls.created_by == principal.user_id
    if not is_admin and not is_teacher_of_class and not is_creator:
        raise HTTPException(status_code=403, detail="只有该班级教师、管理员或创建者才能删除班级")
    await db.delete(cls)
    await db.commit()
    return {"deleted": True, "class_id": class_id}


# ── 学生管理 ──────────────────────────────────────────────

@router.post("/{class_id}/students", response_model=StudentResponse, status_code=status.HTTP_201_CREATED)
async def add_student(
    class_id: str, body: StudentAdd, principal: Principal, db: DbSession, _write: WriteClaims,
):
    _require_admin_or_teacher(principal)
    enrollment = await service.add_student(db, class_id, body.user_id, body.student_number, body.major)
    if not enrollment:
        raise HTTPException(status_code=409, detail="学生已在班级中")
    await db.commit()
    result = await db.execute(
        select(ClassEnrollmentModel).where(ClassEnrollmentModel.id == enrollment.id)
        .options(selectinload(ClassEnrollmentModel.student))
    )
    enrollment = result.scalar_one()
    return _enrollment_to_response(enrollment)


@router.delete("/{class_id}/students/{user_id}", status_code=status.HTTP_200_OK)
async def remove_student(class_id: str, user_id: str, principal: Principal, db: DbSession, _write: WriteClaims):
    _require_admin_or_teacher(principal)
    ok = await service.remove_student(db, class_id, user_id)
    if not ok:
        raise HTTPException(status_code=404, detail="学生不在该班级中")
    await db.commit()
    return {"removed": True, "class_id": class_id, "user_id": user_id}


@router.post("/{class_id}/students/add", status_code=status.HTTP_200_OK)
async def add_student_by_account(
    class_id: str, body: ClassAddStudentRequest, principal: Principal, db: DbSession, _write: WriteClaims,
):
    _require_admin_or_teacher(principal)
    user = await service.get_user_by_username(db, body.account)
    if user is None:
        raise HTTPException(status_code=404, detail="该用户不存在，请先让学生通过登录页注册")
    role_codes = await service.user_role_codes(db, user.id)
    if "student" not in role_codes:
        raise HTTPException(status_code=400, detail="该账号不是学生账号")
    enrollment = await service.add_student(db, class_id, user.id, body.account, body.major)
    if enrollment is None:
        raise HTTPException(status_code=409, detail="该学生已在班级中")
    await db.commit()
    result = await db.execute(
        select(ClassEnrollmentModel).where(ClassEnrollmentModel.id == enrollment.id)
        .options(selectinload(ClassEnrollmentModel.student))
    )
    enrollment = result.scalar_one()
    return _enrollment_to_response(enrollment)


@router.get("/{class_id}/students", response_model=StudentListResponse)
async def list_class_students(
    class_id: str, principal: Principal, db: DbSession,
    skip: int = Query(0, ge=0), limit: int = Query(200, ge=1, le=500),
):
    is_admin = principal.is_admin
    if not is_admin:
        is_teacher = await service.is_class_teacher(db, class_id, principal.user_id)
        if not is_teacher:
            raise HTTPException(status_code=403, detail="无权限查看该班级学生")
    enrollments, total = await service.list_class_students(db, class_id, skip, limit)
    return StudentListResponse(items=[_enrollment_to_response(e) for e in enrollments], total=total)


# ── 科任老师管理 ──────────────────────────────────────────

@router.post("/{class_id}/teachers", response_model=TeacherResponse, status_code=status.HTTP_201_CREATED)
async def add_teacher(
    class_id: str, body: TeacherAdd, principal: Principal, db: DbSession, _write: WriteClaims,
):
    _require_admin_or_teacher(principal)
    ct = await service.add_teacher(db, class_id, body.user_id, body.subject)
    if not ct:
        raise HTTPException(status_code=409, detail="该老师已在此班级中")
    await db.commit()
    result = await db.execute(
        select(ClassTeacherModel).where(ClassTeacherModel.id == ct.id)
        .options(selectinload(ClassTeacherModel.teacher))
    )
    ct = result.scalar_one()
    return _teacher_to_response(ct)


@router.delete("/{class_id}/teachers/{user_id}", status_code=status.HTTP_200_OK)
async def remove_teacher(class_id: str, user_id: str, principal: Principal, db: DbSession, _write: WriteClaims):
    _require_admin_or_teacher(principal)
    ok = await service.remove_teacher(db, class_id, user_id)
    if not ok:
        raise HTTPException(status_code=404, detail="该老师不在此班级中")
    await db.commit()
    return {"removed": True, "class_id": class_id, "user_id": user_id}


@router.get("/{class_id}/teachers", response_model=TeacherListResponse)
async def list_class_teachers(
    class_id: str, principal: Principal, db: DbSession,
    skip: int = Query(0, ge=0), limit: int = Query(200, ge=1, le=500),
):
    is_admin = principal.is_admin
    if not is_admin:
        is_teacher = await service.is_class_teacher(db, class_id, principal.user_id)
        if not is_teacher:
            raise HTTPException(status_code=403, detail="无权限查看该班级老师")
    teachers, total = await service.list_class_teachers(db, class_id, skip, limit)
    return TeacherListResponse(items=[_teacher_to_response(t) for t in teachers], total=total)


# ── 加入申请（学生提交 / 教师审核） ─────────────────────────

@router.post("/{class_id}/join", response_model=JoinRequestResponse, status_code=status.HTTP_201_CREATED)
async def join_class(class_id: str, body: JoinRequestCreate, principal: Principal, db: DbSession, _write: WriteClaims):
    """学生提交加入班级申请。"""
    user_id = principal.user_id
    existing_pending = await service.get_user_pending_request(db, class_id, user_id)
    if existing_pending:
        raise HTTPException(status_code=409, detail="您已提交过该班级的加入申请，请等待审核")
    req = await service.submit_join_request(db, class_id, user_id, body.student_number)
    await db.commit()
    result = await db.execute(
        select(ClassJoinRequestModel).where(ClassJoinRequestModel.id == req.id)
        .options(selectinload(ClassJoinRequestModel.user_), selectinload(ClassJoinRequestModel.cls))
    )
    req = result.scalar_one()
    return _join_request_to_response(req)


@router.get("/{class_id}/join-requests", response_model=JoinRequestListResponse)
async def list_join_requests(class_id: str, principal: Principal, db: DbSession):
    """该班教师/创建者/admin 查看待审核申请。"""
    is_admin = principal.is_admin
    if not is_admin:
        is_teacher = await service.is_class_teacher(db, class_id, principal.user_id)
        is_creator = await service.is_class_creator(db, class_id, principal.user_id)
        if not is_teacher and not is_creator:
            raise HTTPException(status_code=403, detail="只有该班级的教师、创建者或管理员才能查看申请")
    requests = await service.list_pending_requests(db, class_id)
    items = [_join_request_to_response(r) for r in requests]
    return JoinRequestListResponse(items=items, total=len(items))


@router.post("/{class_id}/join-requests/{req_id}/approve", response_model=JoinRequestResponse)
async def approve_join_request(
    class_id: str, req_id: str, body: ApproveJoinRequest, principal: Principal, db: DbSession, _write: WriteClaims,
):
    """通过加入申请（可选覆盖学号）。"""
    _require_admin_or_teacher(principal)
    req = await service.approve_join_request(db, req_id, principal.user_id, body.student_number)
    if not req:
        raise HTTPException(status_code=404, detail="申请不存在或已处理")
    await db.commit()
    result = await db.execute(
        select(ClassJoinRequestModel).where(ClassJoinRequestModel.id == req.id)
        .options(selectinload(ClassJoinRequestModel.user_), selectinload(ClassJoinRequestModel.cls))
    )
    req = result.scalar_one()
    return _join_request_to_response(req)


@router.post("/{class_id}/join-requests/{req_id}/reject", response_model=JoinRequestResponse)
async def reject_join_request(
    class_id: str, req_id: str, principal: Principal, db: DbSession, _write: WriteClaims,
):
    """拒绝加入申请。"""
    _require_admin_or_teacher(principal)
    req = await service.reject_join_request(db, req_id, principal.user_id)
    if not req:
        raise HTTPException(status_code=404, detail="申请不存在或已处理")
    await db.commit()
    result = await db.execute(
        select(ClassJoinRequestModel).where(ClassJoinRequestModel.id == req.id)
        .options(selectinload(ClassJoinRequestModel.user_), selectinload(ClassJoinRequestModel.cls))
    )
    req = result.scalar_one()
    return _join_request_to_response(req)
