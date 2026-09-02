"""In-process execution implementation behind the turn-dispatch port."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import re
from collections.abc import Awaitable, Callable
from typing import Any

from gateway.contracts import GatewayEventType, TurnStatus
from core.rbac import Permission
from gateway.dispatch import TurnTask
from gateway.engine import AgentEngine
from gateway.state import TurnExecutionState
from core.model_runtime.usage import UsageAttributionContext, bind_usage_attribution
from server.quota.contracts import FinishTurn


EventSink = Callable[[str, str, GatewayEventType, dict], Awaitable[None]]
_EXERCISE_RESULT_RE = re.compile(r"<!--\s*exercise-result:\s*(\{.*?\})\s*-->", re.DOTALL)
_GUIDED_RESULT_RE = re.compile(r"<!--\s*guided-result:\s*(\{.*?\})\s*-->", re.DOTALL)


def _extract_result(pattern: re.Pattern[str], text: str) -> tuple[str, dict[str, Any] | None]:
    match = pattern.search(text)
    if match is None:
        return text.strip(), None
    try:
        value = json.loads(match.group(1))
    except json.JSONDecodeError:
        return pattern.sub("", text).strip(), None
    return pattern.sub("", text).strip(), value if isinstance(value, dict) else None


class InProcessTurnExecutor:
    """Runs and finalizes turn work for the memory dispatcher."""

    def __init__(
        self,
        engine: AgentEngine,
        repository: TurnExecutionState,
        emit: EventSink,
        on_turn_completed: Callable[[str], None] | None = None,
    ) -> None:
        self._engine = engine
        self._repository = repository
        self._emit = emit
        self._on_turn_completed = on_turn_completed
        parameters = inspect.signature(engine.run_turn).parameters
        parameter_count = len(parameters)
        self._accepts_learning = parameter_count >= 6
        self._accepts_teaching_materials = parameter_count >= 7
        self._accepts_model_profile = "model_profile" in parameters

    async def run(self, task: TurnTask, execution_context: Any | None = None) -> None:
        await asyncio.to_thread(self._repository.update_turn, task.turn_id, TurnStatus.RUNNING)
        await self._emit(task.turn_id, task.context.session_id, GatewayEventType.TURN_STARTED, {"status": TurnStatus.RUNNING.value})
        quota_heartbeat_task: asyncio.Task[None] | None = None
        try:
            service = getattr(self._repository, "quota_service", None)
            if service is not None and task.reservation_id is not None:
                started = await asyncio.to_thread(
                    service.begin_reservation,
                    task.reservation_id,
                )
                if not started:
                    raise RuntimeError("quota reservation is no longer active")
                quota_heartbeat_task = asyncio.create_task(
                    self._quota_heartbeat(task.reservation_id, service),
                    name=f"quota-lease:{task.turn_id}",
                )
            # A fenced Worker passes its freshly-resolved authorization context.
            # Re-check immediately before the model/tool execution boundary.
            if execution_context is not None and hasattr(execution_context, "require"):
                execution_context.require(Permission.AGENT_TURN_SUBMIT)
            attribution = UsageAttributionContext(
                request_id=task.turn_id,
                user_id=task.context.user_id,
                workspace_id=task.context.workspace_id,
                conversation_id=task.context.session_id,
                turn_id=task.turn_id,
                reservation_id=task.reservation_id,
                worker_id=getattr(execution_context, "worker_id", None),
                purpose=(
                    "worker"
                    if getattr(execution_context, "worker_id", None)
                    else "coordinator"
                ),
            )
            with bind_usage_attribution(attribution):
                final_text = await self._run_engine(task)
                final_text, exercise_state = await self._finalize_learning(task, final_text)
                await asyncio.to_thread(
                    self._repository.update_turn,
                    task.turn_id,
                    TurnStatus.COMPLETED,
                    final_text=final_text,
                    exercise_state=exercise_state,
                )
        except asyncio.CancelledError:
            await self._engine.cancel_turn(task.context, task.turn_id)
            await asyncio.to_thread(self._repository.update_turn, task.turn_id, TurnStatus.CANCELLED)
            await self._emit(task.turn_id, task.context.session_id, GatewayEventType.TURN_CANCELLED, {"status": TurnStatus.CANCELLED.value})
            await self._finish_quota(task)
            raise
        except Exception as error:
            await asyncio.to_thread(self._repository.update_turn, task.turn_id, TurnStatus.FAILED, error_kind=type(error).__name__, error_message=str(error))
            await self._emit(task.turn_id, task.context.session_id, GatewayEventType.TURN_FAILED, {"status": TurnStatus.FAILED.value, "error_kind": type(error).__name__, "message": str(error)[:500]})
            await self._finish_quota(task)
            return
        finally:
            if quota_heartbeat_task is not None:
                quota_heartbeat_task.cancel()
                await asyncio.gather(quota_heartbeat_task, return_exceptions=True)
        await self._finish_quota(task)
        await self._emit(task.turn_id, task.context.session_id, GatewayEventType.MESSAGE_COMPLETED, {"content": final_text})
        await self._emit(task.turn_id, task.context.session_id, GatewayEventType.TURN_COMPLETED, {"status": TurnStatus.COMPLETED.value, "content": final_text})
        if self._on_turn_completed is not None:
            self._on_turn_completed(task.context.session_id)

    async def _finish_quota(self, task: TurnTask) -> None:
        service = getattr(self._repository, "quota_service", None)
        if service is None or task.reservation_id is None:
            return
        await asyncio.to_thread(
            service.finish_turn,
            FinishTurn(
                reservation_id=task.reservation_id,
                turn_id=task.turn_id,
                idempotency_key=f"turn-finished:{task.turn_id}",
            ),
        )

    async def _quota_heartbeat(self, reservation_id: str, service: Any) -> None:
        interval = max(1.0, float(getattr(service, "lease_seconds", 300)) / 3)
        while True:
            await asyncio.sleep(interval)
            active = await asyncio.to_thread(service.heartbeat, reservation_id)
            if not active:
                return

    async def _run_engine(self, task: TurnTask) -> str:
        kwargs: dict[str, Any] = {}
        if self._accepts_learning:
            kwargs.update(
                learning_context=task.learning_context,
                learning_progress=task.learning_progress,
                exercise_state=task.exercise_state,
            )
        if self._accepts_teaching_materials:
            kwargs["teaching_materials"] = task.teaching_materials
        if self._accepts_model_profile:
            kwargs["model_profile"] = task.model_profile
        return await self._engine.run_turn(
            task.context, task.turn_id, task.content, **kwargs
        )

    async def _finalize_learning(self, task: TurnTask, final_text: str) -> tuple[str, object]:
        if task.guided_session_id is not None:
            final_text, guided_result = _extract_result(_GUIDED_RESULT_RE, final_text)
            await asyncio.to_thread(self._repository.advance_guided_session, task.guided_session_id, tutor_message=final_text, known_concepts=(guided_result or {}).get("known_concepts"), misconceptions=(guided_result or {}).get("misconceptions"), completed=(guided_result or {}).get("status") == "completed")
            await asyncio.to_thread(self._repository.update_turn_guided_status, task.turn_id, status="completed" if (guided_result or {}).get("status") == "completed" else "active")
        exercise_state = task.exercise_state
        if task.exercise_session_id is not None and exercise_state is not None:
            final_text, result = _extract_result(_EXERCISE_RESULT_RE, final_text)
            try:
                if exercise_state.status == "idle":
                    question = str((result or {}).get("question") or final_text).strip()
                    await asyncio.to_thread(self._repository.record_exercise_question, task.exercise_session_id, question)
                    exercise_state = await asyncio.to_thread(self._repository.exercise_state, task.exercise_session_id)
                elif exercise_state.status == "awaiting_answer" and result and result.get("kind") == "grading":
                    matches = result.get("matches")
                    if not isinstance(matches, list):
                        raise ValueError("grading result must contain rubric matches")
                    exercise_state = await asyncio.to_thread(self._repository.grade_exercise_answer, task.exercise_session_id, answer=task.content, matches=matches, feedback=str(result.get("feedback") or ""))
            except ValueError:
                pass
        return final_text, exercise_state
