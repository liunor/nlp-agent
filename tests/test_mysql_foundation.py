from __future__ import annotations

import os
from collections.abc import AsyncIterator
from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import text

from configs.settings import Settings
from core.session_context import SessionContext
from server.infrastructure.mysql import DatabaseConfig, UnitOfWorkFactory, create_engine, create_session_factory
from server.infrastructure.mysql.runtime import MySQLRuntime
from server.infrastructure.mysql.base import Base
from server.infrastructure.mysql.models import (
    PermissionModel,
    RoleModel,
    RolePermissionModel,
    RolePermissionScopeModel,
    AuthorizationAuditLogModel,
    ConversationMessageModel,
    ConversationModel,
    ConversationTranscriptModel,
    ExerciseSessionModel,
    GuidedSessionModel,
    SessionModel,
    TurnModel,
    UserModel,
    UserRoleModel,
    WorkspaceMemberModel,
    WorkspaceModel,
)
from server.infrastructure.mysql.table_comments import TABLE_COMMENTS


def test_database_config_requires_aiomysql_and_valid_pool_limits() -> None:
    config = DatabaseConfig("mysql+aiomysql://user:password@localhost/nlp_agent")

    assert config.pool_size == 10
    with pytest.raises(ValueError, match="mysql\\+aiomysql"):
        DatabaseConfig("sqlite:///gateway.sqlite3")
    with pytest.raises(ValueError, match="pool_size"):
        replace(config, pool_size=0)


def test_engine_applies_connection_and_statement_timeouts() -> None:
    config = DatabaseConfig(
        "mysql+aiomysql://user:password@localhost/nlp_agent",
        connect_timeout_s=7,
        statement_timeout_s=31,
    )
    with patch("server.infrastructure.mysql.engine.create_async_engine") as create_async_engine:
        create_engine(config)

    assert create_async_engine.call_args.kwargs["connect_args"] == {
        "connect_timeout": 7,
        "init_command": "SET SESSION max_execution_time = 31000",
    }


def test_database_runtime_uses_environment_dsn_without_storing_it_in_yaml() -> None:
    settings = Settings(NLP_AGENT_DATABASE_URL="mysql+aiomysql://user:password@localhost/nlp_agent")

    assert settings.database_runtime["driver"] == "mysql"
    assert settings.database_runtime["url"].startswith("mysql+aiomysql://")
    assert settings.database_runtime["dsn_env"] == "NLP_AGENT_DATABASE_URL"


def test_foundation_models_define_identity_session_foreign_keys() -> None:
    assert UserModel.__tablename__ == "nlp_users"
    assert WorkspaceModel.__tablename__ == "nlp_workspaces"
    assert SessionModel.__tablename__ == "nlp_sessions"
    assert {foreign_key.target_fullname for foreign_key in SessionModel.__table__.foreign_keys} == {
        "nlp_users.id",
        "nlp_workspaces.id",
    }
    assert {constraint.name for constraint in SessionModel.__table__.constraints if constraint.name} >= {
        "uq_nlp_sessions_token_hash",
    }


def test_conversation_models_accept_runtime_session_identifiers() -> None:
    context = SessionContext.create(user_id="student")
    columns = (
        ConversationModel.id,
        TurnModel.conversation_id,
        ConversationMessageModel.conversation_id,
        ExerciseSessionModel.conversation_id,
        GuidedSessionModel.conversation_id,
    )

    assert len(context.session_id) <= 128
    assert {column.property.columns[0].type.length for column in columns} == {128}


def test_conversation_transcripts_have_a_persistent_ordering_timestamp() -> None:
    column = ConversationTranscriptModel.__table__.c.created_at

    assert column.nullable is False
    assert column.server_default is not None


def test_rbac_models_define_normalized_role_permission_relationships() -> None:
    assert UserModel.authorization_version.property.columns[0].server_default.arg == "1"
    assert RoleModel.__tablename__ == "nlp_roles"
    assert PermissionModel.__tablename__ == "nlp_permissions"
    assert UserRoleModel.__tablename__ == "nlp_user_roles"
    assert RolePermissionModel.__tablename__ == "nlp_role_permissions"
    assert RolePermissionScopeModel.__tablename__ == "nlp_role_permission_scopes"
    assert WorkspaceMemberModel.__tablename__ == "nlp_workspace_members"
    assert AuthorizationAuditLogModel.__tablename__ == "nlp_authorization_audit_logs"
    assert {key.target_fullname for key in UserRoleModel.__table__.foreign_keys} == {
        "nlp_users.id",
        "nlp_roles.id",
    }
    assert "nlp_users.id" in {
        key.target_fullname for key in UserRoleModel.__table__.foreign_keys
    }
    assert {key.target_fullname for key in RolePermissionModel.__table__.foreign_keys} == {
        "nlp_roles.id",
        "nlp_permissions.id",
        "nlp_users.id",
    }


def test_mysql_table_comments_cover_all_active_models() -> None:
    retired_tables = {"nlp_gateway_compat"}
    active_model_tables = set(Base.metadata.tables) - retired_tables

    assert set(TABLE_COMMENTS) == active_model_tables
    assert all(TABLE_COMMENTS.values())
    assert {
        table_name: Base.metadata.tables[table_name].comment
        for table_name in TABLE_COMMENTS
    } == TABLE_COMMENTS


@pytest.mark.asyncio
async def test_unit_of_work_rolls_back_uncommitted_transactions() -> None:
    session = MagicMock()
    session.begin = AsyncMock()
    session.rollback = AsyncMock()
    session.close = AsyncMock()
    session.in_transaction.return_value = True
    factory = MagicMock(return_value=session)

    async with UnitOfWorkFactory(factory).begin() as unit_of_work:
        assert unit_of_work.session is session
        session.begin.assert_awaited_once()

    session.rollback.assert_awaited_once()
    session.close.assert_awaited_once()


@pytest.fixture
async def mysql_engine() -> AsyncIterator:
    database_url = os.getenv("NLP_AGENT_DATABASE_URL")
    if not database_url:
        pytest.skip("MySQL integration database is not configured")
    engine = create_engine(DatabaseConfig(database_url))
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_alembic_foundation_schema_is_available_in_mysql(mysql_engine) -> None:
    async with mysql_engine.connect() as connection:
        rows = await connection.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = DATABASE() AND table_name IN "
                "('nlp_users', 'nlp_workspaces', 'nlp_sessions')"
            )
        )

    assert set(rows.scalars()) == {"nlp_users", "nlp_workspaces", "nlp_sessions"}


@pytest.mark.asyncio
async def test_mysql_table_comments_are_applied(mysql_engine) -> None:
    expected = {
        **TABLE_COMMENTS,
        "alembic_version": "Alembic 数据库迁移版本记录，不承载业务数据。",
    }
    async with mysql_engine.connect() as connection:
        rows = await connection.execute(
            text(
                "SELECT table_name, table_comment "
                "FROM information_schema.tables "
                "WHERE table_schema = DATABASE() "
                "AND (table_name LIKE 'nlp_%' OR table_name = 'alembic_version')"
            )
        )

    actual = {row[0]: row[1] for row in rows}
    assert {table_name: actual.get(table_name) for table_name in expected} == expected


@pytest.mark.asyncio
async def test_session_factory_creates_explicit_transactions(mysql_engine) -> None:
    session_factory = create_session_factory(mysql_engine)

    async with UnitOfWorkFactory(session_factory).begin() as unit_of_work:
        assert unit_of_work.session is not None
        await unit_of_work.commit()


@pytest.mark.asyncio
async def test_mysql_runtime_verifies_configured_database_and_disposes_engine(monkeypatch) -> None:
    engine = MagicMock()
    engine.dispose = AsyncMock()
    verify = AsyncMock()
    monkeypatch.setattr("server.infrastructure.mysql.runtime.create_engine", lambda _config: engine)
    monkeypatch.setattr("server.infrastructure.mysql.runtime.verify_database_ready", verify)

    runtime = MySQLRuntime.from_runtime(
        {"url": "mysql+aiomysql://user:password@db:3306/nlp_agent"}
    )

    await runtime.start()
    await runtime.close()

    verify.assert_awaited_once_with(engine)
    engine.dispose.assert_awaited_once()
