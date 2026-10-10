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
