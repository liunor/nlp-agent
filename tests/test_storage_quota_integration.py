"""MySQL seam tests for the account storage ledger.

These tests are skipped when the configured integration database is absent, but
run against the migrated schema in CI.  They intentionally exercise the
public quota service rather than private SQL aggregation helpers.
"""

from __future__ import annotations

import os
import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from server.infrastructure.mysql import DatabaseConfig, create_engine, create_session_factory
from server.infrastructure.mysql.models import (
    ConversationMessageModel,
    ConversationModel,
    StorageAccountModel,
    StorageReservationModel,
    ToolCallModel,
    TurnModel,
    WorkspaceModel,
    UserModel,
)
from server.storage.policy import StorageBucket, policy_with_overrides
from server.storage.quota import AsyncStorageQuota, StorageQuotaExceeded
from server.storage.service import reconcile_all_storage_accounts
import server.storage.quota as quota_module


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
async def test_reconcile_counts_legacy_messages_and_tool_calls(mysql_session_factory) -> None:
    async with mysql_session_factory() as session:
        await session.begin()
        try:
            workspace_id = await session.scalar(select(WorkspaceModel.id).limit(1))
            if workspace_id is None:
                pytest.skip("migrated database has no workspace fixture")
            user_id = str(uuid4())
            conversation_id = str(uuid4())
            turn_id = str(uuid4())
            session.add(UserModel(id=user_id, username=f"quota-{uuid4().hex[:12]}", password_hash="test", display_name="Quota Test"))
            await session.flush()
            session.add(
                ConversationModel(
                    id=conversation_id,
                    workspace_id=workspace_id,
                    owner_user_id=user_id,
                    title="legacy conversation",
                )
            )
            await session.flush()
            session.add(
                TurnModel(
                    id=turn_id,
                    conversation_id=conversation_id,
                    workspace_id=workspace_id,
                    user_id=user_id,
                    input_text="legacy input",
                )
            )
            await session.flush()
            session.add(
                ConversationMessageModel(
                    id=str(uuid4()),
                    conversation_id=conversation_id,
                    turn_id=turn_id,
                    sequence=1,
                    role="user",
                    content="legacy message",
                )
            )
            session.add(
                ToolCallModel(
                    id=str(uuid4()),
                    turn_id=turn_id,
                    operation_id="legacy-op",
                    claim_generation=0,
                    tool_name="legacy-tool",
                    idempotency_key=f"{turn_id}:legacy-op",
                    request_json={"request": "x" * 128},
                    result_json={"result": "y" * 128},
                )
            )
            await session.flush()

            assert await reconcile_all_storage_accounts(session) >= 1
            account = await session.scalar(
                select(StorageAccountModel).where(StorageAccountModel.owner_user_id == user_id)
            )
            assert account is not None
            assert account.core_used_bytes >= 256
        finally:
            await session.rollback()


@pytest.mark.asyncio
async def test_finalize_rejects_measured_usage_after_write(mysql_session_factory) -> None:
    async with mysql_session_factory() as session:
        await session.begin()
        try:
            workspace_id = await session.scalar(select(WorkspaceModel.id).limit(1))
            if workspace_id is None:
                pytest.skip("migrated database has no workspace fixture")
            user_id = str(uuid4())
            session.add(UserModel(id=user_id, username=f"quota-{uuid4().hex[:12]}", password_hash="test", display_name="Quota Test"))
            await session.flush()
            quota = AsyncStorageQuota(session, owner_user_id=user_id, roles={"student"})
            reservation = await quota.reserve(
                StorageBucket.CORE,
                1,
                resource_type="integration-test",
            )
            account = await session.scalar(
                select(StorageAccountModel).where(StorageAccountModel.owner_user_id == user_id)
            )
            assert account is not None
            account.core_quota_override_bytes = 16
            await session.flush()
            quota.policy = policy_with_overrides(
                quota.policy,
                {"core_quota_bytes": 16},
            )
            session.add(
                ConversationModel(
                    id=str(uuid4()),
                    workspace_id=workspace_id,
                    owner_user_id=user_id,
                    title="x" * 128,
                )
            )
            await session.flush()
            with pytest.raises(StorageQuotaExceeded):
                await quota.finalize(reservation, reconcile=True)
        finally:
            await session.rollback()


@pytest.mark.asyncio
async def test_concurrent_reservations_cannot_oversubscribe_account(mysql_session_factory) -> None:
    user_id = str(uuid4())
    async with mysql_session_factory() as session:
        async with session.begin():
            session.add(UserModel(id=user_id, username=f"quota-{uuid4().hex[:12]}", password_hash="test", display_name="Quota Test"))
            await session.flush()
            quota = AsyncStorageQuota(session, owner_user_id=user_id, roles={"student"})
            await quota.reconcile()
            account = await session.scalar(select(StorageAccountModel).where(StorageAccountModel.owner_user_id == user_id))
            assert account is not None
            account.core_quota_override_bytes = 100

    async def reserve_once() -> str:
        async with mysql_session_factory() as session:
            async with session.begin():
                quota = AsyncStorageQuota(session, owner_user_id=user_id, roles={"student"})
                try:
                    await quota.reserve(StorageBucket.CORE, 60, resource_type="concurrency-test")
                except StorageQuotaExceeded:
                    return "rejected"
                return "accepted"

    try:
        results = await asyncio.gather(reserve_once(), reserve_once())
        assert sorted(results) == ["accepted", "rejected"]
    finally:
        async with mysql_session_factory() as session:
            async with session.begin():
                await session.execute(delete(StorageReservationModel).where(StorageReservationModel.owner_user_id == user_id))
                await session.execute(delete(StorageAccountModel).where(StorageAccountModel.owner_user_id == user_id))
                await session.execute(delete(UserModel).where(UserModel.id == user_id))


@pytest.mark.asyncio
async def test_concurrent_reservations_cannot_oversubscribe_global_pool(mysql_session_factory, monkeypatch) -> None:
    monkeypatch.setattr(quota_module.settings, "NLP_AGENT_STORAGE_GLOBAL_DATA_LIMIT_BYTES", 100)
    user_ids = [str(uuid4()), str(uuid4())]
    async with mysql_session_factory() as session:
        async with session.begin():
            for user_id in user_ids:
                session.add(UserModel(id=user_id, username=f"quota-{uuid4().hex[:12]}", password_hash="test", display_name="Quota Test"))
            await session.flush()
            for user_id in user_ids:
                await AsyncStorageQuota(session, owner_user_id=user_id, roles={"student"}).reconcile()

    async def reserve_once(user_id: str) -> str:
        async with mysql_session_factory() as session:
            async with session.begin():
                quota = AsyncStorageQuota(session, owner_user_id=user_id, roles={"student"})
                try:
                    await quota.reserve(StorageBucket.CORE, 60, resource_type="global-concurrency-test")
                except StorageQuotaExceeded:
                    return "rejected"
                return "accepted"

    try:
        results = await asyncio.gather(*(reserve_once(user_id) for user_id in user_ids))
        assert sorted(results) == ["accepted", "rejected"]
    finally:
        async with mysql_session_factory() as session:
            async with session.begin():
                for user_id in user_ids:
                    await session.execute(delete(StorageReservationModel).where(StorageReservationModel.owner_user_id == user_id))
                    await session.execute(delete(StorageAccountModel).where(StorageAccountModel.owner_user_id == user_id))
                    await session.execute(delete(UserModel).where(UserModel.id == user_id))
