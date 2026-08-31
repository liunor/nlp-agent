"""Regression coverage for the feedback feature extracted from PR #101."""

from datetime import datetime

import pytest

from core.identity import AuthenticatedPrincipal
from server.infrastructure.mysql.models import FeedbackMessageModel, FeedbackThreadModel
from server.web.contracts import FeedbackBody, FeedbackReplyBody, FeedbackUpdateBody
from server.web.feedback import reply_feedback, submit_feedback


class _QuotaSession:
    def __init__(self, daily_count: int):
        self.daily_count = daily_count
        self.statements = []
        self.added = []

    async def scalar(self, statement):
        self.statements.append(statement)
        statement_text = str(statement)
        if "nlp_feedback_threads" in statement_text:
            return None
        if "nlp_feedback_messages" in statement_text:
            return self.daily_count
        return "user-1"

    def add(self, value):
        self.added.append(value)

    async def flush(self):
        return None


class _ReplySession:
    def __init__(self, thread):
        self.thread = thread
        self.added = []

    async def scalar(self, _statement):
        return self.thread

    def add(self, value):
        self.added.append(value)

    async def flush(self):
        return None


def test_feedback_contracts_normalize_and_validate_public_values() -> None:
    assert FeedbackBody(body="  反馈  ", category=" BUG ").model_dump() == {
        "body": "反馈",
        "category": "bug",
    }
    assert FeedbackUpdateBody(status="planned", category="ux", priority="high")
    assert FeedbackReplyBody(body="  已处理  ").body == "已处理"
    with pytest.raises(ValueError):
        FeedbackBody(body="反馈", category="invalid")


@pytest.mark.asyncio
async def test_feedback_daily_limit_is_enforced_before_thread_write() -> None:
    session = _QuotaSession(daily_count=3)

    with pytest.raises(ValueError, match="feedback_daily_limit"):
        await submit_feedback(session, AuthenticatedPrincipal(user_id="user-1"), "too many")

    assert not session.added


@pytest.mark.asyncio
async def test_developer_reply_rejects_empty_body_at_service_boundary() -> None:
    now = datetime(2026, 8, 31, 12, 0, 0)
    thread = FeedbackThreadModel(
        id="thread-1", user_id="student-1", created_at=now, updated_at=now
    )
    session = _ReplySession(thread)

    with pytest.raises(ValueError, match="feedback_body_empty"):
        await reply_feedback(
            session,
            AuthenticatedPrincipal(user_id="developer-1"),
            thread.id,
            " \t\n ",
        )

    assert not session.added


def test_feedback_thread_model_declares_metadata_constraints() -> None:
    constraints = {
        constraint.name: str(getattr(constraint, "sqltext", "")).replace("`", "").lower()
        for constraint in FeedbackThreadModel.__table__.constraints
        if constraint.name
    }

    assert "ck_nlp_feedback_threads_status" in constraints
    assert "ck_nlp_feedback_threads_category" in constraints
    assert "ck_nlp_feedback_threads_priority" in constraints
    assert "status in" in constraints["ck_nlp_feedback_threads_status"]
    assert "category in" in constraints["ck_nlp_feedback_threads_category"]
    assert "priority in" in constraints["ck_nlp_feedback_threads_priority"]
    assert "student_read_at" in FeedbackThreadModel.__table__.columns


def test_feedback_message_model_keeps_student_and_developer_sender_types() -> None:
    constraints = {
        constraint.name: str(getattr(constraint, "sqltext", "")).replace("`", "").lower()
        for constraint in FeedbackMessageModel.__table__.constraints
        if constraint.name
    }
    assert "ck_nlp_feedback_messages_sender_type" in constraints
