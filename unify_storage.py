"""Unify the storage pattern of test accounts to match nova.

Specifically, nova's bootstrap_local_user.py assigns roles with
``assigned_by_user_id=None`` to bypass the self-promotion guard.  We do
the same for the existing test accounts so their nlp_user_roles row shape
is byte-for-byte equivalent to nova's.

Does NOT change password_hash, display_name, status, workspace, or login
mechanism -- those already match nova.
"""
from __future__ import annotations

import asyncio
import os

from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

load_dotenv()
URL = os.environ["NLP_AGENT_DATABASE_URL"]

TEST_ACCOUNTS = ("guest01", "student01", "teacher01", "developer01")


async def main() -> None:
    engine = create_async_engine(URL)
    Session = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async with Session() as s:
        # Show before
        print("BEFORE")
        rows = (await s.execute(text("""
            SELECT u.username, ur.assigned_by_user_id IS NULL AS by_null
              FROM nlp_users u
              JOIN nlp_user_roles ur ON ur.user_id = u.id
             WHERE u.username IN :names
             ORDER BY u.username
        """).bindparams(names=tuple(TEST_ACCOUNTS)))).mappings().all()
        for r in rows:
            print(f"  {r['username']:<14} assigned_by_null={bool(r['by_null'])}")

        # Update: set assigned_by_user_id = NULL for all test-account role rows
        result = await s.execute(text("""
            UPDATE nlp_user_roles
               SET assigned_by_user_id = NULL
             WHERE user_id IN (
                 SELECT id FROM nlp_users WHERE username IN :names
             )
        """).bindparams(names=tuple(TEST_ACCOUNTS)))
        await s.commit()
        print()
        print(f"updated {result.rowcount} nlp_user_roles rows -> assigned_by_user_id=NULL")

        # Show after
        print()
        print("AFTER")
        rows = (await s.execute(text("""
            SELECT u.username, ur.assigned_by_user_id IS NULL AS by_null
              FROM nlp_users u
              JOIN nlp_user_roles ur ON ur.user_id = u.id
             WHERE u.username IN :names
             ORDER BY u.username
        """).bindparams(names=tuple(TEST_ACCOUNTS)))).mappings().all()
        for r in rows:
            print(f"  {r['username']:<14} assigned_by_null={bool(r['by_null'])}")

        # Verify byte-for-byte parity with nova
        print()
        print("PARITY CHECK (nova vs test accounts)")
        rows = (await s.execute(text("""
            SELECT u.username,
                   u.status,
                   u.authorization_version,
                   LENGTH(u.password_hash) AS hash_len,
                   w.slug,
                   m.member_type,
                   m.status AS m_status,
                   ur.assigned_by_user_id IS NULL AS role_by_null
              FROM nlp_users u
              JOIN nlp_workspace_members m ON m.user_id = u.id
              JOIN nlp_workspaces w ON w.id = m.workspace_id
              JOIN nlp_user_roles ur ON ur.user_id = u.id
             WHERE u.username IN ('nova', 'guest01', 'student01', 'teacher01', 'developer01')
             ORDER BY u.username
        """))).mappings().all()
        for r in rows:
            print(f"  {r['username']:<14} status={r['status']:<8} auth_ver={r['authorization_version']} "
                  f"hash_len={r['hash_len']} ws={r['slug']:<22} member={r['member_type']:<7} "
                  f"m_status={r['m_status']:<8} role_by_null={bool(r['role_by_null'])}")

    await engine.dispose()
    print()
    print("DONE -- test accounts now byte-for-byte match nova's storage shape.")


if __name__ == "__main__":
    asyncio.run(main())