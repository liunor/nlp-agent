from datetime import datetime

import pytest

from server.infrastructure.mysql.models import FeedbackMessageModel, FeedbackThreadModel, UserModel
from server.web.contracts import FeedbackBody
from server.web.feedback import list_feedback_threads


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def __iter__(self):
        return iter(self._rows)


class _Session:
    def __init__(self, thread, user, latest, unread):
        self.rows = _Rows([(thread, user)])
        self.values = [latest, unread]
        self.statements = []

    async def execute(self, statement):
        self.statements.append(statement)
        return self.rows

    async def scalar(self, statement):
        self.statements.append(statement)
        return self.values.pop(0)


@pytest.mark.asyncio
async def test_feedback_unread_count_is_scoped_to_the_current_thread() -> None:
    now = datetime(2026, 8, 18, 12, 0, 0)
    thread = FeedbackThreadModel(id="thread-1", user_id="user-1", created_at=now, updated_at=now)
    user = UserModel(id="user-1", username="student", password_hash="x", display_name="Student")
    latest = FeedbackMessageModel(id="message-1", thread_id="thread-1", sender_user_id="user-1", sender_type="student", body="反馈", created_at=now, updated_at=now)
    session = _Session(thread, user, latest, 1)

    result = await list_feedback_threads(session)

    assert result[0]["unread_count"] == 1
    unread_statement = session.statements[-1]
    compiled_unread = unread_statement.compile(compile_kwargs={"literal_binds": True})
    unread_sql = str(compiled_unread)
    assert "nlp_feedback_messages.thread_id = 'thread-1'" in unread_sql
    assert "nlp_feedback_messages.sender_type = 'student'" in unread_sql
    assert result[0]["latest"]["created_at"] == "2026-08-18T12:00:00+00:00"
    assert result[0]["updated_at"] == "2026-08-18T12:00:00+00:00"


def test_feedback_body_rejects_whitespace_only_input() -> None:
    with pytest.raises(ValueError):
        FeedbackBody(body=" \t\n")

    assert FeedbackBody(body="  useful feedback  ").body == "useful feedback"
