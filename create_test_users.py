"""创建测试账号: guest01 / student01 / teacher01 / developer01。

因项目 UserService.create_user 在「用户+工作区+成员」同批 flush 时存在
依赖排序缺陷 (workspace_members.workspace_id 为复合主键+外键, 导致工作区
INSERT 被跳过), 这里复刻其创建逻辑, 但用显式分步 flush 保证顺序正确。
密码统一: Test123456。不改项目源码。

运行: ./.venv/Scripts/python.exe create_test_users.py
"""
from __future__ import annotations

import os
import asyncio
import uuid
from dotenv import load_dotenv
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from server.user.service import PasswordHasherSingleton
from server.rbac.service import rbac_service
from server.infrastructure.mysql.models import (
    UserModel,
    WorkspaceModel,
    WorkspaceMemberModel,
)

USERS = [
    ("guest01", "Guest Test", "guest"),
    ("student01", "Student Test", "student"),
    ("teacher01", "Teacher Test", "teacher"),
    ("developer01", "Developer Test", "developer"),
]
PASSWORD = "Test123456"


async def main() -> None:
    load_dotenv()
    url = os.getenv("NLP_AGENT_DATABASE_URL")
    if not url:
        print("ERROR: 未找到 NLP_AGENT_DATABASE_URL")
        raise SystemExit(2)

    engine = create_async_engine(url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    hasher = PasswordHasherSingleton.get()

    async with maker() as session:
        nova = (
            await session.execute(select(UserModel).where(UserModel.username == "nova"))
        ).scalar_one_or_none()
        nova_id = nova.id if nova else None

        for username, display, role in USERS:
            existing = (
                await session.execute(
                    select(UserModel).where(UserModel.username == username)
                )
            ).scalar_one_or_none()
            if existing:
                print(f"SKIP {username} (已存在, 跳过)")
                continue

            # 1) 用户 (argon2 哈希)
            uid = str(uuid.uuid4())
            user = UserModel(
                id=uid,
                username=username,
                password_hash=hasher.hash(PASSWORD),
                display_name=display,
                status="active",
                authorization_version=1,
            )
            session.add(user)
            await session.flush()

            # 2) 个人工作区
            ws_id = str(uuid.uuid4())
            ws = WorkspaceModel(
                id=ws_id,
                slug=f"user-{username}",
                name=f"{display}'s Workspace",
                status="active",
            )
            session.add(ws)
            await session.flush()

            # 3) 工作区 owner 成员
            member = WorkspaceMemberModel(
                workspace_id=ws.id, user_id=user.id, member_type="owner", status="active"
            )
            session.add(member)
            await session.flush()

            # 4) 授予角色
            await rbac_service.replace_user_roles(
                session, user_id=uid, role_codes={role}, assigned_by_user_id=nova_id
            )
            await session.commit()
            print(f"CREATED {username:<12} 角色={role:<10} id={uid}")

    await engine.dispose()
    print("\n完成。所有账号密码均为: Test123456")


if __name__ == "__main__":
    asyncio.run(main())
