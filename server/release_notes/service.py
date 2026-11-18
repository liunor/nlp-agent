"""Release note application service for the developer control plane and student UI.

Each row is one published or draft release version.  The version number itself
is injected into the web UI at build time from ``package.json``; this table is
the single source of truth for the accompanying changelog copy.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.mysql import insert

from server.infrastructure.mysql.models import ReleaseNoteModel, ReleaseNoteReadModel

VALID_STATUSES = ("draft", "published")


def _semver_key(version: str) -> tuple[int, ...]:
    """Numeric parts so ``1.10.0`` ranks after ``1.9.0`` rather than by string order."""
    parts: list[int] = []
    for part in version.split("."):
        try:
            parts.append(int(part))
        except ValueError:
            parts.append(0)
    return tuple(parts)


def _release_order_key(row: ReleaseNoteModel) -> tuple[object, tuple[int, ...]]:
    return (row.released_at, _semver_key(row.version))


class ReleaseNoteConflictError(ValueError):
    """A release note already exists for the requested version."""


class ReleaseNoteNotFoundError(ValueError):
    """No release note exists for the requested id."""


class ReleaseNoteCrud:
    """Narrow persistence primitives so the service logic stays testable."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list(self, *, statuses: tuple[str, ...]) -> list[ReleaseNoteModel]:
        statement = select(ReleaseNoteModel)
        if statuses:
            statement = statement.where(ReleaseNoteModel.status.in_(statuses))
        return list(await self._session.scalars(statement))

    async def by_version(self, version: str) -> ReleaseNoteModel | None:
        return await self._session.scalar(
            select(ReleaseNoteModel).where(ReleaseNoteModel.version == version)
        )

    async def get(self, note_id: str) -> ReleaseNoteModel | None:
        return await self._session.scalar(
            select(ReleaseNoteModel).where(ReleaseNoteModel.id == note_id)
        )

    async def add(self, note: ReleaseNoteModel) -> None:
        self._session.add(note)

    async def delete(self, note: ReleaseNoteModel) -> None:
        await self._session.delete(note)

    async def has_read(self, user_id: str, note_id: str) -> bool:
        return await self._session.get(ReleaseNoteReadModel, (user_id, note_id)) is not None

    async def mark_read(self, user_id: str, note_id: str) -> None:
        statement = insert(ReleaseNoteReadModel).values(
            user_id=user_id, release_note_id=note_id, read_at=datetime.now(UTC)
        )
        await self._session.execute(statement.on_duplicate_key_update(user_id=user_id))


class ReleaseNoteService:
    def __init__(
        self,
        crud_factory: Callable[[AsyncSession], ReleaseNoteCrud],
    ) -> None:
        self._crud_factory = crud_factory

    async def list(
        self, session: AsyncSession, *, include_drafts: bool = False
    ) -> list[ReleaseNoteModel]:
        statuses: tuple[str, ...] = (
            ("draft", "published") if include_drafts else ("published",)
        )
        rows = await self._crud_factory(session).list(statuses=statuses)
        rows.sort(key=_release_order_key, reverse=True)
        return rows

    async def create(
        self,
        session: AsyncSession,
        *,
        version: str,
        released_at: object,
        notes: list[str],
        status: str,
    ) -> ReleaseNoteModel:
        self._validate(version=version, notes=notes, status=status)
        crud = self._crud_factory(session)
        if await crud.by_version(version) is not None:
            raise ReleaseNoteConflictError(f"version {version!r} already exists")
        note = ReleaseNoteModel(
            id=str(uuid.uuid4()),
            version=version,
            released_at=released_at,
            notes_json=list(notes),
            status=status,
        )
        await crud.add(note)
        return note

    async def latest_unread(self, session: AsyncSession, *, user_id: str) -> ReleaseNoteModel | None:
        rows = await self.list(session)
        if not rows or await self._crud_factory(session).has_read(user_id, rows[0].id):
            return None
        return rows[0]

    async def mark_read(self, session: AsyncSession, *, user_id: str, note_id: str) -> None:
        crud = self._crud_factory(session)
        note = await crud.get(note_id)
        if note is None or note.status != "published":
            raise ReleaseNoteNotFoundError(note_id)
        await crud.mark_read(user_id, note_id)

    async def update(
        self,
        session: AsyncSession,
        *,
        note_id: str,
        version: str,
        released_at: object,
        notes: list[str],
        status: str,
    ) -> ReleaseNoteModel:
        self._validate(version=version, notes=notes, status=status)
        crud = self._crud_factory(session)
        note = await crud.get(note_id)
        if note is None:
            raise ReleaseNoteNotFoundError(note_id)
        existing = await crud.by_version(version)
        if existing is not None and existing.id != note_id:
            raise ReleaseNoteConflictError(f"version {version!r} already exists")
        note.version = version
        note.released_at = released_at
        note.notes_json = list(notes)
        note.status = status
        return note

    async def delete(self, session: AsyncSession, *, note_id: str) -> None:
        crud = self._crud_factory(session)
        note = await crud.get(note_id)
        if note is None:
            raise ReleaseNoteNotFoundError(note_id)
        await crud.delete(note)

    @staticmethod
    def _validate(*, version: str, notes: list[str], status: str) -> None:
        if status not in VALID_STATUSES:
            raise ValueError(f"invalid release note status {status!r}")
        if len(version) > 32:
            raise ValueError("version is too long")
        if not notes:
            raise ValueError("release note items cannot be empty")
        if any(not item.strip() for item in notes):
            raise ValueError("release note items cannot be blank")


release_note_service = ReleaseNoteService(ReleaseNoteCrud)
