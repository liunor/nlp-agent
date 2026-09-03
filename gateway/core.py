"""Lifecycle-owning Backend Gateway Core; HTTP frameworks adapt to this class."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import uuid
from collections import defaultdict
from functools import partial
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from core.identity import AccessDeniedError, AuthenticatedPrincipal
from core.rbac import (
    Permission,
    ResourceRef,
    authorization_service,
    required_permission_for_high_risk_tool,
)
from core.session_context import SessionContext
from core.learning import LearningContext, TeachingMaterials, default_progress
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
from gateway.dispatch import (
    ExecutionAuthorizationContext,
    InProcessTurnDispatcher,
    TurnDispatcher,
    TurnTask,
)
from gateway.engine import AgentEngine, LangGraphAgentEngine
from gateway.events import GatewayEventBroker, GatewayEventSubscription
from gateway.repository import GatewayRepository
from gateway.mysql_repository import MySQLGatewayRepository
from gateway.outbox_dispatcher import OutboxTurnDispatcher
from gateway.turn_execution import InProcessTurnExecutor
from gateway.redis_transport import RedisEventBridge, RedisTransportConfig, RedisTurnDispatcher
from gateway.redis_transport import TurnTaskCodec
from server.agent.session_service import DatabaseSessionService, LocalSessionService, local_session_service
from server.application.turn_reliability import TurnReliabilityService
from server.infrastructure.mysql import MySQLRuntime
from server.quota.contracts import AdmitTurn, QuotaProblem
from server.quota.errors import QuotaErrorCode, QuotaRejectedError
from server.quota.operations import QuotaOperationsService
from server.quota.reaper import QuotaReservationReaper
from server.session.summary import schedule_summary

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_UPLOADS_ROOT = _PROJECT_ROOT / ".data" / "uploads"


def _session_uploads_root(context: SessionContext) -> Path:
    """Return the upload namespace for a session (mirrors input_resolver logic)."""
    return _DEFAULT_UPLOADS_ROOT / context.workspace_id / context.user_id / context.session_id


def _enrich_content_with_attachments(
    context: SessionContext,
    content: str,
    attachments: list[dict[str, str]],
) -> str:
    """Build the canonical, session-scoped text persisted for one turn."""

    if not attachments:
        return content
    uploads_root = _session_uploads_root(context)
    attachment_lines: list[str] = []
    for attachment in attachments:
        file_name = attachment.get("file_name", "")
        if not file_name or "/" in file_name or "\\" in file_name or ".." in file_name:
            raise ValueError("attachment file_name is invalid")
        if not (uploads_root / file_name).is_file():
            raise FileNotFoundError(f"attachment not found: {file_name}")
        # ImageInputResolver resolves a bare filename inside the authenticated
        # session upload namespace.  Do not expose a misleading project path.
        attachment_lines.append(f"[图片] {file_name}\n路径: {file_name}")

    block = "\n".join(attachment_lines)
    prefix = f"{content}\n\n" if content else ""
    return f"{prefix}---附件---\n{block}\n---附件结束---"

_EXPLICIT_EXERCISE_START_RE = re.compile(
    r"(?:开始|继续|新(?:的)?|再来|下一).{0,8}(?:练习|复习|题)|(?:练习|复习).{0,8}(?:开始|继续|下一题)",
    re.IGNORECASE,
)

def _is_explicit_exercise_start(content: str) -> bool:
    return bool(_EXPLICIT_EXERCISE_START_RE.search(content.strip()))


class BackendGateway:
    """The single writer and lifecycle owner for all backend Agent traffic."""

    def __init__(
        self,
        *,
        engine: AgentEngine | None = None,
        dispatcher: TurnDispatcher | None = None,
        repository: GatewayRepository | None = None,
        sessions: LocalSessionService | DatabaseSessionService = local_session_service,
        shutdown_grace_s: float | None = None,
        event_retention_days: int | None = None,
        max_events_per_session: int | None = None,
        retention_cleanup_interval_s: float | None = None,
    ) -> None:
        from configs.settings import settings

        gateway_config = settings.gateway_runtime
        self.engine = engine or LangGraphAgentEngine()
        self._database_runtime: MySQLRuntime | None = None
        if repository is not None:
            self.repository = repository
        elif str(gateway_config.get("persistence", "")).lower() == "mysql":
            from configs.settings import settings as runtime_settings
            dsn = runtime_settings.NLP_AGENT_DATABASE_URL.strip()
            if not dsn:
                raise RuntimeError("MySQL persistence mode requires NLP_AGENT_DATABASE_URL")
            self._database_runtime = MySQLRuntime.from_runtime(settings.database_runtime)
            quota_enforcement = runtime_settings.quota_enforcement_enabled
            self.repository = MySQLGatewayRepository(
                dsn,
                knowledge_point_prompt_budget=max(
                    1, int(gateway_config.get("knowledge_point_prompt_budget", 12_000))
                ),
                quota_enforcement=quota_enforcement,
            )
        else:
            raise RuntimeError("runtime persistence must be mysql; SQLite is migration-CLI only")
        self._quota_rollout = settings.quota_rollout
        self.quota_service = getattr(self.repository, "quota_service", None)
        self.sessions = sessions
        self.events = GatewayEventBroker()
        # Explicit repositories and dispatchers are used by tests and local
        # integrations; only the fully automatic runtime path should create
        # the production Redis/MySQL dispatcher.
        self._remote_execution = (
            dispatcher is None
            and repository is None
            and gateway_config.get("transport") == "redis"
        )
        self._event_bridge = None
        if dispatcher is None and self._remote_execution:
            redis_config = RedisTransportConfig(
                url=str(gateway_config.get("redis_url", "redis://127.0.0.1:6379/0")),
                task_stream=str(gateway_config.get("redis_turn_stream", "nlp-agent:turns")),
                task_group=str(gateway_config.get("redis_turn_group", "nlp-agent-workers")),
                event_channel=str(gateway_config.get("redis_event_channel", "nlp-agent:events")),
                control_channel=str(gateway_config.get("redis_control_channel", "nlp-agent:control")),
                reclaim_idle_ms=int(gateway_config.get("redis_reclaim_idle_ms", 60_000)),
                quota_snapshot_channel=str(
                    gateway_config.get(
                        "redis_quota_snapshot_channel", "nlp-agent:quota-snapshot"
                    )
                ),
                cancel_key_prefix=str(
                    gateway_config.get("redis_cancel_key_prefix", "nlp-agent:cancel:")
                ),
                cancel_ttl_s=int(gateway_config.get("redis_cancel_ttl_s", 604_800)),
                dead_letter_stream=str(
                    gateway_config.get(
                        "redis_dead_letter_stream", "nlp-agent:turns:dead"
                    )
                ),
            )
            redis_dispatcher = RedisTurnDispatcher.from_config(redis_config)
            if self._database_runtime is None:
                raise RuntimeError("Redis production dispatch requires MySQL runtime")
            self.dispatcher = OutboxTurnDispatcher(
                TurnReliabilityService(),
                redis_dispatcher,
            )
            self._event_bridge = RedisEventBridge(
                redis_dispatcher.client,
                redis_config,
                self.events,
                observe=redis_dispatcher.observe,
            )
        else:
            executor = InProcessTurnExecutor(
                self.engine,
                self.repository,
                self._emit_from_engine,
                on_turn_completed=(
                    partial(schedule_summary, self._database_runtime.session_factory)
                    if self._database_runtime is not None
                    else None
                ),
            )
            self.dispatcher = dispatcher or InProcessTurnDispatcher(executor.run)
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
        self.quota_reap_interval_s = max(
            1.0,
            float(gateway_config.get("quota_reap_interval_s", 30)),
        )
        self.quota_operations_interval_s = max(
            1.0,
            float(gateway_config.get("quota_operations_interval_s", 3_600)),
        )
        self._maintenance_stop = asyncio.Event()
        self._maintenance_task: asyncio.Task[None] | None = None
        self._quota_reaper: QuotaReservationReaper | None = None
        self._lifecycle_lock = asyncio.Lock()
        self._started = False
        self._accepting = False

    @property
    def authorization_session_factory(self):
        """Optional MySQL session factory used by the web auth adapter."""
        return self._database_runtime.session_factory if self._database_runtime is not None else None

    async def start(self) -> None:
        async with self._lifecycle_lock:
            if self._started:
                return
            if self._database_runtime is not None:
                await self._database_runtime.start()
            if self.quota_service is not None:
                await asyncio.to_thread(self.quota_service.verify_schema)
            if not self._remote_execution:
                await self.engine.start(self._emit_from_engine)
            if self._event_bridge is not None:
                await self._event_bridge.start()
            interrupted = [] if self._remote_execution else await asyncio.to_thread(self.repository.recover_interrupted)
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
            if self.quota_service is not None:
                self._quota_reaper = QuotaReservationReaper(
                    self.quota_service,
                    interval_seconds=self.quota_reap_interval_s,
                    operations_service=QuotaOperationsService(self.quota_service.engine),
                    operations_interval_seconds=self.quota_operations_interval_s,
                )
                self._quota_reaper.start()
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
        authorization_service.require(principal, Permission.AGENT_SESSION_CREATE, workspace_id=workspace_id)
        return await self.sessions.create(
            principal, workspace_id=workspace_id, channel=channel
        )

    async def submit_turn(
        self, principal: AuthenticatedPrincipal, request: SubmitTurnRequest, *, auth_session_id: str | None = None
    ) -> TurnAccepted:
        self._require_started()
        async with self._session_turn_locks[request.session_id]:
            return await self._submit_turn_locked(principal, request, auth_session_id=auth_session_id)

    async def _submit_turn_locked(
        self, principal: AuthenticatedPrincipal, request: SubmitTurnRequest, *, auth_session_id: str | None = None
    ) -> TurnAccepted:
        context = await self.sessions.resolve(principal, request.session_id)
        if auth_session_id:
            context = context.model_copy(update={"auth_session_id": auth_session_id})
        authorization_service.require(principal, Permission.AGENT_TURN_SUBMIT, workspace_id=context.workspace_id)
        if request.model_profile is not None:
            from core.model_runtime.factory import get_global_model_factory

            factory = get_global_model_factory()
            profile = factory.config.profile(request.model_profile)
            if not factory.profile_available(request.model_profile):
                raise ValueError(f"模型 {profile.label} 暂不可用，请联系管理员配置模型服务。")
        if request.evaluation is not None:
            context = context.model_copy(
                update={"observability_attributes": request.evaluation.trace_attributes()}
            )
        await self.sessions.touch(principal, request.session_id)
        enriched_content = _enrich_content_with_attachments(
            context, request.content, request.attachments
        )
        if request.idempotency_key:
            existing = await asyncio.to_thread(
                self.repository.turn_for_idempotency,
                user_id=context.user_id,
                session_id=context.session_id,
                idempotency_key=request.idempotency_key,
            )
            if (
                existing is not None
                and existing.input_text != enriched_content
            ):
                raise TurnConflictError(
                    "idempotency key was already used for a different request"
                )
            if existing is not None and not (
                existing.status == TurnStatus.FAILED
                and existing.error_kind == "dispatch_failed"
            ):
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
        quota_admission = None
        reservation_id = None
        if self.quota_service is not None and self._quota_rollout.enabled_for(
            context.user_id, context.workspace_id
        ):
            from core.model_runtime.factory import get_global_model_factory

            factory = get_global_model_factory()
            profile_name = request.model_profile or factory.config.default_model_profile
            if not profile_name:
                raise QuotaRejectedError(
                    QuotaProblem(
                        code=QuotaErrorCode.ADMISSION_DENIED,
                        reason="额度校验需要明确的模型 Profile",
                        remaining_micro=0,
                        retryable=False,
                    )
                )
            identity = factory.profile_identity(profile_name, "coordinator")
            quota_admission = AdmitTurn(
                request_id=request.idempotency_key or turn_id,
                user_id=context.user_id,
                workspace_id=context.workspace_id,
                turn_id=turn_id,
                model_profile=profile_name,
                model_role="coordinator",
                estimated_input_tokens=factory.estimate_input_tokens(
                    profile_name, [enriched_content]
                ),
                estimated_output_tokens=identity.max_output_tokens or 0,
                pricing_key=identity.pricing_key,
                idempotency_key=request.idempotency_key or turn_id,
            )
            reservation_id = self.quota_service.reservation_id_for_turn(turn_id)
        task = TurnTask(
            context=context,
            turn_id=turn_id,
            content=enriched_content,
            learning_context=learning_context,
            learning_progress=progress,
            exercise_state=exercise,
            teaching_materials=teaching_materials,
            guided_session_id=guided_session.get("id"),
            exercise_session_id=(teaching_session.get("id") if teaching_session is not None else None),
            model_profile=request.model_profile,
            authorization=ExecutionAuthorizationContext(
                submitter_user_id=principal.user_id,
                workspace_id=context.workspace_id,
                authorization_version=principal.authorization_version,
            ),
            reservation_id=reservation_id,
        )
        turn, duplicate = await asyncio.to_thread(
            self.repository.create_turn,
            turn_id=turn_id,
            session_id=context.session_id,
            workspace_id=context.workspace_id,
            user_id=context.user_id,
            input_text=enriched_content,
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
            dispatch_payload=TurnTaskCodec.dumps(task) if self._remote_execution else None,
            quota_admission=quota_admission,
            quota_role_codes=tuple(principal.roles),
            quota_classroom_ids=tuple(principal.classroom_ids),
        )
        resubmitted = bool(
            duplicate
            and turn.status == TurnStatus.FAILED
            and turn.error_kind == "dispatch_failed"
        )
        if duplicate and not resubmitted:
            return TurnAccepted(
                turn_id=turn.turn_id,
                session_id=turn.session_id,
                status=turn.status,
                duplicate=True,
            )
        if resubmitted:
            turn = await asyncio.to_thread(
                self.repository.update_turn,
                turn.turn_id,
                TurnStatus.ACCEPTED,
                dispatch_payload=TurnTaskCodec.dumps(task) if self._remote_execution else None,
            )
        await self._emit(
            turn.turn_id,
            context.session_id,
            GatewayEventType.TURN_ACCEPTED,
            {"status": TurnStatus.ACCEPTED.value},
        )
        task = task.__class__(
            context=task.context, turn_id=turn.turn_id, content=task.content,
            learning_context=task.learning_context, learning_progress=task.learning_progress,
            exercise_state=task.exercise_state, teaching_materials=task.teaching_materials,
            guided_session_id=task.guided_session_id, exercise_session_id=task.exercise_session_id,
            model_profile=task.model_profile, authorization=task.authorization,
            reservation_id=(
                self.quota_service.reservation_id_for_turn(turn.turn_id)
                if self.quota_service is not None
                else None
            ),
        )
        try:
            await self.dispatcher.submit(task)
        except Exception as error:
            await asyncio.to_thread(
                self.repository.update_turn,
                turn.turn_id,
                TurnStatus.FAILED,
                error_kind="dispatch_failed",
                error_message=str(error),
            )
            if self.quota_service is not None and task.reservation_id:
                await asyncio.to_thread(
                    self.quota_service.release_reservation,
                    task.reservation_id,
                    turn_id=turn.turn_id,
                    idempotency_key=f"dispatch-failed:{turn.turn_id}",
                )
            await self._emit(
                turn.turn_id,
                context.session_id,
                GatewayEventType.TURN_FAILED,
                {
                    "status": TurnStatus.FAILED.value,
                    "error_kind": "dispatch_failed",
                    "message": str(error)[:500],
                },
            )
            raise
        return TurnAccepted(
            turn_id=turn.turn_id,
            session_id=context.session_id,
            status=TurnStatus.ACCEPTED,
            duplicate=resubmitted,
        )

    async def inject_message(
        self, principal: AuthenticatedPrincipal, request: InjectMessageRequest
    ) -> TurnAccepted:
        self._require_started()
        context = await self.sessions.resolve(principal, request.session_id)
        authorization_service.require(principal, Permission.AGENT_SESSION_UPDATE, workspace_id=context.workspace_id)
        if self._remote_execution:
            active = await asyncio.to_thread(
                self.repository.active_turn_for_session, context.session_id
            )
            turn_id = active.turn_id if active is not None else None
            if turn_id is not None and hasattr(self.dispatcher, "inject"):
                await self.dispatcher.inject(turn_id, request.content)
        else:
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
        authorization_service.require(principal, Permission.AGENT_TURN_CANCEL, workspace_id=turn.workspace_id)
        if turn.status not in {TurnStatus.ACCEPTED, TurnStatus.RUNNING}:
            return turn
        context = await self.sessions.resolve(principal, turn.session_id)
        request_cancellation = getattr(self.repository, "request_turn_cancellation", None)
        if request_cancellation is not None:
            updated = await asyncio.to_thread(
                request_cancellation,
                turn_id=turn_id,
                requested_by=principal.user_id,
                reason="user_requested",
            )
            if updated is not None:
                turn = updated
        await self.dispatcher.cancel(turn_id)
        updated = await asyncio.to_thread(self.repository.get_turn, turn_id)
        return updated or turn

    async def get_turn(
        self, principal: AuthenticatedPrincipal, turn_id: str
    ) -> TurnRecord:
        authorization_service.require(principal, Permission.AGENT_SESSION_READ)
        turn = await asyncio.to_thread(self.repository.get_turn, turn_id)
        if turn is None:
            raise ResourceNotFoundError(turn_id)
        authorization_service.require_resource(
            principal,
            Permission.AGENT_SESSION_READ,
            ResourceRef("turn", owner_user_id=turn.user_id, workspace_id=turn.workspace_id),
        )
        return turn

    async def replay_events(
        self,
        principal: AuthenticatedPrincipal,
        turn_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 500,
    ) -> list[GatewayEvent]:
        authorization_service.require(principal, Permission.AGENT_EVENT_REPLAY)
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
        context = await self.sessions.resolve(principal, session_id)
        authorization_service.require(principal, Permission.AGENT_SESSION_READ, workspace_id=context.workspace_id)
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
        context = await self.sessions.resolve(principal, session_id)
        authorization_service.require(principal, Permission.AGENT_SESSION_READ, workspace_id=context.workspace_id)
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
        authorization_service.require(
            principal,
            Permission.LEARNING_CONTENT_READ_WORKSPACE,
            workspace_id=workspace_id,
        )
        return await asyncio.to_thread(self.repository.get_teaching_catalog, workspace_id)

    async def update_teaching_catalog(self, principal: AuthenticatedPrincipal, workspace_id: str, catalog: dict[str, Any]) -> dict[str, Any]:
        authorization_service.require(
            principal,
            Permission.LEARNING_CONTENT_MANAGE,
            workspace_id=workspace_id,
        )
        return await asyncio.to_thread(self.repository.update_teaching_catalog, workspace_id, catalog)

    async def get_knowledge_page(
        self,
        principal: AuthenticatedPrincipal,
        workspace_id: str,
        knowledge_point_id: str,
    ) -> dict[str, Any] | None:
        authorization_service.require(
            principal,
            Permission.LEARNING_CONTENT_READ_WORKSPACE,
            workspace_id=workspace_id,
        )
        return await asyncio.to_thread(
            self.repository.get_knowledge_page, workspace_id, knowledge_point_id
        )

    async def list_knowledge_pages(
        self,
        principal: AuthenticatedPrincipal,
        workspace_id: str,
    ) -> list[dict[str, Any]]:
        authorization_service.require(
            principal,
            Permission.LEARNING_CONTENT_READ_WORKSPACE,
            workspace_id=workspace_id,
        )
        return await asyncio.to_thread(self.repository.list_knowledge_pages, workspace_id)

    async def get_published_knowledge_page(
        self,
        principal: AuthenticatedPrincipal,
        workspace_id: str,
        knowledge_point_id: str,
    ) -> dict[str, Any] | None:
        authorization_service.require(
            principal,
            Permission.LEARNING_CONTENT_READ_WORKSPACE,
            workspace_id=workspace_id,
        )
        return await asyncio.to_thread(
            self.repository.get_published_knowledge_page,
            workspace_id,
            knowledge_point_id,
        )

    async def update_knowledge_page(
        self,
        principal: AuthenticatedPrincipal,
        workspace_id: str,
        knowledge_point_id: str,
        draft_markdown: str,
        *,
        expected_revision: int,
    ) -> dict[str, Any]:
        authorization_service.require(
            principal,
            Permission.LEARNING_CONTENT_MANAGE,
            workspace_id=workspace_id,
        )
        return await asyncio.to_thread(
            self.repository.update_knowledge_page,
            workspace_id,
            knowledge_point_id,
            draft_markdown,
            expected_revision=expected_revision,
        )

    async def publish_knowledge_page(
        self,
        principal: AuthenticatedPrincipal,
        workspace_id: str,
        knowledge_point_id: str,
        *,
        expected_revision: int,
    ) -> dict[str, Any]:
        authorization_service.require(
            principal,
            Permission.LEARNING_CONTENT_MANAGE,
            workspace_id=workspace_id,
        )
        return await asyncio.to_thread(
            self.repository.publish_knowledge_page,
            workspace_id,
            knowledge_point_id,
            expected_revision=expected_revision,
        )

    async def apply_knowledge_book_import(
        self,
        principal: AuthenticatedPrincipal,
        workspace_id: str,
        pages: list[dict[str, Any]],
        assets: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        authorization_service.require(
            principal,
            Permission.LEARNING_CONTENT_MANAGE,
            workspace_id=workspace_id,
        )
        return await asyncio.to_thread(
            self.repository.apply_knowledge_book_import,
            workspace_id,
            pages,
            assets,
        )

    async def get_knowledge_book_asset(
        self,
        principal: AuthenticatedPrincipal,
        workspace_id: str,
        asset_path: str,
    ) -> dict[str, Any] | None:
        authorization_service.require(
            principal,
            Permission.LEARNING_CONTENT_READ_WORKSPACE,
            workspace_id=workspace_id,
        )
        return await asyncio.to_thread(
            self.repository.get_knowledge_book_asset,
            workspace_id,
            asset_path,
        )

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
                missing = await self.replay_events(principal, turn_id, after_sequence=last_sequence, limit=2000)
                for event in missing:
                    yield event
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
            authorization_service.require(principal, Permission.AGENT_SESSION_DELETE, workspace_id=context.workspace_id)
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
        context = await self.sessions.resolve(principal, session_id)
        permission = required_permission_for_high_risk_tool(tool_name)
        authorization_service.require(
            principal,
            permission,
            workspace_id=context.workspace_id,
        )
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
            active_turns=self.dispatcher.active_count(),
            subscribers=self.events.subscriber_count,
            database=repository["database"],
            durable_events=repository["durable_events"],
        )

    async def close(self, *, force: bool = False) -> None:
        async with self._lifecycle_lock:
            if not self._started:
                await asyncio.to_thread(self.repository.flush)
                self.repository.close()
                if self._database_runtime is not None:
                    await self._database_runtime.close()
                return
            self._accepting = False
            self._maintenance_stop.set()
            if self._maintenance_task is not None:
                await asyncio.gather(self._maintenance_task, return_exceptions=True)
                self._maintenance_task = None
            if self._quota_reaper is not None:
                await self._quota_reaper.stop()
                self._quota_reaper = None
            if self._event_bridge is not None:
                await self._event_bridge.close()
            await self.dispatcher.close(
                force=force, grace_s=self.shutdown_grace_s
            )
            if not self._remote_execution:
                await self.engine.close()
            await asyncio.to_thread(self.repository.flush)
            self.repository.close()
            if self._database_runtime is not None:
                await self._database_runtime.close()
            self._session_turn_locks.clear()
            self._started = False
