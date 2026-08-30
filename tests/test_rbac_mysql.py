from __future__ import annotations

import os
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from server.infrastructure.mysql import DatabaseConfig, create_engine, create_session_factory
from server.infrastructure.mysql.models import RoleModel, UserModel
from server.rbac.service import UnknownRoleError, rbac_service


@pytest.fixture
async def mysql_session_factory():
    database_url = os.getenv("NLP_AGENT_DATABASE_URL")
    if not database_url:
        pytest.skip("MySQL integration database is not configured")
    engine = create_engine(DatabaseConfig(database_url))
    try:
        yield create_session_factory(engine)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_mysql_rbac_assignment_replaces_roles_and_bumps_authorization_version(
    mysql_session_factory: async_sessionmaker,
) -> None:
    user_id = str(uuid4())
    async with mysql_session_factory() as session:
        async with session.begin():
            session.add(
                UserModel(
                    id=user_id,
                    username=f"rbac-{user_id[:8]}",
                    password_hash="not-used-by-this-test",
                    display_name="RBAC test user",
                )
            )
        async with session.begin():
            assigned = await rbac_service.replace_user_roles(
                session,
                user_id=user_id,
                role_codes={"student", "teacher"},
                assigned_by_user_id=None,
            )
            assert assigned == {"student", "teacher"}
        async with session.begin():
            assert await rbac_service.roles_for(session, user_id) == {"student", "teacher"}
            assigned = await rbac_service.replace_user_roles(
                session,
                user_id=user_id,
                role_codes={"guest"},
                assigned_by_user_id=None,
            )
            assert assigned == {"guest"}
        async with session.begin():
            user = await session.get(UserModel, user_id)
            assert user is not None
            assert user.authorization_version == 3
            assert await rbac_service.roles_for(session, user_id) == {"guest"}
            await session.delete(user)


@pytest.mark.asyncio
async def test_mysql_rbac_rejects_unknown_role_without_changing_assignments(
    mysql_session_factory: async_sessionmaker,
) -> None:
    user_id = str(uuid4())
    async with mysql_session_factory() as session:
        async with session.begin():
            session.add(
                UserModel(
                    id=user_id,
                    username=f"rbac-{user_id[:8]}",
                    password_hash="not-used-by-this-test",
                    display_name="RBAC test user",
                )
            )
        async with session.begin():
            with pytest.raises(UnknownRoleError, match="not-a-role"):
                await rbac_service.replace_user_roles(
                    session,
                    user_id=user_id,
                    role_codes={"not-a-role"},
                    assigned_by_user_id=None,
                )
        async with session.begin():
            assert await rbac_service.roles_for(session, user_id) == frozenset()
            user = await session.get(UserModel, user_id)
            assert user is not None
            await session.delete(user)


@pytest.mark.asyncio
async def test_mysql_rbac_rejects_disabled_role_without_changing_assignments(
    mysql_session_factory: async_sessionmaker,
) -> None:
    user_id = str(uuid4())
    role_id = str(uuid4())
    async with mysql_session_factory() as session:
        async with session.begin():
            session.add(
                UserModel(
                    id=user_id,
                    username=f"rbac-{user_id[:8]}",
                    password_hash="not-used-by-this-test",
                    display_name="RBAC test user",
                )
            )
            session.add(
                RoleModel(
                    id=role_id,
                    code=f"disabled-{role_id[:8]}",
                    name="Disabled role",
                    status="disabled",
                    is_builtin=False,
                )
            )
        async with session.begin():
            with pytest.raises(UnknownRoleError, match="unknown or inactive"):
                await rbac_service.replace_user_roles(
                    session,
                    user_id=user_id,
                    role_codes={f"disabled-{role_id[:8]}"},
                    assigned_by_user_id=None,
                )
        async with session.begin():
            assert await rbac_service.roles_for(session, user_id) == frozenset()
            user = await session.get(UserModel, user_id)
            assert user is not None
            await session.delete(user)
            role = await session.get(RoleModel, role_id)
            assert role is not None
            await session.delete(role)
