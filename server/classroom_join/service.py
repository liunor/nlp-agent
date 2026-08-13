"""班级管理系统 CRUD 操作。

独立于 workspace 作用域的「班级 / 加入申请」功能，沿用项目的 UUID / AsyncSession 风格。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from server.infrastructure.mysql.models import (
    ClassEnrollmentModel,
    ClassJoinRequestModel,
    ClassModel,
    ClassTeacherModel,
    UserModel,
)
from server.rbac.service import rbac_service


# ════════════════════════════════════════════════════════
#  班级
# ════════════════════════════════════════════════════════

async def create_class(
    db: AsyncSession, name: str, grade: str | None, created_by: str, subject: str = "班主任"
) -> ClassModel:
    cls = ClassModel(id=str(uuid.uuid4()), name=name, grade=grade, created_by=created_by)
    db.add(cls)
    await db.flush()
    await db.refresh(cls)
    # 创建者自动成为班级科任老师
    db.add(ClassTeacherModel(id=str(uuid.uuid4()), class_id=cls.id, teacher_id=created_by, subject=subject, status="active"))
    await db.flush()
    return cls


async def get_class(db: AsyncSession, class_id: str) -> ClassModel | None:
    result = await db.execute(
        select(ClassModel)
        .where(ClassModel.id == class_id)
        .options(selectinload(ClassModel.enrollments), selectinload(ClassModel.teachers))
    )
    return result.scalar_one_or_none()


async def list_classes(
    db: AsyncSession,
    skip: int = 0,
    limit: int = 100,
    status: str | None = None,
    teacher_id: str | None = None,
) -> tuple[list[ClassModel], int]:
    base = select(ClassModel)
    if status:
        base = base.where(ClassModel.status == status)
    if teacher_id is not None:
        sub = (
            select(ClassTeacherModel.class_id)
            .where(ClassTeacherModel.teacher_id == teacher_id, ClassTeacherModel.status == "active")
            .distinct()
        )
        base = base.where(ClassModel.id.in_(sub))

    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar() or 0
    query = (
        base.options(selectinload(ClassModel.enrollments), selectinload(ClassModel.teachers))
        .order_by(ClassModel.id)
        .offset(skip)
        .limit(limit)
    )
    result = await db.execute(query)
    return list(result.scalars().all()), total


async def update_class(
    db: AsyncSession, class_id: str, name: str | None = None, grade: str | None = None
) -> ClassModel | None:
    cls = await get_class(db, class_id)
    if not cls:
        return None
    if name is not None:
        cls.name = name
    if grade is not None:
        cls.grade = grade
    cls.updated_at = datetime.now(timezone.utc)
    await db.flush()
    await db.refresh(cls)
    return cls


async def archive_class(db: AsyncSession, class_id: str) -> ClassModel | None:
    cls = await get_class(db, class_id)
    if not cls:
        return None
    cls.status = "archived"
    cls.updated_at = datetime.now(timezone.utc)
    await db.flush()
    return cls


# ════════════════════════════════════════════════════════
#  学生
# ════════════════════════════════════════════════════════

async def add_student(
    db: AsyncSession,
    class_id: str,
    user_id: str,
    student_number: str | None = None,
    major: str | None = None,
) -> ClassEnrollmentModel | None:
    existing = await db.execute(
        select(ClassEnrollmentModel).where(
            ClassEnrollmentModel.class_id == class_id,
            ClassEnrollmentModel.user_id == user_id,
        )
    )
    if existing.scalar_one_or_none():
        return None
    enrollment = ClassEnrollmentModel(
        id=str(uuid.uuid4()),
        class_id=class_id, user_id=user_id, student_number=student_number, major=major or ""
    )
    db.add(enrollment)
    await db.flush()
    await db.refresh(enrollment)
    return enrollment


async def remove_student(db: AsyncSession, class_id: str, user_id: str) -> bool:
    result = await db.execute(
        select(ClassEnrollmentModel).where(
            ClassEnrollmentModel.class_id == class_id,
            ClassEnrollmentModel.user_id == user_id,
        )
    )
    enrollment = result.scalar_one_or_none()
    if not enrollment:
        return False
    await db.delete(enrollment)
    await db.flush()
    return True


async def list_class_students(
    db: AsyncSession, class_id: str, skip: int = 0, limit: int = 200
) -> tuple[list[ClassEnrollmentModel], int]:
    base = select(ClassEnrollmentModel).where(ClassEnrollmentModel.class_id == class_id)
    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar() or 0
    query = (
        base.options(selectinload(ClassEnrollmentModel.student))
        .order_by(ClassEnrollmentModel.id)
        .offset(skip)
        .limit(limit)
    )
    result = await db.execute(query)
    return list(result.scalars().all()), total


async def get_user_by_username(db: AsyncSession, username: str) -> UserModel | None:
    result = await db.execute(select(UserModel).where(UserModel.username == username))
    return result.scalar_one_or_none()


# ════════════════════════════════════════════════════════
#  科任老师
# ════════════════════════════════════════════════════════

async def add_teacher(
    db: AsyncSession, class_id: str, teacher_id: str, subject: str | None = None
) -> ClassTeacherModel | None:
    existing = await db.execute(
        select(ClassTeacherModel).where(
            ClassTeacherModel.class_id == class_id,
            ClassTeacherModel.teacher_id == teacher_id,
        )
    )
    if existing.scalar_one_or_none():
        return None
    ct = ClassTeacherModel(id=str(uuid.uuid4()), class_id=class_id, teacher_id=teacher_id, subject=subject)
    db.add(ct)
    await db.flush()
    await db.refresh(ct)
    return ct


async def remove_teacher(db: AsyncSession, class_id: str, teacher_id: str) -> bool:
    result = await db.execute(
        select(ClassTeacherModel).where(
            ClassTeacherModel.class_id == class_id,
            ClassTeacherModel.teacher_id == teacher_id,
        )
    )
    ct = result.scalar_one_or_none()
    if not ct:
        return False
    await db.delete(ct)
    await db.flush()
    return True


async def list_class_teachers(
    db: AsyncSession, class_id: str, skip: int = 0, limit: int = 200
) -> tuple[list[ClassTeacherModel], int]:
    base = select(ClassTeacherModel).where(ClassTeacherModel.class_id == class_id)
    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar() or 0
    query = (
        base.options(selectinload(ClassTeacherModel.teacher))
        .order_by(ClassTeacherModel.id)
        .offset(skip)
        .limit(limit)
    )
    result = await db.execute(query)
    return list(result.scalars().all()), total


# ════════════════════════════════════════════════════════
#  跨表查询
# ════════════════════════════════════════════════════════

async def get_student_classes(db: AsyncSession, user_id: str) -> list[ClassEnrollmentModel]:
    result = await db.execute(
        select(ClassEnrollmentModel)
        .where(ClassEnrollmentModel.user_id == user_id)
        .options(
            selectinload(ClassEnrollmentModel.cls)
            .selectinload(ClassModel.teachers)
            .selectinload(ClassTeacherModel.teacher)
        )
        .order_by(ClassEnrollmentModel.id)
    )
    return list(result.scalars().all())


async def get_teacher_classes(db: AsyncSession, teacher_id: str) -> list[ClassTeacherModel]:
    result = await db.execute(
        select(ClassTeacherModel)
        .where(ClassTeacherModel.teacher_id == teacher_id, ClassTeacherModel.status == "active")
        .options(
            selectinload(ClassTeacherModel.cls)
            .selectinload(ClassModel.teachers)
            .selectinload(ClassTeacherModel.teacher)
        )
        .order_by(ClassTeacherModel.id)
    )
    return list(result.scalars().all())


async def is_class_teacher(db: AsyncSession, class_id: str, teacher_id: str) -> bool:
    result = await db.execute(
        select(ClassTeacherModel).where(
            ClassTeacherModel.class_id == class_id,
            ClassTeacherModel.teacher_id == teacher_id,
            ClassTeacherModel.status == "active",
        )
    )
    return result.scalar_one_or_none() is not None


async def is_class_creator(db: AsyncSession, class_id: str, user_id: str) -> bool:
    result = await db.execute(
        select(ClassModel.id).where(ClassModel.id == class_id, ClassModel.created_by == user_id)
    )
    return result.scalar_one_or_none() is not None


# ════════════════════════════════════════════════════════
#  班级加入申请
# ════════════════════════════════════════════════════════

async def submit_join_request(
    db: AsyncSession, class_id: str, user_id: str, student_number: str | None = None
) -> ClassJoinRequestModel:
    req = ClassJoinRequestModel(
        id=str(uuid.uuid4()),
        class_id=class_id, user_id=user_id, student_number=student_number, status="pending"
    )
    db.add(req)
    await db.flush()
    await db.refresh(req)
    return req


async def list_pending_requests(db: AsyncSession, class_id: str) -> list[ClassJoinRequestModel]:
    result = await db.execute(
        select(ClassJoinRequestModel)
        .where(ClassJoinRequestModel.class_id == class_id, ClassJoinRequestModel.status == "pending")
        .options(selectinload(ClassJoinRequestModel.user_), selectinload(ClassJoinRequestModel.cls))
        .order_by(ClassJoinRequestModel.requested_at)
    )
    return list(result.scalars().all())


async def approve_join_request(
    db: AsyncSession, request_id: str, reviewed_by: str, student_number_override: str | None = None
) -> ClassJoinRequestModel | None:
    result = await db.execute(
        select(ClassJoinRequestModel)
        .where(ClassJoinRequestModel.id == request_id, ClassJoinRequestModel.status == "pending")
        .options(selectinload(ClassJoinRequestModel.user_))
    )
    req = result.scalar_one_or_none()
    if not req:
        return None
    enrollment = ClassEnrollmentModel(
        id=str(uuid.uuid4()),
        class_id=req.class_id,
        user_id=req.user_id,
        student_number=student_number_override or req.student_number,
        status="active",
    )
    db.add(enrollment)
    req.status = "approved"
    req.reviewed_at = datetime.now(timezone.utc)
    req.reviewed_by = reviewed_by
    await db.flush()
    await db.refresh(req)
    return req


async def reject_join_request(
    db: AsyncSession, request_id: str, reviewed_by: str
) -> ClassJoinRequestModel | None:
    result = await db.execute(
        select(ClassJoinRequestModel).where(
            ClassJoinRequestModel.id == request_id, ClassJoinRequestModel.status == "pending"
        )
    )
    req = result.scalar_one_or_none()
    if not req:
        return None
    req.status = "rejected"
    req.reviewed_at = datetime.now(timezone.utc)
    req.reviewed_by = reviewed_by
    await db.flush()
    await db.refresh(req)
    return req


async def get_user_join_requests(db: AsyncSession, user_id: str) -> list[ClassJoinRequestModel]:
    result = await db.execute(
        select(ClassJoinRequestModel)
        .where(ClassJoinRequestModel.user_id == user_id)
        .options(selectinload(ClassJoinRequestModel.cls), selectinload(ClassJoinRequestModel.user_))
        .order_by(ClassJoinRequestModel.requested_at.desc())
    )
    return list(result.scalars().all())


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


async def get_class_detail(db: AsyncSession, class_id: str) -> dict | None:
    cls_result = await db.execute(
        select(ClassModel)
        .where(ClassModel.id == class_id)
        .options(selectinload(ClassModel.teachers).selectinload(ClassTeacherModel.teacher))
    )
    cls = cls_result.scalar_one_or_none()
    if not cls:
        return None

    student_count = (
        await db.execute(
            select(func.count()).select_from(ClassEnrollmentModel).where(
                ClassEnrollmentModel.class_id == class_id, ClassEnrollmentModel.status == "active"
            )
        )
    ).scalar() or 0
    pending = await count_pending_requests(db, class_id)

    teachers = []
    for ct in cls.teachers:
        if ct.status != "active":
            continue
        teachers.append({
            "teacher_id": ct.teacher_id,
            "username": ct.teacher.username if ct.teacher else "",
            "display_name": (ct.teacher.display_name or ct.teacher.username) if ct.teacher else "",
            "subject": ct.subject,
        })

    return {
        "id": cls.id,
        "name": cls.name,
        "cohort": cls.grade,
        "created_at": cls.created_at.isoformat() if cls.created_at else "",
        "student_count": student_count,
        "teachers": teachers,
        "pending_count": pending,
    }


async def count_pending_requests(db: AsyncSession, class_id: str) -> int:
    result = await db.execute(
        select(func.count()).select_from(ClassJoinRequestModel).where(
            ClassJoinRequestModel.class_id == class_id, ClassJoinRequestModel.status == "pending"
        )
    )
    return result.scalar() or 0


async def get_available_classes(
    db: AsyncSession, user_id: str, skip: int = 0, limit: int = 100
) -> tuple[list[ClassModel], int]:
    enrolled_sub = select(ClassEnrollmentModel.class_id).where(
        ClassEnrollmentModel.user_id == user_id
    )
    pending_sub = select(ClassJoinRequestModel.class_id).where(
        ClassJoinRequestModel.user_id == user_id, ClassJoinRequestModel.status == "pending"
    )
    excluded = enrolled_sub.union(pending_sub)

    base = select(ClassModel).where(
        ClassModel.status == "active", ~ClassModel.id.in_(excluded)
    )
    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar() or 0
    query = (
        base.options(
            selectinload(ClassModel.enrollments),
            selectinload(ClassModel.teachers).selectinload(ClassTeacherModel.teacher),
        )
        .order_by(ClassModel.id)
        .offset(skip)
        .limit(limit)
    )
    result = await db.execute(query)
    return list(result.scalars().all()), total


async def user_role_codes(db: AsyncSession, user_id: str) -> set[str]:
    """Return the role codes assigned to a user (reuses RBAC service)."""
    return set(await rbac_service.user_role_codes(db, user_id))
