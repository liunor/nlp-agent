"""CLI adapter for the lifecycle-owning Backend Gateway Core."""

from __future__ import annotations

import asyncio
import sys

from gateway.contracts import GatewayEventType, SubmitTurnRequest
from gateway.core import BackendGateway


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def check_config() -> bool:
    from configs.settings import settings

    config = settings.planner_llm
    print(f"Coordinator: {config['model_id']} ({config['base_url']})")
    print(f"Worker:      {settings.tool_llm['model_id']}")
    if not config.get("api_key_configured"):
        print("Missing DEEPSEEK_API_KEY; create .env in the project root.")
        return False
    return True


async def _input(prompt: str) -> str:
    return await asyncio.to_thread(input, prompt)


async def bootstrap_developer() -> None:
    """Provision the first developer without storing a fixed account in code."""
    from getpass import getpass

    from sqlalchemy import select

    from configs.settings import settings
    from server.infrastructure.mysql import MySQLRuntime
    from server.infrastructure.mysql.models import (
        RoleModel,
        UserModel,
        UserRoleModel,
    )
    from server.rbac.service import rbac_service
    from server.user.schemas import UserCreate
    from server.user.service import UserService

    username = (await _input("Developer username: ")).strip().lower()
    display_name = (await _input("Display name: ")).strip()
    password = await asyncio.to_thread(getpass, "Password (at least 8 characters): ")
    confirmation = await asyncio.to_thread(getpass, "Confirm password: ")
    if not username or not display_name or len(password) < 8 or password != confirmation:
        raise SystemExit("invalid username/display name/password confirmation")

    runtime = MySQLRuntime.from_runtime(settings.database_runtime)
    await runtime.start()
    try:
        async with runtime.session_factory.begin() as session:
            existing_developer = await session.scalar(
                select(UserModel.id)
                .join(UserRoleModel, UserRoleModel.user_id == UserModel.id)
                .join(RoleModel, RoleModel.id == UserRoleModel.role_id)
                .where(
                    RoleModel.code == "developer",
                    RoleModel.status == "active",
                    UserModel.status == "active",
                    UserModel.deleted_at.is_(None),
                )
            )
            if existing_developer is not None:
                raise SystemExit("an active developer already exists; use the developer platform")

            service = UserService(session)
            user = await session.scalar(
                select(UserModel).where(UserModel.username_lower == username).with_for_update()
            )
            if user is None:
                user = await service.create_user(
                    UserCreate(
                        username=username,
                        display_name=display_name,
                        password=password,
                    )
                )
            else:
                if user.deleted_at is not None or user.status != "active":
                    raise SystemExit("the requested username belongs to a disabled or deleted user")
                user.display_name = display_name
                await service.change_password(user.id, password)

            await rbac_service.replace_user_roles(
                session,
                user_id=user.id,
                role_codes={"developer"},
                assigned_by_user_id=None,
            )
            print(f"Developer account provisioned: {user.username}")
    finally:
        await runtime.close()


async def main() -> None:
    if not check_config():
        raise SystemExit(1)

    from getpass import getpass

    from configs.settings import settings
    from server.agent.session_service import DatabaseSessionService
    from server.rbac.service import rbac_service
    from server.web.database_auth import DatabaseSessionAuth

    gateway = BackendGateway()
    await gateway.start()
    session_factory = gateway.authorization_session_factory
    if session_factory is None:
        await gateway.close()
        raise SystemExit("CLI chat requires MySQL persistence and a database-backed user")
    auth = DatabaseSessionAuth.from_config(settings.web_runtime)
    username = (await _input("Username: ")).strip()
    password = await asyncio.to_thread(getpass, "Password: ")
    token, claims = await auth.login(session_factory, username, password, client_key="cli")
    async with session_factory() as session:
        principal = await rbac_service.principal_for_user_id(session, claims.user_id)
    gateway.sessions = DatabaseSessionService(session_factory)
    existing_sessions = await gateway.sessions.list(principal)
    active_session_id = existing_sessions[0]["session_id"] if existing_sessions else None
    if not active_session_id:
        active_session_id = (
            await gateway.create_session(principal, workspace_id=claims.workspace_id, channel="cli")
        ).session_id

    print("Enter a question. Commands: /new, /sessions, /load <id>, /exit")
    try:
        while True:
            query = (await _input("\nYou: ")).strip()
            if not query:
                continue
            if query == "/exit":
                break
            if query == "/new":
                active_session_id = (
                    await gateway.create_session(
                        principal, workspace_id=claims.workspace_id, channel="cli"
                    )
                ).session_id
                print(f"[system] New session: {active_session_id}")
                continue
            if query == "/sessions":
                for session in await gateway.sessions.list(principal):
                    print(f"- {session['session_id']} | {session.get('last_active', 0)}")
                continue
            if query.startswith("/load "):
                target = query.split(maxsplit=1)[1]
                try:
                    await gateway.sessions.resolve(principal, target)
                except (FileNotFoundError, PermissionError):
                    print("[system] Session not found")
                else:
                    active_session_id = target
                    print(f"[system] Loaded: {target}")
                continue

            accepted = await gateway.submit_turn(
                principal,
                SubmitTurnRequest(session_id=active_session_id, content=query),
            )
            printed = False
            async for event in gateway.stream_events(principal, accepted.turn_id):
                if event.type != GatewayEventType.MESSAGE_DELTA:
                    continue
                if event.payload.get("channel") == "reasoning":
                    continue
                delta = event.payload.get("delta", "")
                if not delta:
                    continue
                if not printed:
                    print("\nAgent: ", end="", flush=True)
                    printed = True
                print(delta, end="", flush=True)
            if printed:
                print()
            else:
                turn = await gateway.get_turn(principal, accepted.turn_id)
                print(f"\nAgent: {turn.final_text or turn.error_message or ''}")
    finally:
        await gateway.close()
        await auth.revoke_token(session_factory, token)


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else "chat"
    if command in {"serve", "web"}:
        from server.web.__main__ import run

        run()
    elif command in {"monitor", "observe"}:
        from server.monitor.__main__ import run

        run()
    elif command == "worker":
        from server.worker.runtime import run_worker

        asyncio.run(run_worker())
    elif command in {"bootstrap-developer", "bootstrap_developer"}:
        asyncio.run(bootstrap_developer())
    elif command in {"chat", "--chat", "-c"}:
        asyncio.run(main())
    else:
        print("Usage: python main.py [chat|serve|monitor|worker|bootstrap-developer]")
        raise SystemExit(2)
