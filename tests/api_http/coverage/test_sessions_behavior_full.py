"""Durable AgentSession behavior at the real HTTP boundary."""

from __future__ import annotations

import pytest

from ..support.http import json_response, problem_response
from ..support.resources import create_session


pytestmark = pytest.mark.api_full


def test_session_read_rename_listing_and_empty_subresources(
    authenticated_client,
    student_user,
) -> None:
    created = create_session(
        authenticated_client,
        workspace_id=student_user.workspace_id,
    )
    session_id = created["session_id"]
    assert created["workspace_id"] == student_user.workspace_id

    resolved = json_response(
        authenticated_client.get(f"/api/v1/sessions/{session_id}"),
        200,
    )
    assert resolved["session_id"] == session_id
    assert resolved["user_id"] == student_user.user_id

    renamed = json_response(
        authenticated_client.patch(
            f"/api/v1/sessions/{session_id}",
            json={"title": "Full behavior session"},
        ),
        200,
    )
    assert renamed == {"session_id": session_id, "title": "Full behavior session"}

    listed = json_response(
        authenticated_client.get("/api/v1/sessions?limit=10&offset=0"),
        200,
    )
    assert any(
        item["session_id"] == session_id
        and item["title"] == "Full behavior session"
        for item in listed["items"]
    )

    messages = json_response(
        authenticated_client.get(f"/api/v1/sessions/{session_id}/messages"),
        200,
    )
    turns = json_response(
        authenticated_client.get(f"/api/v1/sessions/{session_id}/turns?limit=10"),
        200,
    )
    assert messages == {"items": []}
    assert turns == {"items": []}

    invalid = authenticated_client.patch(
        f"/api/v1/sessions/{session_id}",
        json={"title": ""},
    )
    assert invalid.status_code == 422

    deleted = authenticated_client.delete(f"/api/v1/sessions/{session_id}")
    assert deleted.status_code == 204
    problem_response(
        authenticated_client.get(f"/api/v1/sessions/{session_id}"),
        404,
    )
