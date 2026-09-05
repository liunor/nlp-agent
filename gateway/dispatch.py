"""Turn-dispatch boundary with the current in-process adapter."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from core.learning import ExerciseState, LearningContext, LearningProgress, TeachingMaterials
from core.session_context import SessionContext


@dataclass(frozen=True, slots=True)
class ExecutionAuthorizationContext:
    """Submitter identity carried across dispatch boundaries, including Redis."""

    submitter_user_id: str
    workspace_id: str
    authorization_version: int


@dataclass(frozen=True, slots=True)
class TurnTask:
    """A persisted turn ready for execution by a dispatcher."""

    context: SessionContext
    turn_id: str
    content: str
    learning_context: LearningContext | None
    learning_progress: LearningProgress | None
    exercise_state: ExerciseState | None
    teaching_materials: TeachingMaterials
    guided_session_id: str | None
    exercise_session_id: str | None
    model_profile: str | None = None
    authorization: ExecutionAuthorizationContext | None = None
    reservation_id: str | None = None


class TurnDispatcher(Protocol):
    """Schedules persisted turn work without exposing an execution transport."""

    async def submit(self, task: TurnTask) -> None: ...

    async def cancel(self, turn_id: str) -> None: ...

    async def close(self, *, force: bool = False, grace_s: float = 0) -> None: ...

    def active_count(self) -> int: ...


TurnTaskHandler = Callable[[TurnTask], Awaitable[None]]


class InProcessTurnDispatcher:
    """Memory adapter that preserves the current single-process asyncio behavior."""

    def __init__(self, execute: TurnTaskHandler) -> None:
        self._execute = execute
        self._tasks: dict[str, asyncio.Task[None]] = {}

    async def submit(self, task: TurnTask) -> None:
        future = asyncio.create_task(
            self._execute(task), name=f"gateway-turn:{task.turn_id}"
        )
        self._tasks[task.turn_id] = future
        future.add_done_callback(
            lambda completed, turn_id=task.turn_id: self._discard(turn_id, completed)
        )

    def _discard(self, turn_id: str, completed: asyncio.Task[None]) -> None:
        if self._tasks.get(turn_id) is completed:
            self._tasks.pop(turn_id, None)

    async def cancel(self, turn_id: str) -> None:
        task = self._tasks.get(turn_id)
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def close(self, *, force: bool = False, grace_s: float = 0) -> None:
        tasks = [task for task in self._tasks.values() if not task.done()]
        pending = set(tasks)
        if pending and not force and grace_s > 0:
            _done, pending = await asyncio.wait(pending, timeout=grace_s)
        for task in pending:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()

    def active_count(self) -> int:
        return sum(not task.done() for task in self._tasks.values())
