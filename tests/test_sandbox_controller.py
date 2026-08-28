from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest


def test_execution_output_preserves_stdout_and_stderr_streams() -> None:
    from server.sandbox.execution_events import execution_output_streams

    assert execution_output_streams({"stdout": "out\n", "stderr": "err\n"}) == (
        ("stdout", "out\n"),
        ("stderr", "err\n"),
    )


def test_execution_failure_payload_is_structured_and_bounded() -> None:
    from server.sandbox.execution_events import execution_failure_payload

    payload = execution_failure_payload(RuntimeError("secret details"))
    assert payload["error_type"] == "RuntimeError"
    assert payload["error"] == "RuntimeError: secret details"
    assert len(payload["error"]) <= 128


def test_runtime_result_status_marks_kernel_errors_as_failed() -> None:
    from server.sandbox.execution_events import execution_result_failure_payload, execution_result_failed

    result = {"status": "failed", "stdout": "partial", "stderr": "NameError: x"}
    assert execution_result_failed(result)
    assert execution_result_failure_payload(result) == {
        "error_type": "SandboxExecutionError",
        "error": "NameError: x",
    }


@pytest.mark.asyncio
async def test_event_append_retries_transient_store_failure() -> None:
    from server.sandbox.execution_events import append_event_with_retry

    attempts = 0

    async def append() -> dict[str, object]:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise ConnectionError("temporary Redis failure")
        return {"event_id": "3"}

    assert await append_event_with_retry(append, base_delay_seconds=0) == {"event_id": "3"}
    assert attempts == 3


@pytest.mark.asyncio
async def test_execute_commits_started_execution_before_manager_rpc(monkeypatch) -> None:
    """The Manager must never wait on the Web request transaction's locks."""

    from core.identity import AuthenticatedPrincipal
    from server.infrastructure.mysql.models import SandboxExecutionModel
    from server.sandbox import controller
    from server.sandbox.controller import ExecuteBody, execute_sandbox
    from server.web.database_auth import DatabaseSessionClaims
    from configs.settings import settings

    events: list[str] = []
    environment = SimpleNamespace(id="environment-1", owner_user_id="user-1")
    lease = SimpleNamespace(
        id="lease-1",
        environment_id="environment-1",
        auth_session_id="session-1",
        runtime_instance_id="runtime-1",
    )

    class RequestSession:
        async def scalar(self, _statement):
            raise AssertionError("execute must not use the request-scoped transaction")

    class TransactionSession:
        def __init__(self, factory, role: str) -> None:
            self.factory = factory
            self.role = role
            self.execution = None
            self.scalar_calls = 0

        async def scalar(self, _statement):
            self.scalar_calls += 1
            return environment if self.scalar_calls == 1 else lease

        async def get(self, model, _identifier, **_kwargs):
            if model is SandboxExecutionModel:
                return self.factory.execution
            return None

        def add(self, item):
            if isinstance(item, SandboxExecutionModel):
                self.execution = item
                self.factory.execution = item

        async def flush(self):
            events.append(f"flush:{self.role}")

    class Transaction:
        def __init__(self, factory, session: TransactionSession) -> None:
            self.factory = factory
            self.session = session

        async def __aenter__(self):
            events.append(f"begin:{self.session.role}")
            return self.session

        async def __aexit__(self, exc_type, _value, _traceback):
            if exc_type is None:
                events.append(f"commit:{self.session.role}")
                if self.session.role == "started":
                    self.factory.started_committed = True
            else:
                events.append(f"rollback:{self.session.role}")

    class SessionFactory:
        def __init__(self) -> None:
            self.started_committed = False
            self.execution = None
            self.sessions = [
                TransactionSession(self, "started"),
                TransactionSession(self, "finished"),
            ]

        def begin(self):
            return Transaction(self, self.sessions.pop(0))

    factory = SessionFactory()

    class Gateway:
        authorization_session_factory = factory

        async def execute(self, _scope, *, source, ticket):
            del source, ticket
            assert factory.started_committed, "Manager RPC ran while started execution was uncommitted"
            events.append("manager:execute")
            return {"status": "completed", "stdout": "ok\n", "stderr": ""}

    class EventStore:
        async def append(self, _execution_id, *, user_id, event_type, payload):
            del user_id, payload
            events.append(f"event:{event_type}")
            return {"event_id": event_type, "seq": len(events)}

    class Hub:
        async def broadcast(self, *_args, **_kwargs):
            return None

    app_state = SimpleNamespace(
        gateway=Gateway(),
        sandbox_execution_gateway=Gateway(),
        hub=Hub(),
    )
    request = SimpleNamespace(app=SimpleNamespace(state=app_state))
    principal = AuthenticatedPrincipal(user_id="user-1", workspace_ids=frozenset({"workspace-1"}))
    claims = DatabaseSessionClaims(
        user_id="user-1",
        workspace_id="workspace-1",
        session_id="session-1",
        token_hash_value="token",
        csrf_hash_value="csrf",
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
        authorization_version=1,
    )

    monkeypatch.setattr(controller, "sandbox_events", EventStore())
    monkeypatch.setattr(settings, "NLP_AGENT_SANDBOX_ARTIFACT_STORE_ROOT", "")

    result = await execute_sandbox(
        request,
        ExecuteBody(source="print('ok')"),
        RequestSession(),
        principal,
        claims,
        None,
    )

    assert result["status"] == "completed"
    assert events.index("commit:started") < events.index("manager:execute")
    assert events.index("commit:finished") > events.index("manager:execute")


@pytest.mark.asyncio
async def test_finish_event_failure_marks_execution_failed(monkeypatch) -> None:
    """Terminal Redis failure must not leave a completed audit row."""

    from server.sandbox import controller
    from server.sandbox.execution_events import SandboxEventDeliveryError
    from server.infrastructure.mysql.models import SandboxExecutionModel
    from configs.settings import settings

    execution = SimpleNamespace(
        id="execution-1",
        status="running",
        completed_at=None,
        exit_reason=None,
        resource_summary_json=None,
    )

    class Session:
        async def get(self, model, _identifier, **_kwargs):
            assert model is SandboxExecutionModel
            return execution

    class Transaction:
        async def __aenter__(self):
            return Session()

        async def __aexit__(self, *_args):
            return False

    class Factory:
        def begin(self):
            return Transaction()

    class EventStore:
        def __init__(self) -> None:
            self.events: list[str] = []

        async def append(self, _execution_id, *, event_type, **_kwargs):
            if event_type == "execution.completed":
                raise ConnectionError("redis unavailable")
            self.events.append(event_type)
            return {"event_id": event_type, "seq": len(self.events)}

    class Hub:
        async def broadcast(self, *_args, **_kwargs):
            return None

    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(hub=Hub())))
    store = EventStore()
    monkeypatch.setattr(controller, "sandbox_events", store)
    monkeypatch.setattr(settings, "NLP_AGENT_SANDBOX_ARTIFACT_STORE_ROOT", "")

    with pytest.raises(SandboxEventDeliveryError):
        await controller._finish_execution(
            request,
            Factory(),
            execution_id="execution-1",
            owner_user_id="user-1",
            status_value="completed",
            result={"status": "completed", "stdout": "ok"},
        )

    assert execution.status == "failed"
    assert execution.resource_summary_json["event_delivery"]["status"] == "failed"
    assert "execution.completed" not in store.events
    assert "execution.failed" in store.events


@pytest.mark.asyncio
async def test_started_event_failure_is_finalized_before_manager_call(monkeypatch) -> None:
    """Opening-event failures must close the row and skip Docker dispatch."""

    from core.identity import AuthenticatedPrincipal
    from configs.settings import settings
    from server.infrastructure.mysql.models import SandboxExecutionModel
    from server.sandbox import controller
    from server.sandbox.controller import ExecuteBody, execute_sandbox
    from server.web.database_auth import DatabaseSessionClaims

    environment = SimpleNamespace(id="environment-1", owner_user_id="user-1")
    lease = SimpleNamespace(
        id="lease-1",
        environment_id="environment-1",
        auth_session_id="session-1",
        runtime_instance_id="runtime-1",
    )

    class Session:
        def __init__(self, factory, *, initial: bool) -> None:
            self.factory = factory
            self.initial = initial
            self.scalar_calls = 0

        async def scalar(self, _statement):
            self.scalar_calls += 1
            return environment if self.initial and self.scalar_calls == 1 else lease

        async def get(self, model, _identifier, **_kwargs):
            assert model is SandboxExecutionModel
            return self.factory.execution

        def add(self, item):
            if isinstance(item, SandboxExecutionModel):
                self.factory.execution = item

        async def flush(self):
            return None

    class Transaction:
        def __init__(self, session):
            self.session = session

        async def __aenter__(self):
            return self.session

        async def __aexit__(self, *_args):
            return False

    class Factory:
        def __init__(self) -> None:
            self.execution = None
            self.begin_calls = 0

        def begin(self):
            self.begin_calls += 1
            return Transaction(Session(self, initial=self.begin_calls == 1))

    factory = Factory()
    manager_calls = 0

    class Gateway:
        authorization_session_factory = factory

        async def execute(self, *_args, **_kwargs):
            nonlocal manager_calls
            manager_calls += 1
            return {"status": "completed"}

    class EventStore:
        def __init__(self) -> None:
            self.events: list[str] = []

        async def append(self, _execution_id, *, event_type, **_kwargs):
            if event_type == "execution.started":
                raise ConnectionError("redis unavailable")
            self.events.append(event_type)
            return {"event_id": event_type, "seq": len(self.events)}

    class Hub:
        async def broadcast(self, *_args, **_kwargs):
            return None

    gateway = Gateway()
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(gateway=gateway, sandbox_execution_gateway=gateway, hub=Hub()))
    )
    store = EventStore()
    monkeypatch.setattr(controller, "sandbox_events", store)
    monkeypatch.setattr(settings, "NLP_AGENT_SANDBOX_ARTIFACT_STORE_ROOT", "")
    claims = DatabaseSessionClaims(
        user_id="user-1",
        workspace_id="workspace-1",
        session_id="session-1",
        token_hash_value="token",
        csrf_hash_value="csrf",
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
        authorization_version=1,
    )

    with pytest.raises(ConnectionError, match="redis unavailable"):
        await execute_sandbox(
            request,
            ExecuteBody(source="print('ok')"),
            SimpleNamespace(),
            AuthenticatedPrincipal(user_id="user-1", workspace_ids=frozenset({"workspace-1"})),
            claims,
            None,
        )

    assert manager_calls == 0
    assert factory.execution.status == "failed"
    assert "execution.failed" in store.events
