from __future__ import annotations

import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone

os.environ.setdefault("NLP_AGENT_DATABASE_URL", "mysql+aiomysql://test:test@localhost/test")

from fastapi.testclient import TestClient

from server.monitor.app import create_monitor_app
from server.web.auth import SameOriginSessionAuth


def test_monitor_owns_authorization_audit_routes(monkeypatch) -> None:
    auth = SameOriginSessionAuth(
        secret="monitor-audit-test-secret",
        cookie_name="monitor_audit_test",
        allowed_origins=["http://testserver"],
    )

    class FakeRuntime:
        @staticmethod
        async def start() -> None:
            return None

        @staticmethod
        async def close() -> None:
            return None

        @staticmethod
        def session_factory():
            @asynccontextmanager
            async def session():
                yield object()

            return session()

    monkeypatch.setattr("server.monitor.app.MySQLRuntime.from_runtime", lambda _runtime: FakeRuntime())

    class AuditRow:
        id = "audit-1"
        actor_user_id = "developer-1"
        target_user_id = None
        decision = "deny"
        reason_code = "permission_denied"
        permission_code = "system:user:manage"
        resource_type = "user"
        resource_id = "user-1"
        detail_json = {"source": "test"}
        created_at = datetime(2026, 8, 31, 8, 0, tzinfo=timezone.utc)

    row = AuditRow()

    async def audit_page(_session, **_kwargs):
        return [row], 1

    async def audit_summary(_session, *, since):
        assert since.tzinfo is None
        return {"total": 1, "by_decision": {"deny": 1}, "top_reasons": [{"reason_code": "permission_denied", "count": 1}]}

    monkeypatch.setattr("server.monitor.app.rbac_service.audit_page", audit_page)
    monkeypatch.setattr("server.monitor.app.rbac_service.audit_summary", audit_summary)

    class FakeTelemetryRuntime:
        def health(self):
            return {}

        async def close(self):
            return None

    app = create_monitor_app(runtime=FakeTelemetryRuntime(), auth=auth, allowed_hosts=["testserver"])
    with TestClient(app) as client:
        login = client.post("/api/v1/auth/session", headers={"Origin": "http://testserver"})
        assert login.status_code == 201
        page = client.get("/api/v1/audit/authorization?limit=20&offset=0&decision=deny")
        stats = client.get("/api/v1/audit/authorization/stats?days=7")

    assert page.status_code == 200
    assert page.json()["items"][0]["id"] == "audit-1"
    assert page.json()["has_more"] is False
    assert stats.status_code == 200
    assert stats.json()["period_days"] == 7
