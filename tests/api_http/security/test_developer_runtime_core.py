"""Developer control-plane authorization and safe configuration contracts."""

from __future__ import annotations

import uuid

import httpx
import pytest

from ..support.environment import SeededUser
from ..support.http import json_response, problem_response


pytestmark = pytest.mark.api_core


@pytest.mark.parametrize("role", ["guest", "student", "teacher"])
def test_non_developer_cannot_enter_developer_control_plane(
    authenticated_client_for,
    guest_user: SeededUser,
    student_user: SeededUser,
    teacher_user: SeededUser,
    role: str,
) -> None:
    client = authenticated_client_for(
        {"guest": guest_user, "student": student_user, "teacher": teacher_user}[role]
    )
    response = client.get("/api/v1/developer/snapshot")
    problem = problem_response(response, 403)
    assert problem["code"] == "forbidden"


def test_developer_snapshot_redacts_secrets_and_exposes_runtime_shape(
    authenticated_client_for,
    developer_user: SeededUser,
) -> None:
    client = authenticated_client_for(developer_user)
    payload = json_response(client.get("/api/v1/developer/snapshot"), 200)
    for key in ("runtime", "features", "models", "tools", "skills", "agents", "workspace", "web"):
        assert key in payload
    rendered = repr(payload).lower()
    assert "api-http-test-secret" not in rendered
    assert "api_key_configured': true" not in rendered


def test_developer_sandbox_surface_is_not_accidentally_exposed(
    authenticated_client_for,
    developer_user: SeededUser,
) -> None:
    client = authenticated_client_for(developer_user)
    document = client.get("/api/openapi.json")
    assert document.status_code == 200, document.text
    paths = document.json().get("paths", {})
    assert not any(path.startswith("/api/v1/developer/sandbox") for path in paths)
    unmounted_routes = (
        ("get", "/api/v1/developer/sandbox/overview"),
        ("get", "/api/v1/developer/sandbox/runtimes"),
        ("get", "/api/v1/developer/sandbox/preload-compatibility"),
        ("post", "/api/v1/developer/sandbox/capacity/prewarm"),
        ("post", "/api/v1/developer/sandbox/runtimes/not-a-runtime/drain"),
        ("get", "/api/v1/developer/sandbox/runtimes/not-a-runtime"),
        ("get", "/api/v1/developer/sandbox/executions"),
        ("get", "/api/v1/developer/sandbox/executions/not-an-execution/events"),
    )
    for method, path in unmounted_routes:
        response = getattr(client, method)(path)
        assert response.status_code in {404, 405}, response.text


def test_developer_feedback_management_is_real_http_and_role_scoped(
    authenticated_client_for,
    student_user: SeededUser,
    developer_user: SeededUser,
) -> None:
    student = authenticated_client_for(student_user)
    developer = authenticated_client_for(developer_user)
    created = json_response(
        student.post(
            "/api/v1/feedback",
            json={"body": "Phase 6 developer review", "category": "ux"},
        ),
        201,
    )
    thread_id = str(created["thread_id"])
    message_id = str(created["message"]["id"])

    listing = json_response(developer.get("/api/v1/developer/feedback"), 200)
    assert any(item["thread_id"] == thread_id for item in listing["items"])
    detail = json_response(developer.get(f"/api/v1/developer/feedback/{thread_id}"), 200)
    assert detail["thread_id"] == thread_id
    assert detail["messages"][0]["id"] == message_id

    read = developer.post(
        f"/api/v1/developer/feedback/{thread_id}/read",
        json={"read_through_message_id": message_id},
    )
    json_response(read, 200)
    updated = json_response(
        developer.patch(
            f"/api/v1/developer/feedback/{thread_id}",
            json={"status": "in_progress", "priority": "high"},
        ),
        200,
    )
    assert updated["status"] == "in_progress"
    reply = json_response(
        developer.post(
            f"/api/v1/developer/feedback/{thread_id}/reply",
            json={"body": "Reviewed by the deterministic developer client."},
        ),
        200,
    )
    assert reply["thread_id"] == thread_id
    assert developer.delete(f"/api/v1/developer/feedback/{thread_id}").status_code == 204
    problem_response(
        developer.get(f"/api/v1/developer/feedback/{thread_id}"), 404
    )


def test_developer_tools_skills_profiles_release_notes_and_mcp_boundaries(
    authenticated_client_for,
    developer_user: SeededUser,
) -> None:
    client = authenticated_client_for(developer_user)
    policies = json_response(
        client.put("/api/v1/developer/tools/policies", json={"policies": {}}),
        200,
    )
    assert "catalog_revision" in policies
    custom = json_response(
        client.put(
            "/api/v1/developer/tools/custom",
            json={"custom": {"modules": [], "manifests": {}}},
        ),
        200,
    )
    assert custom["restart_required"] is True

    skill_name = f"phase6-{uuid.uuid4().hex[:12]}"
    skill_content = (
        "---\n"
        f"name: {skill_name}\n"
        "description: Deterministic Phase 6 skill\n"
        "allowed_tools: []\n"
        "---\n"
        "Use this skill only in the HTTP test process.\n"
    )
    try:
        skill = json_response(
            client.put(
                f"/api/v1/developer/skills/{skill_name}",
                json={"content": skill_content},
            ),
            200,
        )
        assert skill["name"] == skill_name
        read = json_response(client.get(f"/api/v1/developer/skills/{skill_name}"), 200)
        assert read["content"] == skill_content
    finally:
        client.delete(f"/api/v1/developer/skills/{skill_name}")

    invalid_skill = client.put(
        "/api/v1/developer/skills/phase6-invalid",
        json={"content": "not markdown frontmatter"},
    )
    problem = problem_response(invalid_skill, 422)
    assert problem["code"] == "developer_configuration_invalid"

    profile_name = f"phase6-{uuid.uuid4().hex[:12]}"
    profile = json_response(
        client.put(
            f"/api/v1/developer/worker-profiles/{profile_name}",
            json={"profile": {"description": "Deterministic Phase 6 profile"}},
        ),
        200,
    )
    assert profile["profile"] == profile_name
    assert client.delete(f"/api/v1/developer/worker-profiles/{profile_name}").status_code == 200

    note = json_response(
        client.post(
            "/api/v1/developer/release-notes",
            json={
                "version": "6.0.0",
                "released_at": "2026-09-09T00:00:00Z",
                "notes": ["Phase 6 deterministic runtime coverage"],
                "status": "draft",
            },
        ),
        201,
    )
    assert note["version"] == "6.0.0"
    notes = json_response(client.get("/api/v1/developer/release-notes"), 200)
    assert any(item["id"] == note["id"] for item in notes["items"])
    assert client.delete(f"/api/v1/developer/release-notes/{note['id']}").status_code == 204

    # Name validation and malformed request bodies terminate before any MCP
    # connector is opened. The managed process has no external MCP config.
    invalid_name = client.put(
        "/api/v1/developer/mcp/not.allowed",
        json={"config": {"transport": "stdio", "command": "not-started"}},
    )
    problem = problem_response(invalid_name, 422)
    assert problem["code"] == "developer_configuration_invalid"
    mcp = json_response(
        client.put(
            "/api/v1/developer/mcp/phase6stub",
            json={"config": {"transport": "stdio", "command": "phase6-stub"}},
        ),
        200,
    )
    assert mcp["server"] == "phase6stub"
    tested = json_response(
        client.post(
            "/api/v1/developer/mcp/phase6stub/test",
            json={"config": {"transport": "stdio", "command": "phase6-stub"}},
        ),
        200,
    )
    assert tested["tools"] == ["phase6_stub_tool"]
    assert client.delete("/api/v1/developer/mcp/phase6stub").status_code == 200
    malformed = client.put("/api/v1/developer/mcp/phase6", json={"config": []})
    problem_response(malformed, 422)
    problem_response(
        client.get("/api/v1/developer/skills/phase6-missing"),
        404,
    )
    reserved = client.put(
        "/api/v1/developer/worker-profiles/web_reader",
        json={"profile": {"description": "must be rejected"}},
    )
    problem_response(reserved, 422)
