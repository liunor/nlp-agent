"""Persistent student feedback conversations."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.identity import AuthenticatedPrincipal
from server.infrastructure.mysql.models import FeedbackMessageModel, FeedbackThreadModel, UserModel


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


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
    message = FeedbackMessageModel(id=str(uuid4()), thread_id=thread.id, sender_user_id=principal.user_id, sender_type="student", body=body.strip(), created_at=now, updated_at=now)
    session.add(message)
    thread.updated_at = now
    await session.flush()
    return {"thread_id": thread.id, "message": _message_payload(message)}


async def list_feedback_threads(session: AsyncSession) -> list[dict]:
    rows = await session.execute(select(FeedbackThreadModel, UserModel).join(UserModel, UserModel.id == FeedbackThreadModel.user_id).order_by(FeedbackThreadModel.updated_at.desc()))
    result = []
    for thread, user in rows:
        latest = await session.scalar(select(FeedbackMessageModel).where(FeedbackMessageModel.thread_id == thread.id).order_by(FeedbackMessageModel.created_at.desc()).limit(1))
        unread_query = select(func.count(FeedbackMessageModel.id)).where(FeedbackMessageModel.thread_id == thread.id, FeedbackMessageModel.sender_type == "student")
        if thread.developer_read_at is not None:
            unread_query = unread_query.where(FeedbackMessageModel.created_at > thread.developer_read_at)
        unread = await session.scalar(unread_query)
        result.append({"thread_id": thread.id, "user_id": user.id, "username": user.username, "unread_count": int(unread or 0), "updated_at": _iso_utc(thread.updated_at), "latest": _message_payload(latest) if latest else None})
    return result


async def get_feedback_thread(session: AsyncSession, thread_id: str) -> dict:
    row = await session.execute(select(FeedbackThreadModel, UserModel).join(UserModel, UserModel.id == FeedbackThreadModel.user_id).where(FeedbackThreadModel.id == thread_id))
    result = row.first()
    if result is None:
        raise LookupError(thread_id)
    thread, user = result
    messages = list((await session.scalars(select(FeedbackMessageModel).where(FeedbackMessageModel.thread_id == thread.id).order_by(FeedbackMessageModel.created_at.asc()))).all())
    return {"thread_id": thread.id, "user_id": user.id, "username": user.username, "messages": [_message_payload(message) for message in messages]}


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
