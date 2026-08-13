"""班级 / 加入申请 Pydantic 契约（适配项目 UUID 风格）。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


# ── 班级 ──────────────────────────────────────────────

class ClassCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=64, description="班级名称")
    grade: str | None = Field(None, max_length=32, description="届别")
    subject: str = Field("班主任", max_length=32, description="科目")


class ClassUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=64)
    grade: str | None = Field(None, max_length=32)


class ClassResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    grade: str | None
    status: str
    created_by: str
    student_count: int = 0
    teacher_count: int = 0
    created_at: datetime
    updated_at: datetime


class ClassListResponse(BaseModel):
    items: list[ClassResponse]
    total: int


# ── 学生 ──────────────────────────────────────────────

class StudentAdd(BaseModel):
    user_id: str = Field(..., description="学生用户 ID")
    student_number: str | None = Field(None, max_length=32, description="学号")
    major: str | None = Field(None, max_length=64, description="专业")


class StudentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    class_id: str
    user_id: str
    username: str = ""
    display_name: str = ""
    student_number: str | None
    major: str = ""
    status: str
    joined_at: datetime


class ClassAddStudentRequest(BaseModel):
    """教师按账号添加学生（账号不存在则返回 404）。"""

    account: str = Field(..., min_length=1, max_length=128, description="学生账号")
    name: str | None = Field(None, max_length=128, description="学生姓名（可选）")
    major: str | None = Field(None, max_length=64, description="专业")


class StudentListResponse(BaseModel):
    items: list[StudentResponse]
    total: int


# ── 科任老师 ───────────────────────────────────────────

class TeacherAdd(BaseModel):
    user_id: str = Field(..., description="教师用户 ID")
    subject: str | None = Field(None, max_length=32, description="科目")


class TeacherResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    class_id: str
    teacher_id: str
    username: str = ""
    display_name: str = ""
    subject: str | None
    status: str


class TeacherListResponse(BaseModel):
    items: list[TeacherResponse]
    total: int


# ── 我的班级（学生/老师视角） ─────────────────────────────

class ClassTeacherInfo(BaseModel):
    teacher_id: str
    display_name: str
    username: str = ""
    subject: str | None


class MyClassInfo(BaseModel):
    class_id: str
    class_name: str
    grade: str | None
    student_number: str | None = None
    subject: str | None = None
    student_count: int = 0
    pending_count: int = 0
    teachers: list[ClassTeacherInfo] = Field(default_factory=list)


class MyClassesResponse(BaseModel):
    role: str
    classes: list[MyClassInfo]


# ── 班级详情 ────────────────────────────────────────────

class ClassDetailTeacher(BaseModel):
    teacher_id: str
    username: str = ""
    display_name: str
    subject: str | None


class ClassDetailResponse(BaseModel):
    id: str
    name: str
    cohort: str | None = None
    created_at: str = ""
    student_count: int = 0
    teachers: list[ClassDetailTeacher] = Field(default_factory=list)
    pending_count: int = 0


# ── 加入申请 ──────────────────────────────────────────────

class JoinRequestCreate(BaseModel):
    student_number: str | None = Field(None, max_length=32, description="学号（选填）")


class ApproveJoinRequest(BaseModel):
    student_number: str | None = Field(None, max_length=32, description="覆盖学号（选填）")


class JoinRequestResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    class_id: str
    class_name: str = ""
    grade: str | None = None
    user_id: str
    user_name: str = ""
    display_name: str = ""
    student_number: str | None
    status: str
    requested_at: datetime
    reviewed_at: datetime | None
    reviewed_by: str | None


class JoinRequestListResponse(BaseModel):
    items: list[JoinRequestResponse]
    total: int


class AvailableClassResponse(BaseModel):
    class_id: str
    name: str
    grade: str | None
    teacher_count: int = 0
    student_count: int = 0
    teachers: list[dict] = Field(default_factory=list, description="科任老师列表")


class AvailableClassListResponse(BaseModel):
    items: list[AvailableClassResponse]
    total: int
