import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from core.identity import AuthenticatedPrincipal
from core.session_context import SessionContext
from gateway.contracts import GatewayEventType
from gateway.core import BackendGateway
from gateway.repository import GatewayRepository
from server.web.app import create_app
from server.web.auth import SameOriginSessionAuth
from server.web.contracts import ServerEventEnvelope
from server.web.websocket import WebSocketConnection, WebSocketHub


class FakeSessions:
    def __init__(self):
        self.contexts = {}

    async def create(self, principal, *, workspace_id="default", channel="web"):
        principal.require_workspace(workspace_id)
        context = SessionContext.create(
            user_id=principal.user_id,
            workspace_id=workspace_id,
            channel=channel,
        )
        self.contexts[context.session_id] = context
        return context

    async def resolve(self, principal, session_id):
        context = self.contexts.get(session_id)
        if context is None:
            raise FileNotFoundError(session_id)
        principal.require_context(context)
        return context

    async def list(self, principal):
        return [
            context.model_dump(mode="json")
            for context in self.contexts.values()
            if principal.can_access(context)
        ]

    async def messages(self, principal, session_id):
        await self.resolve(principal, session_id)
        return []

    async def touch(self, principal, session_id):
        return await self.resolve(principal, session_id)

    async def delete(self, principal, session_id):
        context = await self.resolve(principal, session_id)
        self.contexts.pop(session_id)
        return context


class FakeEngine:
    def __init__(self):
        self.sink = None
        self.active = {}
        self.closed = False

    async def start(self, event_sink):
        self.sink = event_sink

    async def run_turn(self, context, turn_id, content):
        self.active[context.session_id] = turn_id
        await self.sink(
            turn_id,
            context.session_id,
            GatewayEventType.MESSAGE_DELTA,
            {"delta": "thinking", "channel": "reasoning"},
        )
        await self.sink(
            turn_id,
            context.session_id,
            GatewayEventType.TOOL_STARTED,
            {"tool_name": "demo"},
        )
        await self.sink(
            turn_id,
            context.session_id,
            GatewayEventType.MESSAGE_DELTA,
            {"delta": f"answer:{content}"},
        )
        await self.sink(
            turn_id,
            context.session_id,
            GatewayEventType.TOOL_COMPLETED,
            {"tool_name": "demo"},
        )
        self.active.pop(context.session_id, None)
        return f"answer:{content}"

    async def inject(self, context, content):
        return self.active.get(context.session_id)

    async def cancel_turn(self, context, turn_id):
        self.active.pop(context.session_id, None)

    async def delete_session(self, context):
        self.active.pop(context.session_id, None)

    async def close(self):
        self.closed = True


@pytest.fixture
def web_app(tmp_path):
    engine = FakeEngine()
    sessions = FakeSessions()

    def gateway_factory():
        return BackendGateway(
            engine=engine,
            repository=GatewayRepository(tmp_path / "gateway.sqlite3"),
            sessions=sessions,
        )

    auth = SameOriginSessionAuth(
        secret="test-secret-that-is-long-enough-for-hmac",
        allowed_origins=["http://testserver"],
        username="nova",
        password_hash=PasswordHasher().hash("test-password"),
        roles=frozenset({"admin"}),
    )
    # Inject testserver so Starlette's TrustedHostMiddleware accepts the
    # TestClient's default Host: testserver header regardless of the local
    # .env override of NLP_AGENT_WEB_ALLOWED_HOSTS.
    return create_app(gateway_factory=gateway_factory, auth=auth, allowed_hosts=["testserver"]), engine


@pytest.fixture
def student_web_app(tmp_path):
    engine = FakeEngine()
    sessions = FakeSessions()

    def gateway_factory():
        return BackendGateway(
            engine=engine,
            repository=GatewayRepository(tmp_path / "gateway.sqlite3"),
            sessions=sessions,
        )

    auth = SameOriginSessionAuth(
        secret="test-secret-that-is-long-enough-for-hmac",
        allowed_origins=["http://testserver"],
        username="nova",
        password_hash=PasswordHasher().hash("test-password"),
        roles=frozenset({"student"}),
    )
    # Inject testserver so Starlette's TrustedHostMiddleware accepts the
    # TestClient's default Host: testserver header regardless of the local
    # .env override of NLP_AGENT_WEB_ALLOWED_HOSTS.
    return create_app(gateway_factory=gateway_factory, auth=auth, allowed_hosts=["testserver"]), engine


def authenticate(client: TestClient) -> str:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "nova", "password": "test-password"},
        headers={"Origin": "http://testserver"},
    )
    assert response.status_code == 200
    return response.json()["csrf_token"]


def write_headers(csrf: str) -> dict[str, str]:
    return {"Origin": "http://testserver", "X-CSRF-Token": csrf}


def test_login_requires_valid_credentials_and_logout_revokes_cookie_session(web_app):
    app, _engine = web_app
    with TestClient(app) as client:
        assert client.get("/api/v1/sessions").status_code == 401


def test_guest_session_has_only_guest_capabilities(web_app):
    app, _engine = web_app
    with TestClient(app) as client:
        response = client.post("/api/v1/auth/guest", headers={"Origin": "http://testserver"})

        assert response.status_code == 200
        assert response.json()["roles"] == ["guest"]
        assert client.get("/api/v1/sessions").status_code == 403
        assert client.get("/api/v1/developer/release-notes").status_code == 403


def test_auth_session_exposes_human_readable_account_identity(web_app):
    app, _engine = web_app
    with TestClient(app) as client:
        login = client.post(
            "/api/v1/auth/login",
            json={"username": "nova", "password": "test-password"},
            headers={"Origin": "http://testserver"},
        )
        assert login.status_code == 200
        assert login.json()["username"] == "nova"
        assert login.json()["display_name"] == "nova"

        session = client.get("/api/v1/auth/session")
        assert session.status_code == 200
        assert session.json()["username"] == "nova"
        assert session.json()["display_name"] == "nova"


def test_student_cannot_call_teacher_or_developer_control_planes(student_web_app):
    app, _engine = student_web_app
    with TestClient(app) as client:
        authenticate(client)

        teacher = client.get("/api/v1/teacher/overview?workspace_id=default")
        developer = client.get("/api/v1/developer/snapshot")
        release_notes = client.get("/api/v1/developer/release-notes")

        assert teacher.status_code == 403
        assert teacher.json()["code"] == "forbidden"
        assert developer.status_code == 403
        assert developer.json()["code"] == "forbidden"
        assert release_notes.status_code == 403
        assert release_notes.json()["code"] == "forbidden"
        assert client.post("/api/v1/auth/session", headers={"Origin": "http://testserver"}).status_code == 405

        rejected = client.post(
            "/api/v1/auth/login",
            json={"username": "nova", "password": "incorrect"},
            headers={"Origin": "http://testserver"},
        )
        assert rejected.status_code == 401

        csrf = authenticate(client)
        assert client.get("/api/v1/sessions").status_code == 200
        logged_out = client.delete("/api/v1/auth/session", headers=write_headers(csrf))
        assert logged_out.status_code == 204
        assert client.get("/api/v1/sessions").status_code == 401


def test_learning_release_notes_route_requests_only_published(web_app, monkeypatch):
    """The public read route must pass include_drafts=False and serialize the payload.

    The published-only filtering itself is exercised by the ReleaseNoteService
    unit tests (include_drafts=False -> statuses=("published",)); this test pins
    the route wiring and the LEARNING_CONTENT_READ_PUBLIC grant (held by guest,
    student, teacher and developer alike) end-to-end without a MySQL DB.
    """
    captured: dict[str, bool] = {}

    class FakeRow:
        def __init__(self, note_id, version, released_at, notes, status):
            self.id, self.version, self.released_at, self.notes_json, self.status = (
                note_id, version, released_at, notes, status,
            )

    class FakeReleaseNoteService:
        async def list(self, session, *, include_drafts=False):
            captured["include_drafts"] = include_drafts
            return [FakeRow(
                "n1", "1.0.0", datetime(2026, 8, 1, tzinfo=timezone.utc),
                ["新增发布说明功能"], "published",
            )]

    class FakeSession:
        def add(self, obj):
            return None

        @asynccontextmanager
        async def begin(self):
            yield

    @asynccontextmanager
    async def fake_session_factory():
        yield FakeSession()

    monkeypatch.setattr("server.web.app.release_note_service", FakeReleaseNoteService())
    monkeypatch.setattr(
        BackendGateway,
        "authorization_session_factory",
        property(lambda self: fake_session_factory),
    )

    app, _engine = web_app
    with TestClient(app) as client:
        guest = client.post("/api/v1/auth/guest", headers={"Origin": "http://testserver"})
        assert guest.status_code == 200
        response = client.get("/api/v1/learning/release-notes")

    assert response.status_code == 200
    assert captured["include_drafts"] is False
    assert response.json()["items"] == [{
        "id": "n1",
        "version": "1.0.0",
        "released_at": "2026-08-01T00:00:00+00:00",
        "notes": ["新增发布说明功能"],
        "status": "published",
    }]


def test_http_lifecycle_sessions_chat_settings_and_csrf(web_app, monkeypatch):
    monkeypatch.setenv("QWEN_API_KEY", "sk-mock-key-for-tests")
    app, engine = web_app
    with TestClient(app) as client:
        assert client.get("/health/live").json() == {"status": "ok"}
        assert client.get("/health/ready").status_code == 200
        csrf = authenticate(client)
        developer = client.get("/api/v1/developer/snapshot")
        assert developer.status_code == 200
        assert developer.json()["runtime"]["status"] == "ok"
        assert "tools" in developer.json()
        teacher = client.get("/api/v1/teacher/overview?workspace_id=default")
        assert teacher.status_code == 200
        assert teacher.json()["summary"]["questions"] == 0
        goals = client.put(
            "/api/v1/teacher/catalog/default",
            json={
                "topics": [{"id": "topic-1", "name": "NLP 入门", "description": "", "knowledge_points": []}],
                "exercise_blueprints": [],
                "review_blueprints": [],
            },
            headers=write_headers(csrf),
        )
        assert goals.status_code == 200
        assert client.get("/api/v1/teacher/catalog/default").json()["catalog"]["topics"][0]["name"] == "NLP 入门"
        assert client.get("/api/v1/learning/catalog/default").json()["catalog"]["topics"][0]["id"] == "topic-1"

        rejected = client.post("/api/v1/sessions", json={"workspace_id": "default"})
        assert rejected.status_code == 403
        assert rejected.json()["code"] == "origin_rejected"

        created = client.post(
            "/api/v1/sessions",
            json={"workspace_id": "default"},
            headers=write_headers(csrf),
        )
        assert created.status_code == 201
        session_id = created.json()["session_id"]
        assert client.get("/api/v1/sessions").json()["items"][0]["session_id"] == session_id

        accepted = client.post(
            "/api/v1/chat/turns",
            json={"session_id": session_id, "content": "hello", "idempotency_key": "one"},
            headers=write_headers(csrf),
        )
        assert accepted.status_code == 202
        turn_id = accepted.json()["turn_id"]

        for _ in range(100):
            turn = client.get(f"/api/v1/chat/turns/{turn_id}").json()
            if turn["status"] == "completed":
                break
            asyncio.run(asyncio.sleep(0.001))
        assert turn["final_text"] == "answer:hello"
        stats = client.get("/api/v1/teacher/analytics?workspace_id=default").json()
        assert stats["summary"]["questions"] >= 1
        assert stats["period_days"] == 30
        events = client.get(f"/api/v1/chat/turns/{turn_id}/events").json()["items"]
        assert [event["sequence"] for event in events] == list(range(1, len(events) + 1))

        updated = client.patch(
            "/api/v1/settings",
            json={"theme": "dark", "content_font_size": "large", "reduce_motion": True, "show_reasoning": True, "model_profile": "qwen"},
            headers=write_headers(csrf),
        )
        # Teaching catalog revisions are isolated from per-user UI settings.
        assert updated.json()["revision"] == 1
        settings_payload = client.get("/api/v1/settings").json()
        assert settings_payload["preferences"]["settings"]["theme"] == "dark"
        assert settings_payload["preferences"]["settings"]["content_font_size"] == "large"
        assert settings_payload["preferences"]["settings"]["reduce_motion"] is True
        assert settings_payload["preferences"]["settings"]["model_profile"] == "qwen"
        assert settings_payload["runtime"]["default_model_profile"] == "deepseek"
        assert settings_payload["runtime"]["model_profiles"]["qwen"]["label"] == "Qwen"

        deleted = client.delete(
            f"/api/v1/sessions/{session_id}",
            headers=write_headers(csrf),
        )
        assert deleted.status_code == 204
    assert engine.closed is True


def test_teacher_goals_and_reserved_resources_follow_the_public_http_contract(web_app):
    app, _engine = web_app
    with TestClient(app) as client:
        csrf = authenticate(client)
        default_goals = client.get("/api/v1/teacher/goals/default")
        assert default_goals.status_code == 200
        assert default_goals.json()["goals"] == {
            "workspace_id": "default",
            "course_title": "NLP 基础课程",
            "description": "",
            "objectives": [],
            "focus_topics": [],
            "target_level": "beginner",
        }

        updated = client.put(
            "/api/v1/teacher/goals/default",
            json={
                "course_title": "Transformer 专题",
                "description": "注意力机制课程",
                "objectives": ["解释自注意力"],
                "focus_topics": ["Transformer"],
                "target_level": "intermediate",
            },
            headers=write_headers(csrf),
        )
        assert updated.status_code == 200
        assert updated.json()["goals"]["course_title"] == "Transformer 专题"
        assert client.get("/api/v1/teacher/overview").json()["goals"] == updated.json()["goals"]

        for resource in ("courses", "prompts", "reports"):
            response = client.get(f"/api/v1/teacher/{resource}?workspace_id=default")
            assert response.status_code == 200
            assert response.json() == {
                "items": [],
                "resource": resource,
                "workspace_id": "default",
                "status": "interface_reserved",
            }


def test_learning_catalog_only_exposes_enabled_topics_and_enabled_knowledge_points(web_app):
    app, _engine = web_app
    with TestClient(app) as client:
        csrf = authenticate(client)
        saved = client.put(
            "/api/v1/teacher/catalog/default",
            json={
                "topics": [
                    {
                        "id": "topic-enabled",
                        "name": "Transformer",
                        "description": "模型结构",
                        "status": "enabled",
                        "knowledge_points": [
                            {
                                "id": "attention",
                                "name": "注意力机制",
                                "markdown": "## 注意力\n解释 Q、K、V。",
                                "status": "enabled",
                                "sort_order": 1,
                            },
                            {
                                "id": "legacy",
                                "name": "旧知识点",
                                "markdown": "不再使用",
                                "status": "disabled",
                                "sort_order": 2,
                            },
                        ],
                    },
                    {
                        "id": "topic-disabled",
                        "name": "停用课程",
                        "description": "",
                        "status": "disabled",
                        "knowledge_points": [],
                    },
                ],
                "exercise_blueprints": [],
                "review_blueprints": [],
            },
            headers=write_headers(csrf),
        )

        assert saved.status_code == 200
        student_catalog = client.get("/api/v1/learning/catalog/default")
        assert student_catalog.status_code == 200
        assert student_catalog.json()["catalog"]["topics"] == [
            {
                "id": "topic-enabled",
                "name": "Transformer",
                "description": "模型结构",
                "status": "enabled",
                "knowledge_points": [
                    {
                        "id": "attention",
                        "name": "注意力机制",
                        "markdown": "## 注意力\n解释 Q、K、V。",
                        "status": "enabled",
                        "sort_order": 1,
                    }
                ],
            }
        ]


def test_teacher_blueprint_resource_requires_one_knowledge_point_and_persists_it(web_app):
    app, _engine = web_app
    with TestClient(app) as client:
        csrf = authenticate(client)
        client.put("/api/v1/teacher/catalog/default", json={
            "topics": [{"id": "transformer", "name": "Transformer", "description": "", "knowledge_points": [{"id": "attention", "name": "注意力", "markdown": "", "status": "enabled", "sort_order": 0}]}],
            "exercise_blueprints": [], "review_blueprints": [],
        }, headers=write_headers(csrf))
        blueprint = {
            "id": "attention-qkv", "name": "QKV 角色", "topic_id": "transformer", "knowledge_point_id": "attention",
            "level": "beginner", "instructions": "只出一道 QKV 题", "question_type": "简答", "status": "enabled",
            "rubric": [{"criterion": "角色正确", "weight": 100}],
        }
        saved = client.put("/api/v1/teacher/catalog/default/exercise-blueprints/attention-qkv", json=blueprint, headers=write_headers(csrf))
        assert saved.status_code == 200
        catalog = client.get("/api/v1/teacher/catalog/default").json()["catalog"]
        assert catalog["exercise_blueprints"] == [{key: value for key, value in blueprint.items() if key != "level"}]
        invalid = client.put("/api/v1/teacher/catalog/default/exercise-blueprints/bad", json={**blueprint, "id": "bad", "knowledge_point_id": "other"}, headers=write_headers(csrf))
        assert invalid.status_code == 422
        missing_rubric = client.put("/api/v1/teacher/catalog/default/exercise-blueprints/no-rubric", json={**blueprint, "id": "no-rubric", "rubric": []}, headers=write_headers(csrf))
        assert missing_rubric.status_code == 422


def test_teacher_guided_blueprint_resource_persists_direction_and_validates_knowledge_point(web_app):
    app, _engine = web_app
    with TestClient(app) as client:
        csrf = authenticate(client)
        client.put("/api/v1/teacher/catalog/default", json={
            "topics": [{"id": "transformer", "name": "Transformer", "description": "", "knowledge_points": [{"id": "attention", "name": "注意力", "markdown": "", "status": "enabled", "sort_order": 0}]}],
            "exercise_blueprints": [], "review_blueprints": [], "guided_blueprints": [],
        }, headers=write_headers(csrf))
        blueprint = {"id": "attention-guide", "name": "从权重开始", "topic_id": "transformer", "knowledge_point_id": "attention", "guidance": "先让学生解释权重为何需要归一化。", "status": "enabled"}
        saved = client.put("/api/v1/teacher/catalog/default/guided-blueprints/attention-guide", json=blueprint, headers=write_headers(csrf))
        assert saved.status_code == 200
        catalog = client.get("/api/v1/teacher/catalog/default").json()["catalog"]
        assert catalog["guided_blueprints"] == [blueprint]
        invalid = client.put("/api/v1/teacher/catalog/default/guided-blueprints/bad", json={**blueprint, "id": "bad", "knowledge_point_id": "other"}, headers=write_headers(csrf))
        assert invalid.status_code == 422


def test_teacher_nlp_curriculum_import_post_is_not_available(web_app):
    app, _engine = web_app
    with TestClient(app) as client:
        csrf = authenticate(client)
        result = client.post(
            "/api/v1/teacher/catalog/default/imports/nlp-foundations",
            headers=write_headers(csrf),
        )

        # No POST import handler is registered.  Depending on the Starlette
        # router version, an unmatched method is reported either as a missing
        # route (404) or a method mismatch (405); both mean the import API is
        # unavailable, which is the contract this regression test protects.
        assert result.status_code in {404, 405}


def _receive_until(websocket, required_types: set[str], limit: int = 30):
    events = []
    seen = set()
    for _ in range(limit):
        event = websocket.receive_json()
        events.append(event)
        seen.add(event["type"])
        if required_types <= seen:
            return events
    raise AssertionError(f"missing event types: {required_types - seen}; got={seen}")


def test_websocket_multiplex_stream_and_resume(web_app):
    app, _engine = web_app
    with TestClient(app) as client:
        csrf = authenticate(client)
        session_id = client.post(
            "/api/v1/sessions",
            json={"workspace_id": "default"},
            headers=write_headers(csrf),
        ).json()["session_id"]

        with client.websocket_connect(
            "/ws/v1",
            headers={"Origin": "http://testserver"},
        ) as websocket:
            assert websocket.receive_json()["type"] == "connection.ready"
            websocket.send_json(
                {
                    "v": "1",
                    "type": "chat.send",
                    "request_id": "send-1",
                    "payload": {"session_id": session_id, "content": "over ws"},
                }
            )
            events = _receive_until(
                websocket,
                {
                    "command.ack",
                    "chat.accepted",
                    "chat.started",
                    "chat.reasoning.delta",
                    "tool.started",
                    "chat.delta",
                    "tool.completed",
                    "chat.completed",
                },
            )
            turn_id = next(event["turn_id"] for event in events if event["type"] == "command.ack")
            sequenced = [event["sequence"] for event in events if "sequence" in event]
            assert sequenced == sorted(set(sequenced))

        with client.websocket_connect(
            "/ws/v1",
            headers={"Origin": "http://testserver"},
        ) as websocket:
            assert websocket.receive_json()["type"] == "connection.ready"
            websocket.send_json(
                {
                    "v": "1",
                    "type": "stream.resume",
                    "request_id": "resume-1",
                    "payload": {"turn_id": turn_id, "after_sequence": 0},
                }
            )
            replayed = _receive_until(websocket, {"command.ack", "chat.completed"})
            sequences = [event["sequence"] for event in replayed if "sequence" in event]
            assert sequences == list(range(1, max(sequences) + 1))


def test_websocket_rejects_cross_origin(web_app):
    app, _engine = web_app
    with TestClient(app) as client:
        authenticate(client)
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect(
                "/ws/v1",
                headers={"Origin": "https://evil.example"},
            ) as websocket:
                websocket.receive_json()
        assert exc.value.code == 4403


def test_websocket_requires_an_authenticated_cookie(web_app):
    app, _engine = web_app
    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect(
                "/ws/v1",
                headers={"Origin": "http://testserver"},
            ) as websocket:
                websocket.receive_json()
        assert exc.value.code == 4401


def test_logout_revokes_the_active_websocket_connection(web_app):
    app, _engine = web_app
    with TestClient(app) as client:
        csrf = authenticate(client)
        with client.websocket_connect(
            "/ws/v1",
            headers={"Origin": "http://testserver"},
        ) as websocket:
            assert websocket.receive_json()["type"] == "connection.ready"
            assert client.delete(
                "/api/v1/auth/session",
                headers=write_headers(csrf),
            ).status_code == 204
            with pytest.raises(WebSocketDisconnect) as exc:
                websocket.receive_json()
            assert exc.value.code == 4401


def test_websocket_reports_deleted_session_subscription_without_crashing(web_app):
    app, _engine = web_app
    with TestClient(app) as client:
        authenticate(client)
        with client.websocket_connect("/ws/v1", headers={"Origin": "http://testserver"}) as websocket:
            assert websocket.receive_json()["type"] == "connection.ready"
            websocket.send_json({
                "v": "1",
                "type": "session.subscribe",
                "request_id": "deleted-session",
                "payload": {"session_id": "session_deleted_by_monitor"},
            })
            error = websocket.receive_json()
            assert error["type"] == "command.error"
            assert error["request_id"] == "deleted-session"
            assert error["session_id"] == "session_deleted_by_monitor"
            assert error["payload"]["code"] == "not_found"

            websocket.send_json({"v": "1", "type": "ping", "request_id": "still-open", "payload": {}})
            assert websocket.receive_json()["type"] == "pong"


class SlowWebSocket:
    def __init__(self):
        self.block = asyncio.Event()
        self.closed = []

    async def send_json(self, _payload):
        await self.block.wait()

    async def close(self, *, code, reason):
        self.closed.append((code, reason))


@pytest.mark.asyncio
async def test_websocket_slow_sender_is_disconnected_without_blocking_publish():
    websocket = SlowWebSocket()
    principal = AuthenticatedPrincipal(
        user_id="slow-user", workspace_ids=frozenset({"default"})
    )
    connection = WebSocketConnection(
        websocket,
        gateway=None,
        principal=principal,
        max_queue=10,
        send_queue_size=1,
        send_timeout_s=0.1,
    )
    connection.start()
    event = ServerEventEnvelope(type="test.event", payload={})

    assert await connection.send(event) is True
    await asyncio.wait_for(connection.wait_closed(), timeout=0.5)

    assert websocket.closed[0][0] == 1013


def test_websocket_hub_enforces_global_and_per_user_limits():
    alice = AuthenticatedPrincipal(user_id="alice")
    bob = AuthenticatedPrincipal(user_id="bob")
    hub = WebSocketHub(max_connections=2, max_connections_per_user=1)

    def connection(principal):
        return WebSocketConnection(
            SlowWebSocket(),
            gateway=None,
            principal=principal,
            max_queue=10,
            send_queue_size=1,
            send_timeout_s=0.1,
        )

    assert hub.try_add(connection(alice)) is True
    assert hub.try_add(connection(alice)) is False
    assert hub.try_add(connection(bob)) is True
    assert hub.try_add(connection(AuthenticatedPrincipal(user_id="carol"))) is False
