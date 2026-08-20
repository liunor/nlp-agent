"""Data-integrity regression tests (阶段4 / P1 closure).

Covers the persistence + uniqueness paths required by review §4.3 / P1:

- P1-1: a request-scoped session now commits on exit. ``server/user`` and
  ``server/workspace`` only ``flush()`` (never ``commit()``); before 阶段4 the
  ``get_db_session`` dependency opened a session that was closed without a
  commit, so those writes — and the 阶段3 audit events — never reached the
  database. This test drives the *exact* dependency contract and fails on the
  old behaviour.
- §4.3 / 阶段4: ``nlp_users.username_lower`` is a STORED generated column
  (``LOWER(username)``) with a unique index, so two usernames differing only by
  case are rejected at the database layer.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError as SQLAlchemyIntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from server.auth.dependencies import get_db_session
from server.infrastructure.mysql import DatabaseConfig, create_engine, create_session_factory
from server.infrastructure.mysql.models import SessionModel, WorkspaceModel
from server.user.schemas import UserCreate, UserUpdate
from server.user.service import SelfDeleteForbiddenError, UserService


# --- minimal stand-ins for the FastAPI Request/App carrying the gateway factory ---
class _Gateway:
    def __init__(self, factory: async_sessionmaker) -> None:
        self.authorization_session_factory = factory


class _State:
    def __init__(self, gateway: _Gateway) -> None:
        self.gateway = gateway


class _App:
    def __init__(self, gateway: _Gateway) -> None:
        self.state = _State(gateway)


class _Request:
    def __init__(self, gateway: _Gateway) -> None:
        self.app = _App(gateway)


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
async def test_request_transaction_commits_user_write(mysql_session_factory) -> None:
    """P1-1: 请求内的写（更新用户）必须在请求结束时提交，否则不会落库。"""
    factory = mysql_session_factory
    username = f"diuser{uuid4().hex[:10]}"

    # 先建一个用户（显式事务提交，与依赖无关）
    async with factory() as s:
        async with s.begin():
            user = await UserService(s).create_user(
                UserCreate(username=username, password="password123", display_name="DI User 1")
            )
    uid = user.id

    # 复现 get_db_session 的依赖契约：yield 之后应当提交
    async def _drive() -> None:
        async for session in get_db_session(_Request(_Gateway(factory))):
            await UserService(session).update_user(uid, UserUpdate(display_name="DI_Persisted"))

    await _drive()

    # 用全新 session 读取：修复前应读到旧值 "DI User 1"（断言失败）
    async with factory() as s2:
        reloaded = await UserService(s2).get_user(uid)
        assert reloaded.display_name == "DI_Persisted"


@pytest.mark.asyncio
async def test_username_lower_generated_unique(mysql_session_factory) -> None:
    """§4.3 / 阶段4: 两条仅大小写不同的用户名应被 username_lower 唯一索引拒绝。"""
    factory = mysql_session_factory
    username = f"bobdi{uuid4().hex[:10]}"
    async with factory() as s:
        # 绕过 Pydantic 校验，直接插入两条仅大小写不同的用户名，隔离验证生成列
        await s.execute(
            text(
                "INSERT INTO nlp_users "
                "(id, username, password_hash, display_name, status, authorization_version, created_at, updated_at) "
                "VALUES (:id, :username, 'x', 'B', 'active', 1, UTC_TIMESTAMP(6), UTC_TIMESTAMP(6))"
            ),
            {"id": str(uuid4()), "username": username.upper()},
        )
        await s.flush()
        try:
            await s.execute(
                text(
                    "INSERT INTO nlp_users "
                    "(id, username, password_hash, display_name, status, authorization_version, created_at, updated_at) "
                    "VALUES (:id, :username, 'x', 'B2', 'active', 1, UTC_TIMESTAMP(6), UTC_TIMESTAMP(6))"
                ),
                {"id": str(uuid4()), "username": username.lower()},
            )
            await s.commit()
        except SQLAlchemyIntegrityError:
            await s.rollback()
            return
        pytest.fail("username_lower 唯一索引未阻止大小写不同的重复用户名")


@pytest.mark.asyncio
async def test_disable_user_revokes_sessions(mysql_session_factory) -> None:
    """P1-2 / 阶段5：禁用用户时必须撤销其全部数据库会话。"""
    factory = mysql_session_factory
    suffix = uuid4().hex[:10]
    async with factory() as s:
        async with s.begin():
            svc = UserService(s)
            admin = await svc.create_user(
                UserCreate(username=f"admindis{suffix}", password="password123", display_name="Admin")
            )
            target = await svc.create_user(
                UserCreate(username=f"targetdis{suffix}", password="password123", display_name="Target")
            )
            ws = WorkspaceModel(
                id=str(uuid4()), slug=f"ws-dis-{suffix}", name="WS Dis", status="active"
            )
            s.add(ws)
            # Flush the workspace first so the SessionModel FK parent row
            # exists before its INSERT under the async driver (aiomysql does
            # not always topologically order these without an explicit flush).
            await s.flush()
            sess = SessionModel(
                id=str(uuid4()),
                user_id=target.id,
                workspace_id=ws.id,
                token_hash=str(uuid4()),
                csrf_hash="c",
                expires_at=datetime.now(timezone.utc).replace(tzinfo=None),
            )
            s.add(sess)
            await s.flush()

            await svc.update_user_status(
                target.id, "disabled", actor_user_id=admin.id
            )
            await s.refresh(sess)
            assert sess.revoked_at is not None


@pytest.mark.asyncio
async def test_soft_delete_prevents_self_delete(mysql_session_factory) -> None:
    """P1-2 / 阶段5：管理员不能删除自己的账号（自删防护使用 user_id 比较）。"""
    factory = mysql_session_factory
    async with factory() as s:
        async with s.begin():
            svc = UserService(s)
            admin = await svc.create_user(
                UserCreate(username=f"selfdel{uuid4().hex[:10]}", password="password123", display_name="Self")
            )
            with pytest.raises(SelfDeleteForbiddenError):
                await svc.soft_delete_user(admin.id, actor_user_id=admin.id)


@pytest.mark.asyncio
async def test_soft_delete_filters_user_and_revokes_sessions(
    mysql_session_factory,
) -> None:
    """P1-2 / 阶段5：软删后用户从列表消失，且其会话被撤销。"""
    factory = mysql_session_factory
    suffix = uuid4().hex[:10]
    target_username = f"targetdel2{suffix}"
    async with factory() as s:
        async with s.begin():
            svc = UserService(s)
            admin = await svc.create_user(
                UserCreate(username=f"admindel2{suffix}", password="password123", display_name="Admin")
            )
            target = await svc.create_user(
                UserCreate(username=target_username, password="password123", display_name="Target")
            )
            ws = WorkspaceModel(
                id=str(uuid4()), slug=f"ws-sd-{suffix}", name="WS SD", status="active"
            )
            s.add(ws)
            # Flush the workspace first so the SessionModel FK parent row
            # exists before its INSERT under the async driver (aiomysql does
            # not always topologically order these without an explicit flush).
            await s.flush()
            sess = SessionModel(
                id=str(uuid4()),
                user_id=target.id,
                workspace_id=ws.id,
                token_hash=str(uuid4()),
                csrf_hash="c",
                expires_at=datetime.now(timezone.utc).replace(tzinfo=None),
            )
            s.add(sess)
            await s.flush()

            await svc.soft_delete_user(target.id, actor_user_id=admin.id)

            users, total = await svc.list_users()
            assert target.id not in {u.id for u in users}
            assert await svc.get_user_by_username(target_username) is None

            await s.refresh(sess)
            assert sess.revoked_at is not None
