"""Lifecycle-owning Backend Gateway Core; HTTP frameworks adapt to this class."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import re
import uuid
from collections import defaultdict
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from core.identity import AccessDeniedError, AuthenticatedPrincipal
from core.session_context import SessionContext
from core.learning import ExerciseState, LearningContext, TeachingMaterials, default_progress
from gateway.contracts import (
    GatewayEvent,
    GatewayEventType,
    GatewayHealth,
    GatewayNotStartedError,
    InjectMessageRequest,
    ResourceNotFoundError,
    SubmitTurnRequest,
    TeachingConfigurationError,
    TurnAccepted,
    TurnConflictError,
    TurnRecord,
    TurnStatus,
)
from gateway.engine import AgentEngine, LangGraphAgentEngine
from gateway.events import GatewayEventBroker, GatewayEventSubscription
from gateway.repository import GatewayRepository
from server.agent.session_service import LocalSessionService, local_session_service
from typing import AsyncGenerator
from security.content_guard import get_content_guard
from security.audit import get_audit_logger
from security.content_guard import get_content_guard
from security.prompt_guard import get_prompt_guard
from security.audit import get_audit_logger

_EXERCISE_RESULT_RE = re.compile(r"<!--\s*exercise-result:\s*(\{.*?\})\s*-->", re.DOTALL)
_GUIDED_RESULT_RE = re.compile(r"<!--\s*guided-result:\s*(\{.*?\})\s*-->", re.DOTALL)
_EXPLICIT_EXERCISE_START_RE = re.compile(
    r"(?:开始|继续|新(?:的)?|再来|下一).{0,8}(?:练习|复习|题)|(?:练习|复习).{0,8}(?:开始|继续|下一题)",
    re.IGNORECASE,
)


def _extract_exercise_result(text: str) -> tuple[str, dict[str, Any] | None]:
    """Remove the hidden model envelope and return its validated JSON object when present."""
    match = _EXERCISE_RESULT_RE.search(text)
    if match is None:
        return text.strip(), None
    try:
        value = json.loads(match.group(1))
    except json.JSONDecodeError:
        return text.strip(), None
    return _EXERCISE_RESULT_RE.sub("", text).strip(), value if isinstance(value, dict) else None


def _extract_guided_result(text: str) -> tuple[str, dict[str, Any] | None]:
    match = _GUIDED_RESULT_RE.search(text)
    if match is None:
        return text.strip(), None
    try:
        value = json.loads(match.group(1))
    except json.JSONDecodeError:
        return _GUIDED_RESULT_RE.sub("", text).strip(), None
    return _GUIDED_RESULT_RE.sub("", text).strip(), value if isinstance(value, dict) else None


def _is_explicit_exercise_start(content: str) -> bool:
    return bool(_EXPLICIT_EXERCISE_START_RE.search(content.strip()))


class BackendGateway:
    """The single writer and lifecycle owner for all backend Agent traffic."""

    def __init__(
        self,
        *,
        engine: AgentEngine | None = None,
        repository: GatewayRepository | None = None,
        sessions: LocalSessionService = local_session_service,
        shutdown_grace_s: float | None = None,
        event_retention_days: int | None = None,
        max_events_per_session: int | None = None,
        retention_cleanup_interval_s: float | None = None,
    ) -> None:
        self.content_guard = get_content_guard()
        self.prompt_guard = get_prompt_guard()
        self.audit = get_audit_logger()
        project = Path(__file__).resolve().parent.parent
        from configs.settings import settings

        gateway_config = settings.gateway_runtime
        database = Path(gateway_config.get("database", ".data/gateway/gateway.sqlite3"))
        if not database.is_absolute():
            database = project / database
        self.engine = engine or LangGraphAgentEngine()
        engine_parameter_count = len(inspect.signature(self.engine.run_turn).parameters)
        self._engine_accepts_learning = engine_parameter_count >= 6
        self._engine_accepts_teaching_materials = engine_parameter_count >= 7
        self.repository = repository or GatewayRepository(
            database,
            knowledge_point_prompt_budget=max(
                1, int(gateway_config.get("knowledge_point_prompt_budget", 12_000))
            ),
        )
        self.sessions = sessions
        self.events = GatewayEventBroker()
        self._turn_tasks: dict[str, asyncio.Task[None]] = {}
        self._session_turn_locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self.shutdown_grace_s = max(
            0.0,
            float(
                shutdown_grace_s
                if shutdown_grace_s is not None
                else gateway_config.get("shutdown_grace_s", 30)
            ),
        )
        self.event_retention_days = max(
            1,
            int(
                event_retention_days
                if event_retention_days is not None
                else gateway_config.get("event_retention_days", 7)
            ),
        )
        self.max_events_per_session = max(
            1,
            int(
                max_events_per_session
                if max_events_per_session is not None
                else gateway_config.get("max_events_per_session", 50_000)
            ),
        )
        self.retention_cleanup_interval_s = max(
            1.0,
            float(
                retention_cleanup_interval_s
                if retention_cleanup_interval_s is not None
                else gateway_config.get("retention_cleanup_interval_s", 3_600)
            ),
        )
        self._maintenance_stop = asyncio.Event()
        self._maintenance_task: asyncio.Task[None] | None = None
        self._lifecycle_lock = asyncio.Lock()
        self._started = False
        self._accepting = False

    async def start(self) -> None:
        async with self._lifecycle_lock:
            if self._started:
                return
            await self.engine.start(self._emit_from_engine)
            interrupted = await asyncio.to_thread(self.repository.recover_interrupted)
            for turn in interrupted:
                await self._emit(
                    turn.turn_id,
                    turn.session_id,
                    GatewayEventType.TURN_FAILED,
                    {"status": "interrupted", "error_kind": "gateway_restart"},
                )
            await self.prune_events()
            self._maintenance_stop.clear()
            self._maintenance_task = asyncio.create_task(
                self._event_maintenance_loop(),
                name="gateway-event-retention",
            )
            self._started = True
            self._accepting = True

    async def _event_maintenance_loop(self) -> None:
        while not self._maintenance_stop.is_set():
            try:
                await asyncio.wait_for(
                    self._maintenance_stop.wait(),
                    timeout=self.retention_cleanup_interval_s,
                )
            except asyncio.TimeoutError:
                await self.prune_events()

    async def prune_events(self) -> dict[str, int]:
        return await asyncio.to_thread(
            self.repository.prune_events,
            retention_days=self.event_retention_days,
            max_events_per_session=self.max_events_per_session,
        )

    async def begin_shutdown(self) -> None:
        """Enter draining state before network channels are stopped."""
        async with self._lifecycle_lock:
            self._accepting = False

    def _require_started(self) -> None:
        if not self._started or not self._accepting:
            raise GatewayNotStartedError("Backend Gateway is not accepting turns")

    async def create_session(
        self,
        principal: AuthenticatedPrincipal,
        *,
        workspace_id: str = "default",
        channel: str = "web",
    ) -> SessionContext:
        self._require_started()
        return await self.sessions.create(
            principal, workspace_id=workspace_id, channel=channel
        )

    async def submit_turn(
        self, principal: AuthenticatedPrincipal, request: SubmitTurnRequest
    ) -> TurnAccepted:
        self._require_started()

        user_input = request.content
        session_id = request.session_id or "unknown"
        # 1. 内容安全审核
        is_safe, reason, score = self.content_guard.validate_input(user_input)
        if not is_safe:
            self.audit.log_security_event("content_block", {
                "session": session_id,
                "user": principal.user_id,
                "input": user_input[:100],
                "reason": reason
            })
            raise ValueError(f"内容违规: {reason}")

        # 2. 提示词注入检测
        is_safe, threat, score = self.prompt_guard.scan_user_input(user_input)
        if not is_safe:
            self.audit.log_security_event("prompt_injection", {
                "session": session_id,
                "user": principal.user_id,
                "threat": threat
            })
            raise ValueError(f"检测到注入攻击: {threat}")

        async with self._session_turn_locks[request.session_id]:
            return await self._submit_turn_locked(principal, request)

    async def _submit_turn_locked(
        self, principal: AuthenticatedPrincipal, request: SubmitTurnRequest
    ) -> TurnAccepted:
        context = await self.sessions.resolve(principal, request.session_id)
        if request.evaluation is not None:
            context = context.model_copy(
                update={"observability_attributes": request.evaluation.trace_attributes()}
            )
        await self.sessions.touch(principal, request.session_id)
        if request.idempotency_key:
            existing = await asyncio.to_thread(
                self.repository.turn_for_idempotency,
                user_id=context.user_id,
                session_id=context.session_id,
                idempotency_key=request.idempotency_key,
            )
            if existing is not None:
                return TurnAccepted(
                    turn_id=existing.turn_id,
                    session_id=existing.session_id,
                    status=existing.status,
                    duplicate=True,
                )
        active = await asyncio.to_thread(
            self.repository.active_turn_for_session, context.session_id
        )
        if active is not None:
            raise TurnConflictError(active.turn_id)
        previous_context, progress, exercise = await asyncio.to_thread(
            self.repository.latest_learning_state, context.session_id
        )
        await asyncio.to_thread(self.repository.expire_guided_sessions, session_id=context.session_id)
        await asyncio.to_thread(self.repository.expire_exercise_sessions, session_id=context.session_id)
        learning_context = request.learning_context or previous_context
        topic_changed = (
            request.learning_context is not None
            and previous_context is not None
            and request.learning_context.topic_id != previous_context.topic_id
        )
        mode_changed = (
            request.learning_context is not None
            and previous_context is not None
            and request.learning_context.mode != previous_context.mode
        )
        level_changed = (
            request.learning_context is not None
            and previous_context is not None
            and request.learning_context.level != previous_context.level
        )
        if topic_changed:
            progress = None
        if topic_changed or mode_changed or level_changed:
            exercise = None
        if previous_context is not None and previous_context.mode == "socratic" and (topic_changed or mode_changed):
            await asyncio.to_thread(self.repository.end_guided_sessions, session_id=context.session_id)
        if previous_context is not None and previous_context.mode in {"practice", "review"} and (topic_changed or mode_changed or level_changed):
            await asyncio.to_thread(self.repository.end_exercise_sessions, session_id=context.session_id)
        if learning_context is not None and progress is None:
            progress = default_progress(learning_context)
        learning_topic = None
        if learning_context is not None and learning_context.topic_id:
            learning_topic = await asyncio.to_thread(
                self.repository.teaching_topic,
                context.workspace_id,
                learning_context.topic_id,
            )
            if learning_topic is None:
                catalog = await asyncio.to_thread(
                    self.repository.get_teaching_catalog,
                    context.workspace_id,
                )
                if catalog["catalog"].get("topics"):
                    raise TeachingConfigurationError(
                        f"学习主题不可用：“{learning_context.topic_name or learning_context.topic_id}”，请重新选择主题。"
                    )
        teaching_session = None
        guided_session: dict[str, Any] = {}
        if learning_context is not None and learning_context.mode == "socratic":
            guided_blueprint = await asyncio.to_thread(
                self.repository.select_guided_blueprint,
                workspace_id=context.workspace_id,
                topic_id=learning_context.topic_id or "",
            ) if learning_context.topic_id else None
            guided_session = await asyncio.to_thread(
                self.repository.start_or_resume_guided_session,
                session_id=context.session_id,
                workspace_id=context.workspace_id,
                user_id=context.user_id,
                topic_id=learning_context.topic_id or "",
                first_message=request.content,
                guided_blueprint=guided_blueprint,
            )
        if learning_context is not None and learning_context.topic_id and learning_context.mode in {"practice", "review"}:
            teaching_session = await asyncio.to_thread(
                self.repository.active_exercise_session,
                session_id=context.session_id,
                topic_id=learning_context.topic_id,
                mode=learning_context.mode,
            )
            if teaching_session is None:
                latest = await asyncio.to_thread(
                    self.repository.active_or_latest_exercise_session,
                    session_id=context.session_id, topic_id=learning_context.topic_id,
                    mode=learning_context.mode,
                )
                if latest is not None and latest["status"] == "completed" and not _is_explicit_exercise_start(request.content):
                    teaching_session = latest
                else:
                    teaching_session = await asyncio.to_thread(
                        self.repository.start_exercise_session,
                        session_id=context.session_id, workspace_id=context.workspace_id,
                        user_id=context.user_id, topic_id=learning_context.topic_id,
                        mode=learning_context.mode,
                    )
            if teaching_session is None:
                mode_name = "练习" if learning_context.mode == "practice" else "复习"
                raise TeachingConfigurationError(f"该主题尚未配置{mode_name}蓝图。")
            exercise = await asyncio.to_thread(
                self.repository.exercise_state, teaching_session["id"]
            )
        blueprint = teaching_session["blueprint_snapshot"] if teaching_session else None
        teaching_materials = TeachingMaterials(
            learning_topic=learning_topic or {},
            exercise_blueprint=blueprint if learning_context and learning_context.mode == "practice" and blueprint else {},
            review_blueprint=blueprint if learning_context and learning_context.mode == "review" and blueprint else {},
            guided_session=guided_session,
            guided_blueprint=guided_session.get("guided_blueprint", {}),
        )
        turn_id = str(uuid.uuid4())
        turn, duplicate = await asyncio.to_thread(
            self.repository.create_turn,
            turn_id=turn_id,
            session_id=context.session_id,
            workspace_id=context.workspace_id,
            user_id=context.user_id,
            input_text=request.content,
            idempotency_key=request.idempotency_key,
            learning_context=learning_context,
            learning_progress=progress,
            guided_session_id=guided_session.get("id") or None,
            guided_blueprint_id=str(guided_session.get("guided_blueprint", {}).get("id") or "") or None,
            guided_blueprint_snapshot_sha256=(
                hashlib.sha256(
                    json.dumps(guided_session.get("guided_blueprint", {}), ensure_ascii=False, sort_keys=True).encode("utf-8")
                ).hexdigest()
                if guided_session.get("guided_blueprint") else None
            ),
            guided_session_attempts=int(guided_session.get("attempts") or 0),
            guided_session_status=str(guided_session.get("status") or "active"),
            exercise_state=exercise,
        )
        if duplicate:
            return TurnAccepted(
                turn_id=turn.turn_id,
                session_id=turn.session_id,
                status=turn.status,
                duplicate=True,
            )
        await self._emit(
            turn.turn_id,
            context.session_id,
            GatewayEventType.TURN_ACCEPTED,
            {"status": TurnStatus.ACCEPTED.value},
        )
        task = asyncio.create_task(
            self._run_turn(
                context, turn.turn_id, request.content, learning_context, progress,
                exercise, teaching_materials, guided_session.get("id"),
                teaching_session.get("id") if teaching_session is not None else None,
            ),
            name=f"gateway-turn:{turn.turn_id}",
        )
        self._turn_tasks[turn.turn_id] = task
        task.add_done_callback(lambda _task, tid=turn.turn_id: self._turn_tasks.pop(tid, None))
        return TurnAccepted(
            turn_id=turn.turn_id,
            session_id=context.session_id,
            status=TurnStatus.ACCEPTED,
        )

    async def _run_turn(
        self, context: SessionContext, turn_id: str, content: str,
        learning_context: LearningContext | None, progress, exercise,
        teaching_materials: TeachingMaterials, guided_session_id: str | None,
        exercise_session_id: str | None,
    ) -> None:
        await asyncio.to_thread(
            self.repository.update_turn, turn_id, TurnStatus.RUNNING
        )
        await self._emit(
            turn_id,
            context.session_id,
            GatewayEventType.TURN_STARTED,
            {"status": TurnStatus.RUNNING.value},
        )
        try:
            if self._engine_accepts_teaching_materials:
                final_text = await self.engine.run_turn(
                    context, turn_id, content,
                    learning_context=learning_context,
                    learning_progress=progress,
                    exercise_state=exercise,
                    teaching_materials=teaching_materials,
                )
            elif self._engine_accepts_learning:
                final_text = await self.engine.run_turn(
                    context, turn_id, content,
                    learning_context=learning_context,
                    learning_progress=progress,
                    exercise_state=exercise,
                )
            else:
                final_text = await self.engine.run_turn(context, turn_id, content)
        except asyncio.CancelledError:
            await self.engine.cancel_turn(context, turn_id)
            await asyncio.to_thread(
                self.repository.update_turn, turn_id, TurnStatus.CANCELLED
            )
            await self._emit(
                turn_id,
                context.session_id,
                GatewayEventType.TURN_CANCELLED,
                {"status": TurnStatus.CANCELLED.value},
            )
            raise
        except Exception as error:
            await asyncio.to_thread(
                self.repository.update_turn,
                turn_id,
                TurnStatus.FAILED,
                error_kind=type(error).__name__,
                error_message=str(error),
            )
            await self._emit(
                turn_id,
                context.session_id,
                GatewayEventType.TURN_FAILED,
                {
                    "status": TurnStatus.FAILED.value,
                    "error_kind": type(error).__name__,
                    "message": str(error)[:500],
                },
            )
            return
        is_safe, reason, score = self.content_guard.validate_output(final_text)
        if not is_safe:
            self.audit.log_security_event("output_filtered", {
                "session": context.session_id,
                "turn": turn_id,
                "reason": reason,
                "original_preview": final_text[:100]
            })
            # 替换为安全兜底回复
            final_text = "抱歉，生成的回复不符合安全规范，已拦截。"

        if guided_session_id is not None:
            final_text, guided_result = _extract_guided_result(final_text)
            await asyncio.to_thread(
                self.repository.advance_guided_session,
                guided_session_id,
                tutor_message=final_text,
                known_concepts=(guided_result or {}).get("known_concepts"),
                misconceptions=(guided_result or {}).get("misconceptions"),
                completed=(guided_result or {}).get("status") == "completed",
            )
            await asyncio.to_thread(
                self.repository.update_turn_guided_status,
                turn_id,
                status="completed" if (guided_result or {}).get("status") == "completed" else "active",
            )
        final_exercise_state = exercise
        if exercise_session_id is not None and exercise is not None:
            final_text, exercise_result = _extract_exercise_result(final_text)
            try:
                if exercise.status == "idle":
                    question = str((exercise_result or {}).get("question") or final_text).strip()
                    await asyncio.to_thread(
                        self.repository.record_exercise_question, exercise_session_id, question
                    )
                    final_exercise_state = await asyncio.to_thread(
                        self.repository.exercise_state, exercise_session_id
                    )
                elif exercise.status == "awaiting_answer" and exercise_result and exercise_result.get("kind") == "grading":
                    matches = exercise_result.get("matches")
                    if not isinstance(matches, list):
                        raise ValueError("grading result must contain rubric matches")
                    final_exercise_state = await asyncio.to_thread(
                        self.repository.grade_exercise_answer,
                        exercise_session_id, answer=content, matches=matches,
                        feedback=str(exercise_result.get("feedback") or ""),
                    )
            except ValueError:
                # The chat answer remains available, but an invalid model envelope must never mutate learning records.
                pass
        await asyncio.to_thread(
            self.repository.update_turn,
            turn_id,
            TurnStatus.COMPLETED,
            final_text=final_text,
            exercise_state=final_exercise_state,
        )
        await self._emit(
            turn_id,
            context.session_id,
            GatewayEventType.MESSAGE_COMPLETED,
            {"content": final_text},
        )
        await self._emit(
            turn_id,
            context.session_id,
            GatewayEventType.TURN_COMPLETED,
            {"status": TurnStatus.COMPLETED.value, "content": final_text},
        )

    async def inject_message(
        self, principal: AuthenticatedPrincipal, request: InjectMessageRequest
    ) -> TurnAccepted:
        self._require_started()

        user_input = request.content
        session_id = request.session_id
        is_safe, reason, score = self.content_guard.validate_input(user_input)
        if not is_safe:
            self.audit.log_security_event("content_block", {...})
            raise ValueError(f"内容违规: {reason}")
        is_safe, threat, score = self.prompt_guard.scan_user_input(user_input)
        if not is_safe:
            self.audit.log_security_event("prompt_injection", {...})
            raise ValueError(f"检测到注入攻击: {threat}")

        context = await self.sessions.resolve(principal, request.session_id)
        turn_id = await self.engine.inject(context, request.content)
        if turn_id is None:
            raise TurnConflictError("session has no active turn")
        await self._emit(
            turn_id,
            context.session_id,
            GatewayEventType.MESSAGE_INJECTED,
            {"content": request.content},
        )
        turn = await asyncio.to_thread(self.repository.get_turn, turn_id)
        return TurnAccepted(
            turn_id=turn_id,
            session_id=context.session_id,
            status=turn.status if turn else TurnStatus.RUNNING,
        )

    async def cancel_turn(
        self, principal: AuthenticatedPrincipal, turn_id: str
    ) -> TurnRecord:
        turn = await self.get_turn(principal, turn_id)
        if turn.status not in {TurnStatus.ACCEPTED, TurnStatus.RUNNING}:
            return turn
        context = await self.sessions.resolve(principal, turn.session_id)
        await self.engine.cancel_turn(context, turn_id)
        task = self._turn_tasks.get(turn_id)
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        updated = await asyncio.to_thread(self.repository.get_turn, turn_id)
        return updated or turn

    async def get_turn(
        self, principal: AuthenticatedPrincipal, turn_id: str
    ) -> TurnRecord:
        turn = await asyncio.to_thread(self.repository.get_turn, turn_id)
        if turn is None:
            raise ResourceNotFoundError(turn_id)
        if not principal.is_admin and (
            turn.user_id != principal.user_id
            or (
                "*" not in principal.workspace_ids
                and turn.workspace_id not in principal.workspace_ids
            )
        ):
            raise AccessDeniedError(turn_id)
        return turn

    async def replay_events(
        self,
        principal: AuthenticatedPrincipal,
        turn_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 500,
    ) -> list[GatewayEvent]:
        await self.get_turn(principal, turn_id)
        return await asyncio.to_thread(
            self.repository.events_after,
            turn_id,
            after_sequence=after_sequence,
            limit=limit,
        )

    async def list_turns(
        self,
        principal: AuthenticatedPrincipal,
        session_id: str,
        *,
        limit: int = 100,
    ) -> list[TurnRecord]:
        await self.sessions.resolve(principal, session_id)
        return await asyncio.to_thread(
            self.repository.list_turns,
            session_id,
            limit=limit,
        )

    async def subscribe_session_events(
        self,
        principal: AuthenticatedPrincipal,
        session_id: str,
        *,
        max_queue: int = 500,
    ) -> GatewayEventSubscription:
        self._require_started()
        await self.sessions.resolve(principal, session_id)
        return self.events.open_subscription(
            session_id=session_id,
            maxsize=max_queue,
        )

    async def latest_event_sequence(
        self,
        principal: AuthenticatedPrincipal,
        turn_id: str,
    ) -> int:
        await self.get_turn(principal, turn_id)
        return await asyncio.to_thread(
            self.repository.latest_event_sequence,
            turn_id,
        )

    async def get_user_settings(
        self,
        principal: AuthenticatedPrincipal,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self.repository.get_user_settings,
            principal.user_id,
        )

    async def update_user_settings(
        self,
        principal: AuthenticatedPrincipal,
        changes: dict[str, Any],
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self.repository.update_user_settings,
            principal.user_id,
            changes,
        )

    async def get_teaching_catalog(self, principal: AuthenticatedPrincipal, workspace_id: str) -> dict[str, Any]:
        principal.require_workspace(workspace_id)
        return await asyncio.to_thread(self.repository.get_teaching_catalog, workspace_id)

    async def update_teaching_catalog(self, principal: AuthenticatedPrincipal, workspace_id: str, catalog: dict[str, Any]) -> dict[str, Any]:
        principal.require_workspace(workspace_id)
        return await asyncio.to_thread(self.repository.update_teaching_catalog, workspace_id, catalog)

    async def stream_events(
        self,
        principal: AuthenticatedPrincipal,
        turn_id: str,
        *,
        after_sequence: int = 0,
        max_queue: int = 500,
    ) -> AsyncIterator[GatewayEvent]:
        turn = await self.get_turn(principal, turn_id)
        subscription_id, queue = self.events.subscribe(
            turn_id=turn_id, maxsize=max_queue
        )
        last_sequence = max(0, after_sequence)
        try:
            while True:
                history = await self.replay_events(
                    principal, turn_id, after_sequence=last_sequence, limit=2000
                )
                for event in history:
                    last_sequence = event.sequence
                    yield event
                if len(history) < 2000:
                    break
            if any(event.type in {
                GatewayEventType.TURN_COMPLETED,
                GatewayEventType.TURN_FAILED,
                GatewayEventType.TURN_CANCELLED,
            } for event in history):
                return
            current_turn = await self.get_turn(principal, turn_id)
            if current_turn.status in {
                TurnStatus.COMPLETED,
                TurnStatus.FAILED,
                TurnStatus.CANCELLED,
                TurnStatus.INTERRUPTED,
            }:
                return
            while True:
                event = await queue.get()
                if event.sequence <= last_sequence:
                    continue
                if event.sequence > last_sequence + 1:
                    while last_sequence + 1 < event.sequence:
                        missing = await self.replay_events(
                            principal,
                            turn_id,
                            after_sequence=last_sequence,
                            limit=min(2000, event.sequence - last_sequence - 1),
                        )
                        if not missing:
                            break
                        for replayed in missing:
                            last_sequence = replayed.sequence
                            yield replayed
                    if event.sequence <= last_sequence:
                        continue
                last_sequence = event.sequence
                yield event
                if event.type in {
                    GatewayEventType.TURN_COMPLETED,
                    GatewayEventType.TURN_FAILED,
                    GatewayEventType.TURN_CANCELLED,
                }:
                    return
        finally:
            self.events.unsubscribe(subscription_id)

    async def delete_session(
        self, principal: AuthenticatedPrincipal, session_id: str
    ) -> None:
        async with self._session_turn_locks[session_id]:
            context = await self.sessions.resolve(principal, session_id)
            active = await asyncio.to_thread(
                self.repository.active_turn_for_session, session_id
            )
            if active is not None:
                await self.cancel_turn(principal, active.turn_id)
            await self.engine.delete_session(context)
            await self.sessions.delete(principal, session_id)
            await asyncio.to_thread(self.repository.delete_session, session_id)
        from core.observability.runtime import global_telemetry

        await global_telemetry.flush()
        await asyncio.to_thread(
            global_telemetry.repository.delete_session, session_id
        )

    async def grant_high_risk_tool(
        self,
        principal: AuthenticatedPrincipal,
        *,
        session_id: str,
        tool_name: str,
        reason: str,
        ttl_s: float = 300,
    ) -> dict[str, Any]:
        await self.sessions.resolve(principal, session_id)
        from core.tool_registry import physical_tool_manager

        grant = physical_tool_manager.grant_high_risk_tool(
            session_id=session_id,
            tool_name=tool_name,
            granted_by=principal.user_id,
            reason=reason,
            ttl_s=ttl_s,
        )
        return grant.model_dump(mode="json")

    async def _emit_from_engine(
        self,
        turn_id: str,
        session_id: str,
        event_type: GatewayEventType,
        payload: dict,
    ) -> None:
        await self._emit(turn_id, session_id, event_type, payload)

    async def _emit(
        self,
        turn_id: str,
        session_id: str,
        event_type: GatewayEventType,
        payload: dict | None = None,
    ) -> GatewayEvent:
        event = await asyncio.to_thread(
            self.repository.append_event,
            turn_id=turn_id,
            session_id=session_id,
            event_type=event_type,
            payload=payload,
        )
        self.events.publish(event)
        return event

    async def health(self) -> GatewayHealth:
        repository = await asyncio.to_thread(self.repository.health)
        return GatewayHealth(
            status="ok" if self._started else "stopped",
            started=self._started,
            accepting_turns=self._accepting,
            active_turns=sum(not task.done() for task in self._turn_tasks.values()),
            subscribers=self.events.subscriber_count,
            database=repository["database"],
            durable_events=repository["durable_events"],
        )

    async def close(self, *, force: bool = False) -> None:
        async with self._lifecycle_lock:
            if not self._started:
                await asyncio.to_thread(self.repository.flush)
                self.repository.close()
                return
            self._accepting = False
            self._maintenance_stop.set()
            if self._maintenance_task is not None:
                await asyncio.gather(self._maintenance_task, return_exceptions=True)
                self._maintenance_task = None
            tasks = [task for task in self._turn_tasks.values() if not task.done()]
            pending = set(tasks)
            if pending and not force and self.shutdown_grace_s > 0:
                _done, pending = await asyncio.wait(
                    pending,
                    timeout=self.shutdown_grace_s,
                )
            for task in pending:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            await self.engine.close()
            await asyncio.to_thread(self.repository.flush)
            self.repository.close()
            self._turn_tasks.clear()
            self._session_turn_locks.clear()
            self._started = False


class GatewayCore:
    def __init__(self):
        self.content_guard = get_content_guard()
        self.audit = get_audit_logger()

    # 非流式响应处理
    async def process_request(self, prompt: str, session_id: str) -> str:
        raw_reply = "模型生成的原始回复"

        # 输出审核
        is_safe, reason, score = self.content_guard.validate_output(raw_reply)
        if not is_safe:
            self.audit.log_security_event("output_filtered",
                                          {"session": session_id, "reason": reason})
            # 返回兜底回复（绝不能泄露原始违规内容）
            return "抱歉，生成的回复不符合安全规范，已拦截。"

        return raw_reply

    # 流式响应处理（逐块审核）
    async def process_stream(self, prompt: str, session_id: str) -> AsyncGenerator[str, None]:
        # 假设从 server 获取流式生成器
        async for chunk in self.server.stream_generate(prompt):
            # 对每个 chunk 进行输出审核
            is_safe, _, _ = self.content_guard.validate_output(chunk)
            if not is_safe:
                # 截断后续流，发送安全提示
                yield " [内容被拦截] "
                break
            yield chunk