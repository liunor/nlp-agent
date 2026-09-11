"""Session title summarization tests (DB-free; LLM mocked).

The summarizer's correctness rests on the properties exercised here: the prompt
renders the whole dialogue (both speakers -- the anchor turn plus as many of the
newest turns as fit the budget), a title is regenerated whenever the dialogue
advances past the basis it was built from but never for an unchanged dialogue,
manual titles are never overwritten, it never raises on model failure, the lease
claim keeps a second worker from paying for a duplicate LLM call, and every
write is a single-row conditional UPDATE scoped to the exact conversation id
(the id already arrived from an authorized turn context, so there is no
cross-tenant write path).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy.dialects import mysql

from core.model_runtime.usage import current_usage_attribution
from server.session.summary import (
    MAX_INPUT_CHARS,
    MAX_MESSAGE_CHARS,
    SUMMARY_TURN_WINDOW,
    _backoff_summary,
    _clean_input,
    _clean_result,
    _clean_title,
    _decide,
    _due_sessions_statement,
    _render_turns,
    _select_turns,
    _SummaryState,
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


def test_clean_result_strips_an_echoed_learning_preamble():
    # Assistant text is rendered into the prompt as well now, so a reply that
    # echoes the learning context must not spend prompt budget on that metadata.
    raw = (
        '<!-- nlp-learning-context:{"topic_name":"Transformer"} -->\n'
        "[学习设置：主题=Transformer；难度=入门；教学方式=讲解]\n"
        "注意力机制通过权重聚焦相关信息。"
    )
    assert _clean_result(raw) == "注意力机制通过权重聚焦相关信息。"


def test_clean_title_strips_quotes_and_markdown():
    assert _clean_title('"## 注意力机制入门"') == "注意力机制入门"
    assert _clean_title("**Transformer 编码器**") == "Transformer 编码器"


def test_select_turns_keeps_every_readable_turn():
    turns = _make_turns(6, datetime(2026, 1, 1))
    assert [t.id for t in _select_turns(turns)] == [f"turn-{i}" for i in range(6)]


def test_select_turns_drops_turns_without_readable_content():
    turns = [
        _turn("t1", "", None, datetime(2026, 1, 1)),
        _turn("t2", "有内容", "有回答", datetime(2026, 1, 1, 0, 0, 10)),
    ]
    assert [t.id for t in _select_turns(turns)] == ["t2"]


def test_render_turns_renders_both_sides_of_every_turn():
    turns = [
        _turn("t1", "什么是 BERT？", "BERT 是预训练模型", datetime(2026, 1, 1)),
        _turn("t2", "后续追问", "后续回答", datetime(2026, 1, 1, 0, 0, 10)),
    ]
    assert _render_turns(turns) == "\n".join(
        [
            "[user]: 什么是 BERT？",
            "[assistant]: BERT 是预训练模型",
            "[user]: 后续追问",
            "[assistant]: 后续回答",
        ]
    )


def test_render_turns_falls_back_to_assistant_when_user_empty():
    turns = [_turn("t1", "", "我先来问一句", datetime(2026, 1, 1))]
    assert _render_turns(turns) == "[assistant]: 我先来问一句"


def test_render_turns_caps_a_single_long_message():
    turns = [_turn("t1", "长" * 5000, None, datetime(2026, 1, 1))]
    text = _render_turns(turns)
    assert text.startswith("[user]: ")
    # One message is capped well below the whole-prompt budget so other turns
    # still fit alongside it.
    assert len(text) == len("[user]: ") + MAX_MESSAGE_CHARS
    assert len(text) <= MAX_INPUT_CHARS


def test_render_turns_keeps_the_anchor_and_the_newest_turns():
    # 40 substantive turns cannot all fit MAX_INPUT_CHARS, so the budget keeps
    # the opening turn (the topic anchor) plus the newest ones, in order.
    turns = [
        _turn(
            f"t{i}",
            f"问题{i}" * 20,
            f"答案{i}" * 20,
            datetime(2026, 1, 1) + timedelta(seconds=i),
        )
        for i in range(40)
    ]
    text = _render_turns(turns)
    assert len(text) <= MAX_INPUT_CHARS
    lines = text.split("\n")
    assert lines[0].startswith("[user]: 问题0")
    assert "答案39" in lines[-1]
    # The middle of a long conversation is dropped rather than squeezing every
    # turn into a truncated rendering.
    assert len(lines) < len(turns) * 2


def test_a_capped_anchor_and_newest_turn_always_fit_the_budget():
    # Invariant the whole per-turn cadence rests on: even with the opening
    # exchange and the newest turn both at the per-message cap, they fit inside
    # MAX_INPUT_CHARS together.  If that ever stops holding, the budget would drop
    # the newest turn and regeneration would burn a utility call to rewrite the
    # title from a prompt identical to the previous one.
    turns = [
        _turn(
            f"t{i}",
            "问" * (MAX_MESSAGE_CHARS * 3),
            "答" * (MAX_MESSAGE_CHARS * 3),
            datetime(2026, 1, 1) + timedelta(seconds=i),
        )
        for i in range(4)
    ]
    text = _render_turns(turns)
    lines = text.split("\n")
    assert len(text) <= MAX_INPUT_CHARS
    assert lines[0] == f"[user]: {'问' * MAX_MESSAGE_CHARS}"
    assert lines[-1] == f"[assistant]: {'答' * MAX_MESSAGE_CHARS}"
    # Anchor plus at least the newest turn, i.e. more than one exchange.
    assert len(lines) >= 4


def test_turn_window_covers_everything_the_budget_could_render():
    # ``_load_state`` reads at most SUMMARY_TURN_WINDOW turns.  The window must be
    # at least the number of turns the prompt budget could ever hold, so that the
    # budget -- not the window -- is always what limits the recent turns a title
    # tracks.  (For a conversation longer than the window the anchor becomes the
    # oldest turn inside it rather than the conversation opener; that is a
    # deliberate trade-off, and the right one for a title meant to follow the
    # latest state of a long-running session.)
    # Smallest realistic turn: a one-character question and answer.
    smallest_turn = len("[user]: x") + 1 + len("[assistant]: x") + 1
    assert MAX_INPUT_CHARS // smallest_turn <= SUMMARY_TURN_WINDOW


@pytest.mark.asyncio
async def test_build_conversation_text_renders_the_dialogue(monkeypatch):
    turns = _make_turns(2, datetime(2026, 1, 1))
    factory = _SessionFactory()

    async def fake_load_state(session, session_id):
        return _SummaryState(None, False, turns, 0, "workspace-1", "user-1")

    monkeypatch.setattr("server.session.summary._load_state", fake_load_state)

    text = await build_conversation_text("session-1", factory)
    assert text == "\n".join(
        [
            "[user]: 问题 0",
            "[assistant]: 答案 0",
            "[user]: 问题 1",
            "[assistant]: 答案 1",
        ]
    )


# --- decide / recompute threshold ------------------------------------------


def test_decide_generates_on_first_turn():
    turns = _make_turns(1, datetime(2026, 1, 1))
    assert _decide(turns, None, False) == turns[-1].completed_at


def test_decide_regenerates_when_the_dialogue_advanced():
    turns = _make_turns(3, datetime(2026, 1, 1))
    # The standing title was built from the first turn; two newer turns exist.
    assert _decide(turns, turns[0].completed_at, False) == turns[-1].completed_at


def test_decide_skips_when_nothing_is_newer_than_the_title():
    turns = _make_turns(3, datetime(2026, 1, 1))
    assert _decide(turns, turns[-1].completed_at, False) is None


def test_decide_skips_when_the_title_is_newer_than_every_turn():
    turns = _make_turns(2, datetime(2026, 1, 1))
    future = turns[-1].completed_at + timedelta(seconds=1)
    assert _decide(turns, future, False) is None


def test_decide_skips_when_no_turn_completed():
    turns = [_turn("t1", "问题", "答案", None)]
    assert _decide(turns, None, False) is None


def test_decide_skips_manual_title():
    turns = _make_turns(1, datetime(2026, 1, 1))
    assert _decide(turns, None, True) is None


def test_decide_skips_manual_title_even_when_the_dialogue_advanced():
    turns = _make_turns(3, datetime(2026, 1, 1))
    assert _decide(turns, turns[0].completed_at, True) is None


# --- orchestration with mocked LLM + DB ------------------------------------


class _FakeLLM:
    def __init__(self, title: str = "注意力机制入门"):
        self.title = title
        self.invocations: list = []
        self.attributions: list = []

    async def ainvoke(self, messages, **kwargs):
        self.invocations.append((messages, kwargs))
        self.attributions.append(current_usage_attribution())
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
    workspace_id="workspace-1",
    owner_user_id="user-1",
):
    factory = _SessionFactory(claim_rowcount=claim_rowcount)

    async def fake_load_state(session, session_id):
        return _SummaryState(
            title_updated_at,
            title_is_manual,
            turns,
            summary_attempts,
            workspace_id,
            owner_user_id,
        )

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
    attribution = llm.attributions[0]
    assert attribution is not None
    assert attribution.request_id == "session-summary:session-1:1"
    assert attribution.user_id == "user-1"
    assert attribution.workspace_id == "workspace-1"
    assert attribution.conversation_id == "session-1"
    assert attribution.turn_id is None
    assert attribution.purpose == "other"
    assert current_usage_attribution() is None


@pytest.mark.asyncio
async def test_generate_skips_when_usage_identity_is_missing(monkeypatch):
    turns = _make_turns(1, datetime(2026, 1, 1))
    llm = _FakeLLM()
    factory = _patch_env(
        monkeypatch, turns, None, llm, owner_user_id=None
    )

    assert await generate_and_store_summary("session-1", factory) is False
    assert llm.invocations == []
    assert len(factory.session.writes) == 2
    backoff_writes = [
        params
        for _statement, params in factory.session.writes
        if params and "until" in params
    ]
    assert len(backoff_writes) == 1
    assert backoff_writes[0]["id"] == "session-1"


@pytest.mark.asyncio
async def test_generate_skips_when_the_dialogue_has_not_advanced(monkeypatch):
    turns = _make_turns(3, datetime(2026, 1, 1))
    llm = _FakeLLM()
    # The standing title already covers the newest completed turn.
    factory = _patch_env(monkeypatch, turns, turns[-1].completed_at, llm)

    assert await generate_and_store_summary("session-1", factory) is False
    assert llm.invocations == []
    assert factory.session.writes == []


@pytest.mark.asyncio
async def test_generate_regenerates_for_a_newer_turn(monkeypatch):
    turns = _make_turns(3, datetime(2026, 1, 1))
    llm = _FakeLLM("最新主题")
    # The title was built from the first turn; two newer turns have since landed.
    factory = _patch_env(monkeypatch, turns, turns[0].completed_at, llm)

    assert await generate_and_store_summary("session-1", factory) is True

    params = _title_writes(factory)[0]
    assert params["title"] == "最新主题"
    # The stored basis advances to the newest completed turn, so the next
    # regeneration only becomes due after yet another turn completes.
    assert params["basis"] == turns[-1].completed_at
    # The prompt saw the whole dialogue, not only the opening question.
    prompt = llm.invocations[0][0][0].content
    assert "[user]: 问题 0" in prompt
    assert "[assistant]: 答案 2" in prompt


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
async def test_generate_still_runs_after_a_long_outage(monkeypatch):
    # ``summary_attempts`` drives the exponential-backoff lease; it is not a
    # lifetime cap.  A session that kept failing through a model outage must start
    # titling again by itself once the model recovers, so a high counter still
    # generates instead of being skipped forever.
    turns = _make_turns(2, datetime(2026, 1, 1))
    llm = _FakeLLM("恢复后的标题")
    factory = _patch_env(monkeypatch, turns, None, llm, summary_attempts=99)

    assert await generate_and_store_summary("session-1", factory) is True
    assert len(llm.invocations) == 1
    assert _title_writes(factory)[0]["title"] == "恢复后的标题"
    # The successful write clears the counter, so the next turn's failure starts
    # backing off from BASE_BACKOFF_S again.
    statement = next(s for s, p in factory.session.writes if p and "title" in p)
    assert "summary_attempts=0" in str(statement)


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
    # Single-row conditional UPDATE keyed only by the conversation id.  Manual
    # titles are never overwritten, and neither is a title whose basis is
    # already at or newer than this one -- so a late worker cannot roll back a
    # fresher title.
    assert "WHERE id=:id" in str(statement)
    assert "title_is_manual=0" in str(statement)
    assert "(title_updated_at IS NULL OR title_updated_at < :basis)" in str(statement)
    assert params["basis"] == turns[0].completed_at


@pytest.mark.asyncio
async def test_claim_is_basis_fenced(monkeypatch):
    turns = _make_turns(2, datetime(2026, 1, 1))
    factory = _patch_env(monkeypatch, turns, turns[0].completed_at, _FakeLLM("主题"))

    await generate_and_store_summary("session-1", factory)

    statement, params = next(
        (s, p) for s, p in factory.session.writes if p and "lease_until" in p
    )
    # The lease claim re-validates the basis, so two workers racing on the same
    # newly completed turn cannot both pay for an LLM call.
    assert "(title_updated_at IS NULL OR title_updated_at < :basis)" in str(statement)
    assert params["basis"] == turns[-1].completed_at


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


# --- sweep query (compiled against the MySQL dialect, no DB needed) ----------


def _compiled_sql(statement) -> str:
    return str(statement.compile(dialect=mysql.dialect()))


def test_due_sessions_selects_on_a_newer_completed_turn():
    sql = _compiled_sql(_due_sessions_statement(now=datetime(2026, 1, 1), batch=25))
    # The due condition is a correlated EXISTS over turns newer than the basis the
    # standing title was built from, replacing the old write-once filter that only
    # ever matched sessions with no title at all.
    assert "EXISTS" in sql
    assert "nlp_turns.conversation_id = nlp_conversations.id" in sql
    assert "nlp_turns.completed_at > nlp_conversations.title_updated_at" in sql
    assert "nlp_conversations.title_updated_at IS NULL" in sql


def test_due_sessions_never_filters_on_the_attempt_counter():
    # ``summary_attempts`` drives the backoff lease only.  Filtering on it here is
    # what used to disable titling permanently once a session had failed enough
    # times, which no longer makes sense now that every turn re-arms generation.
    sql = _compiled_sql(_due_sessions_statement(now=datetime(2026, 1, 1), batch=25))
    assert "summary_attempts" not in sql


def test_due_sessions_prioritises_untitled_then_recent_activity():
    sql = _compiled_sql(_due_sessions_statement(now=datetime(2026, 1, 1), batch=25))
    order_by = sql.split("ORDER BY", 1)[1]
    # Never-titled sessions first -- those are the ones a learner still sees as an
    # empty or first-question fallback -- then most recent activity.  Recency
    # alone would starve a quiet but genuinely due session whenever the due rate
    # exceeds one batch per cadence.
    assert "CASE WHEN" in order_by
    assert order_by.index("title_updated_at IS NULL") < order_by.index(
        "last_message_at DESC"
    )
    assert "LIMIT" in sql


def test_due_sessions_still_respects_manual_titles_and_leases():
    sql = _compiled_sql(_due_sessions_statement(now=datetime(2026, 1, 1), batch=25))
    assert "nlp_conversations.title_is_manual IS false" in sql or (
        "nlp_conversations.title_is_manual = " in sql
    )
    assert "summary_lease_expires_at" in sql
    assert "nlp_conversations.status = " in sql
