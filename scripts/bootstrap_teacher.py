"""Bootstrap a Pro_NLP teacher account without an existing admin.

Run after Alembic migration: ``uv run python main.py bootstrap-teacher``.
The command creates the user when missing, or resets the display name and
password of an existing active user, then assigns the built-in ``teacher``
role. Passwords are only read interactively or via arguments for local
bootstrap and never written to the repository.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from configs.settings import settings


async def _input(prompt: str) -> str:
    return await asyncio.to_thread(input, prompt)


async def bootstrap(
    username: str | None, display_name: str | None, password: str | None
) -> None:
    from getpass import getpass

    from sqlalchemy import select

    from server.infrastructure.mysql import MySQLRuntime
    from server.infrastructure.mysql.models import RoleModel, UserModel, UserRoleModel
    from server.user.schemas import UserCreate
    from server.user.service import UserService

    if not username:
        username = (await _input("Teacher username: ")).strip().lower()
    else:
        username = username.strip().lower()
    if not display_name:
        display_name = (await _input("Display name: ")).strip()
    if not password:
        password = await asyncio.to_thread(
            getpass, "Password (at least 8 characters): "
        )
        confirmation = await asyncio.to_thread(getpass, "Confirm password: ")
        if password != confirmation:
            raise SystemExit("password confirmation does not match")
    if not username or not display_name or len(password) < 8:
        raise SystemExit("invalid username/display name/password")

    runtime = MySQLRuntime.from_runtime(settings.database_runtime)
    await runtime.start()
    try:
        async with runtime.session_factory.begin() as session:
            service = UserService(session)
            user = await session.scalar(
                select(UserModel)
                .where(UserModel.username_lower == username.casefold())
                .with_for_update()
            )
            if user is None:
                user = await service.create_user(
                    UserCreate(
                        username=username,
                        display_name=display_name,
                        password=password,
                    )
                )
                action = "created"
            else:
                if user.deleted_at is not None or user.status != "active":
                    raise SystemExit(
                        "the requested username belongs to a disabled or deleted user"
                    )
                user.display_name = display_name
                await service.change_password(user.id, password)
                action = "updated"

            teacher_role = await session.scalar(
                select(RoleModel).where(
                    RoleModel.code == "teacher", RoleModel.status == "active"
                )
            )
            if teacher_role is None:
                raise SystemExit("the built-in teacher role is missing or disabled")
            current_roles = set(
                await session.scalars(
                    select(RoleModel.code)
                    .join(UserRoleModel, UserRoleModel.role_id == RoleModel.id)
                    .where(
                        UserRoleModel.user_id == user.id,
                        RoleModel.status == "active",
                    )
                )
            )
            if "teacher" not in current_roles:
                session.add(
                    UserRoleModel(
                        user_id=user.id,
                        role_id=teacher_role.id,
                        assigned_by_user_id=None,
                    )
                )
                user.authorization_version += 1
            print(f"teacher account {action}: {user.username}")
    finally:
        await runtime.close()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Bootstrap a Pro_NLP teacher account")
    parser.add_argument("--username", default="")
    parser.add_argument("--display-name", default="")
    parser.add_argument("--password", default="")
    args = parser.parse_args(argv)
    asyncio.run(
        bootstrap(
            args.username or None, args.display_name or None, args.password or None
        )
    )


if __name__ == "__main__":
    main()
