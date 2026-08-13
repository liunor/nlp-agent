"""Local bootstrap for a fixed-auth account (e.g. the NLP_AGENT_AUTH_USERNAME user).

The app authenticates the fixed credentials from .env, but every authenticated
request reloads the principal from MySQL via ``rbac_service.principal_for_username``.
If the user has no row in ``nlp_users`` (and no role membership), that lookup raises
``PermissionError`` and the request fails with 403 -- which makes the UI look like
"login did nothing".

Run once after Alembic migration:

    uv run python scripts/bootstrap_local_user.py --username nova --password nova123456 --role developer

The script is idempotent: it skips user creation / role assignment when already present.
"""

from __future__ import annotations

import argparse
import asyncio
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from argon2 import PasswordHasher, Type

from configs.settings import settings
from server.infrastructure.mysql import MySQLRuntime
from server.infrastructure.mysql.models import (
    RoleModel,
    UserModel,
    WorkspaceModel,
    WorkspaceMemberModel,
)
from server.rbac.service import rbac_service


def _hasher() -> PasswordHasher:
    return PasswordHasher(time_cost=4, memory_cost=65536, parallelism=4, hash_len=32, salt_len=16, type=Type.ID)


async def bootstrap(username: str, password: str, display_name: str, role_codes: set[str]) -> None:
    runtime = MySQLRuntime.from_runtime(settings.database_runtime)
    await runtime.start()
    try:
        async with runtime.session_factory() as session:
            user = await session.scalar(select(UserModel).where(UserModel.username == username))
            if user is None:
                user = UserModel(
                    id=str(uuid.uuid4()),
                    username=username,
                    password_hash=_hasher().hash(password),
                    display_name=display_name,
                    status="active",
                    authorization_version=1,
                )
                session.add(user)
                await session.flush()
                print(f"created user '{username}' (id={user.id})")

                workspace = WorkspaceModel(
                    id=str(uuid.uuid4()),
                    slug=f"user-{username}",
                    name=f"{display_name}'s Workspace",
                    status="active",
                )
                session.add(workspace)
                await session.flush()

                session.add(
                    WorkspaceMemberModel(
                        workspace_id=workspace.id,
                        user_id=user.id,
                        member_type="owner",
                        status="active",
                    )
                )
                await session.flush()
                print(f"created personal workspace {workspace.id} (owner)")
            else:
                print(f"user '{username}' already exists (id={user.id})")

            # assigned_by_user_id=None mirrors bootstrap_developer.py and avoids the
            # "users cannot grant themselves additional roles" self-promotion guard.
            assigned = await rbac_service.replace_user_roles(
                session,
                user_id=user.id,
                role_codes=set(role_codes),
                assigned_by_user_id=None,
            )
            await session.commit()
            print(f"assigned roles to '{username}': {sorted(assigned)}")
    finally:
        await runtime.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Bootstrap a local fixed-auth user")
    parser.add_argument("--username", default="nova", help="must match NLP_AGENT_AUTH_USERNAME")
    parser.add_argument("--password", default="nova123456", help="password for the nlp_users row")
    parser.add_argument("--display-name", default="Nova", help="display name")
    parser.add_argument(
        "--role",
        default="developer",
        help="role code to assign (guest/student/teacher/developer)",
    )
    args = parser.parse_args()
    asyncio.run(
        bootstrap(
            username=args.username,
            password=args.password,
            display_name=args.display_name,
            role_codes={args.role},
        )
    )


if __name__ == "__main__":
    main()
