from fastapi.testclient import TestClient
import pytest
from starlette.websockets import WebSocketDisconnect
from datetime import datetime, timedelta, timezone

from configs.settings import settings
from core.identity import AuthenticatedPrincipal
from core.observability.context import TelemetryContext, bind_telemetry_context
from core.observability.models import SpanKind, SpanStatus, TokenUsage
from core.observability.runtime import TelemetryRuntime
from server.monitor.app import create_monitor_app
from server.monitor.reset import _clear_directory
from server.user.service import PasswordHasherSingleton
from server.web.auth import SameOriginSessionAuth
from server.web.database_auth import DatabaseSessionAuth, DatabaseSessionClaims


class ResetSpy:
    calls = 0

    async def reset(self):
        self.calls += 1
        return {"sessions": 1, "gateway": {}, "telemetry": {}, "files": {}}


def test_monitor_root_without_trailing_slash_serves_the_spa_shell(tmp_path, monkeypatch):
    (tmp_path / "index.html").write_text("<html>Monitor</html>", encoding="utf-8")
    monkeypatch.setitem(settings._config["monitor"], "static_dir", str(tmp_path))

    app = create_monitor_app(
        runtime=TelemetryRuntime(tmp_path / "telemetry.sqlite3"),
        auth=SameOriginSessionAuth(
            secret="monitor-static-test-secret",
            cookie_name="monitor_static_test",
            allowed_origins=["http://testserver"],
        ),
        allowed_hosts=["testserver"],
    )

    with TestClient(app) as client:
        response = client.get("/monitor")

    assert response.status_code == 200
    assert response.text == "<html>Monitor</html>"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["content-security-policy"].startswith("default-src 'self'")
    assert response.headers["permissions-policy"] == "camera=(), microphone=(), geolocation=()"


def test_monitor_lifespan_exposes_the_injected_sandbox_manager(tmp_path):
    class Manager:
        async def capacity_snapshot(self):
            return {"adaptive_target": 2, "host_total": 1, "host_total_max": 4}

    manager = Manager()
    app = create_monitor_app(
        runtime=TelemetryRuntime(tmp_path / "telemetry.sqlite3"),
        auth=SameOriginSessionAuth(
            secret="monitor-manager-test-secret",
            cookie_name="monitor_manager_test",
            allowed_origins=["http://testserver"],
        ),
        allowed_hosts=["testserver"],
        sandbox_manager=manager,
    )

    with TestClient(app):
        assert app.state.sandbox_manager is manager


def test_reset_cleanup_keeps_active_checkpoint_database_and_removes_orphans(tmp_path):
    checkpoint = tmp_path / "coordinator_memory.sqlite3"
    checkpoint.write_text("active", encoding="utf-8")
    (tmp_path / "coordinator_memory.sqlite3-wal").write_text("wal", encoding="utf-8")
    (tmp_path / "coordinator_memory.sqlite3-shm").write_text("shm", encoding="utf-8")
    orphan = tmp_path / "completed-worker.json"
    orphan.write_text("remove", encoding="utf-8")

    removed = _clear_directory(
        tmp_path,
        preserve_names={
            "coordinator_memory.sqlite3",
            "coordinator_memory.sqlite3-wal",
            "coordinator_memory.sqlite3-shm",
        },
    )

    assert removed == 1
    assert checkpoint.exists()
    assert not orphan.exists()


def test_monitor_is_admin_only_and_queries_observability(tmp_path):
    runtime = TelemetryRuntime(tmp_path / "telemetry.sqlite3", flush_interval_s=0.01)
    context = TelemetryContext.create(session_id="session-monitor", turn_id="turn-monitor")
    runtime.start_trace(context)
    runtime.complete_trace(
        context,
        usage=TokenUsage(input_tokens=10, output_tokens=4, total_tokens=14, source="provider"),
    )
    auth = SameOriginSessionAuth(
        secret="monitor-test-secret",
        cookie_name="monitor_test",
        allowed_origins=["http://testserver"],
    )
    resetter = ResetSpy()
    # Inject testserver so Starlette's TrustedHostMiddleware accepts the
    # TestClient's default Host: testserver header regardless of the local
    # .env override of NLP_AGENT_MONITOR_ALLOWED_HOSTS.
    app = create_monitor_app(
        runtime=runtime,
        auth=auth,
        resetter=resetter,  # type: ignore[arg-type]
        allowed_hosts=["testserver"],
    )
    with TestClient(app) as client:
        assert client.get("/api/v1/observability/overview").status_code == 401
        login = client.post("/api/v1/auth/session", headers={"Origin": "http://testserver"})
        assert login.status_code == 201
        assert client.get("/api/v1/observability/sessions").status_code == 404
        overview = client.get("/api/v1/observability/overview").json()
        assert overview["requests"] == 1
        assert overview["tokens"]["total_tokens"] == 14
        traces = client.get("/api/v1/observability/traces").json()["items"]
        assert traces[0]["trace_id"] == context.trace_id
        detail = client.get(f"/api/v1/observability/traces/{context.trace_id}").json()
        assert detail["trace"]["session_id"] == "session-monitor"
        rejected = client.post("/api/v1/observability/storage/prune?trace_days=30&event_days=30")
        assert rejected.status_code == 403
        accepted = client.post(
            "/api/v1/observability/storage/prune?trace_days=30&event_days=30",
            headers={"Origin": "http://testserver", "X-CSRF-Token": login.json()["csrf_token"]},
        )
        assert accepted.status_code == 200
        reset = client.post(
            "/api/v1/observability/storage/reset",
            headers={"Origin": "http://testserver", "X-CSRF-Token": login.json()["csrf_token"]},
        )
        assert reset.status_code == 200
        assert reset.json()["sessions"] == 1
        assert resetter.calls == 1


def test_monitor_system_usage_reads_the_detailed_all_user_ledger(tmp_path):
    class DetailedUsageReader:
        def __init__(self):
            self.calls: list[int] = []

        def system_snapshot(self, *, days: int, include_users: bool = True):
            self.calls.append(days)
            return {
                "scope": "system",
                "period_days": days,
                "events": 2,
                "priced_events": 2,
                "unpriced_events": 0,
                "credits_complete": True,
                "credit_status": "complete",
                "credits_micro": 200,
                "priced_credits_micro": 200,
                "tokens": {"total_tokens": 20},
                "users": [{"user_id": "alice", "events": 2}] if include_users else [],
            }

        def system_user_page(self, *, days: int, limit: int, offset: int):
            return {"scope": "system", "period_days": days, "items": [{"user_id": "alice"}], "total": 1, "offset": offset, "limit": limit, "has_more": False}

        def system_trend(self, *, window_minutes: int, bucket_minutes: int):
            return {"scope": "system", "window_minutes": window_minutes, "bucket_minutes": bucket_minutes, "granularity": "five_minute", "breakdown": []}

        def close(self):
            return None

    runtime = TelemetryRuntime(tmp_path / "telemetry.sqlite3")
    auth = SameOriginSessionAuth(
        secret="monitor-test-secret",
        cookie_name="monitor_test",
        allowed_origins=["http://testserver"],
    )
    reader = DetailedUsageReader()
    app = create_monitor_app(
        runtime=runtime,
        auth=auth,
        usage_reader=reader,  # type: ignore[arg-type]
        allowed_hosts=["testserver"],
    )

    with TestClient(app) as client:
        login = client.post("/api/v1/auth/session", headers={"Origin": "http://testserver"})
        assert login.status_code == 201
        response = client.get("/api/v1/observability/usage/system?days=7")
        compact = client.get("/api/v1/observability/usage/system?days=7&include_users=false")
        unpaged_users = client.get("/api/v1/observability/usage/system?days=7&include_users=true")
        users = client.get("/api/v1/observability/usage/system/users?days=7&limit=12&offset=0")
        trend = client.get("/api/v1/observability/usage/system/trend?window_minutes=120&bucket_minutes=5")

    assert response.status_code == 200
    assert response.json()["scope"] == "system"
    assert response.json()["tokens"]["total_tokens"] == 20
    assert "catalog" in response.json()
    assert "providers" in response.json()["catalog"]
    assert compact.status_code == 200
    assert compact.json()["users"] == []
    assert unpaged_users.status_code == 422
    assert unpaged_users.json()["code"] == "user_breakdown_requires_pagination"
    assert reader.calls == [7, 7]
    assert users.status_code == 200
    assert users.json()["items"][0]["user_id"] == "alice"
    assert trend.status_code == 200
    assert trend.json()["granularity"] == "five_minute"


def test_monitor_trace_groups_prioritize_problem_finding_and_page_chain_rows(tmp_path):
    runtime = TelemetryRuntime(tmp_path / "telemetry.sqlite3")
    first = TelemetryContext.create(
        session_id="session-chain",
        turn_id="turn-chain",
        user_id="alice",
        workspace_id="workspace-a",
    )
    runtime.start_trace(
        first,
        attributes={
            "chain_id": "workflow-42",
            "chain_name": "文档处理",
            "entrypoint": "/api/v1/chat",
        },
    )
    runtime.complete_trace(first, status=SpanStatus.ERROR)
    second = TelemetryContext.create(
        session_id="session-chain",
        turn_id="turn-chain",
        user_id="alice",
        workspace_id="workspace-a",
    )
    runtime.start_trace(
        second,
        attributes={
            "chain_id": "workflow-42",
            "chain_name": "文档处理",
            "entrypoint": "/api/v1/chat",
        },
    )
    runtime.complete_trace(second)
    auth = SameOriginSessionAuth(
        secret="monitor-test-secret",
        cookie_name="monitor_test",
        allowed_origins=["http://testserver"],
    )
    app = create_monitor_app(
        runtime=runtime,
        auth=auth,
        allowed_hosts=["testserver"],
    )

    with TestClient(app) as client:
        login = client.post("/api/v1/auth/session", headers={"Origin": "http://testserver"})
        assert login.status_code == 201
        groups = client.get(
            "/api/v1/observability/traces/groups?days=30&limit=1&offset=0&focus=errors"
        )
        detail = client.get(
            "/api/v1/observability/traces/chains/workflow-42"
        )

    assert groups.status_code == 200
    assert groups.json()["total"] == 1
    assert groups.json()["items"][0]["chain_name"] == "文档处理"
    assert groups.json()["items"][0]["user_ids"] == ["alice"]
    assert groups.json()["items"][0]["error_count"] == 1
    assert detail.status_code == 200
    assert detail.json()["chain"]["entrypoint"] == "/api/v1/chat"
    assert {item["trace_id"] for item in detail.json()["traces"]} == {
        first.trace_id,
        second.trace_id,
    }


@pytest.mark.asyncio
async def test_monitor_dependency_and_error_pages_expose_bounded_all_user_analytics(tmp_path):
    runtime = TelemetryRuntime(tmp_path / "telemetry.sqlite3", flush_interval_s=0.01)
    context = TelemetryContext.create(
        session_id="analytics-session",
        turn_id="analytics-turn",
        user_id="alice",
        workspace_id="workspace-a",
    )
    runtime.start_trace(context, attributes={"chain_name": "分析链路", "entrypoint": "/api/chat"})
    with bind_telemetry_context(context):
        async with runtime.span(
            SpanKind.MODEL,
            "gateway.model",
            attributes={"provider": "openai", "provider_model": "gpt-5.4"},
        ) as span:
            span.set_status(SpanStatus.TIMEOUT, error_kind="TimeoutError")
    runtime.complete_trace(context, status=SpanStatus.ERROR)
    await runtime.flush()
    auth = SameOriginSessionAuth(
        secret="monitor-test-secret",
        cookie_name="monitor_test",
        allowed_origins=["http://testserver"],
    )
    app = create_monitor_app(runtime=runtime, auth=auth, allowed_hosts=["testserver"])

    with TestClient(app) as client:
        login = client.post("/api/v1/auth/session", headers={"Origin": "http://testserver"})
        dependencies = client.get(
            "/api/v1/observability/dependencies?days=30&window_minutes=120&bucket_minutes=5"
        )
        errors = client.get("/api/v1/observability/errors?days=30&limit=10&offset=0")

    assert login.status_code == 201
    assert dependencies.status_code == 200
    assert dependencies.json()["scope"] == "system"
    assert dependencies.json()["summary"]["active_users"] == 1
    assert dependencies.json()["models"][0]["provider"] == "openai"
    assert len(dependencies.json()["trend"]) == 24
    assert errors.status_code == 200
    assert errors.json()["scope"] == "system"
    assert errors.json()["summary"]["affected_users"] == 1
    assert errors.json()["items"][0]["sample_trace_id"] == context.trace_id
    assert "error_message" not in errors.json()["items"][0]
    await runtime.close()


def test_monitor_login_reuses_account_auth_but_requires_monitor_permission():
    password_hash = PasswordHasherSingleton.get().hash("test-password")
    developer_auth = SameOriginSessionAuth(
        secret="monitor-test-secret",
        cookie_name="monitor_test",
        allowed_origins=["http://testserver"],
        username="developer",
        password_hash=password_hash,
        roles=frozenset({"developer"}),
    )
    app = create_monitor_app(
        runtime=TelemetryRuntime(),
        auth=developer_auth,
        allowed_hosts=["testserver"],
    )

    with TestClient(app) as client:
        login = client.post(
            "/api/v1/auth/login",
            json={"username": "developer", "password": "test-password"},
            headers={"Origin": "http://testserver"},
        )

    assert login.status_code == 200
    assert login.json()["roles"] == ["developer"]

    student_auth = SameOriginSessionAuth(
        secret="monitor-test-secret",
        cookie_name="monitor_test",
        allowed_origins=["http://testserver"],
        username="student",
        password_hash=password_hash,
        roles=frozenset({"student"}),
    )
    student_app = create_monitor_app(
        runtime=TelemetryRuntime(),
        auth=student_auth,
        allowed_hosts=["testserver"],
    )
    existing_token, _claims = student_auth.login("student", "test-password")
    with TestClient(student_app) as client:
        client.cookies.set("monitor_test", existing_token)
        rejected = client.post(
            "/api/v1/auth/login",
            json={"username": "student", "password": "test-password"},
            headers={"Origin": "http://testserver"},
        )

    assert rejected.status_code == 403
    assert rejected.json()["code"] == "forbidden"
    assert student_auth.authenticate(existing_token).user_id == "student"


def test_monitor_production_login_uses_independent_database_session(monkeypatch, tmp_path):
    captured: dict[str, object] = {}

    class FakeDatabaseAuth:
        cookie_name = "nlp_monitor_session"
        secure = False
        ttl_s = 900

        @staticmethod
        def require_same_origin(_origin, _host):
            return None

        async def login(self, _factory, _username, _password, **_kwargs):
            expires_at = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=15)
            return "monitor-token", DatabaseSessionClaims(
                user_id="developer",
                workspace_id="default",
                session_id="monitor-session",
                token_hash_value="monitor-token-hash",
                csrf_hash_value="monitor-csrf-hash",
                expires_at=expires_at,
                authorization_version=0,
                csrf_token="monitor-csrf",
            )

    fake_database_auth = FakeDatabaseAuth()

    def fake_from_config(_cls, config):
        captured.update(config)
        return fake_database_auth

    monkeypatch.setattr(DatabaseSessionAuth, "from_config", classmethod(fake_from_config))

    class SessionContext:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *_args):
            return None

    class FakeMySQLRuntime:
        def session_factory(self):
            return SessionContext()

        async def start(self):
            return None

        async def close(self):
            return None

    monkeypatch.setattr(
        "server.monitor.app.MySQLRuntime.from_runtime",
        lambda _runtime: FakeMySQLRuntime(),
    )

    async def fake_principal_for_user_id(_session, _user_id):
        return AuthenticatedPrincipal(
            user_id="developer",
            workspace_ids=frozenset({"default"}),
            roles=frozenset({"developer"}),
            permissions=frozenset({"system:runtime:monitor"}),
        )

    monkeypatch.setattr(
        "server.monitor.app.rbac_service.principal_for_user_id",
        fake_principal_for_user_id,
    )

    app = create_monitor_app(
        runtime=TelemetryRuntime(tmp_path / "telemetry.sqlite3"),
        allowed_hosts=["testserver"],
    )
    with TestClient(app) as client:
        login = client.post(
            "/api/v1/auth/login",
            json={"username": "developer", "password": "test-password"},
            headers={"Origin": "http://testserver"},
        )

    assert login.status_code == 200
    assert captured["cookie_name"] == "nlp_monitor_session"
    assert "nlp_monitor_session=monitor-token" in login.headers["set-cookie"]


@pytest.mark.asyncio
async def test_monitor_live_events_ignores_a_client_that_disconnects_during_heartbeat(tmp_path):
    runtime = TelemetryRuntime(tmp_path / "telemetry.sqlite3", flush_interval_s=0.01)
    auth = SameOriginSessionAuth(
        secret="monitor-test-secret",
        cookie_name="monitor_test",
        allowed_origins=["http://testserver"],
    )
    app = create_monitor_app(runtime=runtime, auth=auth, resetter=ResetSpy())  # type: ignore[arg-type]
    endpoint = next(route.endpoint for route in app.routes if getattr(route, "path", None) == "/ws/observability")
    token, _claims = auth.issue()

    class DisconnectingWebSocket:
        headers = {"origin": "http://testserver", "host": "testserver"}
        cookies = {"monitor_test": token}
        accepted = False

        async def accept(self):
            self.accepted = True

        async def send_json(self, _payload):
            raise WebSocketDisconnect(code=1006)

        async def close(self, **_kwargs):
            return None

    websocket = DisconnectingWebSocket()
    await endpoint(websocket)

    assert websocket.accepted is True
    await runtime.close()
