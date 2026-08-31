"""Feedback persistence tests against the migrated MySQL schema.

These tests exercise the real ``submit_feedback`` / ``list_feedback_threads`` /
``mark_feedback_read`` services on a database migrated to the current Alembic
head (thread isolation, concurrent first submissions enforced by the
``FOR UPDATE`` row lock, the sender-type CHECK constraint and the pagination
indexes). They skip when ``NLP_AGENT_DATABASE_URL`` is not configured, mirroring
``test_data_integrity.py``.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from core.identity import AuthenticatedPrincipal
from server.infrastructure.mysql import DatabaseConfig, create_engine, create_session_factory
from server.user.schemas import UserCreate
from server.user.service import UserService
from server.web.feedback import (
    delete_feedback_thread,
    delete_feedback_threads,
    list_feedback_threads,
    mark_feedback_read,
    mark_feedback_threads_read,
    reply_feedback,
    submit_feedback,
    update_feedback_thread,
)


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


def _principal(user_id: str) -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(user_id=user_id, workspace_ids=frozenset({"default"}))


async def _create_user(factory, username: str) -> str:
    async with factory() as session:
        async with session.begin():
            user = await UserService(session).create_user(
                UserCreate(username=username, password="password123", display_name=username)
            )
            return user.id


async def _submit(factory, user_id: str, body: str, category: str | None = None) -> dict:
    async with factory() as session:
        async with session.begin():
            return await submit_feedback(session, _principal(user_id), body, category=category)


@pytest.mark.asyncio
async def test_feedback_threads_are_isolated_per_user(mysql_session_factory) -> None:
    factory = mysql_session_factory
    first_user = await _create_user(factory, f"fbiso{uuid4().hex[:10]}")
    second_user = await _create_user(factory, f"fbiso{uuid4().hex[:10]}")

    first = await _submit(factory, first_user, "第一条反馈")
    repeat = await _submit(factory, first_user, "第二条反馈")
    other = await _submit(factory, second_user, "别人的反馈")

    # Same user keeps one thread; different users never share one.
    assert first["thread_id"] == repeat["thread_id"]
    assert other["thread_id"] != first["thread_id"]

    async with factory() as session:
        result = await list_feedback_threads(session)
    by_thread = {item["thread_id"]: item for item in result["items"]}
    assert len(result["items"]) >= 2

    mine = by_thread[first["thread_id"]]
    assert mine["user_id"] == first_user
    assert mine["unread_count"] == 2
    assert mine["latest"]["body"] == "第二条反馈"
    theirs = by_thread[other["thread_id"]]
    assert theirs["user_id"] == second_user
    assert theirs["unread_count"] == 1
    assert theirs["latest"]["body"] == "别人的反馈"


@pytest.mark.asyncio
async def test_concurrent_first_submissions_share_one_thread(mysql_session_factory) -> None:
    factory = mysql_session_factory
    user_id = await _create_user(factory, f"fbcon{uuid4().hex[:10]}")

    async def submit(body: str) -> dict:
        async with factory() as session:
            async with session.begin():
                return await submit_feedback(session, _principal(user_id), body)

    results = await asyncio.gather(submit("并发一"), submit("并发二"))

    thread_ids = {result["thread_id"] for result in results}
    assert len(thread_ids) == 1, "the FOR UPDATE lock must serialize first submissions"

    thread_id = next(iter(thread_ids))
    async with factory() as session:
        count = await session.scalar(
            text("SELECT COUNT(*) FROM nlp_feedback_messages WHERE thread_id = :thread_id"),
            {"thread_id": thread_id},
        )
        unread = await session.scalar(
            text(
                "SELECT COUNT(*) FROM nlp_feedback_messages "
                "WHERE thread_id = :thread_id AND sender_type = 'student'"
            ),
            {"thread_id": thread_id},
        )
    assert count == 2
    assert unread == 2


@pytest.mark.asyncio
async def test_migrated_schema_carries_feedback_constraints_and_indexes(mysql_session_factory) -> None:
    factory = mysql_session_factory
    async with factory() as session:
        columns = {
            row[0]
            for row in await session.execute(
                text(
                    "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
                    "WHERE TABLE_SCHEMA = DATABASE() "
                    "AND TABLE_NAME = 'nlp_feedback_threads'"
                )
            )
        }
        checks = {
            row[0]: row[1]
            for row in await session.execute(
                text(
                    "SELECT cc.CONSTRAINT_NAME, cc.CHECK_CLAUSE "
                    "FROM information_schema.CHECK_CONSTRAINTS cc "
                    "JOIN information_schema.TABLE_CONSTRAINTS tc "
                    "ON tc.CONSTRAINT_NAME = cc.CONSTRAINT_NAME "
                    "AND tc.CONSTRAINT_SCHEMA = cc.CONSTRAINT_SCHEMA "
                    "WHERE tc.TABLE_SCHEMA = DATABASE() "
                    "AND tc.TABLE_NAME = 'nlp_feedback_messages' "
                    "AND tc.CONSTRAINT_TYPE = 'CHECK'"
                )
            )
        }
        indexes = {}
        for row in await session.execute(
            text(
                "SELECT INDEX_NAME, COLUMN_NAME FROM information_schema.STATISTICS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'nlp_feedback_threads'"
            )
        ):
            indexes.setdefault(row[0], set()).add(row[1])

    assert any("sender_type" in clause.replace("`", "").lower() for clause in checks.values()), (
        f"sender_type CHECK constraint missing after migration; found {sorted(checks)}"
    )

    assert "ix_nlp_feedback_threads_updated_at" in indexes
    assert "student_read_at" in columns
    message_indexes = set()
    async with factory() as session:
        for row in await session.execute(
            text(
                "SELECT INDEX_NAME, COLUMN_NAME FROM information_schema.STATISTICS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'nlp_feedback_messages'"
            )
        ):
            message_indexes.add(row[0])
    assert "ix_nlp_feedback_messages_thread_created" in message_indexes


@pytest.mark.asyncio
async def test_sender_type_check_rejects_values_outside_the_vocabulary(mysql_session_factory) -> None:
    factory = mysql_session_factory
    user_id = await _create_user(factory, f"fbchk{uuid4().hex[:10]}")
    thread_id = (await _submit(factory, user_id, "先建立线程"))["thread_id"]

    # MySQL reports CHECK violations as error 3819 through OperationalError
    # (a DBAPIError subclass); the exception must escape the transaction so
    # the session rolls back cleanly.
    with pytest.raises(DBAPIError):
        async with factory() as session:
            async with session.begin():
                await session.execute(
                    text(
                        "INSERT INTO nlp_feedback_messages "
                        "(id, thread_id, sender_user_id, sender_type, body, created_at, updated_at) "
                        "VALUES (:id, :thread_id, :user_id, 'teacher', '非法值', UTC_TIMESTAMP(6), UTC_TIMESTAMP(6))"
                    ),
                    {"id": str(uuid4()), "thread_id": thread_id, "user_id": user_id},
                )

    # Nothing slipped through and the vocabulary stays two-valued.
    async with factory() as session:
        types = {
            row[0]
            for row in await session.execute(
                text(
                    "SELECT DISTINCT sender_type FROM nlp_feedback_messages "
                    "WHERE thread_id = :thread_id"
                ),
                {"thread_id": thread_id},
            )
        }
    assert types == {"student"}, f"invalid sender_type persisted; found {types}"


@pytest.mark.asyncio
async def test_marking_read_flips_the_unread_count(mysql_session_factory) -> None:
    factory = mysql_session_factory
    user_id = await _create_user(factory, f"fbrd{uuid4().hex[:10]}")
    submitted = await _submit(factory, user_id, "请查看这条反馈")
    thread_id = submitted["thread_id"]
    message_id = submitted["message"]["id"]

    async with factory() as session:
        before = await list_feedback_threads(session)
    assert next(item for item in before["items"] if item["thread_id"] == thread_id)["unread_count"] == 1

    async with factory() as session:
        async with session.begin():
            await mark_feedback_read(session, thread_id, message_id)

    async with factory() as session:
        after = await list_feedback_threads(session)
    assert next(item for item in after["items"] if item["thread_id"] == thread_id)["unread_count"] == 0


@pytest.mark.asyncio
async def test_duplicate_thread_per_user_is_rejected_by_unique_constraint(mysql_session_factory) -> None:
    factory = mysql_session_factory
    user_id = await _create_user(factory, f"fbuq{uuid4().hex[:10]}")
    thread_id = (await _submit(factory, user_id, "占用唯一约束"))["thread_id"]

    async with factory() as session:
        async with session.begin():
            with pytest.raises(DBAPIError):
                await session.execute(
                    text(
                        "INSERT INTO nlp_feedback_threads (id, user_id, created_at, updated_at) "
                        "VALUES (:id, :user_id, UTC_TIMESTAMP(6), UTC_TIMESTAMP(6))"
                    ),
                    {"id": str(uuid4()), "user_id": user_id},
                )


@pytest.mark.asyncio
async def test_update_feedback_thread_persists_fields_and_returns_full_thread(mysql_session_factory) -> None:
    factory = mysql_session_factory
    user_id = await _create_user(factory, f"fbup{uuid4().hex[:10]}")
    submitted = await _submit(factory, user_id, "update me")

    async with factory() as session:
        async with session.begin():
            result = await update_feedback_thread(session, submitted["thread_id"], status="planned", category="bug", priority="high")

    assert result["thread_id"] == submitted["thread_id"]
    assert result["status"] == "planned"
    assert result["category"] == "bug"
    assert result["priority"] == "high"
    assert result["messages"][0]["id"] == submitted["message"]["id"]


@pytest.mark.asyncio
async def test_unread_sort_orders_across_pages_before_pagination(mysql_session_factory) -> None:
    factory = mysql_session_factory
    prefix = f"fbpage{uuid4().hex[:10]}"
    unread_user = await _create_user(factory, prefix + "u")
    read_user = await _create_user(factory, prefix + "r")
    unread = await _submit(factory, unread_user, "unread first")
    read = await _submit(factory, read_user, "already read")
    async with factory() as session:
        async with session.begin():
            await mark_feedback_read(session, read["thread_id"], read["message"]["id"])

    async with factory() as session:
        first_page = await list_feedback_threads(session, limit=1, offset=0, sort="unread", search=prefix)
        second_page = await list_feedback_threads(session, limit=1, offset=1, sort="unread", search=prefix)

    assert first_page["items"][0]["thread_id"] == unread["thread_id"]
    assert second_page["items"][0]["thread_id"] == read["thread_id"]


@pytest.mark.asyncio
async def test_conditional_mark_read_does_not_regress_newer_read_time(mysql_session_factory) -> None:
    factory = mysql_session_factory
    user_id = await _create_user(factory, f"fbfu{uuid4().hex[:10]}")
    submitted = await _submit(factory, user_id, "stale update")
    future = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=1)

    async with factory() as session:
        async with session.begin():
            await session.execute(text("UPDATE nlp_feedback_threads SET developer_read_at = :ts WHERE id = :thread_id"), {"ts": future, "thread_id": submitted["thread_id"]})

    async with factory() as session:
        async with session.begin():
            await mark_feedback_read(session, submitted["thread_id"], submitted["message"]["id"])

    async with factory() as session:
        read_at = await session.scalar(text("SELECT developer_read_at FROM nlp_feedback_threads WHERE id = :thread_id"), {"thread_id": submitted["thread_id"]})
    assert read_at == future


@pytest.mark.asyncio
async def test_reply_and_delete_feedback_thread(mysql_session_factory) -> None:
    factory = mysql_session_factory
    student_id = await _create_user(factory, f"fbrs{uuid4().hex[:10]}")
    developer_id = await _create_user(factory, f"fbrd{uuid4().hex[:10]}")
    submitted = await _submit(factory, student_id, "needs reply", category="bug")

    async with factory() as session:
        async with session.begin():
            reply = await reply_feedback(session, _principal(developer_id), submitted["thread_id"], "handled")
    assert reply["message"]["sender_type"] == "developer"

    async with factory() as session:
        async with session.begin():
            await delete_feedback_thread(session, submitted["thread_id"])

    async with factory() as session:
        remaining = await list_feedback_threads(session)
    assert all(item["thread_id"] != submitted["thread_id"] for item in remaining["items"])


@pytest.mark.asyncio
async def test_bulk_feedback_actions_persist_read_state_and_delete_threads(mysql_session_factory) -> None:
    factory = mysql_session_factory
    prefix = f"fbbulk{uuid4().hex[:10]}"
    first_user = await _create_user(factory, prefix + "a")
    second_user = await _create_user(factory, prefix + "b")
    first = await _submit(factory, first_user, "first bulk item")
    second = await _submit(factory, second_user, "second bulk item")
    thread_ids = [first["thread_id"], second["thread_id"]]

    async with factory() as session:
        async with session.begin():
            updated = await mark_feedback_threads_read(session, thread_ids)
    assert updated == 2

    async with factory() as session:
        listing = await list_feedback_threads(session, search=prefix)
    assert {item["unread_count"] for item in listing["items"]} == {0}

    async with factory() as session:
        async with session.begin():
            deleted = await delete_feedback_threads(session, thread_ids)
    assert deleted == 2

    async with factory() as session:
        remaining = await list_feedback_threads(session, search=prefix)
    assert remaining["items"] == []
