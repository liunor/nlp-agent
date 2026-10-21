"""Real CI MySQL checks for incremental, atomic streamed-event accounting."""

from __future__ import annotations

import os
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event, insert, select, text

from gateway.contracts import GatewayEventType
from gateway.execution_fence import LostTurnClaimError, TurnWriteFence, bind_turn_write_fence
from gateway.mysql_repository import MySQLGatewayRepository
from server.infrastructure.mysql.models import ConversationModel, TurnModel, UserModel, WorkspaceModel
from server.storage.quota import StorageQuotaExceeded, SyncStorageQuota


@pytest.fixture
def event_accounting_scope():
    if os.environ.get("GITHUB_ACTIONS") != "true":
        pytest.skip("Requires the isolated migrated CI MySQL service")
    engine = create_engine(os.environ["NLP_AGENT_DATABASE_URL"].replace("mysql+aiomysql://", "mysql+pymysql://"))
    try:
        with engine.connect() as connection, connection.begin():
            owner, session_id, turn_id = (str(uuid4()) for _ in range(3))
            workspace_id = connection.scalar(select(WorkspaceModel.id).limit(1))
            assert workspace_id, "CI migrations must provide the default workspace"
            connection.execute(insert(UserModel).values(
                id=owner, username="event-accounting-"+uuid4().hex[:12],
                password_hash="isolated-test", display_name="Event accounting probe"))
            connection.execute(insert(ConversationModel).values(
                id=session_id, owner_user_id=owner, workspace_id=workspace_id, title="probe"))
            connection.execute(insert(TurnModel).values(
                id=turn_id, conversation_id=session_id, user_id=owner, workspace_id=workspace_id,
                input_text="probe", status="running", claim_generation=1,
                claimed_by="event-accounting-probe",
                lease_expires_at=text("DATE_ADD(UTC_TIMESTAMP(6), INTERVAL 5 MINUTE)")))
            repository = object.__new__(MySQLGatewayRepository)
            storage = SyncStorageQuota(connection, owner_user_id=owner)
            storage.reconcile()
            try:
                yield engine, connection, repository, storage, owner, session_id, turn_id
            finally:
                # Roll back only this fixture's uncommitted records and counters.
                connection.rollback()
    finally:
        engine.dispose()


def _ledger(connection, owner):
    return connection.execute(text(
        "SELECT core_used_bytes,core_reserved_bytes FROM nlp_storage_accounts WHERE owner_user_id=:owner"
    ), {"owner": owner}).one()


def _count_events(connection, turn_id):
    return connection.scalar(text("SELECT COUNT(*) FROM nlp_turn_events WHERE turn_id=:turn"), {"turn": turn_id})


def test_event_ledger_matches_full_measurement_without_history_rescans(event_accounting_scope):
    engine, connection, repository, storage, owner, session_id, turn_id = event_accounting_scope
    before, _ = _ledger(connection, owner)
    statements = []

    def capture(_connection, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        with bind_turn_write_fence(TurnWriteFence(turn_id, 1, "event-accounting-probe")):
            events = [repository.append_event(
                turn_id=turn_id, session_id=session_id, event_type=GatewayEventType.MESSAGE_DELTA,
                payload=payload, _connection=connection,
            ) for payload in [
                {"delta": "hello"}, {"delta": "中文与 emoji: 🌍"},
                {"delta": "quote\" slash\\ newline\n", "nested": {"values": [1, True, None]}},
                {"delta": "x"*4096},
            ]]
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    used, reserved = _ledger(connection, owner)
    measured_events = connection.scalar(text(
        "SELECT SUM(OCTET_LENGTH(CAST(payload_json AS CHAR))) FROM nlp_turn_events WHERE turn_id=:turn"
    ), {"turn": turn_id})
    assert used == before + measured_events
    assert reserved == 0
    assert storage._reconcile()[0] == used
    assert [item.sequence for item in events] == [1, 2, 3, 4]
    assert not any("SUM(" in sql.upper() and "NLP_TURN_EVENTS" in sql.upper() for sql in statements)


def test_event_and_reservation_roll_back_when_measured_final_usage_exceeds_quota(event_accounting_scope, monkeypatch):
    _engine, connection, repository, _storage, owner, session_id, turn_id = event_accounting_scope
    before, reserved_before = _ledger(connection, owner)
    connection.execute(text(
        "UPDATE nlp_storage_accounts SET core_quota_override_bytes=:quota WHERE owner_user_id=:owner"
    ), {"quota": before+5, "owner": owner})
    original_reserve = SyncStorageQuota.reserve

    def underestimate(self, bucket, _amount_bytes, **kwargs):
        return original_reserve(self, bucket, 1, **kwargs)

    monkeypatch.setattr(SyncStorageQuota, "reserve", underestimate)
    with pytest.raises(StorageQuotaExceeded), connection.begin_nested():
        repository.append_event(
            turn_id=turn_id, session_id=session_id, event_type=GatewayEventType.MESSAGE_DELTA,
            payload={"delta": "larger than the deliberately underestimated reservation"}, _connection=connection)
    assert _count_events(connection, turn_id) == 0
    assert _ledger(connection, owner) == (before, reserved_before)
    assert connection.scalar(text(
        "SELECT COUNT(*) FROM nlp_storage_reservations WHERE owner_user_id=:owner"
    ), {"owner": owner}) == 0


def test_old_worker_cannot_write_or_charge_a_reclaimed_turn(event_accounting_scope):
    _engine, connection, repository, _storage, owner, session_id, turn_id = event_accounting_scope
    before = _ledger(connection, owner)
    fence = TurnWriteFence(turn_id, 0, "old-worker")
    with bind_turn_write_fence(fence), pytest.raises(LostTurnClaimError):
        repository.append_event(
            turn_id=turn_id, session_id=session_id, event_type=GatewayEventType.MESSAGE_DELTA,
            payload={"delta": "late reply"}, _connection=connection)
    assert fence.revoked
    assert _count_events(connection, turn_id) == 0
    assert _ledger(connection, owner) == before
