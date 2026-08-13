"""Diagnose why login / permissions behave unexpectedly.

Reads the same config the app uses, and resolves nova's runtime principal
from MySQL the same way the HTTP layer does (rbac_service.principal_for_username).
Prints auth config (password hash masked) and resolved roles/permissions.
"""
from __future__ import annotations

import asyncio
import os

from dotenv import load_dotenv
from sqlalchemy import select

from configs.settings import settings
from server.infrastructure.mysql import MySQLRuntime
from server.rbac.service import rbac_service
from server.infrastructure.mysql.models import UserModel


async def main() -> None:
    load_dotenv()
    print("=== .env / settings: auth config ===")
    uname = os.getenv("NLP_AGENT_AUTH_USERNAME", "")
    uroles = os.getenv("NLP_AGENT_AUTH_ROLES", "")
    phash = os.getenv("NLP_AGENT_AUTH_PASSWORD_HASH", "")
    print(f"  NLP_AGENT_AUTH_USERNAME      = {uname!r}")
    print(f"  NLP_AGENT_AUTH_ROLES         = {uroles!r}")
    print(f"  NLP_AGENT_AUTH_PASSWORD_HASH = {'<set len=%d>' % len(phash) if phash else '<EMPTY>'}")

    runtime = MySQLRuntime.from_runtime(settings.database_runtime)
    await runtime.start()
    try:
        async with runtime.session_factory() as session:
            # Which username does the app actually authenticate as?
            print("\n=== resolve configured username from MySQL ===")
            if uname:
                try:
                    p = await rbac_service.principal_for_username(session, uname)
                    print(f"  principal_for_username({uname!r}) -> roles={sorted(p.roles)} perms={len(p.permissions)} ws={sorted(p.workspace_ids)}")
                except Exception as e:
                    print(f"  principal_for_username({uname!r}) FAILED: {type(e).__name__}: {e}")
            else:
                print("  (auth_username empty -> credentials_configured=False -> login() always raises)")

            # And nova specifically
            print("\n=== resolve 'nova' from MySQL ===")
            nova = await session.scalar(select(UserModel).where(UserModel.username == "nova"))
            if nova is None:
                print("  nova row does NOT exist in nlp_users")
            else:
                print(f"  nova exists: id={nova.id} status={nova.status}")
                try:
                    p2 = await rbac_service.principal_for_user_id(session, nova.id)
                    print(f"  resolved roles={sorted(p2.roles)} permissions={sorted(p2.permissions)}")
                    print(f"  permission count = {len(p2.permissions)}")
                except Exception as e:
                    print(f"  resolve FAILED: {type(e).__name__}: {e}")
    finally:
        await runtime.close()


asyncio.run(main())
