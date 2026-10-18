"""Sandbox auth lifecycle notifications must never enter the Turn work queue."""

from __future__ import annotations

import json
import time

import pytest

from ..support.auth import session_id_for_client
from ..support.chat import submit_turn, wait_for_event, wait_for_turn
from ..support.http import json_response
from ..support.resources import create_session, create_user


pytestmark = pytest.mark.api_async_core


@pytest.mark.parametrize("action,topic,state", [
    ("logout", "sandbox.lease.released", "released"),
    ("disable", "sandbox.lease.revoked", "revoked"),
])
def test_auth_lifecycle_notification_is_retained_without_worker_dead_letter(
    action, topic, state, authenticated_client_for, developer_user,
    authenticated_client, student_user, mysql_probe, redis_probe,
):
    admin = authenticated_client_for(developer_user)
    target = create_user(admin, role="student", prefix="outboxrouting")
    target_client = authenticated_client_for(target)
    auth_session_id = session_id_for_client(target_client, mysql_probe)
    lease = json_response(target_client.post("/api/v1/sandbox/lease"), 200)["lease"]
    assert lease["state"] == "active"
    before_tasks = redis_probe.stream_length()
    dead_stream = f"{redis_probe.task_stream}:dead"
    before_dead = redis_probe.client.xlen(dead_stream)

    response = (
        target_client.delete("/api/v1/auth/session") if action == "logout"
        else admin.post(f"/api/v1/users/{target.user_id}/disable")
    )
    assert response.status_code == 204, response.text
    assert mysql_probe.scalar(
        "SELECT state FROM nlp_sandbox_leases WHERE id=:id", id=lease["id"],
    ) == state
    assert target_client.get("/api/v1/auth/session").status_code == 401

    # Exercise the production Worker relay, not a direct call to _publish.
    outbox_id = None
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        outbox_id = mysql_probe.scalar(
            "SELECT id FROM nlp_outbox_messages WHERE topic=:topic AND status='published' "
            "AND JSON_UNQUOTE(JSON_EXTRACT(payload_json, '$.user_id'))=:user_id",
            topic=topic, user_id=target.user_id,
        )
        if outbox_id:
            break
        time.sleep(0.1)
    assert outbox_id, "lifecycle notification was not published by the Worker relay"
    redis_id = mysql_probe.scalar(
        "SELECT redis_message_id FROM nlp_outbox_messages WHERE id=:id", id=outbox_id,
    )
    notification = redis_probe.client.xrange(
        f"{redis_probe.task_stream}:sandbox-events", min=redis_id, max=redis_id,
    )
    assert len(notification) == 1
    fields = notification[0][1]
    assert fields["outbox_id"] == outbox_id and fields["topic"] == topic
    payload = json.loads(fields["payload"])
    assert payload["user_id"] == target.user_id
    if action == "logout":
        assert payload["auth_session_id"] == auth_session_id
    assert redis_probe.stream_length() == before_tasks

    # A real deterministic chat still runs after the lease notification. The
    # harness substitutes the model; no paid model or live services are used.
    conversation = create_session(authenticated_client, workspace_id=student_user.workspace_id)
    turn = submit_turn(
        authenticated_client, session_id=conversation["session_id"],
        content="Outbox routing isolation",
    )
    assert wait_for_turn(authenticated_client, turn["turn_id"])["status"] == "completed"
    wait_for_event(authenticated_client, turn["turn_id"], "turn.completed")
    assert redis_probe.client.xlen(dead_stream) == before_dead
