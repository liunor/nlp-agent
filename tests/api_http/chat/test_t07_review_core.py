"""T07 review regressions on isolated MySQL and the real HTTP read path."""

import hashlib
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import text

from gateway.contracts import GatewayEventType, TurnStatus
from gateway.execution_fence import LostTurnClaimError, TurnWriteFence, bind_turn_write_fence
from ..support.resources import create_session
from ..auth.test_registration_core import _captcha, CAPTCHA_CODE

pytestmark = pytest.mark.api_async_core


@pytest.fixture(autouse=True)
def _runtime_database(api_http_environment, monkeypatch):
    monkeypatch.setenv("NLP_AGENT_DATABASE_URL", api_http_environment.env["NLP_AGENT_DATABASE_URL"])


def test_mysql_rejects_old_worker_writes_and_http_replays_handover(
    authenticated_client, student_user, mysql_probe, api_http_environment,
):
    from gateway.mysql_repository import MySQLGatewayRepository
    session = create_session(authenticated_client, workspace_id=student_user.workspace_id)
    turn_id = str(uuid.uuid4())
    mysql_probe.execute(
        "INSERT INTO nlp_turns(id,conversation_id,workspace_id,user_id,status,input_text,claim_generation,claimed_by,lease_expires_at) VALUES(:id,:conversation,:workspace,:user,'running','T07 fencing',2,'new-worker',UTC_TIMESTAMP(6)+INTERVAL 60 SECOND)",
        id=turn_id, conversation=session["session_id"], workspace=student_user.workspace_id, user=student_user.user_id,
    )
    repository = MySQLGatewayRepository(api_http_environment.env["NLP_AGENT_DATABASE_URL"])
    try:
        with bind_turn_write_fence(TurnWriteFence(turn_id, 1, "old-worker")):
            with pytest.raises(LostTurnClaimError):
                repository.update_turn(turn_id, TurnStatus.CANCELLED)
        with bind_turn_write_fence(TurnWriteFence(turn_id, 1, "old-worker")):
            with pytest.raises(LostTurnClaimError):
                repository.append_event(turn_id=turn_id, session_id=session["session_id"], event_type=GatewayEventType.MESSAGE_DELTA, payload={"delta": "stale"})
        assert mysql_probe.scalar("SELECT status FROM nlp_turns WHERE id=:id", id=turn_id) == "running"
        assert mysql_probe.scalar("SELECT COUNT(*) FROM nlp_turn_events WHERE turn_id=:id", id=turn_id) == 0
        mysql_probe.execute(
            "INSERT INTO nlp_turn_events(id,turn_id,sequence,claim_generation,event_type,payload_json) VALUES(:id,:turn,1,2,'turn.handover',JSON_OBJECT('reason','lease_expired'))",
            id=str(uuid.uuid4()), turn=turn_id,
        )
        with bind_turn_write_fence(TurnWriteFence(turn_id, 2, "new-worker")):
            repository.update_turn(turn_id, TurnStatus.COMPLETED, final_text="new owner result")
            repository.ensure_event(turn_id=turn_id, session_id=session["session_id"], event_type=GatewayEventType.TURN_COMPLETED, payload={"status": "completed"})
        events = authenticated_client.get(f"/api/v1/chat/turns/{turn_id}/events")
        assert events.status_code == 200, events.text
        assert [event["type"] for event in events.json()["items"]] == ["turn.handover", "message.completed", "turn.completed"]
        assert authenticated_client.get("/api/v1/auth/session").status_code == 200
    finally:
        repository.close()


def test_sms_resend_accepts_new_code_and_preserves_history_over_http(http_client, mysql_probe):
    phone = f"+86139{uuid.uuid4().int % 10**8:08d}"
    for index, code in enumerate(("111111", "222222")):
        captcha_id = _captcha(http_client, mysql_probe)
        sent = http_client.post("/api/v1/auth/sms/send", json={"phone_number": phone, "captcha_id": captcha_id, "captcha_code": CAPTCHA_CODE})
        assert sent.status_code == 200, sent.text
        mysql_probe.execute("UPDATE nlp_auth_codes SET code_hash=:hash WHERE subject=:phone AND kind='sms'", hash=hashlib.sha256(code.encode()).hexdigest(), phone=phone)
        if index == 0:
            mysql_probe.execute("UPDATE nlp_auth_codes SET created_at=UTC_TIMESTAMP(6)-INTERVAL 61 SECOND WHERE subject=:phone", phone=phone)
    assert mysql_probe.scalar("SELECT COUNT(*) FROM nlp_auth_codes WHERE subject=:phone", phone=phone) == 2
    assert mysql_probe.scalar("SELECT COUNT(*) FROM nlp_auth_codes WHERE subject=:phone AND kind='sms_history'", phone=phone) == 1
    captcha_id = _captcha(http_client, mysql_probe)
    registered = http_client.post("/api/v1/auth/register", json={
        "phone_number": phone, "sms_code": "222222", "password": "T07-register-password!",
        "display_name": "T07 resend", "captcha_id": captcha_id, "captcha_code": CAPTCHA_CODE,
    })
    assert registered.status_code == 201, registered.text
    # Request-scoped dependency cleanup may commit just after the response is
    # delivered. Observe durable state with a bounded wait, not a timing race.
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        history = mysql_probe.scalar("SELECT COUNT(*) FROM nlp_auth_codes WHERE subject=:phone AND kind='sms_history'", phone=phone)
        if history == 2:
            break
        time.sleep(0.02)
    assert history == 2
    assert mysql_probe.scalar("SELECT COUNT(*) FROM nlp_auth_codes WHERE subject=:phone AND kind='sms'", phone=phone) == 0


def test_long_event_stream_terminal_repair_is_concurrently_idempotent(authenticated_client, student_user, mysql_probe, api_http_environment):
    from gateway.mysql_repository import MySQLGatewayRepository
    session = create_session(authenticated_client, workspace_id=student_user.workspace_id)
    turn_id = str(uuid.uuid4())
    mysql_probe.execute("INSERT INTO nlp_turns(id,conversation_id,workspace_id,user_id,status,input_text) VALUES(:id,:conversation,:workspace,:user,'failed','long stream')", id=turn_id, conversation=session["session_id"], workspace=student_user.workspace_id, user=student_user.user_id)
    with mysql_probe.engine.begin() as connection:
        connection.execute(text("INSERT INTO nlp_turn_events(id,turn_id,sequence,claim_generation,event_type,payload_json) VALUES(:id,:turn,:sequence,0,'message.delta','{}')"), [
            {"id": str(uuid.uuid4()), "turn": turn_id, "sequence": index} for index in range(1, 2101)
        ])
    repository = MySQLGatewayRepository(api_http_environment.env["NLP_AGENT_DATABASE_URL"])
    try:
        def repair(_index):
            return repository.ensure_event(turn_id=turn_id, session_id=session["session_id"], event_type=GatewayEventType.TURN_FAILED, payload={"status": "failed"})
        with ThreadPoolExecutor(max_workers=4) as pool:
            repaired = list(pool.map(repair, range(8)))
        assert len({event.event_id for event in repaired}) == 1
        assert {event.sequence for event in repaired} == {2101}
        assert mysql_probe.scalar("SELECT COUNT(*) FROM nlp_turn_events WHERE turn_id=:id AND event_type='turn.failed'", id=turn_id) == 1
    finally:
        repository.close()


@pytest.mark.parametrize("status", ["accepted", "running"])
def test_cancel_commits_one_terminal_and_releases_capacity(status, authenticated_client, student_user, mysql_probe):
    session = create_session(authenticated_client, workspace_id=student_user.workspace_id)
    turn = str(uuid.uuid4())
    mysql_probe.execute("INSERT INTO nlp_turns(id,conversation_id,workspace_id,user_id,status,input_text) VALUES(:id,:conversation,:workspace,:user,:status,'cancel target')", id=turn, conversation=session["session_id"], workspace=student_user.workspace_id, user=student_user.user_id, status=status)
    for _ in range(2):
        cancelled = authenticated_client.post(f"/api/v1/chat/turns/{turn}/cancel")
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["status"] == "cancelled"
    assert mysql_probe.scalar("SELECT COUNT(*) FROM nlp_turn_events WHERE turn_id=:id AND event_type='turn.cancelled'", id=turn) == 1
    assert mysql_probe.scalar("SELECT COUNT(*) FROM nlp_turn_cancellations WHERE turn_id=:id", id=turn) == 1
    assert mysql_probe.scalar("SELECT COUNT(*) FROM nlp_turns WHERE id=:id AND status IN ('accepted','running')", id=turn) == 0
