"""Real HTTP Sandbox lifecycle, ticket, ownership, and artifact checks."""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
import pytest

from server.sandbox.ticket import SandboxTicketSigner

from ..support.auth import session_id_for_client
from ..support.environment import SeededUser
from ..support.http import json_response


pytestmark = pytest.mark.api_runtime_core


def _http_error(response: httpx.Response, expected_status: int) -> dict:
    assert response.status_code == expected_status, response.text
    payload = response.json()
    assert isinstance(payload, dict)
    return payload


def _open_sandbox(client: httpx.Client) -> tuple[dict, str]:
    response = client.post("/api/v1/sandbox/lease")
    payload = json_response(response, 200)
    assert payload["runtime_available"] is True
    runtime = payload["runtime"]
    assert runtime["id"]
    assert runtime["ticket"]
    return payload, str(runtime["ticket"])


def _execute(client: httpx.Client, ticket: str) -> dict:
    response = client.post(
        "/api/v1/sandbox/execute",
        json={"source": "print('sandbox-http')", "ticket": ticket},
    )
    return json_response(response, 200)


def test_sandbox_lifecycle_uses_real_lease_ticket_and_replay_is_denied(
    authenticated_client: httpx.Client,
) -> None:
    described = json_response(authenticated_client.get("/api/v1/sandbox"), 200)
    assert described["phase"] == 0
    assert described["runtime_available"] is False

    confirmation = authenticated_client.post(
        "/api/v1/sandbox/confirmations",
        json={"tool_name": "sandbox_reset", "source": ""},
    )
    confirmation_payload = json_response(confirmation, 200)
    assert confirmation_payload["tool_name"] == "sandbox_reset"
    assert confirmation_payload["expires_in"] == 120
    assert confirmation_payload["confirmation_token"]

    lease, ticket = _open_sandbox(authenticated_client)
    assert lease["lease"]["state"] == "active"
    assert lease["runtime"]["id"]
    assert lease["runtime_profile"]["runtime"] == "runsc"
    execution = _execute(authenticated_client, ticket)
    assert execution["status"] == "completed"
    assert execution["stdout"] == "sandbox-http\n"
    assert execution["execution_id"]

    events = json_response(
        authenticated_client.get(
            f"/api/v1/sandbox/executions/{execution['execution_id']}/events"
        ),
        200,
    )
    event_types = [item["type"] for item in events["events"]]
    assert "execution.started" in event_types
    assert "execution.output" in event_types
    assert "execution.completed" in event_types

    usage = json_response(
        authenticated_client.post(
            "/api/v1/sandbox/usage",
            json={"ticket": execution["ticket"]},
        ),
        200,
    )
    assert usage["cpu_percent"] == 0.0
    assert usage["memory_percent"] == 0.0

    restarted = json_response(
        authenticated_client.post(
            "/api/v1/sandbox/restart",
            json={"ticket": execution["ticket"]},
        ),
        200,
    )
    assert restarted["status"] == "restarted"

    # The initial claim nonce is consumed by the first execution. Replaying
    # the same signed ticket must fail before the deterministic runtime runs.
    replay = authenticated_client.post(
        "/api/v1/sandbox/execute",
        json={"source": "print('replay')", "ticket": ticket},
    )
    _http_error(replay, 403)


def test_sandbox_rejects_missing_expired_and_cross_session_capabilities(
    authenticated_client_for,
    api_http_environment,
    mysql_probe,
    student_user: SeededUser,
    teacher_user: SeededUser,
) -> None:
    no_lease_client = authenticated_client_for(student_user)
    no_lease = no_lease_client.post(
        "/api/v1/sandbox/execute",
        json={"source": "print('no lease')"},
    )
    _http_error(no_lease, 409)

    owner = authenticated_client_for(student_user)
    _lease, ticket = _open_sandbox(owner)
    claims = SandboxTicketSigner(api_http_environment.env["NLP_AGENT_WEB_SECRET"]).verify(
        ticket,
        user_id=student_user.user_id,
        auth_session_id=session_id_for_client(owner, mysql_probe),
    )
    expired = SandboxTicketSigner(
        api_http_environment.env["NLP_AGENT_WEB_SECRET"], ttl_seconds=-1
    ).issue(claims)
    expired_response = owner.post(
        "/api/v1/sandbox/usage", json={"ticket": expired}
    )
    _http_error(expired_response, 403)
    execution = _execute(owner, ticket)

    other_user = authenticated_client_for(teacher_user)
    cross_owner_events = other_user.get(
        f"/api/v1/sandbox/executions/{execution['execution_id']}/events"
    )
    _http_error(cross_owner_events, 404)


def test_sandbox_execution_and_artifact_are_owner_scoped(
    authenticated_client_for,
    api_http_environment,
    mysql_probe,
    student_user: SeededUser,
    teacher_user: SeededUser,
) -> None:
    owner = authenticated_client_for(student_user)
    _, ticket = _open_sandbox(owner)
    execution = _execute(owner, ticket)
    execution_id = str(execution["execution_id"])
    artifact_id = str(uuid4())
    locator = f"{execution_id}/{artifact_id}/hello.txt"
    store_root = Path(api_http_environment.env["NLP_AGENT_SANDBOX_ARTIFACT_STORE_ROOT"])
    destination = store_root / locator
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(b"artifact-http")
    mysql_probe.execute(
        "INSERT INTO nlp_sandbox_artifacts "
        "(id,execution_id,owner_user_id,kind,mime_type,locator,sha256,size_bytes,"
        "expires_at) VALUES(:id,:execution_id,:owner_user_id,'file','text/plain',"
        ":locator,:sha256,:size,UTC_TIMESTAMP(6) + INTERVAL 60 SECOND)",
        id=artifact_id,
        execution_id=execution_id,
        owner_user_id=student_user.user_id,
        locator=locator,
        sha256=hashlib.sha256(b"artifact-http").hexdigest(),
        size=len(b"artifact-http"),
    )
    try:
        access = json_response(
            owner.get(f"/api/v1/sandbox/artifacts/{artifact_id}/access"), 200
        )
        parsed = urlsplit(access["url"])
        assert parsed.netloc == "artifact.test"
        content = owner.get(
            parsed.path + "?" + parsed.query,
            headers={
                "Host": "artifact.test",
                "X-Forwarded-Proto": "https",
                "X-Nova-Artifact-Delivery": "1",
            },
        )
        assert content.status_code == 200, content.text
        assert content.content == b"artifact-http"
        assert content.headers["content-type"].startswith("text/plain")

        other = authenticated_client_for(teacher_user)
        _http_error(
            other.get(f"/api/v1/sandbox/artifacts/{artifact_id}/access"),
            404,
        )
        _http_error(
            owner.get(
                parsed.path.replace(artifact_id, str(uuid4())) + "?" + parsed.query,
                headers={
                    "Host": "artifact.test",
                    "X-Forwarded-Proto": "https",
                    "X-Nova-Artifact-Delivery": "1",
                },
            ),
            404,
        )
    finally:
        shutil.rmtree(store_root / execution_id, ignore_errors=True)


def test_sandbox_artifact_traversal_and_missing_resources_are_hidden(
    authenticated_client_for,
    api_http_environment,
    mysql_probe,
    student_user: SeededUser,
) -> None:
    owner = authenticated_client_for(student_user)
    _, ticket = _open_sandbox(owner)
    execution = _execute(owner, ticket)
    execution_id = str(execution["execution_id"])
    artifact_id = str(uuid4())
    mysql_probe.execute(
        "INSERT INTO nlp_sandbox_artifacts "
        "(id,execution_id,owner_user_id,kind,mime_type,locator,sha256,size_bytes,expires_at) "
        "VALUES(:id,:execution_id,:owner_user_id,'file','text/plain','../escape.txt',NULL,0,"
        "UTC_TIMESTAMP(6) + INTERVAL 60 SECOND)",
        id=artifact_id,
        execution_id=execution_id,
        owner_user_id=student_user.user_id,
    )
    access = json_response(
        owner.get(f"/api/v1/sandbox/artifacts/{artifact_id}/access"), 200
    )
    parsed = urlsplit(access["url"])
    content = owner.get(
        parsed.path + "?" + parsed.query,
        headers={
            "Host": "artifact.test",
            "X-Forwarded-Proto": "https",
            "X-Nova-Artifact-Delivery": "1",
        },
    )
    _http_error(content, 404)
    _http_error(
        owner.get(
            f"/api/v1/sandbox/artifacts/{uuid4()}/access"
        ),
        404,
    )
