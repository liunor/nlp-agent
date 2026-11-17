import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock

from server.infrastructure.mysql.models import ReleaseNoteModel
from server.release_notes.service import (
    ReleaseNoteConflictError,
    ReleaseNoteCrud,
    ReleaseNoteNotFoundError,
    ReleaseNoteService,
)


def _service_with(crud: AsyncMock) -> ReleaseNoteService:
    return ReleaseNoteService(lambda _session: crud)


def _note(note_id: str = "note-1", version: str = "1.0.0", status: str = "published") -> ReleaseNoteModel:
    return ReleaseNoteModel(
        id=note_id,
        version=version,
        released_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
        notes_json=["说明"],
        status=status,
    )


@pytest.mark.asyncio
async def test_announcement_checks_only_latest_release_for_this_account() -> None:
    crud = AsyncMock(spec=ReleaseNoteCrud)
    newest = _note("new", "2.2.0")
    crud.list.return_value = [_note("old", "2.1.0"), newest]
    crud.has_read.return_value = False
    service = _service_with(crud)
    assert await service.latest_unread(AsyncMock(), user_id="alice") is newest
    crud.has_read.assert_awaited_once_with("alice", "new")
    crud.has_read.return_value = True
    assert await service.latest_unread(AsyncMock(), user_id="alice") is None
    crud.list.return_value = []
    assert await service.latest_unread(AsyncMock(), user_id="bob") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("row", [None, _note(status="draft")])
async def test_receipt_rejects_missing_or_draft_note(row) -> None:
    crud = AsyncMock(spec=ReleaseNoteCrud)
    crud.get.return_value = row
    with pytest.raises(ReleaseNoteNotFoundError):
        await _service_with(crud).mark_read(AsyncMock(), user_id="alice", note_id="note-1")
    crud.mark_read.assert_not_awaited()


@pytest.mark.asyncio
async def test_receipt_is_account_scoped_and_uses_idempotent_insert() -> None:
    from sqlalchemy.dialects import mysql
    session = AsyncMock()
    crud = ReleaseNoteCrud(session)
    await crud.mark_read("alice", "note-1")
    statement = session.execute.call_args.args[0]
    compiled = statement.compile(dialect=mysql.dialect())
    assert "ON DUPLICATE KEY UPDATE" in str(compiled)
    assert compiled.params["user_id"] == "alice"
    assert compiled.params["release_note_id"] == "note-1"


@pytest.mark.asyncio
async def test_list_published_excludes_drafts() -> None:
    crud = AsyncMock(spec=ReleaseNoteCrud)
    crud.list.return_value = [_note()]
    service = _service_with(crud)

    rows = await service.list(session=AsyncMock(), include_drafts=False)

    assert rows[0].version == "1.0.0"
    crud.list.assert_awaited_once()
    assert crud.list.call_args.kwargs["statuses"] == ("published",)


@pytest.mark.asyncio
async def test_list_include_drafts_passes_both_statuses() -> None:
    crud = AsyncMock(spec=ReleaseNoteCrud)
    crud.list.return_value = []
    service = _service_with(crud)

    await service.list(session=AsyncMock(), include_drafts=True)

    assert crud.list.call_args.kwargs["statuses"] == ("draft", "published")


@pytest.mark.asyncio
async def test_list_orders_by_released_at_then_semantic_version_desc() -> None:
    crud = AsyncMock(spec=ReleaseNoteCrud)
    crud.list.return_value = [
        _note(note_id="a", version="1.9.0"),
        _note(note_id="b", version="1.10.0"),
    ]
    service = _service_with(crud)

    rows = await service.list(session=AsyncMock(), include_drafts=False)

    assert [row.version for row in rows] == ["1.10.0", "1.9.0"]


@pytest.mark.asyncio
async def test_create_rejects_duplicate_version() -> None:
    crud = AsyncMock(spec=ReleaseNoteCrud)
    crud.by_version.return_value = _note(note_id="existing")
    service = _service_with(crud)

    with pytest.raises(ReleaseNoteConflictError, match="already exists"):
        await service.create(
            session=AsyncMock(), version="1.0.0",
            released_at=datetime(2026, 8, 1), notes=["说明"], status="published",
        )


@pytest.mark.asyncio
async def test_create_rejects_blank_note_items() -> None:
    crud = AsyncMock(spec=ReleaseNoteCrud)
    crud.by_version.return_value = None
    service = _service_with(crud)

    with pytest.raises(ValueError, match="cannot be blank"):
        await service.create(
            session=AsyncMock(), version="1.0.0",
            released_at=datetime(2026, 8, 1), notes=["  "], status="published",
        )


@pytest.mark.asyncio
async def test_create_adds_note_when_version_is_free() -> None:
    crud = AsyncMock(spec=ReleaseNoteCrud)
    crud.by_version.return_value = None
    service = _service_with(crud)

    note = await service.create(
        session=AsyncMock(), version="2.0.0",
        released_at=datetime(2026, 8, 1), notes=["新增功能"], status="draft",
    )

    assert note.version == "2.0.0"
    assert note.notes_json == ["新增功能"]
    assert note.status == "draft"
    crud.add.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_raises_not_found_for_unknown_id() -> None:
    crud = AsyncMock(spec=ReleaseNoteCrud)
    crud.get.return_value = None
    service = _service_with(crud)

    with pytest.raises(ReleaseNoteNotFoundError):
        await service.update(
            session=AsyncMock(), note_id="missing", version="1.0.0",
            released_at=datetime(2026, 8, 1), notes=["说明"], status="published",
        )


@pytest.mark.asyncio
async def test_update_rejects_version_taken_by_another_note() -> None:
    crud = AsyncMock(spec=ReleaseNoteCrud)
    crud.get.return_value = _note(note_id="target", version="1.0.0")
    crud.by_version.return_value = _note(note_id="other", version="2.0.0")
    service = _service_with(crud)

    with pytest.raises(ReleaseNoteConflictError, match="already exists"):
        await service.update(
            session=AsyncMock(), note_id="target", version="2.0.0",
            released_at=datetime(2026, 8, 1), notes=["说明"], status="published",
        )


@pytest.mark.asyncio
async def test_update_mutates_fields_and_preserves_id() -> None:
    crud = AsyncMock(spec=ReleaseNoteCrud)
    crud.get.return_value = _note(note_id="target", version="1.0.0")
    crud.by_version.return_value = None
    service = _service_with(crud)

    note = await service.update(
        session=AsyncMock(), note_id="target", version="1.1.0",
        released_at=datetime(2026, 8, 13), notes=["更新"], status="published",
    )

    assert note.id == "target"
    assert note.version == "1.1.0"
    assert note.notes_json == ["更新"]


@pytest.mark.asyncio
async def test_delete_raises_not_found_for_unknown_id() -> None:
    crud = AsyncMock(spec=ReleaseNoteCrud)
    crud.get.return_value = None
    service = _service_with(crud)

    with pytest.raises(ReleaseNoteNotFoundError):
        await service.delete(session=AsyncMock(), note_id="missing")


@pytest.mark.asyncio
async def test_delete_removes_existing_note() -> None:
    crud = AsyncMock(spec=ReleaseNoteCrud)
    crud.get.return_value = _note(note_id="target")
    service = _service_with(crud)

    await service.delete(session=AsyncMock(), note_id="target")

    crud.delete.assert_awaited_once()
