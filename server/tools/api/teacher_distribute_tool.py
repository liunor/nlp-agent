"""Teacher file distribution tool for the agent coordinator/worker."""

from __future__ import annotations

import asyncio
from typing import Any, Literal

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from pydantic import BaseModel, Field
from sqlalchemy import select

from core.identity import AuthenticatedPrincipal
from core.runtime_config import settings
from core.session_context import SessionContext
from server.infrastructure.mysql import MySQLRuntime
from server.infrastructure.mysql.models import RoleModel, UserFileModel, UserModel, UserRoleModel
from server.storage.distribution_service import (
    DistributionError,
    DistributionPermissionError,
    FileDistributionService,
)


class TeacherDistributeFileInput(BaseModel):
    file_name: str = Field(
        description="要分发发送的文件名称（例如：'课件.pdf'、'张量与形状.md'，须已在教师的文件空间中）"
    )
    target_scope: Literal["all_students", "all_users", "all_guests", "all_admins", "role", "classroom", "user"] = Field(
        description="分发目标范围：all_students(全体学生), all_users(全体用户), all_guests(全体游客), all_admins(全体管理员), role(某一角色类别), classroom(指定班级), user(指定某一个用户)"
    )
    target_detail: str | None = Field(
        default=None,
        description="目标具体标识：当 target_scope 为 role 时为角色名('student'/'guest'/'admin'/'teacher')；为 classroom 时为班级ID；为 user 时为用户身份ID或用户名",
    )
    direct_delivery: bool = Field(
        default=True,
        description="是否直接存入接收方文件列表（无需对方手动同意，默认 True）",
    )


@tool("teacher_distribute_file", args_schema=TeacherDistributeFileInput)
def teacher_distribute_file(
    config: RunnableConfig,
    file_name: str,
    target_scope: Literal["all_students", "all_users", "all_guests", "all_admins", "role", "classroom", "user"],
    target_detail: str | None = None,
    direct_delivery: bool = True,
) -> str:
    """向学生或用户发送/分发文件。
    仅限教师或管理员使用。支持向【所有学生】、【所有用户】、【某一类用户】或【指定某个用户】直接发送文件。
    """
    ctx = SessionContext.from_config(config)
    user_id = ctx.user_id
    workspace_id = ctx.workspace_id

    if not user_id or user_id in {"local", "system"}:
        return "分发失败：无法获取当前登录教师身份。"

    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    async def _run() -> str:
        db_runtime = MySQLRuntime.from_runtime(settings.database_runtime)
        session_factory = db_runtime.session_factory

        async with session_factory() as session:
            async with session.begin():
                # 1. 验证用户角色
                roles = set(
                    (
                        await session.scalars(
                            select(RoleModel.code)
                            .join(UserRoleModel, UserRoleModel.role_id == RoleModel.id)
                            .where(UserRoleModel.user_id == user_id, RoleModel.status == "active")
                        )
                    ).all()
                )
                if not any(r in {"teacher", "developer", "admin"} for r in roles):
                    return "分发失败：当前操作账号没有教师或管理员权限，无法发送文件。"

                principal = AuthenticatedPrincipal(
                    user_id=user_id,
                    workspace_ids=frozenset({workspace_id, "*"}),
                    roles=frozenset(roles),
                )

                # 2. 查找源文件
                clean_name = file_name.strip()
                source_file = await session.scalar(
                    select(UserFileModel).where(
                        UserFileModel.owner_user_id == user_id,
                        UserFileModel.display_name == clean_name,
                        UserFileModel.kind == "file",
                        UserFileModel.status == "active",
                    )
                )
                if source_file is None:
                    # 模糊匹配
                    source_file = await session.scalar(
                        select(UserFileModel).where(
                            UserFileModel.owner_user_id == user_id,
                            UserFileModel.display_name.ilike(f"%{clean_name}%"),
                            UserFileModel.kind == "file",
                            UserFileModel.status == "active",
                        )
                    )
                if source_file is None:
                    # 列出用户前 5 个文件做提示
                    available = list(
                        (
                            await session.scalars(
                                select(UserFileModel.display_name)
                                .where(
                                    UserFileModel.owner_user_id == user_id,
                                    UserFileModel.kind == "file",
                                    UserFileModel.status == "active",
                                )
                                .limit(5)
                            )
                        ).all()
                    )
                    hint = f"当前云盘中的文件有：{', '.join(available)}" if available else "当前云盘暂无可用文件，请先上传。"
                    return f"分发失败：未在您的云盘中找到文件 '{clean_name}'。{hint}"

                service = FileDistributionService(session, principal, workspace_id)
                res = await service.distribute_file(
                    source_file_id=source_file.id,
                    target_type=target_scope,
                    target_value=target_detail,
                    direct_delivery=direct_delivery,
                )

                size_kb = round(res["size_bytes"] / 1024, 1)
                mode_str = "直接写入接收方文件空间" if direct_delivery else "已发送接收请求"
                summary = (
                    f"【文件分发报告】\n"
                    f"• 文件名：{res['file_name']} ({size_kb} KB)\n"
                    f"• 分发目标：{res['target_label']}\n"
                    f"• 发送方式：{mode_str}\n"
                    f"• 目标总数：{res['total_targets']} 人\n"
                    f"• 成功送达：{res['success_count']} 人\n"
                    f"• 失败人数：{res['failed_count']} 人"
                )
                if res["failures"]:
                    failure_reasons = "\n".join(
                        f"  - {f.get('display_name', '用户')} ({f.get('identity_id', '')}): {f.get('reason')}"
                        for f in res["failures"][:5]
                    )
                    summary += f"\n• 失败详情（前5条）：\n{failure_reasons}"

                return summary

    try:
        if loop.is_running():
            import nest_asyncio
            nest_asyncio.apply()
            return loop.run_until_complete(_run())
        else:
            return loop.run_until_complete(_run())
    except Exception as err:
        return f"文件分发执行异常：{err}"
