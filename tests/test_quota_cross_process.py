"""Unit and integration tests for cross-process attribution and task boundaries (Milestone 4)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.learning import TeachingMaterials
from core.model_runtime.usage import (
    UsageAttributionContext,
    bind_usage_attribution,
    current_usage_attribution,
)
from core.session_context import SessionContext
from gateway.dispatch import ExecutionAuthorizationContext, TurnTask
from gateway.redis_transport import TurnTaskCodec
from gateway.turn_execution import InProcessTurnExecutor


def test_turn_task_carries_reservation_id_and_request_id():
    """Feature 17: TurnTask accepts and carries reservation_id and request_id."""
    context = SessionContext(session_id="session-42", user_id="user-42", workspace_id="ws-42")
    auth = ExecutionAuthorizationContext(
        submitter_user_id="user-42",
        workspace_id="ws-42",
        authorization_version=1,
    )
    task = TurnTask(
        context=context,
        turn_id="turn-42",
        content="hello quota",
        learning_context=None,
        learning_progress=None,
        exercise_state=None,
        teaching_materials=TeachingMaterials(),
        guided_session_id=None,
        exercise_session_id=None,
        model_profile="deepseek",
        authorization=auth,
        reservation_id="res-abc-123",
        request_id="req-xyz-789",
    )
    assert task.reservation_id == "res-abc-123"
    assert task.request_id == "req-xyz-789"
    assert task.turn_id == "turn-42"
    assert task.context.user_id == "user-42"


def test_turn_task_codec_roundtrip_with_reservation_and_request_id():
    """Feature 18: TurnTaskCodec serializes and deserializes reservation_id and request_id."""
    context = SessionContext(session_id="session-99", user_id="user-99", workspace_id="ws-99")
    task = TurnTask(
        context=context,
        turn_id="turn-99",
        content="task payload with reservation",
        learning_context=None,
        learning_progress=None,
        exercise_state=None,
        teaching_materials=TeachingMaterials(),
        guided_session_id=None,
        exercise_session_id=None,
        model_profile="qwen",
        reservation_id="res-999-000",
        request_id="req-999-111",
    )
    dumped = TurnTaskCodec.dumps(task)
    data = json.loads(dumped)
    assert data["reservation_id"] == "res-999-000"
    assert data["request_id"] == "req-999-111"

    restored = TurnTaskCodec.loads(dumped)
    assert restored.reservation_id == "res-999-000"
    assert restored.request_id == "req-999-111"
    assert restored.turn_id == task.turn_id
    assert restored.content == task.content


def test_turn_task_codec_backward_compatibility_when_reservation_missing():
    """Feature 18: TurnTaskCodec cleanly handles legacy payloads without reservation_id."""
    legacy_json = json.dumps({
        "version": TurnTaskCodec.VERSION,
        "context": {"session_id": "session-old", "user_id": "user-old"},
        "turn_id": "turn-old",
        "content": "legacy payload",
        "learning_context": None,
        "learning_progress": None,
        "exercise_state": None,
        "teaching_materials": {},
        "guided_session_id": None,
        "exercise_session_id": None,
        "model_profile": None,
        "authorization": None,
    })
    restored = TurnTaskCodec.loads(legacy_json)
    assert restored.turn_id == "turn-old"
    assert restored.reservation_id is None
    assert restored.request_id is None


@pytest.mark.asyncio
async def test_in_process_turn_executor_binds_usage_attribution():
    """Feature 19: InProcessTurnExecutor._run_engine binds UsageAttributionContext during execution."""
    captured_attribution = []

    class FakeEngine:
        async def run_turn(self, context, turn_id, content, **kwargs):
            current = current_usage_attribution()
            captured_attribution.append(current)
            return "Execution complete"

        async def cancel_turn(self, context, turn_id):
            pass

    fake_engine = FakeEngine()
    fake_repository = MagicMock()
    fake_repository.update_turn = MagicMock()
    fake_repository.update_turn_guided_status = MagicMock()
    fake_emit = AsyncMock()

    executor = InProcessTurnExecutor(fake_engine, fake_repository, fake_emit)

    context = SessionContext(session_id="sess-run-1", user_id="user-run-1", workspace_id="ws-run-1")
    task = TurnTask(
        context=context,
        turn_id="turn-run-1",
        content="run prompt",
        learning_context=None,
        learning_progress=None,
        exercise_state=None,
        teaching_materials=TeachingMaterials(),
        guided_session_id=None,
        exercise_session_id=None,
        reservation_id="res-run-1",
        request_id="req-run-1",
    )

    await executor.run(task)

    assert len(captured_attribution) == 1
    attr = captured_attribution[0]
    assert attr is not None
    assert attr.reservation_id == "res-run-1"
    assert attr.request_id == "req-run-1"
    assert attr.turn_id == "turn-run-1"
    assert attr.user_id == "user-run-1"
    assert attr.workspace_id == "ws-run-1"
    assert attr.purpose == "coordinator"

    assert current_usage_attribution() is None


@pytest.mark.asyncio
async def test_worker_tool_inherits_parent_reservation_attribution():
    """Feature 20: Worker sandbox inherits parent reservation_id with purpose='worker'."""
    from unittest.mock import patch
    from server.tools.worker_tool import _execute_sandbox_loop
    from langchain_core.messages import AIMessage

    parent_attr = UsageAttributionContext(
        request_id="req-parent-1",
        user_id="user-parent",
        workspace_id="ws-parent",
        conversation_id="session-parent",
        turn_id="turn-parent",
        reservation_id="res-parent-999",
        purpose="coordinator",
    )

    captured_worker_attr = []

    class FakeLLM:
        context_window_tokens = 4000
        max_output_tokens = 1000

        def bind_tools(self, tools):
            return self

        async def ainvoke(self, messages):
            captured_worker_attr.append(current_usage_attribution())
            return AIMessage(content="Worker executed successfully")

    with bind_usage_attribution(parent_attr):
        with patch("server.tools.worker_tool.get_worker_llm", return_value=FakeLLM()), \
             patch("server.tools.worker_tool.get_tool_llm", return_value=FakeLLM()), \
             patch("server.tools.worker_tool.global_context_manager.prepare") as mock_prep:
            mock_view = MagicMock()
            mock_view.messages = []
            mock_view.tokens_before = 10
            mock_view.tokens_after = 10
            mock_view.actions = []
            mock_prep.return_value = mock_view

            context = SessionContext(session_id="session-parent", user_id="user-parent", workspace_id="ws-parent")
            res = await _execute_sandbox_loop(
                worker_id="worker-child-1",
                session_id="session-parent",
                messages=[],
                toolset=[],
                model_name="worker-qwen-plus",
                context=context,
            )

            assert res.status == "completed"

    assert len(captured_worker_attr) >= 1
    w_attr = captured_worker_attr[0]
    assert w_attr is not None
    assert w_attr.reservation_id == "res-parent-999"
    assert w_attr.worker_id == "worker-child-1"
    assert w_attr.purpose == "worker"
    assert w_attr.turn_id == "turn-parent"
