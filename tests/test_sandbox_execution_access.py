from __future__ import annotations

from datetime import datetime, timezone, timedelta
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.identity import AuthenticatedPrincipal
from server.auth.dependencies import (
    get_current_principal,
    get_database_session_claims,
    get_db_session,
)
from server.web.database_auth import DatabaseSessionClaims


class FakeExecutionSession:
    def __init__(self, execution: object) -> None:
        self.execution = execution

    async def get(self, _model: object, _execution_id: str) -> object:
        return self.execution


class FakeEventStore:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def replay(self, execution_id: str, *, user_id: str, after_event_id: str | None = None):
        del after_event_id
        self.calls.append((execution_id, user_id))
        return [{"event_id": "1", "type": "execution.started", "payload": {}}]


def test_execution_event_replay_requires_matching_workspace(monkeypatch) -> None:
    from server.sandbox import controller

    event_store = FakeEventStore()
    monkeypatch.setattr(controller, "sandbox_events", event_store)
    execution = SimpleNamespace(
        id="execution-a",
        owner_user_id="user-a",
        workspace_id="workspace-a",
    )
    app = FastAPI()
    app.include_router(controller.router)
    app.dependency_overrides[get_db_session] = lambda: FakeExecutionSession(execution)
    app.dependency_overrides[get_current_principal] = lambda: AuthenticatedPrincipal(
        user_id="user-a", workspace_ids=frozenset({"workspace-b"}), roles=frozenset({"student"})
    )
    app.dependency_overrides[get_database_session_claims] = lambda: DatabaseSessionClaims(
        user_id="user-a",
        workspace_id="workspace-b",
        session_id="session-a",
        token_hash_value="token",
        csrf_hash_value="csrf",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        authorization_version=1,
    )

    response = TestClient(app).get("/api/v1/sandbox/executions/execution-a/events")

    assert response.status_code == 404
    assert event_store.calls == []
