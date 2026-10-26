"""Exercise repository transactions without replacing event persistence."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import sqlalchemy as sa
from sqlalchemy import event

from gateway.contracts import GatewayEvent, GatewayEventType, TurnStatus
from gateway.mysql_repository import MySQLGatewayRepository


@pytest.fixture
def repository(monkeypatch):
    # SQLite adapts only MySQL's clock/locking syntax here; real row-lock
    # concurrency remains covered by the opt-in MySQL HTTP regressions.
    engine = sa.create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def clock(connection, _record):
        connection.create_function("UTC_TIMESTAMP", 1, lambda _: "2026-10-17 00:00:00")

    @event.listens_for(engine, "before_cursor_execute", retval=True)
    def locking_syntax(_conn, _cursor, statement, parameters, _context, _many):
        return statement.replace(" FOR UPDATE", "").replace(
            "ON DUPLICATE KEY UPDATE turn_id=VALUES(turn_id)",
            "ON CONFLICT(turn_id) DO NOTHING",
        ), parameters

    with engine.begin() as c:
        c.execute(sa.text("CREATE TABLE nlp_turns(id TEXT PRIMARY KEY, conversation_id TEXT, workspace_id TEXT, user_id TEXT, status TEXT, input_text TEXT, claim_generation INTEGER, created_at TEXT, completed_at TEXT, result_text TEXT, error_message TEXT, error_kind TEXT, quota_reservation_id TEXT, learning_state_json TEXT, started_at TEXT)"))
        c.execute(sa.text("CREATE TABLE nlp_turn_events(id TEXT PRIMARY KEY, turn_id TEXT, sequence INTEGER, claim_generation INTEGER, event_type TEXT, payload_json TEXT, created_at TEXT DEFAULT '2026-10-17 00:00:00', UNIQUE(turn_id,sequence))"))
        c.execute(sa.text("CREATE TABLE nlp_turn_cancellations(turn_id TEXT PRIMARY KEY, requested_by TEXT, reason TEXT)"))
        c.execute(sa.text("INSERT INTO nlp_turns(id,conversation_id,workspace_id,user_id,status,input_text,claim_generation,created_at) VALUES('turn','session','workspace','user','accepted','question',0,'2026-10-17 00:00:00')"))
    # Storage accounting is a separate DB subsystem, not the transaction under test.
    quota = MagicMock()
    monkeypatch.setattr("gateway.mysql_repository.SyncStorageQuota", lambda *a, **kw: quota)
    repo = object.__new__(MySQLGatewayRepository)
    repo._engine = engine
    try:
        yield repo
    finally:
        engine.dispose()


@pytest.mark.parametrize("status", ["accepted", "running"])
def test_cancel_commits_state_command_and_one_event_in_same_transaction(repository, status):
    with repository._engine.begin() as c:
        c.execute(sa.text("UPDATE nlp_turns SET status=:status"), {"status": status})
    cancelled = repository.request_turn_cancellation(turn_id="turn", requested_by="user")
    assert cancelled.status == TurnStatus.CANCELLED
    replay = repository.ensure_event(turn_id="turn", session_id="session", event_type=GatewayEventType.TURN_CANCELLED)
    assert replay.session_id == "session"
    with repository._engine.connect() as c:
        assert c.execute(sa.text("SELECT COUNT(*) FROM nlp_turn_cancellations")).scalar_one() == 1
        assert c.execute(sa.text("SELECT COUNT(*) FROM nlp_turn_events")).scalar_one() == 1


def test_terminal_lookup_is_not_limited_to_first_2000_events(repository):
    with repository._engine.begin() as c:
        c.execute(sa.text("INSERT INTO nlp_turn_events(id,turn_id,sequence,claim_generation,event_type,payload_json) VALUES(:id,'turn',:seq,0,:type,:payload)"), [
            {"id": str(i), "seq": i, "type": "turn.completed" if i == 2101 else "message.delta", "payload": json.dumps({"original": True})}
            for i in range(1, 2102)
        ])
    for _ in range(2):
        result = repository.ensure_event(turn_id="turn", session_id="session", event_type=GatewayEventType.TURN_COMPLETED)
        assert result.event_id == "2101"
        assert result.payload == {"original": True}
    with repository._engine.connect() as c:
        assert c.execute(sa.text("SELECT COUNT(*) FROM nlp_turn_events WHERE event_type='turn.completed'")).scalar_one() == 1


def test_cancellation_rolls_back_when_event_persistence_fails(repository, monkeypatch):
    def fail(**kwargs):
        raise RuntimeError("event storage unavailable")

    monkeypatch.setattr(repository, "append_event", fail)
    with pytest.raises(RuntimeError, match="event storage unavailable"):
        repository.request_turn_cancellation(turn_id="turn", requested_by="user")
    assert repository.get_turn("turn").status == TurnStatus.ACCEPTED
    with repository._engine.connect() as c:
        assert c.execute(sa.text("SELECT COUNT(*) FROM nlp_turn_cancellations")).scalar_one() == 0


def test_completed_status_commits_both_terminal_events(repository):
    repository.update_turn("turn", TurnStatus.COMPLETED, final_text="answer")
    assert [e.type for e in repository.events_after("turn")] == [
        GatewayEventType.MESSAGE_COMPLETED, GatewayEventType.TURN_COMPLETED,
    ]


@pytest.mark.asyncio
async def test_mysql_in_process_execution_acquires_real_execution_fence(monkeypatch):
    from configs.settings import settings
    from core.identity import AuthenticatedPrincipal
    from core.learning import TeachingMaterials
    from core.rbac import Permission
    from core.session_context import SessionContext
    from gateway.core import BackendGateway
    from gateway.dispatch import ExecutionAuthorizationContext, TurnTask
    from gateway.execution_fence import current_turn_write_fence

    principal = AuthenticatedPrincipal(user_id="user", workspace_ids=frozenset({"default"}), permissions=frozenset({Permission.AGENT_TURN_SUBMIT}))
    uow = AsyncMock()
    uow.__aenter__.return_value = uow
    factory = MagicMock()
    factory.begin.return_value = uow
    runtime = SimpleNamespace(uow=factory, session_factory=MagicMock())
    monkeypatch.setattr("gateway.core.MySQLRuntime.from_runtime", lambda _: runtime)
    reliability = AsyncMock()
    reliability.claim_turn.return_value = 1
    reliability.quota_reservation_for_turn.return_value = "reservation"
    monkeypatch.setattr("gateway.core.TurnReliabilityService", lambda: reliability)
    monkeypatch.setattr("server.worker.fencing.rbac_service.principal_for_user_id", AsyncMock(return_value=principal))
    monkeypatch.setattr("server.worker.fencing.rbac_service.audit", AsyncMock())
    monkeypatch.setattr("gateway.core.schedule_summary", lambda *args: None)
    monkeypatch.setattr(settings, "NLP_AGENT_GATEWAY_TRANSPORT", "inprocess")
    repo = object.__new__(MySQLGatewayRepository)
    repo._engine = sa.create_engine("sqlite:///:memory:")
    statuses = []

    def update(_turn, status, **kwargs):
        statuses.append(status)
        return SimpleNamespace(status=status)

    repo.update_turn = update
    repo.append_event = repo.ensure_event = lambda **kw: GatewayEvent(event_id="event", sequence=1, **{k: v for k, v in kw.items() if k in {"turn_id", "session_id", "payload"}}, type=kw["event_type"])

    class Engine:
        async def run_turn(self, *_args):
            fence = current_turn_write_fence()
            assert fence is not None
            fence.require_valid("turn")
            return "answer"

    gateway = BackendGateway(repository=repo, engine=Engine())
    task = TurnTask(context=SessionContext(session_id="session", user_id="user"), turn_id="turn", content="hello", learning_context=None, learning_progress=None, exercise_state=None, teaching_materials=TeachingMaterials(), guided_session_id=None, exercise_session_id=None, quota_reservation_id="reservation", authorization=ExecutionAuthorizationContext("user", "default", 0))
    try:
        await gateway.dispatcher._execute(task)
        assert statuses[-1] == TurnStatus.COMPLETED
    finally:
        repo._engine.dispose()
