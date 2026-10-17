"""MySQL-authoritative admission must cover every writer, including recovery."""

from concurrent.futures import ThreadPoolExecutor
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from ..support.resources import create_session

pytestmark = pytest.mark.api_async_core


def _insert(connection, turn, conversation, user, workspace):
    connection.execute(text("INSERT INTO nlp_turns(id,conversation_id,user_id,workspace_id,status,input_text) VALUES(:id,:conversation,:user,:workspace,'accepted','capacity test')"), {"id": turn, "conversation": conversation, "user": user, "workspace": workspace})


def test_concurrent_admission_enforces_user_active_limit(authenticated_client, student_user, mysql_probe):
    conversations = [create_session(authenticated_client, workspace_id=student_user.workspace_id)["session_id"] for _ in range(10)]
    turns = [str(uuid.uuid4()) for _ in conversations]

    def submit(index):
        try:
            with mysql_probe.engine.begin() as connection:
                _insert(connection, turns[index], conversations[index], student_user.user_id, student_user.workspace_id)
            return "accepted"
        except DBAPIError as error:
            assert error.orig.args[0] == 1644
            return str(error.orig.args[1])

    try:
        with ThreadPoolExecutor(max_workers=10) as pool:
            results = list(pool.map(submit, range(10)))
        assert results.count("accepted") == 3
        assert results.count("user_turn_limit_reached") == 7
    finally:
        for turn in turns:
            mysql_probe.execute("DELETE FROM nlp_turns WHERE id=:id", id=turn)


def test_capacity_transitions_rollback_recovery_and_reconcile(authenticated_client, student_user, mysql_probe, api_http_environment):
    from gateway.mysql_repository import MySQLGatewayRepository

    conversation = create_session(authenticated_client, workspace_id=student_user.workspace_id)["session_id"]
    turn = str(uuid.uuid4())
    repository = MySQLGatewayRepository(api_http_environment.env["NLP_AGENT_DATABASE_URL"])
    repository.reconcile_capacity()

    def counts():
        with mysql_probe.engine.connect() as connection:
            return tuple(connection.execute(text("SELECT queued_count,running_count FROM nlp_runtime_capacity WHERE scope_key='global:turns'")).one())

    before = counts()
    try:
        with pytest.raises(RuntimeError, match="abort transaction"):
            with mysql_probe.engine.begin() as connection:
                _insert(connection, turn, conversation, student_user.user_id, student_user.workspace_id)
                raise RuntimeError("abort transaction")
        assert counts() == before
        with mysql_probe.engine.begin() as connection:
            _insert(connection, turn, conversation, student_user.user_id, student_user.workspace_id)
        assert counts() == (before[0] + 1, before[1])
        mysql_probe.execute("UPDATE nlp_turns SET status='running' WHERE id=:id", id=turn)
        assert counts() == (before[0], before[1] + 1)
        mysql_probe.execute("UPDATE nlp_turns SET status='accepted' WHERE id=:id", id=turn)
        assert counts() == (before[0] + 1, before[1])
        mysql_probe.execute("UPDATE nlp_turns SET status='cancelled' WHERE id=:id", id=turn)
        assert counts() == before
        mysql_probe.execute("UPDATE nlp_runtime_capacity SET queued_count=queued_count+9 WHERE scope_key='global:turns'")
        result = repository.reconcile_capacity()
        assert result["drift_detected"] is True
        assert counts() == before
    finally:
        mysql_probe.execute("DELETE FROM nlp_turns WHERE id=:id", id=turn)
        repository.close()


def test_queue_limit_and_http_error_translation(authenticated_client, student_user, mysql_probe, api_http_environment):
    from gateway.mysql_repository import MySQLGatewayRepository
    from gateway.contracts import TurnStatus

    repository = MySQLGatewayRepository(api_http_environment.env["NLP_AGENT_DATABASE_URL"])
    repository.reconcile_capacity()
    queued = mysql_probe.scalar("SELECT queued_count FROM nlp_runtime_capacity WHERE scope_key='global:turns'")
    turn = str(uuid.uuid4())
    conversation = create_session(authenticated_client, workspace_id=student_user.workspace_id)["session_id"]
    other = create_session(authenticated_client, workspace_id=student_user.workspace_id)["session_id"]
    try:
        mysql_probe.execute("UPDATE nlp_runtime_capacity SET queue_capacity=:capacity WHERE scope_key='global:turns'", capacity=queued + 1)
        with mysql_probe.engine.begin() as connection:
            _insert(connection, turn, conversation, student_user.user_id, student_user.workspace_id)
        response = authenticated_client.post("/api/v1/chat/turns", json={"session_id": other, "content": "queue must be full"})
        assert response.status_code == 429, response.text
        assert response.json()["code"] == "queue_capacity_exceeded"
        assert response.headers["Retry-After"] == "5"
        assert authenticated_client.get("/api/v1/auth/session").status_code == 200
        repository.update_turn(turn, TurnStatus.CANCELLED)
    finally:
        mysql_probe.execute("DELETE FROM nlp_turns WHERE id=:id", id=turn)
        mysql_probe.execute("UPDATE nlp_runtime_capacity SET queue_capacity=200 WHERE scope_key='global:turns'")
        repository.close()


def test_dispatch_retry_reuses_the_turn_reservation(authenticated_client, student_user, mysql_probe, api_http_environment):
    from gateway.mysql_repository import MySQLGatewayRepository
    from gateway.contracts import TurnStatus

    repository = MySQLGatewayRepository(api_http_environment.env["NLP_AGENT_DATABASE_URL"])
    conversation = create_session(authenticated_client, workspace_id=student_user.workspace_id)["session_id"]
    turn_id = str(uuid.uuid4())
    reservation_id = str(uuid.uuid4())
    try:
        repository.create_turn(
            turn_id=turn_id,
            session_id=conversation,
            workspace_id=student_user.workspace_id,
            user_id=student_user.user_id,
            input_text="retry after dispatch failure",
            idempotency_key="t07-reservation-retry-001",
            quota_reservation_id=reservation_id,
            reserved_tokens=120,
        )
        repository.update_turn(
            turn_id,
            TurnStatus.FAILED,
            error_kind="dispatch_failed",
            error_message="Redis was temporarily unavailable",
        )
        assert mysql_probe.scalar(
            "SELECT status FROM nlp_model_quota_reservations WHERE id=:id",
            id=reservation_id,
        ) == "settled"

        repository.update_turn(
            turn_id,
            TurnStatus.ACCEPTED,
            quota_reservation_id=reservation_id,
            reserved_tokens=240,
            dispatch_payload="{}",
        )

        assert mysql_probe.scalar(
            "SELECT id FROM nlp_model_quota_reservations WHERE turn_id=:id",
            id=turn_id,
        ) == reservation_id
        assert mysql_probe.scalar(
            "SELECT reserved_tokens FROM nlp_model_quota_reservations WHERE id=:id",
            id=reservation_id,
        ) == 240
        assert mysql_probe.scalar(
            "SELECT status FROM nlp_model_quota_reservations WHERE id=:id",
            id=reservation_id,
        ) == "reserved"
    finally:
        mysql_probe.execute("DELETE FROM nlp_turn_events WHERE turn_id=:id", id=turn_id)
        mysql_probe.execute("DELETE FROM nlp_outbox_messages WHERE JSON_UNQUOTE(JSON_EXTRACT(payload_json,'$.turn_id'))=:id", id=turn_id)
        mysql_probe.execute("DELETE FROM nlp_turns WHERE id=:id", id=turn_id)
        mysql_probe.execute("DELETE FROM nlp_model_quota_reservations WHERE id=:id", id=reservation_id)
        repository.close()


def test_quota_reconciliation_releases_reservations_without_live_turn(mysql_probe, student_user, api_http_environment):
    from gateway.mysql_repository import MySQLGatewayRepository

    repository = MySQLGatewayRepository(api_http_environment.env["NLP_AGENT_DATABASE_URL"])
    reservation_id = str(uuid.uuid4())
    missing_turn_id = str(uuid.uuid4())
    mysql_probe.execute(
        "INSERT INTO nlp_model_quota_accounts(user_id) VALUES(:user) ON DUPLICATE KEY UPDATE user_id=VALUES(user_id)",
        user=student_user.user_id,
    )
    previous_reserved = mysql_probe.scalar(
        "SELECT reserved_tokens FROM nlp_model_quota_accounts WHERE user_id=:user",
        user=student_user.user_id,
    )
    try:
        mysql_probe.execute(
            "UPDATE nlp_model_quota_accounts SET reserved_tokens=reserved_tokens+31 WHERE user_id=:user",
            user=student_user.user_id,
        )
        mysql_probe.execute(
            "INSERT INTO nlp_model_quota_reservations(id,user_id,turn_id,reserved_tokens,status) VALUES(:id,:user,:turn,31,'reserved')",
            id=reservation_id,
            user=student_user.user_id,
            turn=missing_turn_id,
        )

        result = repository.reconcile_model_quota()

        assert result["released_orphan_reservations"] == 1
        assert mysql_probe.scalar(
            "SELECT status FROM nlp_model_quota_reservations WHERE id=:id",
            id=reservation_id,
        ) == "released"
        assert mysql_probe.scalar(
            "SELECT reserved_tokens FROM nlp_model_quota_accounts WHERE user_id=:user",
            user=student_user.user_id,
        ) == previous_reserved
    finally:
        mysql_probe.execute("DELETE FROM nlp_model_quota_reservations WHERE id=:id", id=reservation_id)
        mysql_probe.execute(
            "UPDATE nlp_model_quota_accounts SET reserved_tokens=:reserved WHERE user_id=:user",
            reserved=previous_reserved,
            user=student_user.user_id,
        )
        repository.close()
