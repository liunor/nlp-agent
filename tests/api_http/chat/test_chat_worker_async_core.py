"""Core Chat/Worker tests over the production network adapters."""

from __future__ import annotations

import httpx
import pytest

from ..support.chat import event_types, submit_turn, wait_for_event, wait_for_turn
from ..support.redis import RedisProbe
from ..support.resources import create_session


pytestmark = pytest.mark.api_async_core


def test_chat_turn_is_consumed_and_persisted_with_ordered_events(
    authenticated_client: httpx.Client,
    student_user,
    mysql_probe,
    redis_probe: RedisProbe,
) -> None:
    session = create_session(
        authenticated_client,
        workspace_id=student_user.workspace_id,
    )
    before_stream_length = redis_probe.stream_length()

    accepted = submit_turn(
        authenticated_client,
        session_id=session["session_id"],
        content="Phase 5 real Worker success",
    )
    assert accepted["status"] == "accepted"
    turn_id = str(accepted["turn_id"])

    completed = wait_for_turn(authenticated_client, turn_id)
    assert completed["status"] == "completed"
    assert completed["final_text"] == "deterministic-worker:Phase 5 real Worker success"

    events_response = authenticated_client.get(
        f"/api/v1/chat/turns/{turn_id}/events",
    )
    assert events_response.status_code == 200, events_response.text
    events_payload = events_response.json()
    events = events_payload["items"]
    event_types = [item["type"] for item in events]
    assert event_types[0] == "turn.accepted"
    assert "turn.started" in event_types
    assert "message.completed" in event_types
    assert event_types[-1] == "turn.completed"
    assert [item["sequence"] for item in events] == list(
        range(1, len(events) + 1)
    )

    messages_response = authenticated_client.get(
        f"/api/v1/sessions/{session['session_id']}/messages"
    )
    assert messages_response.status_code == 200, messages_response.text
    assert messages_response.json()["items"]

    turns_response = authenticated_client.get(
        f"/api/v1/sessions/{session['session_id']}/turns"
    )
    assert turns_response.status_code == 200, turns_response.text
    assert any(item["turn_id"] == turn_id for item in turns_response.json()["items"])

    assert redis_probe.stream_length() > before_stream_length
    assert redis_probe.pending_count() == 0
    assert redis_probe.consumer_group()["consumers"] >= 1
    assert mysql_probe.scalar(
        "SELECT status FROM nlp_outbox_messages "
        "WHERE topic='turn.dispatch' "
        "AND JSON_UNQUOTE(JSON_EXTRACT(payload_json, '$.turn_id'))=:turn_id "
        "ORDER BY created_at DESC LIMIT 1",
        turn_id=turn_id,
    ) == "published"


def test_chat_turn_idempotency_is_replayable_but_conflicts_on_changed_input(
    authenticated_client: httpx.Client,
    student_user,
) -> None:
    session = create_session(
        authenticated_client,
        workspace_id=student_user.workspace_id,
    )
    key = "phase5-idempotency-key"
    first = submit_turn(
        authenticated_client,
        session_id=session["session_id"],
        content="idempotent message",
        idempotency_key=key,
    )
    wait_for_turn(authenticated_client, first["turn_id"])

    duplicate_response = authenticated_client.post(
        "/api/v1/chat/turns",
        json={
            "session_id": session["session_id"],
            "content": "idempotent message",
            "idempotency_key": key,
        },
    )
    assert duplicate_response.status_code == 202, duplicate_response.text
    assert duplicate_response.json() == {
        "turn_id": first["turn_id"],
        "session_id": session["session_id"],
        "status": "completed",
        "duplicate": True,
    }

    conflict = authenticated_client.post(
        "/api/v1/chat/turns",
        json={
            "session_id": session["session_id"],
            "content": "changed message",
            "idempotency_key": key,
        },
    )
    assert conflict.status_code == 409, conflict.text
    assert conflict.headers.get("content-type", "").startswith("application/problem+json")
    assert conflict.json()["code"] == "turn_conflict"


@pytest.mark.parametrize(
    ("content", "expected_response_fragment"),
    [
        ("__api_http_timeout__", "模型请求"),
        ("__api_http_failure__", "模型请求"),
    ],
)
def test_worker_provider_failure_is_a_durable_terminal_result(
    authenticated_client: httpx.Client,
    student_user,
    content: str,
    expected_response_fragment: str,
) -> None:
    session = create_session(
        authenticated_client,
        workspace_id=student_user.workspace_id,
    )
    accepted = submit_turn(
        authenticated_client,
        session_id=session["session_id"],
        content=content,
    )
    terminal = wait_for_turn(authenticated_client, accepted["turn_id"])

    # The current coordinator contract converts exhausted provider calls into
    # an explicit safe-stop assistant message, then durably completes the turn.
    assert terminal["status"] == "completed"
    assert expected_response_fragment in terminal["final_text"]
    assert "turn.completed" in event_types(authenticated_client, accepted["turn_id"])


def test_chat_cancel_is_persisted_and_published_without_waiting_fixed_time(
    authenticated_client: httpx.Client,
    student_user,
) -> None:
    session = create_session(
        authenticated_client,
        workspace_id=student_user.workspace_id,
    )
    accepted = submit_turn(
        authenticated_client,
        session_id=session["session_id"],
        content="__api_http_slow__ cancellation target",
    )
    cancelled = authenticated_client.post(
        f"/api/v1/chat/turns/{accepted['turn_id']}/cancel"
    )
    assert cancelled.status_code == 200, cancelled.text
    terminal = wait_for_turn(authenticated_client, accepted["turn_id"])
    assert terminal["status"] == "cancelled"
    assert "turn.cancelled" in wait_for_event(
        authenticated_client,
        accepted["turn_id"],
        "turn.cancelled",
    )


def test_chat_turn_and_session_ids_are_owner_scoped(
    authenticated_client: httpx.Client,
    authenticated_client_for,
    student_user,
    teacher_user,
) -> None:
    foreign_client = authenticated_client_for(teacher_user)
    foreign_session = create_session(
        foreign_client,
        workspace_id=teacher_user.workspace_id,
    )
    foreign_turn = submit_turn(
        foreign_client,
        session_id=foreign_session["session_id"],
        content="foreign owner turn",
    )
    wait_for_turn(foreign_client, foreign_turn["turn_id"])

    for path in (
        f"/api/v1/sessions/{foreign_session['session_id']}",
        f"/api/v1/sessions/{foreign_session['session_id']}/messages",
        f"/api/v1/sessions/{foreign_session['session_id']}/turns",
        f"/api/v1/chat/turns/{foreign_turn['turn_id']}",
        f"/api/v1/chat/turns/{foreign_turn['turn_id']}/events",
    ):
        response = authenticated_client.get(path)
        assert response.status_code in {403, 404}, response.text

    submit_foreign = authenticated_client.post(
        "/api/v1/chat/turns",
        json={
            "session_id": foreign_session["session_id"],
            "content": "attempted foreign submit",
        },
    )
    assert submit_foreign.status_code in {403, 404}, submit_foreign.text
    cancel_foreign = authenticated_client.post(
        f"/api/v1/chat/turns/{foreign_turn['turn_id']}/cancel"
    )
    assert cancel_foreign.status_code in {403, 404}, cancel_foreign.text
