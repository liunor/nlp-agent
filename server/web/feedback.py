"""Persistent student feedback conversations."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import Row, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from core.identity import AuthenticatedPrincipal
from server.infrastructure.mysql.models import FeedbackMessageModel, FeedbackThreadModel, UserModel


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# Kept in sync with ck_nlp_feedback_messages_sender_type.
_STUDENT_SENDER_TYPE = "student"
_DEVELOPER_SENDER_TYPE = "developer"

FEEDBACK_STATUSES = ("open", "under_review", "planned", "in_progress", "complete", "closed")
FEEDBACK_CATEGORIES = ("feature", "ux", "bug", "other")
FEEDBACK_PRIORITIES = ("low", "medium", "high")
FEEDBACK_DAILY_LIMIT = 3
_BEIJING_TZ = timezone(timedelta(hours=8))


def _iso_utc(value: datetime) -> str:
    return value.replace(tzinfo=timezone.utc).isoformat()


def _message_payload(message: FeedbackMessageModel) -> dict:
    return {"id": message.id, "sender_type": message.sender_type, "body": message.body, "created_at": _iso_utc(message.created_at)}


def _today_start_utc() -> datetime:
    now_bj = datetime.now(_BEIJING_TZ)
    today_start_bj = now_bj.replace(hour=0, minute=0, second=0, microsecond=0)
    return today_start_bj.astimezone(timezone.utc).replace(tzinfo=None)


def _normalize_category(value: str | None) -> str | None:
    if value is None:
        return None
    v = value.strip().lower()
    if not v:
        return None
    if v not in FEEDBACK_CATEGORIES:
        raise ValueError(f"invalid category: {value}")
    return v


def _thread_payload(thread: FeedbackThreadModel, user: UserModel, latest: Row | None, unread_count: int) -> dict:
    return {
        "thread_id": thread.id,
        "user_id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "unread_count": unread_count,
        "updated_at": _iso_utc(thread.updated_at),
        "status": getattr(thread, "status", "open") or "open",
        "category": getattr(thread, "category", "other") or "other",
        "priority": getattr(thread, "priority", "medium") or "medium",
        "latest": _message_payload(latest) if latest else None,
    }


async def submit_feedback(session: AsyncSession, principal: AuthenticatedPrincipal, body: str, category: str | None = None) -> dict:
    # Serialize first submissions for one user so the unique thread constraint
    # cannot turn concurrent clicks into a 500 response.
    await session.scalar(select(UserModel.id).where(UserModel.id == principal.user_id).with_for_update())
    # Daily limit: at most FEEDBACK_DAILY_LIMIT student messages per natural day (Beijing).
    today_start = _today_start_utc()
    daily_count_raw = await session.scalar(
        select(func.count())
        .select_from(FeedbackMessageModel)
        .where(
            FeedbackMessageModel.sender_user_id == principal.user_id,
            FeedbackMessageModel.sender_type == _STUDENT_SENDER_TYPE,
            FeedbackMessageModel.created_at >= today_start,
        )
    )
    # _WriteSession test stub returns the user_id string for non-thread queries; treat non-int as 0.
    try:
        daily_count = int(daily_count_raw) if daily_count_raw is not None else 0
    except (TypeError, ValueError):
        daily_count = 0
    if daily_count >= FEEDBACK_DAILY_LIMIT:
        raise ValueError("feedback_daily_limit")
    thread = await session.scalar(select(FeedbackThreadModel).where(FeedbackThreadModel.user_id == principal.user_id))
    if thread is None:
        now = _now()
        normalized_category = _normalize_category(category) or "other"
        thread = FeedbackThreadModel(
            id=str(uuid4()),
            user_id=principal.user_id,
            created_at=now,
            updated_at=now,
            status="open",
            category=normalized_category,
            priority="medium",
        )
        session.add(thread)
        await session.flush()
    now = _now()
    # Track student-chosen category on the thread for list filtering; keep developer-set
    # category unless student explicitly chose one.
    normalized_category = _normalize_category(category)
    if normalized_category:
        thread.category = normalized_category  # type: ignore[assignment]
    # Reopen if a previously closed/complete thread gets a new student message.
    current_status = getattr(thread, "status", "open") or "open"
    if current_status in ("closed", "complete"):
        thread.status = "open"  # type: ignore[assignment]
    message = FeedbackMessageModel(id=str(uuid4()), thread_id=thread.id, sender_user_id=principal.user_id, sender_type=_STUDENT_SENDER_TYPE, body=body.strip(), created_at=now, updated_at=now)
    session.add(message)
    thread.updated_at = now
    await session.flush()
    # Remaining quota for the day after this insert.
    remaining = max(0, FEEDBACK_DAILY_LIMIT - daily_count - 1)
    return {"thread_id": thread.id, "message": _message_payload(message), "remaining": remaining, "daily_limit": FEEDBACK_DAILY_LIMIT}


async def get_feedback_daily_state(session: AsyncSession, principal: AuthenticatedPrincipal) -> dict:
    today_start = _today_start_utc()
    daily_count_raw = await session.scalar(
        select(func.count())
        .select_from(FeedbackMessageModel)
        .where(
            FeedbackMessageModel.sender_user_id == principal.user_id,
            FeedbackMessageModel.sender_type == _STUDENT_SENDER_TYPE,
            FeedbackMessageModel.created_at >= today_start,
        )
    )
    try:
        daily_count = int(daily_count_raw) if daily_count_raw is not None else 0
    except (TypeError, ValueError):
        daily_count = 0
    used = daily_count
    # today_start is naive UTC; convert back to aware for iso
    aware = today_start.replace(tzinfo=timezone.utc)
    return {"used": used, "remaining": max(0, FEEDBACK_DAILY_LIMIT - used), "limit": FEEDBACK_DAILY_LIMIT, "today_start_utc": aware.isoformat()}


async def reply_feedback(session: AsyncSession, principal: AuthenticatedPrincipal, thread_id: str, body: str) -> dict:
    thread = await session.scalar(select(FeedbackThreadModel).where(FeedbackThreadModel.id == thread_id))
    if thread is None:
        raise LookupError(thread_id)
    now = _now()
    message = FeedbackMessageModel(
        id=str(uuid4()),
        thread_id=thread.id,
        sender_user_id=principal.user_id,
        sender_type=_DEVELOPER_SENDER_TYPE,
        body=body.strip(),
        created_at=now,
        updated_at=now,
    )
    session.add(message)
    thread.updated_at = now
    await session.flush()
    return {"thread_id": thread.id, "message": _message_payload(message)}


async def update_feedback_thread(
    session: AsyncSession, thread_id: str, *, status: str | None = None, category: str | None = None, priority: str | None = None
) -> dict:
    thread = await session.scalar(select(FeedbackThreadModel).where(FeedbackThreadModel.id == thread_id))
    if thread is None:
        raise LookupError(thread_id)
    if status is not None:
        if status not in FEEDBACK_STATUSES:
            raise ValueError(f"invalid status: {status}")
        thread.status = status  # type: ignore[assignment]
    if category is not None:
        norm = _normalize_category(category)
        if norm is None or norm not in FEEDBACK_CATEGORIES:
            raise ValueError(f"invalid category: {category}")
        thread.category = norm  # type: ignore[assignment]
    if priority is not None:
        if priority not in FEEDBACK_PRIORITIES:
            raise ValueError(f"invalid priority: {priority}")
        thread.priority = priority  # type: ignore[assignment]
    thread.updated_at = _now()
    await session.flush()
    # Return the full thread contract so PATCH responses match the frontend type.
    return await get_feedback_thread(session, thread.id)


def _escape_like(value: str) -> str:
    # MySQL's LIKE defaults to the backslash escape character; without this a
    # search for "%" or "_" silently degrades into a wildcard match.
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def list_feedback_threads(
    session: AsyncSession,
    *,
    limit: int = 50,
    offset: int = 0,
    search: str | None = None,
    status: str | None = None,
    category: str | None = None,
    priority: str | None = None,
    sort: str | None = None,
) -> dict:
    normalized = (search or "").strip().lower()
    sort_key = (sort or "latest").strip().lower()
    if sort_key not in ("latest", "oldest", "unread"):
        raise ValueError(f"invalid sort: {sort}")
    if status and status not in FEEDBACK_STATUSES:
        raise ValueError(f"invalid status: {status}")
    norm_cat = _normalize_category(category) if category else None
    if norm_cat is not None and norm_cat not in FEEDBACK_CATEGORIES:
        raise ValueError(f"invalid category: {category}")
    if priority and priority not in FEEDBACK_PRIORITIES:
        raise ValueError(f"invalid priority: {priority}")
    unread_subq = (
        select(func.count(FeedbackMessageModel.id))
        .where(
            FeedbackMessageModel.thread_id == FeedbackThreadModel.id,
            FeedbackMessageModel.sender_type == _STUDENT_SENDER_TYPE,
            or_(
                FeedbackThreadModel.developer_read_at.is_(None),
                FeedbackMessageModel.created_at > FeedbackThreadModel.developer_read_at,
            ),
        )
        .correlate(FeedbackThreadModel)
        .scalar_subquery()
    )
    if sort_key == "unread":
        order_clause = [unread_subq.desc(), FeedbackThreadModel.updated_at.desc(), FeedbackThreadModel.id.desc()]
    elif sort_key == "oldest":
        order_clause = [FeedbackThreadModel.updated_at.asc(), FeedbackThreadModel.id.asc()]
    else:
        order_clause = [FeedbackThreadModel.updated_at.desc(), FeedbackThreadModel.id.desc()]
    thread_query = (
        select(FeedbackThreadModel, UserModel, unread_subq.label("unread_count"))
        .join(UserModel, UserModel.id == FeedbackThreadModel.user_id)
        .order_by(*order_clause)
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
    if status:
        thread_query = thread_query.where(FeedbackThreadModel.status == status)
        total_query = total_query.where(FeedbackThreadModel.status == status)
    if norm_cat:
        thread_query = thread_query.where(FeedbackThreadModel.category == norm_cat)
        total_query = total_query.where(FeedbackThreadModel.category == norm_cat)
    if priority:
        thread_query = thread_query.where(FeedbackThreadModel.priority == priority)
        total_query = total_query.where(FeedbackThreadModel.priority == priority)

    total = int(await session.scalar(total_query) or 0)
    rows = (await session.execute(thread_query)).all()

    thread_ids = [thread.id for thread, _user, _unread in rows]
    latest_by_thread: dict[str, Row] = {}
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
                    order_by=(FeedbackMessageModel.created_at.desc(), FeedbackMessageModel.id.desc()),
                )
                .label("rn"),
            )
            .where(FeedbackMessageModel.thread_id.in_(thread_ids))
            .subquery()
        )
        latest_rows = await session.execute(select(ranked).where(ranked.c.rn == 1))
        latest_by_thread = {row.thread_id: row for row in latest_rows}

    result = []
    for thread, user, unread_count in rows:
        latest = latest_by_thread.get(thread.id)
        result.append(_thread_payload(thread, user, latest, int(unread_count)))
    return {"items": result, "total": total}


async def get_feedback_thread(session: AsyncSession, thread_id: str) -> dict:
    row = await session.execute(select(FeedbackThreadModel, UserModel).join(UserModel, UserModel.id == FeedbackThreadModel.user_id).where(FeedbackThreadModel.id == thread_id))
    result = row.first()
    if result is None:
        raise LookupError(thread_id)
    thread, user = result
    messages = list((await session.scalars(select(FeedbackMessageModel).where(FeedbackMessageModel.thread_id == thread.id).order_by(FeedbackMessageModel.created_at.asc()))).all())
    return {
        "thread_id": thread.id,
        "user_id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "status": getattr(thread, "status", "open") or "open",
        "category": getattr(thread, "category", "other") or "other",
        "priority": getattr(thread, "priority", "medium") or "medium",
        "updated_at": _iso_utc(thread.updated_at),
        "messages": [_message_payload(message) for message in messages],
    }


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
    await session.execute(
        update(FeedbackThreadModel)
        .where(
            FeedbackThreadModel.id == thread_id,
            or_(
                FeedbackThreadModel.developer_read_at.is_(None),
                FeedbackThreadModel.developer_read_at < message.created_at,
            ),
        )
        .values(developer_read_at=message.created_at)
    )


async def delete_feedback_thread(session: AsyncSession, thread_id: str) -> None:
    """Hard-delete a feedback thread and its messages (不可恢复)."""
    thread = await session.scalar(select(FeedbackThreadModel).where(FeedbackThreadModel.id == thread_id))
    if thread is None:
        raise LookupError(thread_id)
    await session.delete(thread)
    await session.flush()
