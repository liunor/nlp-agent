from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import shutil
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.dialects import mysql

from core.identity import AuthenticatedPrincipal
from server.infrastructure.mysql.models import FileTransferModel
from server.storage.service import StorageService
from server.storage.transfer_service import FileTransferService
from server.storage.policy import StoragePolicy
from server.storage.quota import QuotaReservation
import server.storage.transfer_service as transfer_module


def _service(db: object | None = None) -> FileTransferService:
    principal = AuthenticatedPrincipal(
        user_id="recipient-1",
        workspace_ids=frozenset({"workspace-1"}),
        roles=frozenset({"student"}),
    )
    return FileTransferService(db, principal, "workspace-1")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_accepting_an_expired_request_returns_the_committed_expired_state() -> None:
    transfer = SimpleNamespace(
        recipient_user_id="recipient-1",
        status="pending",
        expires_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=1),
    )
    service = _service()
    service._locked = AsyncMock(return_value=transfer)  # type: ignore[method-assign]
    service._resolve_without_copy = AsyncMock(return_value={"id": "transfer-1", "status": "expired"})  # type: ignore[method-assign]

    result = await service.accept("transfer-1")

    assert result == {"id": "transfer-1", "status": "expired"}


@pytest.mark.asyncio
async def test_transfer_summary_is_read_only_and_includes_a_monotonic_notification_version() -> None:
    db = SimpleNamespace(
        scalar=AsyncMock(
            side_effect=[2, SimpleNamespace(file_transfer_notification_version=7)]
        )
    )
    service = _service(db)
    service._expire_pending = AsyncMock(side_effect=AssertionError("GET summary must not expire transfers"))  # type: ignore[method-assign]
    service._cleanup_resolved_staging = AsyncMock(side_effect=AssertionError("GET summary must not delete files"))  # type: ignore[method-assign]

    result = await service.summary()

    assert result == {"pending_count": 2, "notification_version": 7}


@pytest.mark.asyncio
async def test_transfer_list_is_read_only() -> None:
    row = SimpleNamespace(id="transfer-1")
    db = SimpleNamespace(scalars=AsyncMock(return_value=SimpleNamespace(all=lambda: [row])))
    service = _service(db)
    service._expire_pending = AsyncMock(side_effect=AssertionError("GET list must not expire transfers"))  # type: ignore[method-assign]
    service._cleanup_resolved_staging = AsyncMock(side_effect=AssertionError("GET list must not delete files"))  # type: ignore[method-assign]
    service.serialize = AsyncMock(return_value={"id": "transfer-1", "status": "pending"})  # type: ignore[method-assign]

    result = await service.list("incoming")

    assert result == [{"id": "transfer-1", "status": "pending"}]


def test_storage_serializer_marks_database_timestamps_as_utc() -> None:
    timestamp = datetime(2026, 10, 10, 6, 15, 0)
    item = SimpleNamespace(
        id="file-1",
        kind="file",
        display_name="lesson.md",
        mime_type="text/markdown",
        sha256="a" * 64,
        size_bytes=18,
        created_at=timestamp,
        updated_at=timestamp,
    )

    result = StorageService.serialize(item)

    assert result["created_at"] == "2026-10-10T06:15:00Z"
    assert result["updated_at"] == "2026-10-10T06:15:00Z"


def test_file_transfer_model_has_a_cleanup_ordering_index() -> None:
    index = next(
        index for index in FileTransferModel.__table__.indexes
        if index.name == "ix_nlp_file_transfers_cleanup_status_created"
    )

    assert tuple(column.name for column in index.columns) == ("status", "created_at", "id")


@pytest.mark.asyncio
async def test_transfer_serializer_marks_database_timestamps_as_utc() -> None:
    timestamp = datetime(2026, 10, 10, 6, 15, 0)
    db = SimpleNamespace(
        scalar=AsyncMock(side_effect=[
            SimpleNamespace(identity_id="NV2SENDER", display_name="发送方"),
            SimpleNamespace(identity_id="NV2RECIPIENT", display_name="接收方"),
        ])
    )
    transfer = SimpleNamespace(
        id="transfer-1",
        status="pending",
        original_name="lesson.md",
        size_bytes=18,
        sender_user_id="sender-1",
        recipient_user_id="recipient-1",
        accepted_file_id=None,
        created_at=timestamp,
        expires_at=timestamp,
    )

    result = await _service(db).serialize(transfer)

    assert result["created_at"] == "2026-10-10T06:15:00Z"
    assert result["expires_at"] == "2026-10-10T06:15:00Z"


@pytest.mark.asyncio
@pytest.mark.parametrize(("viewer", "expected_workspace"), [
    ("recipient-1", "workspace-personal"),
    ("sender-1", None),
])
async def test_accepted_transfer_exposes_opening_workspace_only_to_recipient(viewer, expected_workspace) -> None:
    db = SimpleNamespace(scalar=AsyncMock(return_value=None))
    principal = AuthenticatedPrincipal(user_id=viewer, workspace_ids=frozenset({"workspace-1"}), roles=frozenset({"student"}))
    transfer = SimpleNamespace(
        id="transfer-1", status="accepted", original_name="lesson.txt", size_bytes=6,
        sender_user_id="sender-1", recipient_user_id="recipient-1", recipient_workspace_id="workspace-personal",
        accepted_file_id="received-copy", created_at=None, expires_at=None,
    )

    result = await FileTransferService(db, principal).serialize(transfer)
    assert result["accepted_file_id"] == "received-copy"
    assert result["recipient_workspace_id"] == expected_workspace


@pytest.mark.asyncio
async def test_expiry_maintenance_claims_a_bounded_batch_with_skip_locked() -> None:
    transfer = SimpleNamespace(
        status="pending",
        expires_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=1),
    )
    result = SimpleNamespace(all=lambda: [transfer])
    db = SimpleNamespace(scalars=AsyncMock(return_value=result))
    service = _service(db)
    service._resolve_without_copy = AsyncMock(return_value={"status": "expired"})  # type: ignore[method-assign]

    expired = await service._expire_pending(batch_size=25)

    statement = db.scalars.await_args.args[0]
    sql = str(statement.compile(dialect=mysql.dialect())).upper()
    assert "LIMIT" in sql
    assert "FOR UPDATE SKIP LOCKED" in sql
    assert expired == 1


@pytest.mark.asyncio
async def test_message_cleanup_deletes_only_old_terminal_requests_and_their_reservation() -> None:
    transfer = SimpleNamespace(
        id="transfer-old",
        quota_reservation_id="reservation-old",
    )
    db = SimpleNamespace(
        scalars=AsyncMock(return_value=SimpleNamespace(all=lambda: [transfer])),
        scalar=AsyncMock(return_value=SimpleNamespace(id="reservation-old")),
        delete=AsyncMock(),
        flush=AsyncMock(),
    )
    service = _service(db)

    removed = await service._cleanup_expired_messages(batch_size=25)

    statement = db.scalars.await_args.args[0]
    sql = str(statement.compile(dialect=mysql.dialect())).upper()
    assert "CREATED_AT" in sql
    assert "STAGING_DELETED_AT IS NOT NULL" in sql
    assert "LIMIT" in sql
    assert "FOR UPDATE SKIP LOCKED" in sql
    assert removed == 1
    assert db.delete.await_count == 2
    assert db.delete.await_args_list[0].args[0] is transfer
    assert db.delete.await_args_list[1].args[0].id == "reservation-old"
    assert db.flush.await_count == 2


@pytest.mark.asyncio
async def test_concurrent_duplicate_idempotency_key_returns_the_committed_request(monkeypatch) -> None:
    root = Path.cwd() / f".file-transfer-idempotency-{uuid4().hex}"
    source_path = root / "source.txt"
    source_path.parent.mkdir(parents=True)
    source_path.write_text("snapshot", encoding="utf-8")
    existing = SimpleNamespace(id="winner")
    db = SimpleNamespace(
        scalar=AsyncMock(side_effect=[None, 0, existing]),
        add=Mock(),
        flush=AsyncMock(side_effect=IntegrityError("insert", {}, Exception("duplicate"))),
        refresh=AsyncMock(),
    )
    savepoint = AsyncMock()
    savepoint.__aenter__.return_value = None
    savepoint.__aexit__.return_value = False
    db.begin_nested = Mock(return_value=savepoint)
    service = _service(db)
    service._source = AsyncMock(return_value=SimpleNamespace(id="source", size_bytes=8, storage_key="source", display_name="source.txt", mime_type="text/plain"))  # type: ignore[method-assign]
    service._recipient = AsyncMock(return_value=SimpleNamespace(id="recipient-1"))  # type: ignore[method-assign]
    service._recipient_workspace = AsyncMock(return_value="workspace-1")  # type: ignore[method-assign]
    reservation = QuotaReservation("reservation-1", "recipient-1", transfer_module.StorageBucket.FILES, 8, "file_transfer", 1)
    quota = SimpleNamespace(
        policy=StoragePolicy(100, 100, 100, 10),
        reserve=AsyncMock(return_value=reservation),
        release=AsyncMock(),
    )
    service._recipient_quota = AsyncMock(return_value=quota)  # type: ignore[method-assign]
    service.serialize = AsyncMock(return_value={"id": "winner", "status": "pending"})  # type: ignore[method-assign]
    monkeypatch.setattr(transfer_module, "storage_path_for", lambda *_args: source_path)
    monkeypatch.setattr(transfer_module, "storage_root", lambda: root)

    try:
        result = await service.create("source", "NV2RECIPIENT1234", "same-key")
    finally:
        if root.is_dir():
            shutil.rmtree(root)

    assert result == {"id": "winner", "status": "pending"}
    retry_lookup = db.scalar.await_args_list[2].args[0]
    retry_sql = str(retry_lookup.compile(dialect=mysql.dialect())).upper()
    assert "FOR UPDATE" in retry_sql


@pytest.mark.asyncio
async def test_lookup_recipient_finds_account_by_updated_custom_identity_id() -> None:
    teacher = SimpleNamespace(
        id="teacher-user-1",
        identity_id="TEACHER-01",
        display_name="张老师",
    )
    db = SimpleNamespace(scalar=AsyncMock(return_value=teacher))
    service = _service(db)

    found = await service.lookup_recipient("  teacher-01 ")

    assert found == {"identity_id": "TEACHER-01", "display_name": "张老师"}


@pytest.mark.asyncio
async def test_unread_summary_counts_pending_and_unseen_accepted_transfers() -> None:
    db = SimpleNamespace(
        scalar=AsyncMock(
            side_effect=[2, 3, SimpleNamespace(file_transfer_notification_version=9)]
        )
    )
    service = _service(db)

    result = await service.unread_summary()

    assert result == {
        "pending_count": 2,
        "unseen_accepted_count": 3,
        "unread_count": 5,
        "notification_version": 9,
    }


@pytest.mark.asyncio
async def test_mark_seen_updates_only_unseen_accepted_transfers_for_recipient() -> None:
    db = SimpleNamespace(
        execute=AsyncMock(return_value=SimpleNamespace(rowcount=4)),
        flush=AsyncMock(),
    )
    service = _service(db)

    result = await service.mark_seen()

    assert result == {"ok": True, "updated": 4}
    statement = db.execute.await_args.args[0]
    sql = str(statement.compile(dialect=mysql.dialect())).upper()
    assert "RECIPIENT_SEEN_AT IS NULL" in sql
    assert db.flush.await_count == 1


@pytest.mark.asyncio
async def test_serialize_marks_unread_only_for_unseen_recipient_items() -> None:
    db = SimpleNamespace(scalar=AsyncMock(return_value=None))
    service = _service(db)
    unseen_accepted = SimpleNamespace(
        id="t1",
        status="accepted",
        original_name="doc.md",
        size_bytes=12,
        sender_user_id="sender-1",
        recipient_user_id="recipient-1",
        recipient_workspace_id="ws-1",
        accepted_file_id="f1",
        recipient_seen_at=None,
        created_at=None,
        expires_at=None,
    )
    seen_accepted = SimpleNamespace(
        id="t2",
        status="accepted",
        original_name="doc2.md",
        size_bytes=12,
        sender_user_id="sender-1",
        recipient_user_id="recipient-1",
        recipient_workspace_id="ws-1",
        accepted_file_id="f2",
        recipient_seen_at=datetime(2026, 11, 28, 0, 0, 0),
        created_at=None,
        expires_at=None,
    )

    assert (await service.serialize(unseen_accepted))["is_unread"] is True
    assert (await service.serialize(seen_accepted))["is_unread"] is False


@pytest.mark.asyncio
async def test_resolve_without_copy_on_async_session_refreshes_before_serialize() -> None:
    from sqlalchemy import event, select, text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine("sqlite+aiosqlite://")

    @event.listens_for(engine.sync_engine, "connect")
    def _register_utc_timestamp(connection, _record) -> None:
        connection.create_function("utc_timestamp", -1, lambda *_args: "2026-11-28 00:00:00")

    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "CREATE TABLE nlp_users ("
                    "id TEXT PRIMARY KEY, identity_id TEXT, username TEXT, username_lower TEXT, "
                    "password_hash TEXT, display_name TEXT, status TEXT, authorization_version INTEGER, "
                    "deleted_at DATETIME, last_login_at DATETIME, email TEXT, email_normalized TEXT, "
                    "phone_number TEXT, phone_number_normalized TEXT, registration_source TEXT, "
                    "created_at DATETIME, updated_at DATETIME)"
                )
            )
            await conn.execute(
                text(
                    "CREATE TABLE nlp_storage_reservations ("
                    "id TEXT PRIMARY KEY, owner_user_id TEXT, bucket TEXT, amount_bytes INTEGER, "
                    "amount_items INTEGER, resource_type TEXT, resource_key TEXT, status TEXT, "
                    "reserved_at DATETIME, finalized_at DATETIME, created_at DATETIME)"
                )
            )
            await conn.execute(
                text(
                    "CREATE TABLE nlp_file_transfers ("
                    "id TEXT PRIMARY KEY, sender_user_id TEXT, recipient_user_id TEXT, "
                    "source_workspace_id TEXT, recipient_workspace_id TEXT, source_file_id TEXT, "
                    "original_name TEXT, mime_type TEXT, size_bytes INTEGER, sha256 TEXT, "
                    "staging_key TEXT, quota_reservation_id TEXT, status TEXT, "
                    "recipient_seen_at DATETIME, responded_at DATETIME, staging_deleted_at DATETIME, "
                    "accepted_file_id TEXT, expires_at DATETIME, idempotency_key TEXT, "
                    "created_at DATETIME, updated_at DATETIME)"
                )
            )
            await conn.execute(
                text(
                    "INSERT INTO nlp_users (id, identity_id, username, username_lower, password_hash, display_name, status, authorization_version, registration_source) "
                    "VALUES ('sender-1', 'NV2SENDER', 'sender', 'sender', 'h', '发送方', 'active', 1, 'manual'), "
                    "('recipient-1', 'NV2RECIP', 'recipient', 'recipient', 'h', '接收方', 'active', 1, 'manual')"
                )
            )
            await conn.execute(
                text(
                    "INSERT INTO nlp_file_transfers (id, sender_user_id, recipient_user_id, source_workspace_id, "
                    "recipient_workspace_id, source_file_id, original_name, mime_type, size_bytes, sha256, "
                    "staging_key, quota_reservation_id, status, expires_at, idempotency_key) "
                    "VALUES ('t-async-1', 'sender-1', 'recipient-1', 'workspace-1', 'workspace-1', 'f-src', "
                    "'notes.md', 'text/markdown', 10, 'abc', '.transfers/t-async-1', 'res-missing', 'pending', "
                    "'2099-01-01 00:00:00', 'idem-1')"
                )
            )

        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as db:
            async with db.begin():
                service = _service(db)
                transfer = await db.scalar(select(FileTransferModel).where(FileTransferModel.id == "t-async-1"))
                assert transfer is not None
                result = await service._resolve_without_copy(transfer, "reject", "recipient")

        assert result["id"] == "t-async-1"
        assert result["status"] == "rejected"
        assert result["is_unread"] is False
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_accept_refreshes_transfer_after_flush_before_serialize(monkeypatch) -> None:
    root = Path.cwd() / f".file-transfer-accept-{uuid4().hex}"
    staging_path = root / ".transfers" / "transfer-1"
    staging_path.parent.mkdir(parents=True)
    payload = b"accepted file bytes"
    staging_path.write_bytes(payload)
    import hashlib

    digest = hashlib.sha256(payload).hexdigest()
    transfer = SimpleNamespace(
        id="transfer-1",
        status="pending",
        original_name="lesson.md",
        mime_type="text/markdown",
        size_bytes=len(payload),
        sha256=digest,
        staging_key=".transfers/transfer-1",
        sender_user_id="sender-1",
        recipient_user_id="recipient-1",
        recipient_workspace_id="workspace-1",
        quota_reservation_id="reservation-1",
        accepted_file_id=None,
        recipient_seen_at=None,
        created_at=datetime(2026, 11, 28, 0, 0, 0),
        expires_at=datetime(2099, 1, 1, 0, 0, 0),
    )

    reservation_row = SimpleNamespace(
        id="reservation-1",
        owner_user_id="recipient-1",
        bucket="files",
        amount_bytes=len(payload),
        resource_type="file_transfer",
        amount_items=1,
    )
    db = SimpleNamespace(
        scalar=AsyncMock(side_effect=["recipient-1", reservation_row]),
        scalars=AsyncMock(return_value=SimpleNamespace(all=lambda: [])),
        add=Mock(),
        flush=AsyncMock(),
        refresh=AsyncMock(),
    )
    service = _service(db)
    service._locked = AsyncMock(return_value=transfer)  # type: ignore[method-assign]
    service._recipient_quota = AsyncMock(return_value=SimpleNamespace(finalize=AsyncMock()))  # type: ignore[method-assign]
    service.serialize = AsyncMock(side_effect=lambda row: {"id": row.id, "status": row.status, "refreshed": db.refresh.await_count > 0})  # type: ignore[method-assign]
    monkeypatch.setattr(transfer_module, "storage_root", lambda: root)

    try:
        result = await service.accept("transfer-1")
    finally:
        if root.is_dir():
            shutil.rmtree(root)

    assert result == {"id": "transfer-1", "status": "accepted", "refreshed": True}
    assert db.flush.await_count == 2
    db.refresh.assert_awaited_once_with(transfer)
