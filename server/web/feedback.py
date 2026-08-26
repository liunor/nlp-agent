"""Persistent student feedback conversations."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Row, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.identity import AuthenticatedPrincipal
from server.infrastructure.mysql.models import FeedbackMessageModel, FeedbackThreadModel, UserModel


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# Kept in sync with ck_nlp_feedback_messages_sender_type.
_STUDENT_SENDER_TYPE = "student"


def _iso_utc(value: datetime) -> str:
    return value.replace(tzinfo=timezone.utc).isoformat()


def _message_payload(message: FeedbackMessageModel) -> dict:
    return {"id": message.id, "sender_type": message.sender_type, "body": message.body, "created_at": _iso_utc(message.created_at)}


async def submit_feedback(session: AsyncSession, principal: AuthenticatedPrincipal, body: str) -> dict:
    # Serialize first submissions for one user so the unique thread constraint
    # cannot turn concurrent clicks into a 500 response.
    await session.scalar(select(UserModel.id).where(UserModel.id == principal.user_id).with_for_update())
    thread = await session.scalar(select(FeedbackThreadModel).where(FeedbackThreadModel.user_id == principal.user_id))
    if thread is None:
        now = _now()
        thread = FeedbackThreadModel(id=str(uuid4()), user_id=principal.user_id, created_at=now, updated_at=now)
        session.add(thread)
        await session.flush()
    now = _now()
    message = FeedbackMessageModel(id=str(uuid4()), thread_id=thread.id, sender_user_id=principal.user_id, sender_type=_STUDENT_SENDER_TYPE, body=body.strip(), created_at=now, updated_at=now)
    session.add(message)
    thread.updated_at = now
    await session.flush()
    return {"thread_id": thread.id, "message": _message_payload(message)}


def _escape_like(value: str) -> str:
    # MySQL's LIKE defaults to the backslash escape character; without this a
    # search for "%" or "_" silently degrades into a wildcard match.
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def list_feedback_threads(
    session: AsyncSession, *, limit: int = 50, offset: int = 0, search: str | None = None
) -> dict:
    # Aggregate queries keep the cost constant per page: one page of threads,
    # one window-function pass for each thread's latest message, one GROUP BY
    # for unread counts, and one COUNT(*) — never 2N+1 round trips.
    normalized = (search or "").strip().lower()
    thread_query = (
        select(FeedbackThreadModel, UserModel)
        .join(UserModel, UserModel.id == FeedbackThreadModel.user_id)
        .order_by(FeedbackThreadModel.updated_at.desc(), FeedbackThreadModel.id.desc())
        .limit(limit)
        .offset(offset)
    )
    total_query = (
        select(func.count())
        .select_from(FeedbackThreadModel)
        .join(UserModel, UserModel.id == FeedbackThreadModel.user_id)
    )
    if normalized:
        pattern = f"%{_escape_like(normalized)}%"
        search_filter = or_(
            UserModel.username_lower.like(pattern, escape="\\"),
            func.lower(UserModel.display_name).like(pattern, escape="\\"),
        )
        thread_query = thread_query.where(search_filter)
        total_query = total_query.where(search_filter)

    total = int(await session.scalar(total_query) or 0)
    rows = (await session.execute(thread_query)).all()

    thread_ids = [thread.id for thread, _user in rows]
    latest_by_thread: dict[str, Row] = {}
    unread_by_thread: dict[str, int] = {}
    if thread_ids:
        ranked = (
            select(
                FeedbackMessageModel.id.label("id"),
                FeedbackMessageModel.thread_id.label("thread_id"),
                FeedbackMessageModel.sender_type.label("sender_type"),
                FeedbackMessageModel.body.label("body"),
                FeedbackMessageModel.created_at.label("created_at"),
                func.row_number()
                .over(
                    partition_by=FeedbackMessageModel.thread_id,
                    # id breaks ties when two messages share one microsecond.
                    order_by=(FeedbackMessageModel.created_at.desc(), FeedbackMessageModel.id.desc()),
                )
                .label("rn"),
            )
            .where(FeedbackMessageModel.thread_id.in_(thread_ids))
            .subquery()
        )
        latest_rows = await session.execute(select(ranked).where(ranked.c.rn == 1))
        latest_by_thread = {row.thread_id: row for row in latest_rows}
        unread_rows = await session.execute(
            select(FeedbackMessageModel.thread_id, func.count(FeedbackMessageModel.id))
            .join(FeedbackThreadModel, FeedbackThreadModel.id == FeedbackMessageModel.thread_id)
            .where(
                FeedbackMessageModel.thread_id.in_(thread_ids),
                FeedbackMessageModel.sender_type == _STUDENT_SENDER_TYPE,
                or_(
                    FeedbackThreadModel.developer_read_at.is_(None),
                    FeedbackMessageModel.created_at > FeedbackThreadModel.developer_read_at,
                ),
            )
            .group_by(FeedbackMessageModel.thread_id)
        )
        unread_by_thread = {thread_id: int(count) for thread_id, count in unread_rows}

    result = []
    for thread, user in rows:
        latest = latest_by_thread.get(thread.id)
        result.append({
            "thread_id": thread.id,
            "user_id": user.id,
            "username": user.username,
            "display_name": user.display_name,
            "unread_count": unread_by_thread.get(thread.id, 0),
            "updated_at": _iso_utc(thread.updated_at),
            "latest": _message_payload(latest) if latest else None,
        })
    return {"items": result, "total": total}


async def get_feedback_thread(session: AsyncSession, thread_id: str) -> dict:
    row = await session.execute(select(FeedbackThreadModel, UserModel).join(UserModel, UserModel.id == FeedbackThreadModel.user_id).where(FeedbackThreadModel.id == thread_id))
    result = row.first()
    if result is None:
        raise LookupError(thread_id)
    thread, user = result
    messages = list((await session.scalars(select(FeedbackMessageModel).where(FeedbackMessageModel.thread_id == thread.id).order_by(FeedbackMessageModel.created_at.asc()))).all())
    return {"thread_id": thread.id, "user_id": user.id, "username": user.username, "display_name": user.display_name, "messages": [_message_payload(message) for message in messages]}


async def mark_feedback_read(
    session: AsyncSession, thread_id: str, read_through_message_id: str
) -> None:
    thread = await session.scalar(select(FeedbackThreadModel).where(FeedbackThreadModel.id == thread_id))
    if thread is None:
        raise LookupError(thread_id)
    message = await session.scalar(
        select(FeedbackMessageModel).where(
            FeedbackMessageModel.id == read_through_message_id,
            FeedbackMessageModel.thread_id == thread_id,
        )
    )
    if message is None:
        raise LookupError(read_through_message_id)
    if thread.developer_read_at is None or message.created_at > thread.developer_read_at:
        thread.developer_read_at = message.created_at
    await session.flush()
