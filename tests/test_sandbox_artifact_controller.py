from __future__ import annotations

from types import SimpleNamespace
from datetime import datetime, timezone, timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.auth.dependencies import get_current_principal, get_database_session_claims, get_db_session
from server.web.database_auth import DatabaseSessionClaims
from core.identity import AuthenticatedPrincipal


class FakeSession:
    def __init__(self, artifact: object) -> None:
        self.artifact = artifact

    async def get(self, _model: object, _artifact_id: str) -> object:
        return self.artifact


def test_owner_can_get_a_ticketed_artifact_url(monkeypatch) -> None:
    from configs.settings import settings
    from server.sandbox.artifact_controller import router

    monkeypatch.setattr(settings, "NLP_AGENT_SANDBOX_ARTIFACT_ORIGIN", "https://artifacts.example.test")
    monkeypatch.setattr(settings, "NLP_AGENT_WEB_SECRET", "test-artifact-secret")
    artifact = SimpleNamespace(id="artifact-a", owner_user_id="user-a", locator="owned/output.html", mime_type="text/html")
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db_session] = lambda: FakeSession(artifact)
    app.dependency_overrides[get_current_principal] = lambda: AuthenticatedPrincipal(user_id="user-a", workspace_ids=frozenset(), roles=frozenset({"developer"}))
    app.dependency_overrides[get_database_session_claims] = lambda: DatabaseSessionClaims(user_id="user-a", workspace_id="ws", session_id="s", token_hash_value="t", csrf_hash_value="c", expires_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc), authorization_version=1)

    response = TestClient(app).get("/api/v1/sandbox/artifacts/artifact-a/access")

    assert response.status_code == 200
    assert response.json()["url"].startswith("https://artifacts.example.test/")


def test_expired_artifact_is_not_authorized(monkeypatch) -> None:
    from configs.settings import settings
    from server.sandbox.artifact_controller import router

    monkeypatch.setattr(settings, "NLP_AGENT_SANDBOX_ARTIFACT_ORIGIN", "https://artifacts.example.test")
    monkeypatch.setattr(settings, "NLP_AGENT_WEB_SECRET", "test-artifact-secret")
    artifact = SimpleNamespace(id="artifact-a", owner_user_id="user-a", locator="owned/output.html", mime_type="text/html", expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db_session] = lambda: FakeSession(artifact)
    app.dependency_overrides[get_current_principal] = lambda: AuthenticatedPrincipal(user_id="user-a", workspace_ids=frozenset(), roles=frozenset({"developer"}))
    app.dependency_overrides[get_database_session_claims] = lambda: DatabaseSessionClaims(user_id="user-a", workspace_id="ws", session_id="s", token_hash_value="t", csrf_hash_value="c", expires_at=datetime.now(timezone.utc), authorization_version=1)

    assert TestClient(app).get("/api/v1/sandbox/artifacts/artifact-a/access").status_code == 404


def test_expired_artifact_content_is_not_served(monkeypatch, tmp_path) -> None:
    from configs.settings import settings
    from server.sandbox.artifact_controller import router
    from server.sandbox.artifacts import ArtifactAccessSigner

    monkeypatch.setattr(settings, "NLP_AGENT_WEB_SECRET", "test-artifact-secret")
    monkeypatch.setattr(settings, "NLP_AGENT_SANDBOX_ARTIFACT_STORE_ROOT", str(tmp_path))
    artifact = SimpleNamespace(
        id="artifact-a", owner_user_id="user-a", locator="output.html", mime_type="text/html",
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db_session] = lambda: FakeSession(artifact)
    ticket = ArtifactAccessSigner("test-artifact-secret").issue(artifact_id="artifact-a", owner_user_id="user-a")

    assert TestClient(app).get(f"/api/v1/sandbox/artifacts/artifact-a/content?ticket={ticket}").status_code == 404


def test_artifact_content_origin_must_match_the_configured_artifact_origin() -> None:
    from server.sandbox.artifacts import artifact_request_origin_matches

    assert artifact_request_origin_matches(
        "https://artifacts.example.test",
        configured_origin="https://artifacts.example.test",
    )
    assert not artifact_request_origin_matches(
        "https://nova.example.test",
        configured_origin="https://artifacts.example.test",
    )
