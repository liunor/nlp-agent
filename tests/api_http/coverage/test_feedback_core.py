"""Student feedback and daily quota behavior over real HTTP."""

from __future__ import annotations

import httpx
import pytest

from ..support.database import MySqlProbe
from ..support.http import json_response, problem_response
from ..support.resources import create_user


pytestmark = pytest.mark.api_core


def _student_client(authenticated_client_for, developer_user):
    developer_client = authenticated_client_for(developer_user)
    user = create_user(developer_client, role="student", prefix="phase4feedback")
    return user, authenticated_client_for(user)


def test_student_feedback_round_trip_and_daily_state(
    authenticated_client_for,
    developer_user,
) -> None:
    _user, client = _student_client(authenticated_client_for, developer_user)

    initial = json_response(client.get("/api/v1/feedback/daily-state"), 200)
    assert initial["used"] == 0
    assert initial["remaining"] == initial["limit"] == 3

    created = json_response(
        client.post(
            "/api/v1/feedback",
            json={"body": "  Phase 4 feedback  ", "category": "bug"},
        ),
        201,
    )
    assert created["message"]["body"] == "Phase 4 feedback"
    assert created["message"]["sender_type"] == "student"
    assert created["remaining"] == 2
    assert created["daily_limit"] == 3

    history = json_response(client.get("/api/v1/feedback"), 200)
    assert history["thread_id"] == created["thread_id"]
    assert history["message_total"] == 1
    assert history["messages"][0]["body"] == "Phase 4 feedback"
    assert history["category"] == "bug"

    state = json_response(client.get("/api/v1/feedback/daily-state"), 200)
    assert state["used"] == 1
    assert state["remaining"] == 2

    marked = json_response(client.post("/api/v1/feedback/read"), 200)
    assert marked == {"ok": True, "updated": False}


def test_feedback_daily_limit_is_enforced(
    authenticated_client_for,
    developer_user,
) -> None:
    _user, client = _student_client(authenticated_client_for, developer_user)
    for index in range(3):
        response = client.post(
            "/api/v1/feedback",
            json={"body": f"daily feedback {index}"},
        )
        assert response.status_code == 201, response.text

    limited = client.post(
        "/api/v1/feedback",
        json={"body": "daily feedback over limit"},
    )
    problem = problem_response(limited, 429)
    assert problem["code"] == "feedback_daily_limit"

    state = json_response(client.get("/api/v1/feedback/daily-state"), 200)
    assert state["used"] == 3
    assert state["remaining"] == 0


def test_feedback_isolated_between_users(
    authenticated_client_for,
    developer_user,
) -> None:
    _user_a, client_a = _student_client(authenticated_client_for, developer_user)
    user_b, client_b = _student_client(authenticated_client_for, developer_user)

    created = json_response(
        client_a.post("/api/v1/feedback", json={"body": "private A"}),
        201,
    )
    history_b = json_response(client_b.get("/api/v1/feedback"), 200)
    assert history_b["thread_id"] is None
    assert history_b["messages"] == []
    assert history_b["user_id"] == user_b.user_id
    assert history_b["thread_id"] != created["thread_id"]
    state_b = json_response(client_b.get("/api/v1/feedback/daily-state"), 200)
    assert state_b["used"] == 0


def test_feedback_daily_state_excludes_messages_from_previous_day(
    authenticated_client_for,
    developer_user,
    mysql_probe: MySqlProbe,
) -> None:
    user, client = _student_client(authenticated_client_for, developer_user)
    created = json_response(
        client.post("/api/v1/feedback", json={"body": "old feedback"}),
        201,
    )
    mysql_probe.execute(
        "UPDATE nlp_feedback_messages SET created_at=UTC_TIMESTAMP(6)-INTERVAL 2 DAY "
        "WHERE thread_id=:thread_id AND sender_user_id=:user_id",
        thread_id=created["thread_id"],
        user_id=user.user_id,
    )

    state = json_response(client.get("/api/v1/feedback/daily-state"), 200)
    assert state["used"] == 0
    assert state["remaining"] == 3
