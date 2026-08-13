"""联调: 班级加入申请端到端流程 (走真实 DB + 迁移后的 4 张新表)。

流程: 登录校验 -> teacher01 建班 -> student01 提交申请 -> teacher01 审核通过 -> 验证入班。
运行: ./.venv/Scripts/python.exe test_class_flow.py
"""
from __future__ import annotations

import os
import asyncio
from dotenv import load_dotenv
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from server.user.service import UserService
from server.rbac.service import rbac_service
from server.infrastructure.mysql.models import UserModel
from server.classroom_join.service import (
    create_class,
    submit_join_request,
    list_pending_requests,
    approve_join_request,
    list_class_students,
    get_available_classes,
    get_user_by_username,
)

PASSWORD = "Test123456"


async def main() -> None:
    load_dotenv()
    url = os.getenv("NLP_AGENT_DATABASE_URL")
    engine = create_async_engine(url)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async with maker() as db:
        print("=" * 70)
        print("1) 登录可用性校验 (argon2 验证各账号密码)")
        print("=" * 70)
        ok = True
        for uname in ["guest01", "student01", "teacher01", "developer01"]:
            u = await get_user_by_username(db, uname)
            assert u, f"{uname} 未创建"
            good = await UserService(db).verify_password(u, PASSWORD)
            roles = await rbac_service.user_role_codes(db, u.id)
            flag = "PASS" if good else "FAIL"
            ok &= good
            print(f"  {uname:<12} 密码正确={good}  角色={sorted(roles)}  [{flag}]")

        teacher = await get_user_by_username(db, "teacher01")
        student = await get_user_by_username(db, "student01")

        print()
        print("=" * 70)
        print("2) 班级加入申请流程")
        print("=" * 70)

        # teacher01 建班 (自动成为班级教师)
        cls = await create_class(db, name="自然语言处理一班", grade="2024", created_by=teacher.id)
        await db.flush()
        print(f"  教师建班: {cls.name} (id={cls.id[:8]})")

        # student01 提交加入申请
        req = await submit_join_request(db, cls.id, student.id, student_number="2024001")
        await db.flush()
        print(f"  学生提交申请: req_id={req.id[:8]} status={req.status}")

        # 待审列表应有 1 条
        pending = await list_pending_requests(db, cls.id)
        p_ok = len(pending) == 1
        ok &= p_ok
        print(f"  待审列表数量=1: {p_ok}  (实际 {len(pending)})")

        # teacher01 审核通过
        approved = await approve_join_request(db, req.id, reviewed_by=teacher.id)
        await db.flush()
        a_ok = approved is not None and approved.status == "approved"
        ok &= a_ok
        print(f"  教师审核通过: status={approved.status if approved else None}  [{ 'PASS' if a_ok else 'FAIL'}]")

        # 学生应已入班
        students, total = await list_class_students(db, cls.id)
        e_ok = total == 1 and len(students) == 1 and students[0].user_id == student.id
        ok &= e_ok
        print(f"  班级学生数量=1 且为 student01: {e_ok}  (实际 {total})")

        # 学生视角: 可加入班级列表不应再包含该班 (已入班)
        avail, total = await get_available_classes(db, student.id)
        avail_ids = {c.id for c in avail}
        na_ok = cls.id not in avail_ids
        ok &= na_ok
        print(f"  学生『可加入班级』已排除该班: {na_ok}")

        await db.commit()

    await engine.dispose()
    print()
    print("=" * 70)
    print(f"联调结果: {'ALL PASS ✅' if ok else 'FAIL ❌'}")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
