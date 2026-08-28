from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest


def _config(
    *, user_id: str = "local", session_id: str = "model-session", workspace_id: str = "default"
) -> dict:
    return {
        "configurable": {
            "thread_id": session_id,
            "user_id": user_id,
            "workspace_id": workspace_id,
        }
    }


@pytest.mark.asyncio
async def test_database_scratch_does_not_require_interactive_lease() -> None:
    from server.infrastructure.mysql.models import SandboxExecutionModel, SessionModel, UserModel
    from server.sandbox.model_tools import SandboxModelToolService

    now = datetime.now(UTC).replace(tzinfo=None)

    class FakeSession:
        def __init__(self) -> None:
            self.execution: object | None = None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        def begin_nested(self):
            return self

        async def get(self, model, identifier, **_kwargs):
            if model is SessionModel:
                return SimpleNamespace(
                    id=identifier,
                    user_id="local",
                    workspace_id="default",
                    revoked_at=None,
                    expires_at=now + timedelta(minutes=5),
                    authorization_version=1,
                )
            if model is UserModel:
                return SimpleNamespace(
                    id="local",
                    authorization_version=1,
                    status="active",
                    deleted_at=None,
                )
            if model is SandboxExecutionModel:
                return self.execution
            return None

        async def scalar(self, _statement):
            return None

        def add(self, item):
            if isinstance(item, SandboxExecutionModel):
                self.execution = item

        async def flush(self):
            return None

    session = FakeSession()

    class Factory:
        def __call__(self):
            return session

        def begin(self):
            return session

    service = SandboxModelToolService(mode="inmemory", session_factory=Factory())
    result = await service.run_scratch(source="print(2 + 2)", config=_config())

    assert result["ok"] is True
    assert result["execution_id"]
    assert session.execution is not None
    assert session.execution.lease_id is None


@pytest.mark.asyncio
async def test_model_tools_authorize_with_login_session_not_conversation_id() -> None:
    from server.infrastructure.mysql.models import SessionModel, UserModel
    from server.sandbox.model_tools import SandboxModelToolService

    now = datetime.now(UTC).replace(tzinfo=None)

    class FakeSession:
        requested_session_id: str | None = None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def get(self, model, identifier, **_kwargs):
            if model is SessionModel:
                self.requested_session_id = identifier
                if identifier != "login-session-9":
                    return None
                return SimpleNamespace(
                    id=identifier,
                    user_id="user-1",
                    workspace_id="workspace-1",
                    revoked_at=None,
                    expires_at=now + timedelta(minutes=5),
                    authorization_version=1,
                )
            if model is UserModel:
                return SimpleNamespace(
                    id="user-1", authorization_version=1, status="active", deleted_at=None
                )
            return None

        async def scalar(self, _statement):
            return None

    class Factory:
        def __init__(self) -> None:
            self.session = FakeSession()

        def __call__(self):
            return self.session

    factory = Factory()
    service = SandboxModelToolService(mode="inmemory", session_factory=factory)
    authorized = await service._authorize(
        {
            "configurable": {
                "thread_id": "conversation-42",
                "auth_session_id": "login-session-9",
                "user_id": "user-1",
                "workspace_id": "workspace-1",
            }
        }
    )

    assert authorized is not None
    assert factory.session.requested_session_id == "login-session-9"


@pytest.mark.asyncio
async def test_model_scratch_does_not_share_interactive_kernel() -> None:
    from server.sandbox.model_tools import SandboxModelToolService

    service = SandboxModelToolService(mode="inmemory")
    context = _config()
    from server.sandbox.confirmation import SandboxConfirmationSigner

    token = SandboxConfirmationSigner(service._signing_secret).issue(
        user_id="local",
        session_id="model-session",
        tool_name="sandbox_run_active_kernel",
        code_hash=hashlib.sha256(
            "answer = 41\nprint(answer + 1)".encode("utf-8")
        ).hexdigest(),
    )

    active = await service.run_active(
        source="answer = 41\nprint(answer + 1)",
        config=context,
        confirmed=True,
        confirmation_token=token,
    )
    scratch = await service.run_scratch(source="print('answer' in globals())", config=context)

    assert active["ok"] is True
    assert "42" in active["stdout"]
    replayed = await service.run_active(
        source="answer = 41\nprint(answer + 1)",
        config=context,
        confirmed=True,
        confirmation_token=token,
    )
    assert replayed["code"] == "confirmation_required"
    assert scratch["ok"] is True
    assert scratch["stdout"].strip() == "False"
    assert scratch["execution_id"]
    explanation = await service.explain_execution(
        execution_id=str(scratch["execution_id"]), config=context
    )
    assert explanation["ok"] is True
    assert explanation["execution"]["status"] == "completed"
    assert any(event["type"] == "execution.completed" for event in explanation["events"])


@pytest.mark.asyncio
async def test_model_explain_execution_is_bound_to_workspace() -> None:
    from server.sandbox.model_tools import SandboxModelToolService

    service = SandboxModelToolService(mode="inmemory")
    created = await service.run_scratch(source="print(1)", config=_config(workspace_id="workspace-a"))

    explanation = await service.explain_execution(
        execution_id=str(created["execution_id"]),
        config=_config(workspace_id="workspace-b"),
    )

    assert explanation["ok"] is False
    assert explanation["code"] == "not_found"
    assert explanation["error"] == "sandbox execution was not found"


@pytest.mark.asyncio
async def test_failed_model_result_emits_failed_event() -> None:
    from server.sandbox.model_tools import SandboxModelToolService

    class FailedManager:
        async def run_scratch(self, **_kwargs: object) -> dict[str, object]:
            return {"status": "failed", "stdout": "partial", "stderr": "NameError: missing"}

    service = SandboxModelToolService(mode="docker", manager=FailedManager())
    result = await service.run_scratch(source="print(missing)", config=_config())
    explanation = await service.explain_execution(
        execution_id=str(result["execution_id"]), config=_config()
    )

    assert result["ok"] is True
    assert result["status"] == "failed"
    assert explanation["execution"]["status"] == "failed"
    event_types = [event["type"] for event in explanation["events"]]
    assert "execution.failed" in event_types
    assert "execution.completed" not in event_types


@pytest.mark.asyncio
async def test_started_event_failure_closes_execution_as_failed(monkeypatch) -> None:
    """A Redis outage while opening an execution must not leave it running."""

    from server.sandbox import model_tools
    from server.sandbox.model_tools import SandboxModelToolService

    class FailingEventStore:
        async def append(self, _execution_id, *, event_type, **_kwargs):
            if event_type == "execution.started":
                raise ConnectionError("redis unavailable")
            return {"event_id": event_type, "seq": 1, "type": event_type, "payload": {}}

    monkeypatch.setattr(model_tools, "default_sandbox_event_store", FailingEventStore())
    service = SandboxModelToolService(mode="inmemory")

    result = await service.run_scratch(source="print(1)", config=_config())

    assert result["ok"] is False
    assert result["code"] == "sandbox_event_failed"
    assert service._local_executions
    execution = next(iter(service._local_executions.values()))
    assert execution["status"] == "failed"


@pytest.mark.asyncio
async def test_terminal_event_failure_does_not_report_completed(monkeypatch) -> None:
    """A terminal event delivery failure must converge the audit row to failed."""

    from server.sandbox import model_tools
    from server.sandbox.model_tools import SandboxModelToolService

    class TerminalEventStore:
        def __init__(self) -> None:
            self.events: list[str] = []

        async def append(self, _execution_id, *, event_type, **_kwargs):
            if event_type == "execution.completed":
                raise ConnectionError("redis unavailable")
            self.events.append(event_type)
            return {"event_id": event_type, "seq": len(self.events), "type": event_type, "payload": {}}

        async def replay(self, *_args, **_kwargs):
            return [{"type": item} for item in self.events if item != "execution.completed"]

    store = TerminalEventStore()
    monkeypatch.setattr(model_tools, "default_sandbox_event_store", store)
    service = SandboxModelToolService(mode="inmemory")

    result = await service.run_scratch(source="print(1)", config=_config())

    assert result["ok"] is False
    assert result["code"] == "sandbox_event_failed"
    execution = next(iter(service._local_executions.values()))
    assert execution["status"] == "failed"
    assert "execution.completed" not in store.events


@pytest.mark.asyncio
async def test_active_kernel_requires_explicit_confirmation() -> None:
    from server.sandbox.model_tools import SandboxModelToolService

    service = SandboxModelToolService(mode="inmemory")

    result = await service.run_active(source="print(1)", config=_config(), confirmed=False)

    assert result == {
        "ok": False,
        "code": "confirmation_required",
        "error": "sandbox_run_active_kernel requires explicit user confirmation",
    }


def test_confirmation_token_is_bound_to_user_session_tool_and_code() -> None:
    from server.sandbox.confirmation import SandboxConfirmationSigner

    signer = SandboxConfirmationSigner("test-secret")
    token = signer.issue(
        user_id="local", session_id="model-session", tool_name="sandbox_reset", code_hash=""
    )
    signer.verify(
        token,
        user_id="local",
        session_id="model-session",
        tool_name="sandbox_reset",
        code_hash="",
    )
    with pytest.raises(PermissionError):
        signer.verify(
            token,
            user_id="local",
            session_id="model-session",
            tool_name="sandbox_run_active_kernel",
            code_hash="deadbeef",
        )


@pytest.mark.asyncio
async def test_active_kernel_rejects_boolean_without_server_confirmation_token() -> None:
    from server.sandbox.model_tools import SandboxModelToolService

    service = SandboxModelToolService(mode="inmemory")
    result = await service.run_active(
        source="print(1)", config=_config(), confirmed=True, confirmation_token=None
    )
    assert result["code"] == "confirmation_required"


def test_model_tool_contracts_are_registered_and_hide_runtime_config() -> None:
    from core.tool_registry import physical_tool_manager

    expected = {
        "sandbox_status",
        "sandbox_run_scratch",
        "sandbox_explain_execution",
        "sandbox_interrupt_own",
        "sandbox_run_active_kernel",
        "sandbox_reset",
    }

    assert expected.issubset({item.name for item in physical_tool_manager.runtime.catalog.descriptors()})
    for name in expected:
        descriptor = physical_tool_manager.runtime.catalog.get(name)
        assert descriptor is not None
        assert "config" not in descriptor.instantiate().args_schema.model_json_schema()["properties"]


def test_model_sandbox_helpers_are_event_loop_safe() -> None:
    from server.sandbox.model_tools import SandboxModelToolService

    service = SandboxModelToolService(mode="inmemory")
    result = asyncio.run(service.run_scratch(source="print(2 + 2)", config=_config()))

    assert result["stdout"].strip() == "4"


def test_model_tool_fallback_signing_secret_is_ephemeral(monkeypatch) -> None:
    from configs.settings import settings
    from server.sandbox.model_tools import SandboxModelToolService

    monkeypatch.setattr(settings, "NLP_AGENT_WEB_SECRET", "")
    first = SandboxModelToolService(mode="inmemory")
    second = SandboxModelToolService(mode="inmemory")
    assert first._signing_secret
    assert first._signing_secret != second._signing_secret
