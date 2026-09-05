"""Session title summarization tests (DB-free; LLM mocked).

The summarizer's correctness rests on four properties exercised here: it
generates at most once (first message anchors the topic, manual titles are
never overwritten), it never raises on model failure, the lease claim keeps a
second worker from paying for a duplicate LLM call, and every write is a
single-row conditional UPDATE scoped to the exact conversation id (the id
already arrived from an authorized turn context, so there is no cross-tenant
write path).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from server.session.summary import (
    MAX_SUMMARY_ATTEMPTS,
    _backoff_summary,
    _clean_input,
    _clean_title,
    _decide,
    _render_turns,
    _select_turns,
    build_conversation_text,
    generate_and_store_summary,
)


def _turn(turn_id: str, input_text: str, result_text: str | None, completed_at: datetime):
    return SimpleNamespace(
        id=turn_id,
        input_text=input_text,
        result_text=result_text,
        completed_at=completed_at,
        created_at=completed_at,
    )


def _make_turns(n: int, start: datetime) -> list:
    return [
        _turn(
            f"turn-{i}",
            f"问题 {i}",
            f"答案 {i}",
            start + timedelta(seconds=i * 10),
        )
        for i in range(n)
    ]


# --- pure helpers ----------------------------------------------------------


def test_clean_input_strips_learning_context_preamble():
    raw = (
        '<!-- nlp-learning-context:{"topic_name":"Transformer"} -->\n'
        "[学习设置：主题=Transformer；难度=入门；教学方式=讲解]\n"
        "什么是注意力机制？"
    )
    assert _clean_input(raw) == "什么是注意力机制？"


def test_clean_title_strips_quotes_and_markdown():
    assert _clean_title('"## 注意力机制入门"') == "注意力机制入门"
    assert _clean_title("**Transformer 编码器**") == "Transformer 编码器"


def test_select_turns_keeps_only_first():
    turns = _make_turns(6, datetime(2026, 1, 1))
    assert [t.id for t in _select_turns(turns)] == ["turn-0"]


def test_render_turns_uses_first_speaker_only():
    turns = [
        _turn("t1", "什么是 BERT？", "BERT 是预训练模型", datetime(2026, 1, 1)),
        _turn("t2", "后续追问", "后续回答", datetime(2026, 1, 1, 0, 0, 10)),
    ]
    assert _render_turns(turns) == "[user]: 什么是 BERT？"


def test_render_turns_falls_back_to_assistant_when_user_empty():
    turns = [_turn("t1", "", "我先来问一句", datetime(2026, 1, 1))]
    assert _render_turns(turns) == "[assistant]: 我先来问一句"


def test_render_turns_truncates_long_messages():
    turns = [_turn("t1", "长" * 5000, None, datetime(2026, 1, 1))]
    text = _render_turns(turns)
    assert text.startswith("[user]: ")
    assert len(text) <= len("[user]: ") + 2000


@pytest.mark.asyncio
async def test_build_conversation_text_uses_first_turn(monkeypatch):
    turns = _make_turns(6, datetime(2026, 1, 1))
    factory = _SessionFactory()

    async def fake_load_state(session, session_id):
        return None, False, turns, 0

    monkeypatch.setattr("server.session.summary._load_state", fake_load_state)

    text = await build_conversation_text("session-1", factory)
    assert text == "[user]: 问题 0"


# --- decide / recompute threshold ------------------------------------------


def test_decide_generates_on_first_turn():
    turns = _make_turns(1, datetime(2026, 1, 1))
    assert _decide(turns, None, False) == turns[-1].completed_at


def test_decide_skips_after_first_summary():
    turns = _make_turns(3, datetime(2026, 1, 1))
    assert _decide(turns, turns[0].completed_at, False) is None


def test_decide_skips_manual_title():
    turns = _make_turns(1, datetime(2026, 1, 1))
    assert _decide(turns, None, True) is None


# --- orchestration with mocked LLM + DB ------------------------------------


class _FakeLLM:
    def __init__(self, title: str = "注意力机制入门"):
        self.title = title
        self.invocations: list = []

    async def ainvoke(self, messages, **kwargs):
        self.invocations.append((messages, kwargs))
        return SimpleNamespace(content=self.title)


class _RecordingSession:
    def __init__(self, claim_rowcount: int = 1, write_rowcount: int = 1):
        self.claim_rowcount = claim_rowcount
        self.write_rowcount = write_rowcount
        self.writes: list = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, statement, params=None):
        self.writes.append((statement, params))
        result = MagicMock()
        sql = str(statement)
        if "title=:title" in sql:
            result.rowcount = self.write_rowcount
        elif "summary_lease_expires_at=:lease_until" in sql:
            result.rowcount = self.claim_rowcount
        else:
            result.rowcount = 1
        return result


class _SessionFactory:
    def __init__(self, claim_rowcount: int = 1, write_rowcount: int = 1):
        self.session = _RecordingSession(claim_rowcount, write_rowcount)

    def __call__(self):
        return self.session

    def begin(self):
        return self.session


def _patch_env(
    monkeypatch,
    turns,
    title_updated_at,
    llm,
    *,
    title_is_manual=False,
    claim_rowcount=1,
    summary_attempts=0,
):
    factory = _SessionFactory(claim_rowcount=claim_rowcount)

    async def fake_load_state(session, session_id):
        return title_updated_at, title_is_manual, turns, summary_attempts

    monkeypatch.setattr("server.session.summary._load_state", fake_load_state)
    monkeypatch.setattr("server.session.summary.get_utility_llm", lambda: llm)
    return factory


def _title_writes(factory):
    return [p for _s, p in factory.session.writes if "title" in (p or {})]


@pytest.mark.asyncio
async def test_generate_and_store_writes_title(monkeypatch):
    turns = _make_turns(1, datetime(2026, 1, 1))
    llm = _FakeLLM("注意力机制入门")
    factory = _patch_env(monkeypatch, turns, None, llm)

    assert await generate_and_store_summary("session-1", factory) is True

    title_writes = _title_writes(factory)
    assert len(title_writes) == 1
    params = title_writes[0]
    assert params["id"] == "session-1"
    assert params["title"] == "注意力机制入门"
    assert params["basis"] == turns[0].completed_at


@pytest.mark.asyncio
async def test_generate_skips_already_summarized(monkeypatch):
    turns = _make_turns(3, datetime(2026, 1, 1))
    llm = _FakeLLM()
    factory = _patch_env(monkeypatch, turns, turns[0].completed_at, llm)

    assert await generate_and_store_summary("session-1", factory) is False
    assert llm.invocations == []
    assert factory.session.writes == []


@pytest.mark.asyncio
async def test_generate_skips_manual_title(monkeypatch):
    turns = _make_turns(1, datetime(2026, 1, 1))
    llm = _FakeLLM()
    factory = _patch_env(monkeypatch, turns, None, llm, title_is_manual=True)

    assert await generate_and_store_summary("session-1", factory) is False
    assert llm.invocations == []
    assert factory.session.writes == []


@pytest.mark.asyncio
async def test_generate_degrades_on_llm_failure(monkeypatch):
    turns = _make_turns(1, datetime(2026, 1, 1))
    llm = _FakeLLM()

    async def fail_ainvoke(messages, **kwargs):
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(llm, "ainvoke", fail_ainvoke)
    factory = _patch_env(monkeypatch, turns, None, llm)

    assert await generate_and_store_summary("session-1", factory) is False
    # Title must never be written on LLM failure.
    assert _title_writes(factory) == []
    # Lease must NOT be cleared -- it should be pushed into the future with a
    # backoff so the 5s sweep does not hammer an unavailable model.
    backoff_writes = [
        p for _s, p in factory.session.writes if p and "until" in p
    ]
    assert len(backoff_writes) == 1
    assert backoff_writes[0]["id"] == "session-1"
    # First failure, attempts_so_far=0 -> backoff = BASE_BACKOFF_S (60s) in the
    # future, i.e. strictly greater than the current time at call site.
    assert backoff_writes[0]["until"] > datetime(2026, 1, 1)


@pytest.mark.asyncio
async def test_generate_backs_off_longer_with_each_failure(monkeypatch):
    turns = _make_turns(1, datetime(2026, 1, 1))
    llm = _FakeLLM()

    async def fail_ainvoke(messages, **kwargs):
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(llm, "ainvoke", fail_ainvoke)

    captured = {}

    async def fake_backoff(session, session_id, *, now, attempts_so_far):
        captured["attempts_so_far"] = attempts_so_far

    monkeypatch.setattr("server.session.summary._backoff_summary", fake_backoff)
    # Pretend the row has already failed 3 times.
    factory = _patch_env(monkeypatch, turns, None, llm, summary_attempts=3)

    assert await generate_and_store_summary("session-1", factory) is False
    assert captured["attempts_so_far"] == 3


@pytest.mark.asyncio
async def test_generate_gives_up_after_max_attempts(monkeypatch):
    turns = _make_turns(1, datetime(2026, 1, 1))
    llm = _FakeLLM()
    factory = _patch_env(
        monkeypatch, turns, None, llm, summary_attempts=MAX_SUMMARY_ATTEMPTS
    )

    assert await generate_and_store_summary("session-1", factory) is False
    # LLM must not even be called once the budget is exhausted.
    assert llm.invocations == []
    assert factory.session.writes == []


@pytest.mark.asyncio
async def test_generate_skips_when_lease_already_held(monkeypatch):
    turns = _make_turns(1, datetime(2026, 1, 1))
    llm = _FakeLLM()
    factory = _patch_env(monkeypatch, turns, None, llm, claim_rowcount=0)

    assert await generate_and_store_summary("session-1", factory) is False
    assert llm.invocations == []
    assert _title_writes(factory) == []


@pytest.mark.asyncio
async def test_write_is_scoped_to_target_session(monkeypatch):
    turns = _make_turns(1, datetime(2026, 1, 1))
    factory = _patch_env(monkeypatch, turns, None, _FakeLLM("主题"))

    await generate_and_store_summary("session-A", factory)

    statement, params = next(
        (s, p) for s, p in factory.session.writes if "title" in (p or {})
    )
    assert params["id"] == "session-A"
    # Single-row conditional UPDATE keyed only by the conversation id; manual
    # titles and already-summarized rows are never overwritten.
    assert "WHERE id=:id" in str(statement)
    assert "title_is_manual=0" in str(statement)
    assert "title_updated_at IS NULL" in str(statement)


# --- read-path permission boundary ----------------------------------------


def test_session_list_requires_read_permission_and_exposes_title(monkeypatch):
    from core.rbac import Permission
    from server.agent.session_service import DatabaseSessionService

    requires: list = []
    monkeypatch.setattr(
        "server.agent.session_service.authorization_service.require",
        lambda principal, permission, **kwargs: requires.append((principal, permission)),
    )

    row = SimpleNamespace(
        id="s1",
        created_at=datetime(2026, 1, 1),
        last_message_at=datetime(2026, 1, 2),
        updated_at=datetime(2026, 1, 1),
        owner_user_id="u1",
        workspace_id="ws1",
        channel="web",
        title="注意力机制",
        title_is_manual=False,
    )

    class _ScalarResult:
        def all(self):
            return [row]

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def scalar(self, statement):
            return 0

        async def scalars(self, statement):
            return _ScalarResult()

    class _Factory:
        def __call__(self):
            return _Session()

    principal = SimpleNamespace(user_id="u1", workspace_ids={"ws1"})
    service = DatabaseSessionService(_Factory())

    items = asyncio.run(service.list(principal))

    assert requires and requires[0][1] == Permission.AGENT_SESSION_READ
    assert items[0]["title"] == "注意力机制"


# --- 15-char cap + first-question fallback --------------------------------


def test_clean_title_truncates_to_fifteen_chars():
    long = "这是一个非常长的对话标题超过了十五个字的限制"
    assert _clean_title(long) == "这是一个非常长的对话标题超过了"


def test_first_question_title_strips_preamble_and_truncates():
    from server.agent.session_service import _first_question_title

    assert _first_question_title("什么是注意力机制？") == "什么是注意力机制？"
    assert _first_question_title("") == ""
    raw = (
        '<!-- nlp-learning-context:{"topic_name":"Transformer"} -->\n'
        "[学习设置：主题=Transformer；难度=入门]\n"
        "什么是注意力机制？"
    )
    assert _first_question_title(raw) == "什么是注意力机制？"
    with_attachment = (
        "什么是注意力机制？\n\n---附件---\n[图片] photo.png\n路径: photo.png\n---附件结束---"
    )
    assert _first_question_title(with_attachment) == "什么是注意力机制？"
    assert _first_question_title("这是一个非常长的用户提问需要被截断到十五个字符以内作为标题") == "这是一个非常长的用户提问需要被…"


def test_session_list_falls_back_to_first_question(monkeypatch):
    from server.agent.session_service import DatabaseSessionService

    monkeypatch.setattr(
        "server.agent.session_service.authorization_service.require",
        lambda principal, permission, **kwargs: None,
    )

    row = SimpleNamespace(
        id="s1",
        created_at=datetime(2026, 1, 1),
        last_message_at=datetime(2026, 1, 2),
        updated_at=datetime(2026, 1, 1),
        owner_user_id="u1",
        workspace_id="ws1",
        channel="web",
        title="",
        title_is_manual=False,
    )

    class _ScalarResult:
        def all(self):
            return [row]

    class _TurnResult:
        def all(self):
            return [("s1", "什么是注意力机制？")]

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def scalar(self, statement):
            return 0

        async def scalars(self, statement):
            return _ScalarResult()

        async def execute(self, statement):
            return _TurnResult()

    class _Factory:
        def __call__(self):
            return _Session()

    principal = SimpleNamespace(user_id="u1", workspace_ids={"ws1"})
    service = DatabaseSessionService(_Factory())

    items = asyncio.run(service.list(principal))

    assert items[0]["title"] == "什么是注意力机制？"


def _rename_service(monkeypatch):
    from core.session_context import SessionContext
    from server.agent.session_service import DatabaseSessionService

    monkeypatch.setattr(
        "server.agent.session_service.authorization_service.require",
        lambda principal, permission, **kwargs: None,
    )

    async def fake_resolve(self, principal, session_id):
        return SessionContext(
            session_id=session_id,
            user_id=principal.user_id,
            workspace_id="ws1",
            channel="web",
        )

    monkeypatch.setattr(DatabaseSessionService, "resolve", fake_resolve)

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def execute(self, statement):
            return MagicMock()

    class _Factory:
        def begin(self):
            return _Session()

    return DatabaseSessionService(_Factory())


def test_session_rename_updates_title(monkeypatch):
    service = _rename_service(monkeypatch)
    principal = SimpleNamespace(user_id="u1", workspace_ids={"ws1"})

    result = asyncio.run(service.rename(principal, "s1", "  注意力机制入门  "))

    assert result == {"session_id": "s1", "title": "注意力机制入门"}


def test_session_rename_rejects_empty_title(monkeypatch):
    service = _rename_service(monkeypatch)
    principal = SimpleNamespace(user_id="u1", workspace_ids={"ws1"})

    with pytest.raises(ValueError):
        asyncio.run(service.rename(principal, "s1", "   "))
