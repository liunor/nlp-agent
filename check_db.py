"""只读检查: 数据库是否可达、当前迁移版本、RBAC 种子与班级表现状。

运行: ./.venv/Scripts/python.exe check_db.py
安全: 只 SELECT, 不写库。密码在输出中做掩码。
"""
from __future__ import annotations

import os
import asyncio
from urllib.parse import urlparse

from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


def mask(url: str) -> str:
    p = urlparse(url)
    netloc = p.netloc
    if "@" in netloc:
        userinfo, host = netloc.rsplit("@", 1)
        if ":" in userinfo:
            user, _ = userinfo.split(":", 1)
            netloc = f"{user}:***@{host}"
    return f"{p.scheme}://{netloc}{p.path}"


async def main() -> None:
    load_dotenv()
    raw = os.getenv("NLP_AGENT_DATABASE_URL")
    if not raw:
        print("ERROR: 未找到 NLP_AGENT_DATABASE_URL 环境变量 (检查 .env)")
        raise SystemExit(2)
    print(f"目标数据库: {mask(raw)}")

    engine = create_async_engine(raw, poolclass=__import__("sqlalchemy.pool", fromlist=["NullPool"]).NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
            print("连接: OK")

            # 当前 alembic 版本
            try:
                ver = await conn.execute(text("SELECT version_num FROM alembic_version"))
                row = ver.first()
                print(f"当前迁移版本: {row[0] if row else '(无 alembic_version 表/未初始化)'}")
            except Exception as e:
                print(f"当前迁移版本: 读取失败 ({type(e).__name__}) — 可能尚未初始化")

            tables = [
                "nlp_roles", "nlp_permissions", "nlp_role_permissions",
                "nlp_role_permission_scopes", "nlp_user_roles",
                "nlp_classes", "nlp_class_enrollments", "nlp_class_teachers",
                "nlp_class_join_requests",
            ]
            print("-" * 60)
            print(f"{'表':<28}{'是否存在':<10}{'行数'}")
            print("-" * 60)
            for t in tables:
                try:
                    r = await conn.execute(text(f"SELECT COUNT(*) FROM {t}"))
                    cnt = r.scalar()
                    print(f"{t:<28}{'是':<10}{cnt}")
                except Exception:
                    print(f"{t:<28}{'否(缺失)':<10}-")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
