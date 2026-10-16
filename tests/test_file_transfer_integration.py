"""MySQL integration coverage for file-transfer transactions and concurrency."""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select, update

from core.identity import AuthenticatedPrincipal
from server.infrastructure.mysql import DatabaseConfig, create_engine, create_session_factory
from server.infrastructure.mysql.models import (
    FileTransferModel,
    StorageAccountModel,
    StorageReservationModel,
    UserFileModel,
    UserModel,
    WorkspaceMemberModel,
    WorkspaceModel,
)
from server.storage.service import storage_root
from server.storage.quota import AsyncStorageQuota
from server.storage.transfer_service import FileTransferService, maintain_file_transfers


@pytest.fixture(scope="module")
def migrated_mysql_database() -> str:
    database_url = os.getenv("NLP_AGENT_DATABASE_URL")
    if not database_url:
        pytest.skip("MySQL integration database is not configured")
    repo_root = Path(__file__).resolve().parents[1]
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=repo_root,
        env=os.environ.copy(),
        check=True,
        capture_output=True,
        text=True,
    )
    return database_url


@pytest.fixture
async def mysql_session_factory(migrated_mysql_database):
    engine = create_engine(DatabaseConfig(migrated_mysql_database))
    try:
        yield create_session_factory(engine)
    finally:
        await engine.dispose()


@dataclass(frozen=True)
class TransferFixture:
    workspace_id: str
    sender_id: str
    recipient_id: str
    recipient_identity_id: str
    source_file_id: str
    source_path: Path

    def principal(self, user_id: str) -> AuthenticatedPrincipal:
        return AuthenticatedPrincipal(
            user_id=user_id,
            workspace_ids=frozenset({self.workspace_id}),
            roles=frozenset({"student"}),
        )


@pytest.fixture
async def transfer_fixture(mysql_session_factory) -> TransferFixture:
    async with mysql_session_factory() as session:
        async with session.begin():
            workspace_id = str(uuid4())
            sender_id = str(uuid4())
            recipient_id = str(uuid4())
            source_file_id = str(uuid4())
            workspace = WorkspaceModel(
                id=workspace_id,
                slug=f"transfer-{uuid4().hex[:16]}",
                name="File transfer integration workspace",
            )
            sender = UserModel(
                id=sender_id,
                username=f"transfer-sender-{uuid4().hex[:10]}",
                password_hash="test",
                display_name="Transfer Sender",
            )
            recipient = UserModel(
                id=recipient_id,
                username=f"transfer-recipient-{uuid4().hex[:10]}",
                password_hash="test",
                display_name="Transfer Recipient",
            )
            session.add_all([workspace, sender, recipient])
            await session.flush()
            session.add_all([
                WorkspaceMemberModel(workspace_id=workspace_id, user_id=sender_id, member_type="owner"),
                WorkspaceMemberModel(workspace_id=workspace_id, user_id=recipient_id, member_type="owner"),
            ])
            storage_key = f"{workspace_id}/{sender_id}/{source_file_id}"
            source_path = storage_root() / storage_key
            source_path.parent.mkdir(parents=True, exist_ok=True)
            source_path.write_bytes(b"concurrent transfer payload")
            session.add(UserFileModel(
                id=source_file_id,
                owner_user_id=sender_id,
                workspace_id=workspace_id,
                parent_id=None,
                kind="file",
                display_name="payload.txt",
                storage_key=storage_key,
                mime_type="text/plain",
                size_bytes=source_path.stat().st_size,
            ))
            fixture = TransferFixture(
                workspace_id=str(workspace_id),
                sender_id=sender_id,
                recipient_id=recipient_id,
                recipient_identity_id=recipient.identity_id,
                source_file_id=source_file_id,
                source_path=source_path,
            )
    try:
        yield fixture
    finally:
        async with mysql_session_factory() as session:
            async with session.begin():
                staging_keys = list((await session.scalars(select(FileTransferModel.staging_key).where(
                    (FileTransferModel.sender_user_id == fixture.sender_id)
                    | (FileTransferModel.recipient_user_id == fixture.recipient_id)
                ))).all())
                await session.execute(delete(FileTransferModel).where(
                    (FileTransferModel.sender_user_id == fixture.sender_id)
                    | (FileTransferModel.recipient_user_id == fixture.recipient_id)
                ))
                await session.execute(delete(StorageReservationModel).where(StorageReservationModel.owner_user_id == fixture.recipient_id))
                await session.execute(delete(StorageAccountModel).where(StorageAccountModel.owner_user_id.in_([fixture.sender_id, fixture.recipient_id])))
                await session.execute(delete(UserFileModel).where(UserFileModel.owner_user_id.in_([fixture.sender_id, fixture.recipient_id])))
                await session.execute(delete(WorkspaceMemberModel).where(WorkspaceMemberModel.user_id.in_([fixture.sender_id, fixture.recipient_id])))
                await session.execute(delete(UserModel).where(UserModel.id.in_([fixture.sender_id, fixture.recipient_id])))
                await session.execute(delete(WorkspaceModel).where(WorkspaceModel.id == fixture.workspace_id))
        for staging_key in staging_keys:
            (storage_root() / staging_key).unlink(missing_ok=True)
        for user_id in (fixture.sender_id, fixture.recipient_id):
            user_root = storage_root() / fixture.workspace_id / user_id
            if user_root.is_dir():
                shutil.rmtree(user_root)


@pytest.mark.asyncio
async def test_concurrent_duplicate_send_reserves_capacity_once(mysql_session_factory, transfer_fixture: TransferFixture) -> None:
    async def send_once() -> dict:
        async with mysql_session_factory() as session:
            async with session.begin():
                service = FileTransferService(session, transfer_fixture.principal(transfer_fixture.sender_id), transfer_fixture.workspace_id)
                return await service.create(
                    transfer_fixture.source_file_id,
                    transfer_fixture.recipient_identity_id,
                    "shared-idempotency-key",
                )

    first, second = await asyncio.gather(send_once(), send_once())

    assert first["id"] == second["id"]
    async with mysql_session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(FileTransferModel).where(FileTransferModel.sender_user_id == transfer_fixture.sender_id)) == 1
        account = await session.scalar(select(StorageAccountModel).where(StorageAccountModel.owner_user_id == transfer_fixture.recipient_id))
        assert account is not None
        assert account.files_reserved_bytes == transfer_fixture.source_path.stat().st_size
        assert account.files_reserved_items == 1
        assert account.file_transfer_notification_version == 1


@pytest.mark.asyncio
async def test_reject_accept_and_expire_settle_the_recipient_ledger(mysql_session_factory, transfer_fixture: TransferFixture) -> None:
    async def create(key: str) -> str:
        async with mysql_session_factory() as session:
            async with session.begin():
                service = FileTransferService(session, transfer_fixture.principal(transfer_fixture.sender_id), transfer_fixture.workspace_id)
                return (await service.create(transfer_fixture.source_file_id, transfer_fixture.recipient_identity_id, key))["id"]

    rejected_id = await create("reject-key")
    async with mysql_session_factory() as session:
        async with session.begin():
            result = await FileTransferService(session, transfer_fixture.principal(transfer_fixture.recipient_id), transfer_fixture.workspace_id).reject(rejected_id)
            assert result["status"] == "rejected"

    accepted_id = await create("accept-key")
    async with mysql_session_factory() as session:
        async with session.begin():
            result = await FileTransferService(session, transfer_fixture.principal(transfer_fixture.recipient_id), transfer_fixture.workspace_id).accept(accepted_id)
            assert result["status"] == "accepted"

    expired_id = await create("expire-key")
    async with mysql_session_factory() as session:
        async with session.begin():
            await session.execute(update(FileTransferModel).where(FileTransferModel.id == expired_id).values(
                expires_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=1)
            ))
    async with mysql_session_factory() as session:
        async with session.begin():
            result = await FileTransferService(session, transfer_fixture.principal(transfer_fixture.recipient_id), transfer_fixture.workspace_id).accept(expired_id)
            assert result["status"] == "expired"

    async with mysql_session_factory() as session:
        account = await session.scalar(select(StorageAccountModel).where(StorageAccountModel.owner_user_id == transfer_fixture.recipient_id))
        assert account is not None
        assert account.files_reserved_bytes == 0
        assert account.files_reserved_items == 0
        assert account.files_used_bytes == transfer_fixture.source_path.stat().st_size
        assert await session.scalar(select(func.count()).select_from(UserFileModel).where(
            UserFileModel.owner_user_id == transfer_fixture.recipient_id,
            UserFileModel.status == "active",
        )) == 1


@pytest.mark.asyncio
async def test_concurrent_accept_creates_one_received_file(mysql_session_factory, transfer_fixture: TransferFixture) -> None:
    async with mysql_session_factory() as session:
        async with session.begin():
            service = FileTransferService(session, transfer_fixture.principal(transfer_fixture.sender_id), transfer_fixture.workspace_id)
            transfer_id = (await service.create(
                transfer_fixture.source_file_id,
                transfer_fixture.recipient_identity_id,
                "concurrent-accept-key",
            ))["id"]

    async def accept_once() -> dict:
        async with mysql_session_factory() as session:
            async with session.begin():
                service = FileTransferService(session, transfer_fixture.principal(transfer_fixture.recipient_id), transfer_fixture.workspace_id)
                return await service.accept(transfer_id)

    first, second = await asyncio.gather(accept_once(), accept_once())

    assert first["status"] == second["status"] == "accepted"
    assert first["accepted_file_id"] == second["accepted_file_id"]
    async with mysql_session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(UserFileModel).where(
            UserFileModel.owner_user_id == transfer_fixture.recipient_id,
            UserFileModel.status == "active",
        )) == 1


@pytest.mark.asyncio
async def test_message_cleanup_releases_reservations_without_deleting_received_file(mysql_session_factory, transfer_fixture: TransferFixture) -> None:
    async def create(key: str) -> str:
        async with mysql_session_factory() as session:
            async with session.begin():
                service = FileTransferService(session, transfer_fixture.principal(transfer_fixture.sender_id), transfer_fixture.workspace_id)
                return (await service.create(transfer_fixture.source_file_id, transfer_fixture.recipient_identity_id, key))["id"]

    accepted_id = await create("retention-accepted-key")
    async with mysql_session_factory() as session:
        async with session.begin():
            accepted = await FileTransferService(session, transfer_fixture.principal(transfer_fixture.recipient_id), transfer_fixture.workspace_id).accept(accepted_id)
            accepted_file_id = accepted["accepted_file_id"]

    rejected_id = await create("retention-rejected-key")
    async with mysql_session_factory() as session:
        async with session.begin():
            await FileTransferService(session, transfer_fixture.principal(transfer_fixture.recipient_id), transfer_fixture.workspace_id).reject(rejected_id)

    async with mysql_session_factory() as session:
        incoming = await FileTransferService(session, transfer_fixture.principal(transfer_fixture.recipient_id), transfer_fixture.workspace_id).list("incoming")
        outgoing = await FileTransferService(session, transfer_fixture.principal(transfer_fixture.sender_id), transfer_fixture.workspace_id).list("outgoing")
        assert {item["id"] for item in incoming} == {accepted_id, rejected_id}
        assert {item["id"] for item in outgoing} == {accepted_id, rejected_id}
    async with mysql_session_factory() as session:
        async with session.begin():
            await session.execute(
                update(FileTransferModel)
                .where(FileTransferModel.id.in_([accepted_id, rejected_id]))
                .values(created_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=8))
            )

    result = await maintain_file_transfers(mysql_session_factory, batch_size=200)
    assert result["messages_deleted"] == 2

    async with mysql_session_factory() as session:
        assert await session.scalar(select(FileTransferModel.id).where(FileTransferModel.id.in_([accepted_id, rejected_id]))) is None
        assert await session.scalar(select(StorageReservationModel.id).where(StorageReservationModel.owner_user_id == transfer_fixture.recipient_id)) is None
        received = await session.scalar(select(UserFileModel).where(UserFileModel.id == accepted_file_id))
        assert received is not None
        assert (storage_root() / received.storage_key).is_file()
        assert await FileTransferService(session, transfer_fixture.principal(transfer_fixture.recipient_id), transfer_fixture.workspace_id).list("incoming") == []
        assert await FileTransferService(session, transfer_fixture.principal(transfer_fixture.sender_id), transfer_fixture.workspace_id).list("outgoing") == []


@pytest.mark.asyncio
async def test_accept_failure_rolls_back_database_and_removes_destination(mysql_session_factory, transfer_fixture: TransferFixture, monkeypatch) -> None:
    async with mysql_session_factory() as session:
        async with session.begin():
            service = FileTransferService(session, transfer_fixture.principal(transfer_fixture.sender_id), transfer_fixture.workspace_id)
            transfer = await service.create(
                transfer_fixture.source_file_id,
                transfer_fixture.recipient_identity_id,
                "accept-failure-key",
            )

    async def fail_finalize(*_args, **_kwargs) -> None:
        raise RuntimeError("injected finalize failure")

    monkeypatch.setattr(AsyncStorageQuota, "finalize", fail_finalize)
    with pytest.raises(RuntimeError, match="injected finalize failure"):
        async with mysql_session_factory() as session:
            async with session.begin():
                service = FileTransferService(session, transfer_fixture.principal(transfer_fixture.recipient_id), transfer_fixture.workspace_id)
                await service.accept(transfer["id"])

    async with mysql_session_factory() as session:
        persisted = await session.scalar(select(FileTransferModel).where(FileTransferModel.id == transfer["id"]))
        account = await session.scalar(select(StorageAccountModel).where(StorageAccountModel.owner_user_id == transfer_fixture.recipient_id))
        assert persisted is not None and persisted.status == "pending"
        assert account is not None
        assert account.files_reserved_bytes == transfer_fixture.source_path.stat().st_size
        assert account.files_reserved_items == 1
        assert await session.scalar(select(func.count()).select_from(UserFileModel).where(
            UserFileModel.owner_user_id == transfer_fixture.recipient_id,
            UserFileModel.status == "active",
        )) == 0
        assert (storage_root() / persisted.staging_key).is_file()
