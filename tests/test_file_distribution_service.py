"""Unit tests for the teacher file distribution service and targets resolution."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from core.identity import AuthenticatedPrincipal
from server.storage.distribution_service import (
    DistributionError,
    DistributionPermissionError,
    DistributionTargetError,
    FileDistributionService,
)


def _teacher_principal(user_id: str = "teacher-1", workspace_id: str = "workspace-1") -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        user_id=user_id,
        workspace_ids=frozenset({workspace_id}),
        roles=frozenset({"teacher"}),
    )


def _student_principal(user_id: str = "student-1", workspace_id: str = "workspace-1") -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        user_id=user_id,
        workspace_ids=frozenset({workspace_id}),
        roles=frozenset({"student"}),
    )


def test_student_cannot_initialize_distribution_service() -> None:
    principal = _student_principal()
    db = AsyncMock()
    with pytest.raises(DistributionPermissionError, match="只有教师或管理员用户才能使用文件分发功能"):
        FileDistributionService(db, principal)


@pytest.mark.asyncio
async def test_get_distribution_targets() -> None:
    principal = _teacher_principal()
    mock_classroom = SimpleNamespace(
        id="class-1",
        name="NLP基础1班",
        workspace_id="workspace-1",
        status="active",
    )
    db = AsyncMock()
    db.scalars = AsyncMock(
        side_effect=[
            SimpleNamespace(all=lambda: [mock_classroom]),  # classrooms
        ]
    )
    db.scalar = AsyncMock(
        side_effect=[
            25,  # class-1 member count
            42,  # student_count
            10,  # guest_count
            5,   # admin_count
            60,  # total_user_count
        ]
    )

    service = FileDistributionService(db, principal)
    targets = await service.get_distribution_targets()

    assert targets["summary"]["student_count"] == 42
    assert targets["summary"]["guest_count"] == 10
    assert targets["summary"]["admin_count"] == 5
    assert targets["summary"]["total_user_count"] == 60
    assert len(targets["roles"]) == 4
    assert targets["classrooms"] == [
        {
            "id": "class-1",
            "name": "NLP基础1班",
            "workspace_id": "workspace-1",
            "student_count": 25,
        }
    ]


@pytest.mark.asyncio
async def test_resolve_recipients_all_students() -> None:
    principal = _teacher_principal()
    mock_student = SimpleNamespace(
        id="student-1",
        username="alice",
        display_name="爱丽丝",
        identity_id="NV2ALICE001",
    )
    db = AsyncMock()
    db.scalars = AsyncMock(return_value=SimpleNamespace(all=lambda: [mock_student]))

    service = FileDistributionService(db, principal)
    users, label = await service._resolve_recipients("all_students")

    assert users == [mock_student]
    assert label == "全体学生"


@pytest.mark.asyncio
async def test_resolve_recipients_all_guests_and_admins() -> None:
    principal = _teacher_principal()
    mock_guest = SimpleNamespace(id="guest-1", username="guest_user")
    mock_admin = SimpleNamespace(id="admin-1", username="admin_user")
    db = AsyncMock()
    db.scalars = AsyncMock(
        side_effect=[
            SimpleNamespace(all=lambda: [mock_guest]),
            SimpleNamespace(all=lambda: [mock_admin]),
        ]
    )

    service = FileDistributionService(db, principal)
    guest_users, guest_label = await service._resolve_recipients("all_guests")
    admin_users, admin_label = await service._resolve_recipients("all_admins")

    assert guest_users == [mock_guest]
    assert guest_label == "全体游客"
    assert admin_users == [mock_admin]
    assert admin_label == "全体管理员"


@pytest.mark.asyncio
async def test_resolve_recipients_classroom() -> None:
    principal = _teacher_principal()
    mock_student = SimpleNamespace(
        id="student-2",
        username="bob",
        display_name="鲍勃",
        identity_id="NV2BOB001",
    )
    mock_class = SimpleNamespace(id="class-99", name="高级NLP研讨班")
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=mock_class)
    db.scalars = AsyncMock(return_value=SimpleNamespace(all=lambda: [mock_student]))

    service = FileDistributionService(db, principal)
    users, label = await service._resolve_recipients("classroom", "class-99")

    assert users == [mock_student]
    assert "高级NLP研讨班" in label


@pytest.mark.asyncio
@pytest.mark.parametrize("direct_delivery", [True, False])
async def test_distribute_file_respects_transfer_key_column(tmp_path: Path, direct_delivery: bool) -> None:
    from server.infrastructure.mysql.models import FileTransferModel

    principal = _teacher_principal()
    db = AsyncMock(spec=AsyncSession)
    key_limit = FileTransferModel.__table__.c.idempotency_key.type.length

    async def reject_overlong_transfer_key():
        # The mock session would otherwise accept values rejected by MySQL VARCHAR.
        for call in db.add.call_args_list:
            row = call.args[0]
            if isinstance(row, FileTransferModel):
                assert len(row.idempotency_key) <= key_limit

    db.flush.side_effect = reject_overlong_transfer_key

    source_file_row = SimpleNamespace(
        id="file-1",
        display_name="lesson.md",
        mime_type="text/markdown",
        size_bytes=0,  # Legacy catalog metadata can lag the source file.
        storage_key="workspace-1/teacher-1/file-1",
    )
    service = FileDistributionService(db, principal)
    service._source = AsyncMock(return_value=source_file_row)  # type: ignore[method-assign]

    mock_student = SimpleNamespace(
        id=str(uuid4()),
        username="student_a",
        display_name="学生甲",
        identity_id="NV2STUDENT001",
    )
    service._resolve_recipients = AsyncMock(return_value=([mock_student], "全体学生"))  # type: ignore[method-assign]
    service._recipient_workspace = AsyncMock(return_value="workspace-student")  # type: ignore[method-assign]

    mock_quota = AsyncMock()
    mock_quota.policy = SimpleNamespace(max_file_bytes=10_000, files_quota_bytes=100_000, max_items=50)
    mock_quota.reconcile = AsyncMock()
    mock_quota.reserve = AsyncMock(return_value=SimpleNamespace(id="res-1"))
    mock_quota.finalize = AsyncMock()
    service._recipient_quota = AsyncMock(return_value=mock_quota)  # type: ignore[method-assign]
    service._bump_notification_version = AsyncMock()  # type: ignore[method-assign]

    mock_account = SimpleNamespace(files_used_bytes=1000, files_reserved_bytes=0, files_reserved_items=0)
    db.scalar = AsyncMock(side_effect=[mock_account, 5])
    db.scalars = AsyncMock(return_value=SimpleNamespace(all=lambda: ["other.txt"]))

    # Prepare temp physical file
    source_file_path = tmp_path / "source.md"
    source_file_path.write_bytes(b"# Hello World")

    with patch("server.storage.distribution_service.storage_path_for", return_value=source_file_path):
        with patch("server.storage.distribution_service.storage_root", return_value=tmp_path):
            result = await service.distribute_file(
                source_file_id="file-1",
                target_type="all_students",
                direct_delivery=direct_delivery,
            )

    assert result["total_targets"] == 1
    assert result["success_count"] == 1
    assert result["failed_count"] == 0
    assert result["file_name"] == "lesson.md"
    assert result["direct_delivery"] is direct_delivery
    assert result["size_bytes"] == len(b"# Hello World")
    transfers = [call.args[0] for call in db.add.call_args_list if isinstance(call.args[0], FileTransferModel)]
    assert len(transfers) == 1
    assert transfers[0].status == ("accepted" if direct_delivery else "pending")
    assert transfers[0].idempotency_key.split("_")[1] == result["distribution_id"]
    assert transfers[0].size_bytes == len(b"# Hello World")
    assert (tmp_path / transfers[0].staging_key).read_bytes() == b"# Hello World"
    assert mock_quota.reserve.await_args.args[1] == len(b"# Hello World")
    if direct_delivery:
        received = tmp_path / "workspace-student" / mock_student.id / transfers[0].accepted_file_id
        assert received.read_bytes() == b"# Hello World"
    assert service._bump_notification_version.called


@pytest.mark.asyncio
async def test_distribute_file_defensive_record_persistence(tmp_path: Path) -> None:
    principal = _teacher_principal()
    db = AsyncMock(spec=AsyncSession)

    source_file_row = SimpleNamespace(
        id="file-1",
        display_name="lesson.md",
        mime_type="text/markdown",
        size_bytes=100,
        storage_key="workspace-1/teacher-1/file-1",
    )
    service = FileDistributionService(db, principal)
    service._source = AsyncMock(return_value=source_file_row)

    mock_student = SimpleNamespace(
        id="student-1",
        username="student_a",
        display_name="学生甲",
        identity_id="NV2STUDENT001",
    )
    service._resolve_recipients = AsyncMock(return_value=([mock_student], "全体学生"))
    service._recipient_workspace = AsyncMock(return_value="workspace-student")

    mock_quota = AsyncMock()
    mock_quota.policy = SimpleNamespace(max_file_bytes=10_000, files_quota_bytes=100_000, max_items=50)
    mock_quota.reconcile = AsyncMock()
    mock_quota.reserve = AsyncMock(return_value=SimpleNamespace(id="res-1"))
    mock_quota.finalize = AsyncMock()
    service._recipient_quota = AsyncMock(return_value=mock_quota)
    service._bump_notification_version = AsyncMock()

    mock_account = SimpleNamespace(files_used_bytes=1000, files_reserved_bytes=0, files_reserved_items=0)
    db.scalar = AsyncMock(side_effect=[mock_account, 5])
    db.scalars = AsyncMock(return_value=SimpleNamespace(all=lambda: ["other.txt"]))

    source_file_path = tmp_path / "source.md"
    source_file_path.write_bytes(b"# Hello World")

    with patch("server.storage.distribution_service.storage_path_for", return_value=source_file_path):
        with patch("server.storage.distribution_service.storage_root", return_value=tmp_path):
            result = await service.distribute_file(
                source_file_id="file-1",
                target_type="all_students",
                direct_delivery=True,
            )

    assert result["total_targets"] == 1
    assert result["success_count"] == 1
    assert result["failed_count"] == 0
