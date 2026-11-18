"""MySQL seam tests for the account storage ledger.

These tests are skipped when the configured integration database is absent, but
run against the migrated schema in CI.  They intentionally exercise the
public quota service rather than private SQL aggregation helpers.
"""

from __future__ import annotations

import os
import asyncio
import shutil
from uuid import uuid4

import pytest
from sqlalchemy import delete, null, select

from core.identity import AuthenticatedPrincipal
from server.infrastructure.mysql import DatabaseConfig, create_engine, create_session_factory
from server.infrastructure.mysql.models import (
    ConversationMessageModel,
    ConversationModel,
    LangGraphCheckpointBlobModel,
    LangGraphCheckpointModel,
    LangGraphCheckpointWriteModel,
    StorageAccountModel,
    StorageReservationModel,
    ToolCallModel,
    TurnModel,
    WorkspaceModel,
    UserModel,
)
from server.storage.policy import StorageBucket, policy_with_overrides
from server.storage.errors import StorageQuotaReason
from server.storage.quota import AsyncStorageQuota, StorageQuotaExceeded, SyncStorageQuota
from server.storage.service import StorageService, reconcile_all_storage_accounts, storage_root
import server.storage.quota as quota_module


class _StorageUploadProbe:
    filename = "storage-service-probe.txt"
    content_type = "text/plain"

    async def read(self, _limit: int = -1) -> bytes:
        return b"storage service probe"


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
async def test_binary_checkpoint_usage_matches_admission_and_usage_view(mysql_session_factory) -> None:
    """Opaque bytes and SQL NULL must count equally in both MySQL adapters.

    Everything is scoped to a fresh user and rolled back. In particular, no
    global reconciliation or existing account data is modified by this test.
    """
    async with mysql_session_factory() as session:
        await session.begin()
        try:
            workspace_id, user_id, conversation_id, turn_id = (str(uuid4()) for _ in range(4))
            session.add(WorkspaceModel(id=workspace_id, slug=f"binary-{uuid4().hex[:12]}", name="Binary Test"))
            title, input_text = "二进制计数", "保留原始字节"
            session.add(UserModel(id=user_id, username=f"binary-{uuid4().hex[:12]}", password_hash="test", display_name="Binary Test"))
            await session.flush()
            session.add(ConversationModel(id=conversation_id, workspace_id=workspace_id, owner_user_id=user_id, title=title))
            await session.flush()
            session.add(TurnModel(id=turn_id, conversation_id=conversation_id, workspace_id=workspace_id,
                                  user_id=user_id, input_text=input_text, learning_state_json=null()))
            await session.flush()

            # MsgPack-like data deliberately starts with invalid UTF-8 and
            # contains NUL, quotes and a valid multibyte character sequence.
            payloads = (b"\x81\xff\x00checkpoint", b"\xc4\x02\xfe\xff", b"\xff\x00'\"" + "课".encode(), b"\x92\x00\xc1write")
            session.add(LangGraphCheckpointModel(
                id=str(uuid4()), thread_id=conversation_id, workspace_id=workspace_id, owner_user_id=user_id,
                checkpoint_ns="", checkpoint_id="checkpoint-1", checkpoint_type="msgpack",
                checkpoint_blob=payloads[0], metadata_type="msgpack", metadata_blob=payloads[1],
            ))
            session.add(LangGraphCheckpointBlobModel(
                id=str(uuid4()), thread_id=conversation_id, workspace_id=workspace_id, owner_user_id=user_id,
                checkpoint_ns="", channel="messages", version="1", value_type="msgpack", value_blob=payloads[2],
            ))
            session.add(LangGraphCheckpointWriteModel(
                id=str(uuid4()), thread_id=conversation_id, workspace_id=workspace_id, owner_user_id=user_id,
                checkpoint_ns="", checkpoint_id="checkpoint-1", task_id="task-1", write_index=0,
                channel="messages", value_type="msgpack", value_blob=payloads[3], task_path="",
            ))
            await session.flush()
            expected = len(conversation_id.encode()) + len(title.encode()) + len(input_text.encode()) + sum(map(len, payloads))
            principal = AuthenticatedPrincipal(user_id=user_id, workspace_ids=frozenset({str(workspace_id)}),
                                               roles=frozenset({"student"}))
            service = StorageService(session, principal, str(workspace_id))
            assert await service._core_used_from_db() == expected
            usage = await service.usage()
            assert usage["core"]["committed_used_bytes"] == expected
            connection = await session.connection()
            sync_usage = await connection.run_sync(
                lambda sync: SyncStorageQuota(sync, owner_user_id=user_id, roles={"student"})._reconcile()
            )
            assert sync_usage == (expected, 0)

            account = await session.scalar(select(StorageAccountModel).where(StorageAccountModel.owner_user_id == user_id))
            account.core_quota_override_bytes = expected
            await session.flush()
            # The UI total and both admission paths now agree on the boundary.
            with pytest.raises(StorageQuotaExceeded) as async_error:
                await service.quota.reserve(StorageBucket.CORE, 1, resource_type="binary-test")
            assert async_error.value.reason == StorageQuotaReason.CORE
            with pytest.raises(StorageQuotaExceeded) as sync_error:
                await connection.run_sync(lambda sync: SyncStorageQuota(sync, owner_user_id=user_id,
                                           roles={"student"}).reserve(StorageBucket.CORE, 1, resource_type="binary-test"))
            assert sync_error.value.reason == StorageQuotaReason.CORE
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


@pytest.mark.asyncio
async def test_new_file_and_folder_responses_load_server_defaults(mysql_session_factory) -> None:
    """Async ORM serialization must not trigger an implicit MissingGreenlet load.

    ``created_at``, ``updated_at`` and ``status`` are MySQL server defaults.
    After ``flush`` SQLAlchemy expires them, so the service must explicitly
    refresh a newly inserted row before returning its JSON representation.
    """

    async with mysql_session_factory() as session:
        workspace_id = await session.scalar(select(WorkspaceModel.id).limit(1))
        if workspace_id is None:
            pytest.skip("migrated database has no workspace fixture")
        user_id = str(uuid4())
        session.add(
            UserModel(
                id=user_id,
                username=f"storage-probe-{uuid4().hex[:12]}",
                password_hash="test",
                display_name="Storage Probe",
            )
        )
        await session.flush()
        principal = AuthenticatedPrincipal(
            user_id=user_id,
            workspace_ids=frozenset({str(workspace_id)}),
            roles=frozenset({"student"}),
        )
        user_root = storage_root() / str(workspace_id) / user_id
        try:
            service = StorageService(session, principal, str(workspace_id))
            folder = await service.create_folder("probe-folder")
            assert folder["kind"] == "folder"
            uploaded = await service.create_file(_StorageUploadProbe(), folder["id"])
            assert uploaded["kind"] == "file"
            assert uploaded["created_at"]
            assert uploaded["updated_at"]
        finally:
            await session.rollback()
            if user_root.is_dir():
                shutil.rmtree(user_root)
