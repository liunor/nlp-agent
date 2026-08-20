"""班级加入申请 Pydantic 契约。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class JoinRequestCreate(BaseModel):
    """学生提交加入申请（学号选填）。"""

    student_number: str | None = Field(None, max_length=32, description="学号（选填）")


class ApproveJoinRequest(BaseModel):
    """教师审批时可选覆盖学号。"""

    student_number: str | None = Field(None, max_length=32, description="覆盖学号（选填）")


class JoinRequestResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    class_id: str
    class_name: str = ""
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
