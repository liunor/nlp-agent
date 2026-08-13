"""只读: 列出 nlp_users 及其角色 (不打印密码哈希)。

运行: ./.venv/Scripts/python.exe list_users.py
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
    if "@" in p.netloc:
        userinfo, host = p.netloc.rsplit("@", 1)
        user = userinfo.split(":", 1)[0]
        return f"{p.scheme}://{user}:***@{host}{p.path}"
    return url


async def main() -> None:
    load_dotenv()
    raw = os.getenv("NLP_AGENT_DATABASE_URL")
    if not raw:
        print("ERROR: 未找到 NLP_AGENT_DATABASE_URL")
        raise SystemExit(2)
    print(f"目标数据库: {mask(raw)}\n")

    engine = create_async_engine(
        raw, poolclass=__import__("sqlalchemy.pool", fromlist=["NullPool"]).NullPool
    )
    try:
        async with engine.connect() as conn:
            total = (await conn.execute(text("SELECT COUNT(*) FROM nlp_users"))).scalar()
            print(f"用户总数: {total}\n")

            q = text(
                "SELECT u.username, u.display_name, u.status, u.authorization_version, "
                "GROUP_CONCAT(r.code ORDER BY r.code SEPARATOR ', ') AS roles "
                "FROM nlp_users u "
                "LEFT JOIN nlp_user_roles ur ON ur.user_id = u.id "
                "LEFT JOIN nlp_roles r ON r.id = ur.role_id "
                "GROUP BY u.id ORDER BY u.username"
            )
            rows = (await conn.execute(q)).mappings().all()
            print(f"{'username':<28}{'display_name':<20}{'status':<10}{'roles'}")
            print("-" * 90)
            for row in rows:
                roles = row["roles"] or "(无角色)"
                print(
                    f"{row['username']:<28}{row['display_name']:<20}"
                    f"{row['status']:<10}{roles}"
                )

            print("\n角色分布:")
            print("-" * 30)
            dist = (await conn.execute(text(
                "SELECT r.code, COUNT(ur.user_id) AS cnt "
                "FROM nlp_roles r "
                "LEFT JOIN nlp_user_roles ur ON ur.role_id = r.id "
                "GROUP BY r.id ORDER BY cnt DESC, r.code"
            ))).mappings().all()
            for d in dist:
                print(f"  {d['code']:<16}{d['cnt']} 人")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
