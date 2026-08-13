"""Inspect account storage: nova vs test accounts, show diffs."""
from __future__ import annotations

import asyncio
import os
from dotenv import load_dotenv
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import sessionmaker

load_dotenv()
URL = os.environ["NLP_AGENT_DATABASE_URL"]


async def main() -> None:
    engine = create_async_engine(URL)
    Session = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async with Session() as s:
        # --- nova vs test accounts structural diff ---
        print("=" * 70)
        print("1) USER ROW STRUCTURE (nova vs each test account)")
        print("=" * 70)
        rows = (await s.execute(text("""
            SELECT username, display_name, status, authorization_version,
                   LENGTH(password_hash) AS hash_len,
                   SUBSTRING(password_hash, 1, 7) AS hash_prefix
              FROM nlp_users
             WHERE username IN ('nova','guest01','student01','teacher01','developer01')
             ORDER BY username
        """))).mappings().all()
        for r in rows:
            print(f"  {r['username']:<14} display={r['display_name']:<14} "
                  f"status={r['status']:<8} auth_ver={r['authorization_version']} "
                  f"hash_len={r['hash_len']:<3} prefix={r['hash_prefix']}")

        # --- workspace ownership ---
        print()
        print("=" * 70)
        print("2) PERSONAL WORKSPACE + OWNER MEMBERSHIP")
        print("=" * 70)
        rows = (await s.execute(text("""
            SELECT u.username, w.slug, w.name, w.status AS ws_status, m.member_type, m.status AS m_status
              FROM nlp_users u
              JOIN nlp_workspace_members m ON m.user_id = u.id
              JOIN nlp_workspaces w ON w.id = m.workspace_id
             WHERE u.username IN ('nova','guest01','student01','teacher01','developer01')
             ORDER BY u.username
        """))).mappings().all()
        for r in rows:
            print(f"  {r['username']:<14} ws.slug={r['slug']:<22} "
                  f"member_type={r['member_type']:<8} ws_status={r['ws_status']:<8} m_status={r['m_status']}")

        # --- role assignments ---
        print()
        print("=" * 70)
        print("3) ROLE ASSIGNMENTS")
        print("=" * 70)
        rows = (await s.execute(text("""
            SELECT u.username, GROUP_CONCAT(r.code ORDER BY r.code) AS roles,
                   MAX(ur.assigned_by_user_id IS NULL) AS assigned_by_null
              FROM nlp_users u
              JOIN nlp_user_roles ur ON ur.user_id = u.id
              JOIN nlp_roles r ON r.id = ur.role_id
             WHERE u.username IN ('nova','guest01','student01','teacher01','developer01')
             GROUP BY u.username
             ORDER BY u.username
        """))).mappings().all()
        for r in rows:
            print(f"  {r['username']:<14} roles={r['roles']:<60} "
                  f"assigned_by_null={bool(r['assigned_by_null'])}")

        # --- env config (SingleSourceOfTruth) ---
        print()
        print("=" * 70)
        print("4) ENV CONFIG (the single-credential side of 'nova mode')")
        print("=" * 70)
        for key in ("NLP_AGENT_AUTH_USERNAME", "NLP_AGENT_AUTH_ROLES"):
            v = os.environ.get(key, "")
            masked = "*" * min(8, len(v)) + v[8:] if len(v) > 8 else ("<empty>" if not v else "set")
            print(f"  {key} = {masked}")
        v = os.environ.get("NLP_AGENT_AUTH_PASSWORD_HASH", "")
        print(f"  NLP_AGENT_AUTH_PASSWORD_HASH length={len(v)}  set={'YES' if v else 'NO'}")

    await engine.dispose()


import asyncio
from sqlalchemy.ext.asyncio import AsyncSession

if __name__ == "__main__":
    asyncio.run(main())