from datetime import datetime

import pytest

from core.identity import AuthenticatedPrincipal
from server.infrastructure.mysql.models import FeedbackMessageModel, FeedbackThreadModel, UserModel
from server.web.contracts import FeedbackBody
from server.web.feedback import list_feedback_threads, submit_feedback


class _Result:
    def __init__(self, rows):
        self._rows = list(rows)

    def all(self):
        return list(self._rows)

    def __iter__(self):
        return iter(self._rows)


class _Session:
    """FIFO stub: each scalar/execute call consumes the next queued value."""

    def __init__(self, scalars=(), results=()):
        self.scalars = list(scalars)
        self.results = list(results)
        self.statements = []

    async def scalar(self, statement):
        self.statements.append(statement)
        return self.scalars.pop(0)

    async def execute(self, statement):
        self.statements.append(statement)
        return self.results.pop(0)


def _sql(statement) -> str:
    return str(statement.compile(compile_kwargs={"literal_binds": True}))


@pytest.mark.asyncio
async def test_feedback_list_aggregates_latest_and_unread_per_page() -> None:
    now = datetime(2026, 8, 18, 12, 0, 0)
    thread = FeedbackThreadModel(id="thread-1", user_id="user-1", created_at=now, updated_at=now)
    user = UserModel(id="user-1", username="student", password_hash="x", display_name="Student")
    latest = _Result([
        type("LatestRow", (), {
            "id": "message-1",
            "thread_id": "thread-1",
            "sender_type": "student",
            "body": "反馈",
            "created_at": now,
        })(),
    ])
    session = _Session(scalars=[1], results=[_Result([(thread, user)]), latest, _Result([("thread-1", 3)])])

    result = await list_feedback_threads(session, limit=50, offset=0)

    assert result["total"] == 1
    assert len(result["items"]) == 1
    row = result["items"][0]
    assert row["unread_count"] == 3
    assert row["latest"]["id"] == "message-1"
    assert row["latest"]["created_at"] == "2026-08-18T12:00:00+00:00"
    assert row["updated_at"] == "2026-08-18T12:00:00+00:00"

    # Exactly four statements regardless of thread count:
    # total COUNT, page query, latest-message window, unread GROUP BY.
    assert len(session.statements) == 4
    unread_sql = _sql(session.statements[-1])
    assert "COUNT" in unread_sql.upper()
    assert "sender_type" in unread_sql and "'student'" in unread_sql
    assert "developer_read_at IS NULL" in unread_sql


@pytest.mark.asyncio
async def test_feedback_list_applies_limit_offset_and_search() -> None:
    session = _Session(scalars=[0], results=[_Result([])])

    result = await list_feedback_threads(session, limit=20, offset=40, search="  Alice  ")

    assert result == {"items": [], "total": 0}
    assert len(session.statements) == 2
    page_sql = _sql(session.statements[1])
    assert "LIMIT 20 OFFSET 40" in page_sql
    assert "%alice%" in page_sql


@pytest.mark.asyncio
async def test_feedback_search_escapes_like_wildcards() -> None:
    session = _Session(scalars=[0], results=[_Result([])])

    await list_feedback_threads(session, search="100%_done\\")

    page_sql = _sql(session.statements[1])
    # Wildcards must reach the server as literals, not as % / _ operators.
    assert "%100\\%\\_done\\\\%" in page_sql
    assert "ESCAPE '\\\\'" in page_sql or "ESCAPE '\\'" in page_sql


@pytest.mark.asyncio
async def test_feedback_list_read_cutoff_scopes_unread_to_developer_read_at() -> None:
    now = datetime(2026, 8, 18, 12, 0, 0)
    read_at = datetime(2026, 8, 17, 9, 0, 0)
    thread = FeedbackThreadModel(id="thread-2", user_id="user-2", created_at=now, updated_at=now, developer_read_at=read_at)
    user = UserModel(id="user-2", username="student2", password_hash="x", display_name="Student 2")
    session = _Session(
        scalars=[1],
        results=[
            _Result([(thread, user)]),
            _Result([]),
            _Result([("thread-2", 2)]),
        ],
    )

    result = await list_feedback_threads(session)

    unread_sql = _sql(session.statements[-1])
    assert "nlp_feedback_messages.created_at > nlp_feedback_threads.developer_read_at" in unread_sql
    assert result["items"][0]["unread_count"] == 2


def test_feedback_body_rejects_whitespace_only_input() -> None:
    with pytest.raises(ValueError):
        FeedbackBody(body=" \t\n")

    assert FeedbackBody(body="  useful feedback  ").body == "useful feedback"


def test_feedback_sender_type_is_constrained_at_the_database_layer() -> None:
    constraints = {
        constraint.name: constraint
        for constraint in FeedbackMessageModel.__table__.constraints
        if constraint.name
    }

    check = constraints.get("ck_nlp_feedback_messages_sender_type")
    assert check is not None, "create_all path must carry the sender_type CHECK too"
    sql = str(check.sqltext).replace("`", "").lower()
    assert "sender_type in ('student', 'developer')" in sql


class _WriteSession:
    """Captures statements for submit_feedback; routes scalar calls by table."""

    def __init__(self, existing_thread=None):
        self.existing_thread = existing_thread
        self.statements = []
        self.added = []

    async def scalar(self, statement):
        self.statements.append(statement)
        if "nlp_feedback_threads" in str(statement):
            return self.existing_thread
        return "user-1"

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        return None


@pytest.mark.asyncio
async def test_submit_feedback_serializes_first_submission_with_a_row_lock() -> None:
    session = _WriteSession()

    await submit_feedback(session, AuthenticatedPrincipal(user_id="user-1"), "hello")

    first_sql = str(session.statements[0].compile(compile_kwargs={"literal_binds": True})).upper()
    assert "FOR UPDATE" in first_sql, "concurrent first submissions must serialize on the user row"


@pytest.mark.asyncio
async def test_submit_feedback_creates_one_thread_then_reuses_it() -> None:
    fresh = _WriteSession()
    result = await submit_feedback(fresh, AuthenticatedPrincipal(user_id="user-1"), "first")
    threads = [obj for obj in fresh.added if isinstance(obj, FeedbackThreadModel)]
    messages = [obj for obj in fresh.added if isinstance(obj, FeedbackMessageModel)]

    assert len(threads) == 1 and len(messages) == 1
    assert result["thread_id"] == threads[0].id
    assert messages[0].thread_id == threads[0].id

    existing = threads[0]
    reused = _WriteSession(existing_thread=existing)
    second = await submit_feedback(reused, AuthenticatedPrincipal(user_id="user-1"), "second")

    assert not [obj for obj in reused.added if isinstance(obj, FeedbackThreadModel)]
    assert second["thread_id"] == existing.id
