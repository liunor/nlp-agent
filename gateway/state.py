"""Persistence ports shared by Web and independently deployed Workers."""

from __future__ import annotations

from typing import Any, Protocol

from core.learning import ExerciseState
from gateway.contracts import GatewayEvent, GatewayEventType, TurnRecord, TurnStatus


class TurnExecutionState(Protocol):
    """Worker-facing state port; MSSQL can replace SQLite behind this interface."""

    def update_turn(
        self,
        turn_id: str,
        status: TurnStatus,
        *,
        final_text: str | None = None,
        error_kind: str | None = None,
        error_message: str | None = None,
        exercise_state: ExerciseState | None = None,
        expected_claim_generation: int | None = None,
    ) -> TurnRecord: ...

    def get_turn(self, turn_id: str) -> TurnRecord | None: ...

    def append_event(
        self,
        *,
        turn_id: str,
        session_id: str,
        event_type: GatewayEventType,
        payload: dict[str, Any] | None = None,
        expected_claim_generation: int | None = None,
    ) -> GatewayEvent: ...

    def ensure_event(
        self,
        *,
        turn_id: str,
        session_id: str,
        event_type: GatewayEventType,
        payload: dict[str, Any] | None = None,
        expected_claim_generation: int | None = None,
    ) -> GatewayEvent: ...

    def advance_guided_session(self, guided_session_id: str, **changes: Any) -> dict[str, Any]: ...
    def update_turn_guided_status(self, turn_id: str, *, status: str) -> None: ...
    def record_exercise_question(self, exercise_session_id: str, question: str) -> dict[str, Any]: ...
    def exercise_state(self, exercise_session_id: str) -> ExerciseState: ...
    def grade_exercise_answer(self, exercise_session_id: str, **result: Any) -> ExerciseState: ...
    def close(self) -> None: ...
