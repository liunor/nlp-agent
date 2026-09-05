"""FastAPI same-origin adapter for the lifecycle-owning BackendGateway."""

import asyncio
import json
import logging
import secrets
from collections.abc import Callable
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Any, Literal

logger = logging.getLogger(__name__)

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response, Security, WebSocket, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.security import APIKeyCookie
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import OperationalError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware

from configs.settings import settings
from core.identity import AccessDeniedError, AuthenticatedPrincipal
from core.rbac import Permission, ResourceRef, authorization_service
from core.authorization_audit import begin as begin_authorization_audit, end as end_authorization_audit
from gateway.contracts import (
    GatewayNotStartedError,
    InjectMessageRequest,
    KnowledgeBookRevisionConflictError,
    ResourceNotFoundError,
    SubmitTurnRequest,
    TurnConflictError,
)
from gateway.core import BackendGateway
from server.infrastructure.mysql.models import ClassroomModel
from server.web.auth import (
    AuthenticationError,
    CsrfRejectedError,
    OriginRejectedError,
    SameOriginSessionAuth,
    SessionClaims,
)
from server.web.database_auth import DatabaseSessionAuth, DatabaseSessionClaims
from server.rbac.catalog import permission_display, role_display
from server.agent.session_service import DatabaseSessionService, local_session_service
from server.quota.errors import QuotaDomainError, QuotaRejectedError
from server.quota.management import QuotaManagementService
from server.quota.notifications import (
    DEFAULT_QUOTA_SNAPSHOT_CHANNEL,
    QuotaSnapshotRedisPublisher,
)
from server.quota.operations import QuotaOperationsService
from server.quota.service import QuotaService
from server.session.summary import summary_sweep_loop
from server.web.contracts import (
    CreateSessionBody,
    RenameSessionBody,
    LoginBody,
    ReplaceUserRolesBody,
    ReplaceRolePermissionsBody,
    ReplaceRoleMenusBody,
    CreateClassroomBody,
    ReplaceClassroomMemberBody,
    InjectChatBody,
    SubmitChatBody,
    ToolApprovalBody,
    UpdateCustomToolsBody,
    UpdateToolPoliciesBody,
    UpdateSettingsBody,
    FeedbackBody,
    FeedbackBulkBody,
    FeedbackReadBody,
    FeedbackReplyBody,
    FeedbackUpdateBody,
    FeedbackSortValue,
    FeedbackCategoryValue,
    FeedbackStatusValue,
    FeedbackPriorityValue,
    McpServerBody,
    ModelConfigBody,
    SkillBody,
    WorkerProfileBody,
    ReleaseNoteBody,
    QuotaAdjustmentBody,
    QuotaBillingRepairBody,
    QuotaBillingReconcileBody,
    QuotaBindingBody,
    QuotaCreditOperationBody,
    QuotaRoleCreditOperationBody,
    QuotaGrantBody,
    QuotaGrantRevokeBody,
    QuotaPricingRuleBody,
    QuotaPolicyBody,
    QuotaPolicyUpdateBody,
    QuotaUsageArchiveBody,
    QuotaAlertStatusBody,
)
from server.web.protocol import control_event
from server.web.developer import developer_health, developer_snapshot
from server.web.developer_runtime import (
    DeveloperConfigurationError,
    delete_mcp_server,
    delete_skill,
    delete_worker_profile,
    read_skill,
    test_mcp_server,
    update_custom_tools,
    update_tool_policies,
    upsert_mcp_server,
    upsert_model_preset,
    upsert_model_profile,
    upsert_model_provider,
    upsert_model_route,
    upsert_skill,
    upsert_worker_profile,
)
from server.teacher.models import (
    ExerciseBlueprint,
    GuidedBlueprint,
    TeacherBookArchiveImportApplyRequest,
    TeacherBookArchiveImportPreviewRequest,
    PublishTeacherBookPage,
    ReviewBlueprint,
    TeacherBookImportApplyRequest,
    TeacherBookImportPreviewRequest,
    TeacherAnalysisAnnotations,
    TeacherAIAnalysisRequest,
    UpdateTeacherAnalysisAnnotations,
    UpdateTeacherBookPage,
    UpdateTeacherCatalog,
    UpdateTeachingGoals,
)
from server.teacher.service import teacher_service
from server.rbac.service import (
    LastDeveloperForbiddenError,
    UnknownRoleError,
    rbac_service,
)
from server.infrastructure.mysql.models import RoleModel, UserModel, WorkspaceModel
from server.sandbox.service import sandbox_lifecycle_service
from server.sandbox.artifact_retention import purge_expired_artifacts
from server.sandbox.metrics import record_sandbox_capacity_sample
from server.release_notes.service import (
    ReleaseNoteConflictError,
    ReleaseNoteNotFoundError,
    release_note_service,
)
from server.web.websocket import WebSocketHub, websocket_endpoint
from server.web.feedback import (
    delete_feedback_thread,
    delete_feedback_threads,
    get_feedback_daily_state,
    get_feedback_thread,
    get_own_feedback,
    list_feedback_threads,
    mark_feedback_read,
    mark_feedback_threads_read,
    mark_own_feedback_read,
    reply_feedback,
    submit_feedback,
    update_feedback_thread,
)
from server.auth.dependencies import get_db_session
from server.auth import code_store
from server.auth.captcha import generate_captcha_image
from server.user.schemas import SmsCodeRequest, UserRegister
from server.user.service import (
    InvalidCaptchaError,
    InvalidSmsCodeError,
    PhoneNumberAlreadyUsedError,
    UserAlreadyExistsError,
    UserService,
    generate_sms_code,
)
from server.user.tencent_sms import SmsConfigurationError, create_tencent_sms_provider_from_env
from server.user.phone import InvalidPhoneNumberError, normalize_phone_number

DbSession = Annotated[AsyncSession, Depends(get_db_session)]


GatewayFactory = Callable[[], BackendGateway]

# Strong references to in-flight authorization-audit flush tasks so the
# deferred writes survive garbage collection until they finish.
_pending_audit_tasks: set[asyncio.Task] = set()


class SpaStaticFiles(StaticFiles):
    """Serve the SPA shell only for browser navigations.

    A blanket fallback turns missing API responses and broken JavaScript
    assets into an HTML ``200`` response.  Browsers then report a misleading
    JSON/parse error and operational probes can no longer distinguish a real
    404.  Restricting the fallback to HTML GET/HEAD navigations keeps the
    BrowserRouter deep-link fix while preserving normal HTTP semantics.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        directory = kwargs.get("directory")
        if directory is None and args:
            directory = args[0]
        self._spa_index = Path(directory) / "index.html"

    async def get_response(self, path: str, scope):
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as error:
            headers = {
                key.lower(): value
                for key, value in scope.get("headers", [])
            }
            accept = headers.get(b"accept", b"").decode("latin-1")
            normalized_path = str(scope.get("path", path)).lstrip("/").lower()
            is_navigation = (
                scope.get("method") in {"GET", "HEAD"}
                and "text/html" in accept.lower()
                and normalized_path not in {"api", "ws"}
                and not normalized_path.startswith("api/")
                and not normalized_path.startswith("ws/")
                and not normalized_path.startswith("assets/")
                and not normalized_path.startswith("static/")
                and Path(normalized_path).suffix.lower()
                not in {
                    ".7z",
                    ".avif",
                    ".css",
                    ".csv",
                    ".gif",
                    ".gz",
                    ".ico",
                    ".jpeg",
                    ".jpg",
                    ".js",
                    ".json",
                    ".map",
                    ".pdf",
                    ".png",
                    ".svg",
                    ".txt",
                    ".wasm",
                    ".webmanifest",
                    ".webp",
                    ".woff",
                    ".woff2",
                    ".xml",
                    ".zip",
                }
            )
            if error.status_code == 404 and is_navigation and self._spa_index.is_file():
                return await super().get_response("index.html", scope)
            raise


def _problem(
    request: Request,
    *,
    status_code: int,
    code: str,
    title: str,
    detail: str | None = None,
    extra: dict[str, Any] | None = None,
) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None)
    body: dict[str, Any] = {
        "type": f"urn:nlp-agent:error:{code}",
        "title": title,
        "status": status_code,
        "code": code,
    }
    if detail:
        body["detail"] = detail
    if request_id:
        body["request_id"] = request_id
    if extra:
        body.update(extra)
    return JSONResponse(body, status_code=status_code, media_type="application/problem+json")


def _is_quota_schema_mismatch(error: OperationalError) -> bool:
    message = str(error).lower()
    return any(
        marker in message
        for marker in ("unknown column", "no such column", "doesn't exist", "no such table")
    )


def _public_runtime_settings() -> dict[str, Any]:
    from core.model_runtime.factory import get_global_model_factory

    raw = settings._config
    factory = get_global_model_factory()
    models = {
        name: {
            "provider": item.get("provider"),
            "model_id": item.get("model_id"),
            "context_window_tokens": item.get("context_window_tokens"),
            "max_output_tokens": item.get("max_output_tokens"),
            "capabilities": item.get("capabilities", {}),
        }
        for name, item in raw.get("models", {}).items()
    }
    presets = {
        name: {
            "model": item.get("model"),
            "thinking": item.get("thinking", {}),
            "generation": item.get("generation", {}),
        }
        for name, item in raw.get("model_presets", {}).items()
    }
    return {
        "defaults": raw.get("defaults", {}),
        "model_routes": raw.get("model_routes", {}),
        "models": models,
        "model_presets": presets,
        "default_model_profile": factory.config.default_model_profile,
        "model_profiles": factory.public_profiles(),
        "protocol": {
            "http": "/api/v1",
            "websocket": "/ws/v1",
            "version": "1",
        },
    }


def _public_learning_topic(topic: dict[str, Any]) -> dict[str, Any]:
    """Project teacher-only knowledge-point settings out of the student catalog."""
    public_topic = {
        key: value for key, value in topic.items() if key != "knowledge_points"
    }
    public_topic["knowledge_points"] = [
        {
            key: value
            for key, value in point.items()
            if key != "question_types"
        }
        for point in topic.get("knowledge_points", [])
        if point.get("status", "enabled") == "enabled"
    ]
    return public_topic


def require_explicit_classroom_membership(
    principal: AuthenticatedPrincipal, classroom_id: str
) -> None:
    """Require object-level classroom scope for every non-admin teacher."""
    if not principal.is_admin and classroom_id not in principal.classroom_ids:
        raise HTTPException(status_code=403, detail="当前教师无权查看该课堂")


def _rbac_http_error(error: Exception) -> HTTPException:
    """Translate RBAC domain failures into stable API error contracts."""
    if isinstance(error, KeyError):
        return HTTPException(status_code=404, detail="RBAC resource not found")
    if isinstance(error, UnknownRoleError):
        return HTTPException(status_code=400, detail=str(error))
    if isinstance(error, LastDeveloperForbiddenError):
        return HTTPException(status_code=409, detail=str(error))
    if isinstance(error, PermissionError):
        return HTTPException(status_code=403, detail=str(error))
    if isinstance(error, ValueError):
        return HTTPException(status_code=422, detail=str(error))
    raise error


def create_app(
    *,
    gateway_factory: GatewayFactory = BackendGateway,
    auth: SameOriginSessionAuth | None = None,
    allowed_hosts: list[str] | None = None,
) -> FastAPI:
    web_config = settings.web_runtime
    auth_injected = auth is not None
    # The production web plane never reads the legacy fixed-account fields;
    # browser authentication is always backed by ``nlp_sessions`` below.
    auth = auth or SameOriginSessionAuth.from_config(web_config, include_credentials=False)
    database_auth = DatabaseSessionAuth.from_config(web_config)
    cookie_secure = database_auth.secure if not auth_injected else auth.secure
    hub = WebSocketHub(
        max_connections=int(web_config.get("ws_max_connections", 200)),
        max_connections_per_user=int(
            web_config.get("ws_max_connections_per_user", 10)
        ),
    )
    stream_queue_size = int(settings.gateway_runtime.get("stream_queue_size", 500))
    max_ws_message_bytes = int(web_config.get("max_ws_message_bytes", 1_048_576))
    ws_send_queue_size = int(web_config.get("ws_send_queue_size", 256))
    ws_send_timeout_s = float(web_config.get("ws_send_timeout_s", 10))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        gateway = gateway_factory()
        redis_client = getattr(getattr(gateway, "dispatcher", None), "client", None)
        if redis_client is None:
            redis_client = getattr(getattr(getattr(gateway, "dispatcher", None), "_transport", None), "client", None)
        database_auth.set_redis_client(redis_client)
        # Tests and embedded callers may inject lightweight quota readers on
        # app.state before entering the lifespan.  Preserve those doubles in
        # auth-injected mode while keeping production startup responsible for
        # constructing the real persistence-backed services.
        injected_usage_reader = getattr(app.state, "quota_usage_reader", None)
        injected_quota_read_service = getattr(app.state, "quota_read_service", None)
        usage_reporter = None
        usage_reader = None
        quota_read_service = None
        quota_management = None
        quota_operations = None
        owns_quota_read_service = False
        if (
            not auth_injected
            and gateway.authorization_session_factory is not None
            and gateway.sessions is local_session_service
        ):
            gateway.sessions = DatabaseSessionService(
                gateway.authorization_session_factory
            )
        app.state.gateway = gateway
        # Bind model-facing Sandbox tools to the same authenticated DB session
        # factory as the HTTP gateway.  The module-level tool objects remain
        # stable for LangChain catalogs while their service is request-safe.
        from server.sandbox.manager_rpc import create_sandbox_manager_rpc_client
        from server.sandbox.model_tools import configure_model_sandbox_service

        sandbox_manager = (
            create_sandbox_manager_rpc_client()
            if settings.NLP_AGENT_SANDBOX_RUNTIME_MODE.strip().lower() == "docker"
            else None
        )
        app.state.sandbox_manager = sandbox_manager

        sandbox_model_service = configure_model_sandbox_service(
            mode=settings.NLP_AGENT_SANDBOX_RUNTIME_MODE,
            session_factory=gateway.authorization_session_factory,
            manager=sandbox_manager,
        )
        await gateway.start()
        from server.quota.bootstrap import (
            configure_usage_reporter,
            shutdown_usage_reporter,
        )
        from server.quota.usage import UsageReadService

        database_url = settings.NLP_AGENT_DATABASE_URL.strip()
        if not auth_injected:
            usage_reporter = configure_usage_reporter(
                database_url,
                required=True,
                quota_enforcement=settings.quota_enforcement_enabled,
            )
            if getattr(gateway, "authorization_session_factory", None) is not None:
                quota_read_service = getattr(gateway, "quota_service", None)
                if quota_read_service is None:
                    quota_read_service = QuotaService(database_url)
                    owns_quota_read_service = True
                # Phase 3 management and user snapshots must fail startup if
                # the new management tables were not migrated.
                quota_read_service.verify_schema()
                quota_management = QuotaManagementService(quota_read_service.engine)
                quota_operations = QuotaOperationsService(quota_read_service.engine)
                usage_reader = UsageReadService(
                    database_url,
                    quota_enforcement=True,
                )
        elif auth_injected:
            usage_reader = injected_usage_reader
            quota_read_service = injected_quota_read_service
        app.state.quota_usage_reporter = usage_reporter
        app.state.quota_usage_reader = usage_reader
        app.state.quota_read_service = quota_read_service
        app.state.quota_management = quota_management
        app.state.quota_operations = quota_operations
        redis_client = getattr(getattr(gateway, "dispatcher", None), "client", None)
        authorization_channel = str(settings.gateway_runtime.get("redis_authorization_channel", "nlp-agent:authorization"))
        quota_snapshot_channel = str(
            settings.gateway_runtime.get(
                "redis_quota_snapshot_channel", DEFAULT_QUOTA_SNAPSHOT_CHANNEL
            )
        )
        app.state.quota_snapshot_channel = quota_snapshot_channel
        app.state.quota_snapshot_redis = redis_client
        dispatcher_config = getattr(getattr(gateway, "dispatcher", None), "config", None)
        redis_url = str(
            settings.gateway_runtime.get("redis_url")
            or getattr(dispatcher_config, "url", "")
        )
        quota_snapshot_publisher = (
            QuotaSnapshotRedisPublisher(
                redis_url,
                channel=quota_snapshot_channel,
            )
            if redis_client is not None
            else None
        )
        if quota_snapshot_publisher is not None:
            if quota_read_service is not None:
                quota_read_service.set_snapshot_notifier(quota_snapshot_publisher)
            if usage_reporter is not None:
                usage_reporter.set_snapshot_notifier(quota_snapshot_publisher)

        async def consume_authorization_changes() -> None:
            if redis_client is None:
                return
            pubsub = redis_client.pubsub()
            try:
                await pubsub.subscribe(authorization_channel)
                async for message in pubsub.listen():
                    if message.get("type") != "message":
                        continue
                    try:
                        payload = json.loads(message.get("data") or "{}")
                        user_id = str(payload["user_id"])
                    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                        continue
                    await hub.close_user(user_id, reason="authorization changed")
            except asyncio.CancelledError:
                raise
            finally:
                await pubsub.unsubscribe(authorization_channel)
                await pubsub.aclose()

        authorization_listener = (
            asyncio.create_task(consume_authorization_changes(), name="authorization-invalidation-listener")
            if redis_client is not None else None
        )

        async def consume_quota_snapshot_changes() -> None:
            if redis_client is None:
                return
            pubsub = redis_client.pubsub()
            try:
                await pubsub.subscribe(quota_snapshot_channel)
                async for message in pubsub.listen():
                    if message.get("type") != "message":
                        continue
                    try:
                        payload = json.loads(message.get("data") or "{}")
                        owner_type = payload.get("owner_type")
                        owner_id = payload.get("owner_id")
                        if owner_type not in {None, "user", "workspace"}:
                            continue
                        if owner_type is not None and not isinstance(owner_id, str):
                            continue
                    except (TypeError, ValueError, json.JSONDecodeError):
                        continue
                    event = control_event(
                        "usage.snapshot",
                        payload={
                            "owner_type": owner_type,
                            "owner_id": owner_id,
                            "refresh_required": True,
                        },
                    )
                    if owner_type == "user" and owner_id:
                        await hub.broadcast(event, user_id=owner_id)
                    elif owner_type == "workspace" and owner_id:
                        await hub.broadcast(event, workspace_id=owner_id)
                    else:
                        await hub.broadcast(event)
            except asyncio.CancelledError:
                raise
            finally:
                await pubsub.unsubscribe(quota_snapshot_channel)
                await pubsub.aclose()

        quota_snapshot_listener = (
            asyncio.create_task(
                consume_quota_snapshot_changes(),
                name="quota-snapshot-listener",
            )
            if redis_client is not None
            else None
        )
        sandbox_reconcile_interval_s = max(
            10, int(web_config.get("sandbox_lease_reconcile_interval_s", 60))
        )

        async def reconcile_sandbox_leases() -> None:
            factory = gateway.authorization_session_factory
            if factory is None:
                return
            while True:
                try:
                    await sandbox_lifecycle_service.reconcile_expired_leases(factory)
                    store_root = settings.NLP_AGENT_SANDBOX_ARTIFACT_STORE_ROOT.strip()
                    if store_root:
                        await purge_expired_artifacts(factory, store_root=Path(store_root))
                    await record_sandbox_capacity_sample(factory)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    # Reconciliation is a periodic repair loop. One transient
                    # DB/Redis/filesystem failure must not kill all later runs.
                    logger.exception("sandbox reconciliation pass failed")
                finally:
                    await asyncio.sleep(sandbox_reconcile_interval_s)

        sandbox_reconciler = (
            asyncio.create_task(reconcile_sandbox_leases(), name="sandbox-lease-reconciler")
            if gateway.authorization_session_factory is not None else None
        )

        async def run_summary_sweep() -> None:
            # Durable backfill for titles lost to a restart; the lease claim in
            # ``generate_and_store_summary`` deduplicates it against the Worker.
            await summary_sweep_loop(gateway.authorization_session_factory)

        summary_sweeper = (
            asyncio.create_task(run_summary_sweep(), name="session-summary-sweep")
            if gateway.authorization_session_factory is not None else None
        )
        try:
            yield
        finally:
            if summary_sweeper is not None:
                summary_sweeper.cancel()
                await asyncio.gather(summary_sweeper, return_exceptions=True)
            if sandbox_reconciler is not None:
                sandbox_reconciler.cancel()
                await asyncio.gather(sandbox_reconciler, return_exceptions=True)
            if authorization_listener is not None:
                authorization_listener.cancel()
                await asyncio.gather(authorization_listener, return_exceptions=True)
            if quota_snapshot_listener is not None:
                quota_snapshot_listener.cancel()
                await asyncio.gather(quota_snapshot_listener, return_exceptions=True)
            if quota_snapshot_publisher is not None:
                quota_snapshot_publisher.close()
            if sandbox_manager is not None:
                await sandbox_manager.close()
            await sandbox_model_service.close()
            await gateway.begin_shutdown()
            await hub.close()
            await gateway.close()
            shutdown_usage_reporter(usage_reporter)
            if usage_reader is not None:
                close_usage_reader = getattr(usage_reader, "close", None)
                if close_usage_reader is not None:
                    close_usage_reader()
            if owns_quota_read_service and quota_read_service is not None:
                quota_read_service.close()
            if quota_operations is not None:
                quota_operations.close()

    app = FastAPI(
        title="NLP Agent Web API",
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url=None,
    )
    app.state.auth = auth
    app.state.database_auth = database_auth
    app.state.auth_injected = auth_injected
    app.state.hub = hub
    cookie_auth = APIKeyCookie(name=auth.cookie_name, auto_error=False)
    # An explicit allowed_hosts override (tests/local deployments) wins over the
    # config-derived whitelist so the app never depends on a gitignored .env
    # override of NLP_AGENT_WEB_ALLOWED_HOSTS; otherwise fall back to the
    # configured list and finally the loopback default.
    middleware_hosts = (
        allowed_hosts
        if allowed_hosts is not None
        else list(web_config.get("allowed_hosts", ["127.0.0.1", "localhost"]))
    )
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=middleware_hosts)

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request.state.request_id = request.headers.get("x-request-id") or secrets.token_hex(16)
        audit_token, decisions = begin_authorization_audit()
        try:
            response = await call_next(request)
        finally:
            end_authorization_audit(audit_token)
        session_factory = getattr(request.app.state.gateway, "authorization_session_factory", None)
        audit_successful_reads = bool(web_config.get("audit_successful_reads", False))
        if session_factory is not None and decisions and response.status_code != 401:
            retained = [
                decision
                for decision in decisions
                # Denials and state-changing requests are always retained.
                # Successful GET/HEAD authorization checks are high-volume
                # telemetry; keep them opt-in because the endpoint-specific
                # audit events still record sensitive reads and writes.
                if not (
                    decision.decision == "allow"
                    and request.method in {"GET", "HEAD"}
                    and not audit_successful_reads
                )
            ]
            if retained:
                request_id = request.state.request_id

                async def flush_authorization_audit(
                    factory=session_factory,
                    decisions_to_write=retained,
                    rid=request_id,
                ) -> None:
                    try:
                        async with factory() as session:
                            async with session.begin():
                                for decision in decisions_to_write:
                                    await rbac_service.audit(
                                        session, actor_user_id=decision.actor_user_id, target_user_id=None,
                                        decision=decision.decision, reason_code="authorization_required",
                                        permission_code=decision.permission_code, resource_type=decision.resource_type,
                                        resource_id=decision.resource_id,
                                        detail={"workspace_id": decision.workspace_id, "request_id": rid},
                                    )
                    except Exception as audit_exc:
                        logging.getLogger("audit").warning("authorization audit flush failed: %s", audit_exc)

                # Defer the flush until after the request-scoped transaction
                # commits.  The audit INSERT takes a foreign-key S-lock on the
                # actor's ``nlp_users`` row, which the still-open write
                # transaction holds exclusively until dependency teardown
                # commits it — flushing synchronously here deadlocks (the
                # response cannot be sent, so the commit never runs).
                audit_task = asyncio.get_running_loop().create_task(flush_authorization_audit())
                _pending_audit_tasks.add(audit_task)
                audit_task.add_done_callback(_pending_audit_tasks.discard)
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Cache-Control"] = "no-store"

        # Sliding session cookie: keep the browser cookie's Max-Age in step with
        # the server-side sliding expiry so a TTL increase also extends cookies
        # issued under the previous TTL.  Skip when the endpoint already touched
        # the cookie (login/guest set it, logout deletes it).
        active_auth = auth if request.app.state.auth_injected else database_auth
        claims = getattr(request.state, "auth_claims", None)
        cookie_already_set = any(
            header.split("=", 1)[0].strip().lower() == active_auth.cookie_name.lower()
            for header in response.headers.getlist("set-cookie")
        )
        if claims is not None and not cookie_already_set:
            token = request.cookies.get(active_auth.cookie_name)
            if token:
                response.set_cookie(
                    active_auth.cookie_name,
                    token,
                    max_age=active_auth.ttl_s,
                    httponly=True,
                    secure=cookie_secure,
                    samesite="lax",
                    path="/",
                )
        return response

    async def current_claims(
        request: Request,
        token: Annotated[str | None, Security(cookie_auth)],
    ) -> SessionClaims | DatabaseSessionClaims:
        factory = getattr(request.app.state.gateway, "authorization_session_factory", None)
        if not request.app.state.auth_injected:
            if factory is None:
                raise AuthenticationError("database authentication is unavailable")
            claims = await database_auth.authenticate(factory, token)
            request.state.auth_claims = claims
            return claims
        claims = auth.authenticate(token)
        request.state.auth_claims = claims
        return claims

    async def resolve_principal(
        request: Request, claims: SessionClaims | DatabaseSessionClaims
    ) -> AuthenticatedPrincipal:
        """Resolve roles and workspace membership from MySQL in production."""
        if isinstance(claims, DatabaseSessionClaims):
            factory = getattr(request.app.state.gateway, "authorization_session_factory", None)
            if factory is None:
                raise AuthenticationError("database authentication is unavailable")
            async with factory() as session:
                return await rbac_service.principal_for_user_id(session, claims.user_id)
        # Guests are deliberately anonymous and never require an nlp_users row.
        if claims.roles == frozenset({"guest"}):
            return claims.principal()
        session_factory = getattr(
            request.app.state.gateway, "authorization_session_factory", None
        )
        if session_factory is None:
            return claims.principal()
        async with session_factory() as session:
            return await rbac_service.principal_for_username(session, claims.user_id)

    async def current_principal(
        request: Request,
        claims: Annotated[SessionClaims | DatabaseSessionClaims, Depends(current_claims)],
    ) -> AuthenticatedPrincipal:
        return await resolve_principal(request, claims)

    async def account_identity(
        request: Request,
        claims: SessionClaims | DatabaseSessionClaims,
        principal: AuthenticatedPrincipal,
    ) -> tuple[str, str]:
        """Return stable human-readable account fields for the browser session."""
        if not isinstance(claims, DatabaseSessionClaims):
            # Injected/local authentication has no separate user-profile table.
            return principal.user_id, principal.user_id
        factory = getattr(request.app.state.gateway, "authorization_session_factory", None)
        if factory is None:
            raise AuthenticationError("database authentication is unavailable")
        async with factory() as session:
            row = (
                await session.execute(
                    select(UserModel.username, UserModel.display_name).where(
                        UserModel.id == principal.user_id,
                        UserModel.deleted_at.is_(None),
                    )
                )
            ).one_or_none()
        if row is None:
            raise AuthenticationError("authenticated user profile is unavailable")
        return row.username, row.display_name

    async def write_access(
        request: Request,
        claims: Annotated[SessionClaims | DatabaseSessionClaims, Depends(current_claims)],
        csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    ) -> SessionClaims | DatabaseSessionClaims:
        if isinstance(claims, DatabaseSessionClaims):
            database_auth.require_same_origin(request.headers.get("origin"), request.headers.get("host"))
            database_auth.require_csrf(claims, csrf_token)
        else:
            auth.require_same_origin(request.headers.get("origin"), request.headers.get("host"))
            auth.require_csrf(claims, csrf_token)
        return claims

    Principal = Annotated[AuthenticatedPrincipal, Depends(current_principal)]
    WriteClaims = Annotated[
        SessionClaims | DatabaseSessionClaims, Depends(write_access)
    ]

    @app.exception_handler(AuthenticationError)
    async def authentication_error(request: Request, _error: AuthenticationError):
        return _problem(
            request,
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="authentication_required",
            title="Authentication required",
        )

    @app.exception_handler(OriginRejectedError)
    async def origin_error(request: Request, _error: OriginRejectedError):
        return _problem(
            request,
            status_code=status.HTTP_403_FORBIDDEN,
            code="origin_rejected",
            title="Origin rejected",
        )

    @app.exception_handler(CsrfRejectedError)
    async def csrf_error(request: Request, _error: CsrfRejectedError):
        return _problem(
            request,
            status_code=status.HTTP_403_FORBIDDEN,
            code="csrf_rejected",
            title="CSRF validation failed",
        )

    @app.exception_handler(AccessDeniedError)
    async def access_error(request: Request, _error: AccessDeniedError):
        return _problem(
            request,
            status_code=status.HTTP_403_FORBIDDEN,
            code="forbidden",
            title="Access forbidden",
        )

    @app.exception_handler(PermissionError)
    async def permission_error(request: Request, _error: PermissionError):
        return _problem(
            request,
            status_code=status.HTTP_403_FORBIDDEN,
            code="forbidden",
            title="Access forbidden",
        )

    @app.exception_handler(ResourceNotFoundError)
    @app.exception_handler(FileNotFoundError)
    async def not_found_error(request: Request, _error: Exception):
        return _problem(
            request,
            status_code=status.HTTP_404_NOT_FOUND,
            code="not_found",
            title="Resource not found",
        )

    @app.exception_handler(TurnConflictError)
    async def conflict_error(request: Request, error: TurnConflictError):
        return _problem(
            request,
            status_code=status.HTTP_409_CONFLICT,
            code="turn_conflict",
            title="Session already has an active turn",
            detail=str(error),
        )

    @app.exception_handler(GatewayNotStartedError)
    async def gateway_error(request: Request, _error: GatewayNotStartedError):
        return _problem(
            request,
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="gateway_unavailable",
            title="Backend Gateway is not ready",
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError):
        return _problem(
            request,
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            code="validation_error",
            title="Request validation failed",
            detail=str(error.errors()),
        )

    @app.exception_handler(DeveloperConfigurationError)
    async def developer_configuration_error(request: Request, error: DeveloperConfigurationError):
        return _problem(
            request,
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            code="developer_configuration_invalid",
            title="Developer configuration is invalid",
            detail=str(error),
        )

    @app.get("/health/live", tags=["health"])
    async def health_live():
        return {"status": "ok"}

    @app.get("/health/ready", tags=["health"])
    async def health_ready(request: Request):
        health = await request.app.state.gateway.health()
        ready = health.started and health.accepting_turns and health.status == "ok"
        return JSONResponse(
            {
                "status": "ready" if ready else "not_ready",
                "active_turns": health.active_turns,
                "durable_events": health.durable_events,
            },
            status_code=200 if ready else 503,
        )

    @app.get("/api/v1/auth/captcha", tags=["auth"])
    async def get_captcha(db: DbSession):
        """Generate a CAPTCHA image for registration/SMS verification.

        The answer is persisted in ``nlp_auth_codes`` (shared across all
        instances) with a short TTL; verification goes through
        ``code_store.consume_code``.
        """
        captcha_id, image_data, code = generate_captcha_image()
        await code_store.put_code(
            db,
            kind="captcha",
            subject=captcha_id,
            code=code,
            ttl_s=code_store.CAPTCHA_TTL_S,
        )
        return {"captcha_id": captcha_id, "image": image_data}

    @app.post("/api/v1/auth/sms/send", status_code=status.HTTP_200_OK, tags=["auth"])
    async def send_sms_code(body: SmsCodeRequest, db: DbSession, request: Request):
        """Validate the image CAPTCHA, then issue an SMS verification code.

        Server-side controls (the frontend 60s countdown is UX only):
        - the CAPTCHA answer is consumed single-use from ``nlp_auth_codes``;
          - per-phone cooldown / per-phone hourly / per-IP hourly send limits;
          - real delivery via Tencent Cloud SMS when ``TENCENT_SMS_*`` env vars
            are configured; missing production configuration returns 503 and
            never prints the verification code;
        - the code is stored hashed with a hard 120s expiry enforced at
          consumption time.
        """
        await code_store.purge_expired(db)
        if not await code_store.consume_code(
            db, kind="captcha", subject=body.captcha_id, code=body.captcha_code
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid or expired CAPTCHA",
            )
        try:
            phone = normalize_phone_number(body.phone_number)
        except InvalidPhoneNumberError as error:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error
        client_ip = request.client.host if request.client else None
        try:
            async with code_store.sms_send_lock(db, phone):
                allowed, reason = await code_store.sms_send_allowed(db, phone=phone, client_ip=client_ip)
                if not allowed:
                    raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=reason)
                code = generate_sms_code()
                provider = create_tencent_sms_provider_from_env()
                if provider is not None:
                    if not await provider.send_verification_code(phone, code):
                        await code_store.record_sms_send(db, phone=phone, client_ip=client_ip, outcome="failed")
                        return JSONResponse({"detail": "SMS gateway failed to deliver the code"}, status_code=status.HTTP_502_BAD_GATEWAY)
                else:
                    print(f"[SMS] Verification code for {phone}: {code}")
                await code_store.record_sms_send(db, phone=phone, client_ip=client_ip, outcome="sent")
                await code_store.put_code(db, kind="sms", subject=phone, code=code, ttl_s=code_store.SMS_CODE_TTL_S, client_ip=client_ip)
        except TimeoutError as error:
            raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="sms_send_busy") from error
        except SmsConfigurationError as error:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)) from error
        return {"message": "SMS code sent successfully"}

    @app.post("/api/v1/auth/login", status_code=status.HTTP_200_OK, tags=["auth"])
    async def login(body: LoginBody, request: Request, response: Response):
        factory = getattr(request.app.state.gateway, "authorization_session_factory", None)
        if not request.app.state.auth_injected:
            if factory is None:
                raise AuthenticationError("database authentication is unavailable")
            database_auth.require_same_origin(request.headers.get("origin"), request.headers.get("host"))
            token, claims = await database_auth.login(
                factory,
                body.username,
                body.password,
                client_key=request.client.host if request.client else "unknown",
                previous_token=request.cookies.get(database_auth.cookie_name),
                workspace_id=body.workspace_id,
            )
        else:
            auth.require_same_origin(request.headers.get("origin"), request.headers.get("host"))
            token, claims = auth.login(
                body.username,
                body.password,
                client_key=request.client.host if request.client else "unknown",
                previous_token=request.cookies.get(auth.cookie_name),
            )
        response.set_cookie(
            auth.cookie_name,
            token,
            max_age=auth.ttl_s,
            httponly=True,
            secure=cookie_secure,
            samesite="lax",
            path="/",
        )
        resolved_principal = await resolve_principal(request, claims) if isinstance(claims, DatabaseSessionClaims) else None
        identity_principal = resolved_principal
        if identity_principal is None:
            assert isinstance(claims, SessionClaims)
            identity_principal = claims.principal()
        username, display_name = await account_identity(
            request,
            claims,
            identity_principal,
        )
        return {
            "user_id": claims.user_id,
            "username": username,
            "display_name": display_name,
            "workspace_ids": sorted(resolved_principal.workspace_ids) if resolved_principal is not None else sorted(claims.workspace_ids),
            "roles": sorted(resolved_principal.roles) if resolved_principal is not None else sorted(claims.roles),
            "permissions": sorted(resolved_principal.permissions) if resolved_principal is not None else [],
            "csrf_token": claims.csrf_token,
            "expires_at": claims.expires_at_epoch if isinstance(claims, DatabaseSessionClaims) else claims.expires_at,
            "ephemeral_secret": False if isinstance(claims, DatabaseSessionClaims) else auth.ephemeral_secret,
        }

    @app.get("/api/v1/auth/session", tags=["auth"])
    async def get_auth_session(
        request: Request, claims: Annotated[SessionClaims | DatabaseSessionClaims, Depends(current_claims)]
    ):
        if isinstance(claims, DatabaseSessionClaims):
            factory = getattr(request.app.state.gateway, "authorization_session_factory", None)
            if factory is None:
                raise AuthenticationError("database authentication is unavailable")
            csrf_token = await database_auth.rotate_csrf(factory, claims)
            claims = DatabaseSessionClaims(**{**claims.__dict__, "csrf_token": csrf_token})
        principal = await resolve_principal(request, claims)
        username, display_name = await account_identity(request, claims, principal)
        return {
            "user_id": principal.user_id,
            "username": username,
            "display_name": display_name,
            "workspace_ids": sorted(principal.workspace_ids),
            "roles": sorted(principal.roles),
            "permissions": sorted(principal.permissions),
            "csrf_token": claims.csrf_token,
            "expires_at": claims.expires_at_epoch if isinstance(claims, DatabaseSessionClaims) else claims.expires_at,
        }

    @app.post("/api/v1/auth/register", status_code=status.HTTP_201_CREATED, tags=["auth"])
    async def register_user(body: UserRegister, db: DbSession):
        """Register a phone account through the unified user service."""
        service = UserService(db)
        try:
            user = await service.register_user(body)
        except InvalidCaptchaError as error:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)
            ) from error
        except InvalidSmsCodeError as error:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)
            ) from error
        except (PhoneNumberAlreadyUsedError, UserAlreadyExistsError) as error:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail=str(error)
            ) from error
        except InvalidPhoneNumberError as error:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error
        return {
            "message": "User registered successfully",
            "user_id": user.id,
            "username": user.username,
            "display_name": user.display_name,
        }

    @app.post("/api/v1/auth/guest", status_code=status.HTTP_200_OK, tags=["auth"])
    async def guest_login(request: Request, response: Response):
        if not request.app.state.auth_injected:
            raise AuthenticationError("anonymous guest sessions are disabled; create a guest user")
        auth.require_same_origin(request.headers.get("origin"), request.headers.get("host"))
        token, claims = auth.issue_guest(
            previous_token=request.cookies.get(auth.cookie_name)
        )
        response.set_cookie(
            auth.cookie_name, token, max_age=auth.ttl_s, httponly=True,
            secure=cookie_secure, samesite="lax", path="/",
        )
        return {
            "user_id": claims.user_id, "workspace_ids": [], "roles": ["guest"],
            "csrf_token": claims.csrf_token, "expires_at": claims.expires_at,
        }

    @app.post("/api/v1/auth/ws-ticket", status_code=status.HTTP_200_OK, tags=["auth"])
    async def create_ws_ticket(
        request: Request,
        claims: WriteClaims,
    ):
        if not isinstance(claims, DatabaseSessionClaims):
            # Legacy test/monitor adapters still authenticate the WebSocket by
            # the explicitly injected in-process session.  The production app
            # never takes this branch; returning a marker keeps old adapters
            # source-compatible while the real deployment requires DB tickets.
            return {"ticket": "legacy-injected-session", "expires_in": 0}
        factory = getattr(request.app.state.gateway, "authorization_session_factory", None)
        if factory is None:
            raise AuthenticationError("database authentication is unavailable")
        origin = request.headers.get("origin")
        if not origin:
            raise OriginRejectedError("Origin header is required")
        ticket = await database_auth.issue_ws_ticket(
            factory,
            claims,
            origin=origin,
            host=request.headers.get("host"),
        )
        return {"ticket": ticket, "expires_in": 60}

    def authorization_session_factory(request: Request):
        factory = getattr(request.app.state.gateway, "authorization_session_factory", None)
        if factory is None:
            raise RuntimeError("RBAC administration requires MySQL persistence")
        return factory

    async def quota_subject_exists(
        request: Request,
        *,
        subject_type: str,
        subject_id: str,
    ) -> bool:
        """Reject quota records that cannot ever participate in admission.

        Quota tables intentionally keep owner IDs as strings so they can also
        represent role-selected and legacy scopes.  The management API still
        needs to verify real RBAC subjects before writing a grant or binding;
        otherwise a successful write could create an allocation that no
        request can ever use.  Lightweight SQLite quota-only test apps do not
        have an authorization session factory, so they retain their isolated
        test behavior.
        """
        if subject_type == "default" and subject_id == "*":
            return True
        factory = getattr(request.app.state.gateway, "authorization_session_factory", None)
        if factory is None:
            return True
        model_by_type = {
            "role": RoleModel,
            "user": UserModel,
            "workspace": WorkspaceModel,
            "classroom": ClassroomModel,
        }
        model = model_by_type.get(subject_type)
        if model is None:
            return False
        # Quota policy bindings and role-wide gifts address roles by their
        # stable public code (for example ``student``), while the RBAC table
        # stores an internal UUID as ``id``.  Looking up a role by ``id``
        # would reject every valid role allocation in the production API.
        subject_column = model.code if subject_type == "role" else model.id
        criteria = [subject_column == subject_id, model.status == "active"]
        if model is UserModel:
            criteria.append(model.deleted_at.is_(None))
        async with factory() as session:
            return (await session.scalar(select(model.id).where(*criteria))) is not None

    async def require_quota_subject(
        request: Request,
        *,
        subject_type: str,
        subject_id: str,
    ) -> Response | None:
        if await quota_subject_exists(request, subject_type=subject_type, subject_id=subject_id):
            return None
        return _problem(
            request,
            status_code=422,
            code="quota_subject_not_found",
            title="额度主体不存在",
            detail=f"active {subject_type} subject {subject_id!r} does not exist",
        )

    def quota_management_for(request: Request) -> QuotaManagementService:
        service = getattr(request.app.state, "quota_management", None)
        if service is not None:
            return service
        quota_service = getattr(request.app.state.gateway, "quota_service", None)
        if quota_service is None:
            raise RuntimeError("Quota management requires the Phase 3 database schema")
        return QuotaManagementService(quota_service.engine)

    def quota_operations_for(request: Request) -> QuotaOperationsService:
        service = getattr(request.app.state, "quota_operations", None)
        if service is not None:
            return service
        quota_service = getattr(request.app.state.gateway, "quota_service", None)
        if quota_service is None:
            raise RuntimeError("Quota operations require the Phase 4 database schema")
        return QuotaOperationsService(quota_service.engine)

    async def audit_quota_change(
        request: Request,
        principal: AuthenticatedPrincipal,
        *,
        reason_code: str,
        resource_type: str,
        resource_id: str | None,
        detail: dict[str, Any],
    ) -> None:
        async with authorization_session_factory(request)() as session:
            async with session.begin():
                await rbac_service.audit(
                    session,
                    actor_user_id=principal.user_id,
                    target_user_id=(
                        detail.get("owner_id")
                        if detail.get("owner_type") == "user"
                        else None
                    ),
                    decision="allow",
                    reason_code=reason_code,
                    permission_code=Permission.SYSTEM_QUOTA_MANAGE.value,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    detail=detail,
                )

    async def emit_quota_snapshot(
        *,
        owner_type: str | None = None,
        owner_id: str | None = None,
    ) -> None:
        event = control_event(
            "usage.snapshot",
            payload={
                "owner_type": owner_type,
                "owner_id": owner_id,
                "refresh_required": True,
            },
        )
        redis_client = getattr(app.state, "quota_snapshot_redis", None)
        if redis_client is not None:
            try:
                await redis_client.publish(
                    getattr(
                        app.state,
                        "quota_snapshot_channel",
                        DEFAULT_QUOTA_SNAPSHOT_CHANNEL,
                    ),
                    json.dumps(
                        {
                            "owner_type": owner_type,
                            "owner_id": owner_id,
                            "refresh_required": True,
                        },
                        separators=(",", ":"),
                    ),
                )
                return
            except Exception:
                logger.exception("quota snapshot broadcast publish failed")
        if owner_type == "user" and owner_id:
            await hub.broadcast(event, user_id=owner_id)
        elif owner_type == "workspace" and owner_id:
            await hub.broadcast(event, workspace_id=owner_id)
        else:
            await hub.broadcast(event)

    def quota_domain_problem(request: Request, error: QuotaDomainError) -> JSONResponse:
        return _problem(
            request,
            status_code=status.HTTP_409_CONFLICT,
            code=error.code.value,
            title="Quota management conflict",
            detail=str(error),
        )

    @app.get("/api/v1/developer/quota/policies", tags=["quota"])
    async def list_quota_policies(
        request: Request,
        principal: Principal,
        code: str | None = Query(default=None, max_length=128),
    ):
        authorization_service.require(principal, Permission.SYSTEM_QUOTA_READ)
        return {"items": await asyncio.to_thread(quota_management_for(request).list_policies, code=code)}

    @app.get("/api/v1/developer/quota/pricing-rules", tags=["quota"])
    async def list_quota_pricing_rules(
        request: Request,
        principal: Principal,
        pricing_key: str | None = Query(default=None, max_length=255),
    ):
        authorization_service.require(principal, Permission.SYSTEM_QUOTA_READ)
        return {"items": await asyncio.to_thread(
            quota_management_for(request).list_pricing_rules,
            pricing_key=pricing_key,
        )}

    @app.get("/api/v1/developer/quota/pricing-rules/{pricing_rule_id}", tags=["quota"])
    async def get_quota_pricing_rule(
        pricing_rule_id: str,
        request: Request,
        principal: Principal,
    ):
        authorization_service.require(principal, Permission.SYSTEM_QUOTA_READ)
        try:
            return await asyncio.to_thread(
                quota_management_for(request).get_pricing_rule,
                pricing_rule_id,
            )
        except QuotaDomainError as error:
            return quota_domain_problem(request, error)

    @app.post(
        "/api/v1/developer/quota/pricing-rules",
        status_code=status.HTTP_201_CREATED,
        tags=["quota"],
    )
    async def create_quota_pricing_rule(
        body: QuotaPricingRuleBody,
        request: Request,
        principal: Principal,
        _claims: WriteClaims,
    ):
        authorization_service.require(principal, Permission.SYSTEM_QUOTA_MANAGE)
        try:
            row = await asyncio.to_thread(
                quota_management_for(request).create_pricing_rule,
                **body.model_dump(),
                created_by=principal.user_id,
            )
        except QuotaDomainError as error:
            return quota_domain_problem(request, error)
        except ValueError as error:
            return _problem(
                request,
                status_code=422,
                code="quota_pricing_rule_invalid",
                title="价格规则创建失败",
                detail=str(error),
            )
        await audit_quota_change(
            request,
            principal,
            reason_code="quota_pricing_rule_created",
            resource_type="quota_pricing_rule",
            resource_id=row["pricing_rule_id"],
            detail={
                "pricing_key": row["pricing_key"],
                "version": row["version"],
            },
        )
        return row

    @app.delete("/api/v1/developer/quota/pricing-rules/{pricing_rule_id}", tags=["quota"])
    async def retire_quota_pricing_rule(
        pricing_rule_id: str,
        request: Request,
        principal: Principal,
        _claims: WriteClaims,
    ):
        authorization_service.require(principal, Permission.SYSTEM_QUOTA_MANAGE)
        try:
            row = await asyncio.to_thread(
                quota_management_for(request).retire_pricing_rule,
                pricing_rule_id,
                actor_user_id=principal.user_id,
            )
        except QuotaDomainError as error:
            return quota_domain_problem(request, error)
        await audit_quota_change(
            request,
            principal,
            reason_code="quota_pricing_rule_retired",
            resource_type="quota_pricing_rule",
            resource_id=pricing_rule_id,
            detail={
                "pricing_key": row["pricing_key"],
                "version": row["version"],
            },
        )
        return row

    @app.get("/api/v1/developer/quota/policies/{policy_id}", tags=["quota"])
    async def get_quota_policy(policy_id: str, request: Request, principal: Principal):
        authorization_service.require(principal, Permission.SYSTEM_QUOTA_READ)
        try:
            return await asyncio.to_thread(quota_management_for(request).get_policy, policy_id)
        except QuotaDomainError as error:
            return quota_domain_problem(request, error)

    @app.post("/api/v1/developer/quota/policies", status_code=status.HTTP_201_CREATED, tags=["quota"])
    async def create_quota_policy(
        body: QuotaPolicyBody,
        request: Request,
        principal: Principal,
        _claims: WriteClaims,
    ):
        authorization_service.require(principal, Permission.SYSTEM_QUOTA_MANAGE)
        try:
            row = await asyncio.to_thread(
                quota_management_for(request).create_policy,
                **body.model_dump(),
                created_by=principal.user_id,
            )
        except (QuotaDomainError, ValueError) as error:
            if isinstance(error, QuotaDomainError):
                return quota_domain_problem(request, error)
            return _problem(request, status_code=422, code="quota_policy_invalid", title="策略创建失败", detail=str(error))
        await audit_quota_change(
            request,
            principal,
            reason_code="quota_policy_created",
            resource_type="quota_policy",
            resource_id=row["policy_id"],
            detail={"code": row["code"], "version": row["version"], "status": row["status"]},
        )
        await emit_quota_snapshot()
        return row

    @app.patch("/api/v1/developer/quota/policies/{policy_id}", tags=["quota"])
    async def update_quota_policy(
        policy_id: str,
        body: QuotaPolicyUpdateBody,
        request: Request,
        principal: Principal,
        _claims: WriteClaims,
    ):
        authorization_service.require(principal, Permission.SYSTEM_QUOTA_MANAGE)
        try:
            row = await asyncio.to_thread(
                quota_management_for(request).update_policy,
                policy_id,
                actor_user_id=principal.user_id,
                **body.model_dump(exclude_unset=True),
            )
        except QuotaDomainError as error:
            return quota_domain_problem(request, error)
        except ValueError as error:
            return _problem(request, status_code=422, code="quota_policy_invalid", title="策略更新失败", detail=str(error))
        await audit_quota_change(
            request,
            principal,
            reason_code="quota_policy_updated",
            resource_type="quota_policy",
            resource_id=policy_id,
            detail={"code": row["code"], "version": row["version"], "status": row["status"]},
        )
        await emit_quota_snapshot()
        return row

    # Keep DELETE for the REST CRUD contract and expose an explicit action
    # endpoint as well.  Some local reverse proxies reject DELETE even though
    # Uvicorn supports it; both paths execute the same audited archive logic.
    @app.post("/api/v1/developer/quota/policies/{policy_id}/archive", tags=["quota"])
    @app.delete("/api/v1/developer/quota/policies/{policy_id}", tags=["quota"])
    async def archive_quota_policy(
        policy_id: str,
        request: Request,
        principal: Principal,
        _claims: WriteClaims,
    ):
        authorization_service.require(principal, Permission.SYSTEM_QUOTA_MANAGE)
        try:
            row = await asyncio.to_thread(
                quota_management_for(request).archive_policy,
                policy_id,
                actor_user_id=principal.user_id,
            )
        except QuotaDomainError as error:
            return quota_domain_problem(request, error)
        await audit_quota_change(
            request,
            principal,
            reason_code="quota_policy_archived",
            resource_type="quota_policy",
            resource_id=policy_id,
            detail={"code": row["code"], "version": row["version"], "status": row["status"]},
        )
        await emit_quota_snapshot()
        return row

    @app.post("/api/v1/developer/quota/policies/{policy_id}/publish", tags=["quota"])
    async def publish_quota_policy(
        policy_id: str,
        request: Request,
        principal: Principal,
        _claims: WriteClaims,
    ):
        authorization_service.require(principal, Permission.SYSTEM_QUOTA_MANAGE)
        try:
            row = await asyncio.to_thread(
                quota_management_for(request).publish_policy,
                policy_id,
                actor_user_id=principal.user_id,
            )
        except QuotaDomainError as error:
            return quota_domain_problem(request, error)
        await audit_quota_change(
            request,
            principal,
            reason_code="quota_policy_published",
            resource_type="quota_policy",
            resource_id=policy_id,
            detail={"code": row["code"], "version": row["version"]},
        )
        await emit_quota_snapshot()
        return row

    @app.get("/api/v1/developer/quota/bindings", tags=["quota"])
    async def list_quota_bindings(
        request: Request,
        principal: Principal,
        subject_type: str | None = Query(default=None, max_length=16),
        subject_id: str | None = Query(default=None, max_length=128),
    ):
        authorization_service.require(principal, Permission.SYSTEM_QUOTA_READ)
        return {"items": await asyncio.to_thread(
            quota_management_for(request).list_bindings,
            subject_type=subject_type,
            subject_id=subject_id,
        )}

    @app.get("/api/v1/developer/quota/bindings/{binding_id}", tags=["quota"])
    async def get_quota_binding(binding_id: str, request: Request, principal: Principal):
        authorization_service.require(principal, Permission.SYSTEM_QUOTA_READ)
        try:
            return await asyncio.to_thread(quota_management_for(request).get_binding, binding_id)
        except QuotaDomainError as error:
            return quota_domain_problem(request, error)

    @app.delete("/api/v1/developer/quota/bindings/{binding_id}", tags=["quota"])
    async def retire_quota_binding(
        binding_id: str,
        request: Request,
        principal: Principal,
        _claims: WriteClaims,
    ):
        authorization_service.require(principal, Permission.SYSTEM_QUOTA_MANAGE)
        try:
            row = await asyncio.to_thread(
                quota_management_for(request).retire_binding,
                binding_id,
                actor_user_id=principal.user_id,
            )
        except QuotaDomainError as error:
            return quota_domain_problem(request, error)
        await audit_quota_change(
            request,
            principal,
            reason_code="quota_policy_binding_retired",
            resource_type="quota_policy_binding",
            resource_id=binding_id,
            detail={"subject_type": row["subject_type"], "subject_id": row["subject_id"], "status": row["status"]},
        )
        await emit_quota_snapshot()
        return row

    @app.post("/api/v1/developer/quota/bindings", status_code=status.HTTP_201_CREATED, tags=["quota"])
    async def bind_quota_policy(
        body: QuotaBindingBody,
        request: Request,
        principal: Principal,
        _claims: WriteClaims,
    ):
        authorization_service.require(principal, Permission.SYSTEM_QUOTA_MANAGE)
        subject_error = await require_quota_subject(
            request,
            subject_type=body.subject_type,
            subject_id=body.subject_id,
        )
        if subject_error is not None:
            return subject_error
        try:
            row = await asyncio.to_thread(quota_management_for(request).bind_policy, **body.model_dump())
        except (QuotaDomainError, ValueError) as error:
            if isinstance(error, QuotaDomainError):
                return quota_domain_problem(request, error)
            return _problem(request, status_code=422, code="quota_binding_invalid", title="策略绑定失败", detail=str(error))
        await audit_quota_change(
            request,
            principal,
            reason_code="quota_policy_bound",
            resource_type="quota_policy_binding",
            resource_id=row["binding_id"],
            detail={"subject_type": row["subject_type"], "subject_id": row["subject_id"], "policy_id": row["policy_id"]},
        )
        await emit_quota_snapshot()
        return row

    @app.get("/api/v1/developer/quota/grants", tags=["quota"])
    async def list_quota_grants(
        request: Request,
        principal: Principal,
        owner_type: str | None = Query(default=None, max_length=16),
        owner_id: str | None = Query(default=None, max_length=128),
    ):
        authorization_service.require(principal, Permission.SYSTEM_QUOTA_READ)
        return {"items": await asyncio.to_thread(
            quota_management_for(request).list_grants,
            owner_type=owner_type,
            owner_id=owner_id,
        )}

    @app.get("/api/v1/developer/quota/grants/{grant_id}", tags=["quota"])
    async def get_quota_grant(grant_id: str, request: Request, principal: Principal):
        authorization_service.require(principal, Permission.SYSTEM_QUOTA_READ)
        try:
            return await asyncio.to_thread(quota_management_for(request).get_grant, grant_id)
        except QuotaDomainError as error:
            return quota_domain_problem(request, error)

    @app.post("/api/v1/developer/quota/grants", status_code=status.HTTP_201_CREATED, tags=["quota"])
    async def create_quota_grant(
        body: QuotaGrantBody,
        request: Request,
        principal: Principal,
        _claims: WriteClaims,
    ):
        authorization_service.require(principal, Permission.SYSTEM_QUOTA_MANAGE)
        subject_error = await require_quota_subject(
            request,
            subject_type=body.owner_type,
            subject_id=body.owner_id,
        )
        if subject_error is not None:
            return subject_error
        try:
            row = await asyncio.to_thread(
                quota_management_for(request).create_grant,
                **body.model_dump(),
                created_by=principal.user_id,
            )
        except (QuotaDomainError, ValueError) as error:
            if isinstance(error, QuotaDomainError):
                return quota_domain_problem(request, error)
            return _problem(request, status_code=422, code="quota_grant_invalid", title="额度 Grant 创建失败", detail=str(error))
        await audit_quota_change(
            request,
            principal,
            reason_code="quota_grant_created",
            resource_type="quota_grant",
            resource_id=row["grant_id"],
            detail={"owner_type": row["owner_type"], "owner_id": row["owner_id"], "allocated_micro": row["allocated_micro"]},
        )
        await emit_quota_snapshot(owner_type=row["owner_type"], owner_id=row["owner_id"])
        return row

    @app.post("/api/v1/developer/quota/grants/{grant_id}/revoke", tags=["quota"])
    @app.delete("/api/v1/developer/quota/grants/{grant_id}", tags=["quota"])
    async def revoke_quota_grant(
        grant_id: str,
        request: Request,
        principal: Principal,
        _claims: WriteClaims,
        body: QuotaGrantRevokeBody | None = None,
        idempotency_header: str | None = Header(
            default=None, alias="Idempotency-Key"
        ),
    ):
        authorization_service.require(principal, Permission.SYSTEM_QUOTA_MANAGE)
        idempotency_key = (
            body.idempotency_key
            if body is not None
            else idempotency_header or f"delete:{grant_id}"
        )
        try:
            row = await asyncio.to_thread(
                quota_management_for(request).revoke_grant,
                grant_id,
                actor_user_id=principal.user_id,
                idempotency_key=idempotency_key,
            )
        except QuotaDomainError as error:
            return quota_domain_problem(request, error)
        await audit_quota_change(
            request,
            principal,
            reason_code="quota_grant_revoked",
            resource_type="quota_grant",
            resource_id=grant_id,
            detail={"owner_type": row["owner_type"], "owner_id": row["owner_id"]},
        )
        await emit_quota_snapshot(
            owner_type=row["owner_type"], owner_id=row["owner_id"]
        )
        return row

    @app.get("/api/v1/developer/quota/adjustments", tags=["quota"])
    async def list_quota_adjustments(
        request: Request,
        principal: Principal,
        owner_type: str | None = Query(default=None, max_length=16),
        owner_id: str | None = Query(default=None, max_length=128),
    ):
        authorization_service.require(principal, Permission.SYSTEM_QUOTA_READ)
        return {"items": await asyncio.to_thread(
            quota_management_for(request).list_adjustments,
            owner_type=owner_type,
            owner_id=owner_id,
        )}

    @app.get("/api/v1/developer/quota/adjustments/{adjustment_id}", tags=["quota"])
    async def get_quota_adjustment(adjustment_id: str, request: Request, principal: Principal):
        authorization_service.require(principal, Permission.SYSTEM_QUOTA_READ)
        try:
            return await asyncio.to_thread(quota_management_for(request).get_adjustment, adjustment_id)
        except QuotaDomainError as error:
            return quota_domain_problem(request, error)

    @app.post("/api/v1/developer/quota/adjustments", status_code=status.HTTP_201_CREATED, tags=["quota"])
    async def create_quota_adjustment(
        body: QuotaAdjustmentBody,
        request: Request,
        principal: Principal,
        _claims: WriteClaims,
    ):
        authorization_service.require(principal, Permission.SYSTEM_QUOTA_MANAGE)
        subject_error = await require_quota_subject(
            request,
            subject_type=body.owner_type,
            subject_id=body.owner_id,
        )
        if subject_error is not None:
            return subject_error
        try:
            row = await asyncio.to_thread(
                quota_management_for(request).create_adjustment,
                **body.model_dump(),
                actor_user_id=principal.user_id,
            )
        except (QuotaDomainError, ValueError) as error:
            if isinstance(error, QuotaDomainError):
                return quota_domain_problem(request, error)
            return _problem(request, status_code=422, code="quota_adjustment_invalid", title="手工调整失败", detail=str(error))
        await audit_quota_change(
            request,
            principal,
            reason_code="quota_adjustment_created",
            resource_type="quota_adjustment",
            resource_id=row["adjustment_id"],
            detail={"owner_type": row["owner_type"], "owner_id": row["owner_id"], "amount_micro": row["amount_micro"], "reason": row["reason"]},
        )
        await emit_quota_snapshot(owner_type=row["owner_type"], owner_id=row["owner_id"])
        return row

    @app.get("/api/v1/developer/quota/daily-rollups", tags=["quota"])
    async def list_quota_daily_rollups(
        request: Request,
        principal: Principal,
        start: date = Query(...),
        end: date = Query(...),
        user_id: str | None = Query(default=None, max_length=128),
        workspace_id: str | None = Query(default=None, max_length=128),
    ):
        authorization_service.require(principal, Permission.SYSTEM_QUOTA_READ)
        return {"items": await asyncio.to_thread(
            quota_operations_for(request).list_daily_rollups,
            start=start,
            end=end,
            user_id=user_id,
            workspace_id=workspace_id,
        )}

    @app.get("/api/v1/developer/quota/billing", tags=["quota"])
    async def list_quota_billing(
        request: Request,
        principal: Principal,
        reconciliation_status: str | None = Query(default=None, alias="status", max_length=16),
        limit: int = Query(default=100, ge=1, le=1000),
    ):
        authorization_service.require(principal, Permission.SYSTEM_QUOTA_READ)
        return {"items": await asyncio.to_thread(
            quota_operations_for(request).list_billing_reconciliation,
            status=reconciliation_status,
            limit=limit,
        )}

    @app.post("/api/v1/developer/quota/billing/reconcile", tags=["quota"])
    async def reconcile_quota_billing(
        body: QuotaBillingReconcileBody,
        request: Request,
        principal: Principal,
        _claims: WriteClaims,
    ):
        authorization_service.require(principal, Permission.SYSTEM_QUOTA_MANAGE)
        try:
            result = await asyncio.to_thread(
                quota_operations_for(request).reconcile_provider_billing,
                [item.model_dump() for item in body.statements],
            )
        except (QuotaDomainError, ValueError) as error:
            return _problem(request, status_code=422, code="quota_billing_invalid", title="账单对账失败", detail=str(error))
        await audit_quota_change(
            request,
            principal,
            reason_code="quota_provider_billing_reconciled",
            resource_type="quota_provider_billing",
            resource_id=None,
            detail={"total": result["total"], "discrepancies": result["discrepancies"]},
        )
        return result

    @app.post("/api/v1/developer/quota/billing/{billing_id}/repair", tags=["quota"])
    async def repair_quota_billing(
        billing_id: str,
        body: QuotaBillingRepairBody,
        request: Request,
        principal: Principal,
        _claims: WriteClaims,
    ):
        authorization_service.require(principal, Permission.SYSTEM_QUOTA_MANAGE)
        try:
            row = await asyncio.to_thread(
                quota_operations_for(request).repair_billing,
                billing_id,
                actor_user_id=principal.user_id,
                reason=body.reason,
                idempotency_key=body.idempotency_key,
            )
        except KeyError:
            return _problem(request, status_code=404, code="quota_billing_not_found", title="账单记录不存在")
        except (QuotaDomainError, ValueError) as error:
            return _problem(request, status_code=422, code="quota_billing_repair_invalid", title="账单修复失败", detail=str(error))
        await audit_quota_change(
            request,
            principal,
            reason_code="quota_provider_billing_repaired",
            resource_type="quota_provider_billing",
            resource_id=billing_id,
            detail={"status": row["status"], "reason": body.reason},
        )
        await emit_quota_snapshot()
        return row

    @app.post("/api/v1/developer/quota/credits/gift", status_code=status.HTTP_201_CREATED, tags=["quota"])
    async def gift_quota_credits(
        body: QuotaCreditOperationBody,
        request: Request,
        principal: Principal,
        _claims: WriteClaims,
    ):
        authorization_service.require(principal, Permission.SYSTEM_QUOTA_MANAGE)
        subject_error = await require_quota_subject(
            request,
            subject_type=body.owner_type,
            subject_id=body.owner_id,
        )
        if subject_error is not None:
            return subject_error
        try:
            row = await asyncio.to_thread(
                quota_operations_for(request).gift_credits,
                quota_management_for(request),
                **body.model_dump(),
                actor_user_id=principal.user_id,
            )
        except (QuotaDomainError, ValueError) as error:
            return _problem(request, status_code=422, code="quota_credit_gift_invalid", title="额度赠送失败", detail=str(error))
        await audit_quota_change(
            request,
            principal,
            reason_code="quota_credit_gifted",
            resource_type="quota_credit_operation",
            resource_id=row["operation_id"],
            detail={"operation_type": "gift", "owner_type": row["owner_type"], "owner_id": row["owner_id"], "amount_micro": row["amount_micro"]},
        )
        await emit_quota_snapshot(owner_type=body.owner_type, owner_id=body.owner_id)
        return row

    @app.get("/api/v1/developer/quota/credits", tags=["quota"])
    async def list_quota_credit_operations(
        request: Request,
        principal: Principal,
        limit: int = Query(default=100, ge=1, le=1000),
    ):
        authorization_service.require(principal, Permission.SYSTEM_QUOTA_READ)
        return {"items": await asyncio.to_thread(
            quota_operations_for(request).list_credit_operations,
            limit=limit,
        )}

    @app.post("/api/v1/developer/quota/credits/gift-role", status_code=status.HTTP_201_CREATED, tags=["quota"])
    async def gift_quota_credits_for_role(
        body: QuotaRoleCreditOperationBody,
        request: Request,
        principal: Principal,
        _claims: WriteClaims,
    ):
        authorization_service.require(principal, Permission.SYSTEM_QUOTA_MANAGE)
        subject_error = await require_quota_subject(
            request,
            subject_type="role",
            subject_id=body.role_code,
        )
        if subject_error is not None:
            return subject_error
        try:
            row = await asyncio.to_thread(
                quota_operations_for(request).gift_credits_for_role,
                quota_management_for(request),
                **body.model_dump(),
                actor_user_id=principal.user_id,
            )
        except (QuotaDomainError, ValueError) as error:
            return _problem(
                request,
                status_code=422,
                code="quota_role_credit_gift_invalid",
                title="角色额度赠送失败",
                detail=str(error),
            )
        await audit_quota_change(
            request,
            principal,
            reason_code="quota_role_credit_gifted",
            resource_type="quota_role_credit_operation",
            resource_id=row["target_id"],
            detail={
                "operation_type": "gift",
                "role_code": row["target_id"],
                "recipient_count": row["recipient_count"],
                "amount_micro": body.amount_micro,
            },
        )
        # A role gift can touch many users and their connections may live on
        # different workers.  The hub treats this as a broadcast invalidation;
        # each connected client then refreshes its own user snapshot.
        await emit_quota_snapshot()
        return row

    @app.post("/api/v1/developer/quota/credits/reset", status_code=status.HTTP_201_CREATED, tags=["quota"])
    async def reset_quota_credits(
        body: QuotaCreditOperationBody,
        request: Request,
        principal: Principal,
        _claims: WriteClaims,
    ):
        authorization_service.require(principal, Permission.SYSTEM_QUOTA_MANAGE)
        subject_error = await require_quota_subject(
            request,
            subject_type=body.owner_type,
            subject_id=body.owner_id,
        )
        if subject_error is not None:
            return subject_error
        try:
            row = await asyncio.to_thread(
                quota_operations_for(request).reset_credits,
                quota_management_for(request),
                **body.model_dump(),
                actor_user_id=principal.user_id,
            )
        except (QuotaDomainError, ValueError) as error:
            return _problem(request, status_code=422, code="quota_credit_reset_invalid", title="额度重置失败", detail=str(error))
        await audit_quota_change(
            request,
            principal,
            reason_code="quota_credit_reset",
            resource_type="quota_credit_operation",
            resource_id=row["operation_id"],
            detail={"operation_type": "reset", "owner_type": row["owner_type"], "owner_id": row["owner_id"], "amount_micro": row["amount_micro"]},
        )
        await emit_quota_snapshot(owner_type=body.owner_type, owner_id=body.owner_id)
        return row

    @app.get("/api/v1/developer/quota/alerts", tags=["quota"])
    async def list_quota_alerts(
        request: Request,
        principal: Principal,
        alert_status: str | None = Query(default=None, alias="status", max_length=16),
        limit: int = Query(default=100, ge=1, le=1000),
    ):
        authorization_service.require(principal, Permission.SYSTEM_QUOTA_READ)
        return {"items": await asyncio.to_thread(
            quota_operations_for(request).list_alerts,
            status=alert_status,
            limit=limit,
        )}

    @app.patch("/api/v1/developer/quota/alerts/{alert_id}", tags=["quota"])
    async def update_quota_alert(
        alert_id: str,
        body: QuotaAlertStatusBody,
        request: Request,
        principal: Principal,
        _claims: WriteClaims,
    ):
        authorization_service.require(principal, Permission.SYSTEM_QUOTA_MANAGE)
        try:
            row = await asyncio.to_thread(
                quota_operations_for(request).update_alert,
                alert_id,
                status=body.status,
                actor_user_id=principal.user_id,
                reason=body.reason,
            )
        except KeyError:
            return _problem(request, status_code=404, code="quota_alert_not_found", title="告警不存在")
        except ValueError as error:
            return _problem(request, status_code=422, code="quota_alert_update_invalid", title="告警状态更新失败", detail=str(error))
        await audit_quota_change(
            request,
            principal,
            reason_code="quota_alert_updated",
            resource_type="quota_alert",
            resource_id=alert_id,
            detail={"status": row["status"], "reason": body.reason},
        )
        return row

    @app.get("/api/v1/developer/quota/archive", tags=["quota"])
    async def list_quota_archive_batches(
        request: Request,
        principal: Principal,
        limit: int = Query(default=100, ge=1, le=1000),
    ):
        authorization_service.require(principal, Permission.SYSTEM_QUOTA_READ)
        return {"items": await asyncio.to_thread(
            quota_operations_for(request).list_archive_batches,
            limit=limit,
        )}

    @app.post("/api/v1/developer/quota/archive", tags=["quota"])
    async def archive_quota_usage_events(
        body: QuotaUsageArchiveBody,
        request: Request,
        principal: Principal,
        _claims: WriteClaims,
    ):
        authorization_service.require(principal, Permission.SYSTEM_QUOTA_MANAGE)
        result = await asyncio.to_thread(
            quota_operations_for(request).archive_usage_events,
            before=body.before,
            actor_user_id=principal.user_id,
            batch_size=body.batch_size,
        )
        if result["batch_id"] is not None:
            await audit_quota_change(
                request,
                principal,
                reason_code="quota_usage_archived",
                resource_type="quota_usage_archive_batch",
                resource_id=result["batch_id"],
                detail={"archived_events": result["archived_events"], "cutoff_at": result["cutoff_at"]},
            )
        return result

    @app.post("/api/v1/developer/quota/archive/purge", tags=["quota"])
    async def purge_quota_usage_events(
        body: QuotaUsageArchiveBody,
        request: Request,
        principal: Principal,
        _claims: WriteClaims,
    ):
        authorization_service.require(principal, Permission.SYSTEM_QUOTA_MANAGE)
        try:
            result = await asyncio.to_thread(
                quota_operations_for(request).purge_archived_usage_events,
                before=body.before,
                actor_user_id=principal.user_id,
                batch_size=body.batch_size,
            )
        except ValueError as error:
            return _problem(
                request,
                status_code=422,
                code="quota_archive_purge_blocked",
                title="归档数据尚不能清理",
                detail=str(error),
            )
        await audit_quota_change(
            request,
            principal,
            reason_code="quota_usage_purged",
            resource_type="quota_usage_archive",
            resource_id="retention-purge",
            detail={"deleted_events": result["deleted_events"], "cutoff_at": result["cutoff_at"]},
        )
        return result

    @app.get("/api/v1/developer/quota/buckets", tags=["quota"])
    async def list_quota_buckets(
        request: Request,
        principal: Principal,
        owner_type: str | None = Query(default=None, max_length=16),
        owner_id: str | None = Query(default=None, max_length=128),
        limit: int = Query(default=200, ge=1, le=1000),
    ):
        authorization_service.require(principal, Permission.SYSTEM_QUOTA_READ)
        return {"items": await asyncio.to_thread(
            quota_operations_for(request).list_buckets,
            owner_type=owner_type,
            owner_id=owner_id,
            limit=limit,
        )}

    @app.get("/api/v1/developer/quota/buckets/{bucket_id}/replay", tags=["quota"])
    async def replay_quota_bucket(bucket_id: str, request: Request, principal: Principal):
        authorization_service.require(principal, Permission.SYSTEM_QUOTA_READ)
        try:
            return (await asyncio.to_thread(quota_operations_for(request).replay_bucket, bucket_id)).as_dict()
        except KeyError:
            return _problem(request, status_code=404, code="quota_bucket_not_found", title="额度 Bucket 不存在")

    @app.post("/api/v1/developer/quota/buckets/{bucket_id}/repair", tags=["quota"])
    async def repair_quota_bucket(
        bucket_id: str,
        body: QuotaBillingRepairBody,
        request: Request,
        principal: Principal,
        _claims: WriteClaims,
    ):
        authorization_service.require(principal, Permission.SYSTEM_QUOTA_MANAGE)
        try:
            result = await asyncio.to_thread(
                quota_operations_for(request).repair_bucket,
                bucket_id,
                actor_user_id=principal.user_id,
                reason=body.reason,
                idempotency_key=body.idempotency_key,
            )
        except KeyError:
            return _problem(request, status_code=404, code="quota_bucket_not_found", title="额度 Bucket 不存在")
        await audit_quota_change(
            request,
            principal,
            reason_code="quota_bucket_repaired",
            resource_type="quota_bucket",
            resource_id=bucket_id,
            detail=result.as_dict(),
        )
        await emit_quota_snapshot()
        return result.as_dict()

    @app.get("/api/v1/roles", tags=["rbac"])
    async def list_roles(request: Request, principal: Principal):
        authorization_service.require(principal, Permission.SYSTEM_ROLE_MANAGE)
        async with authorization_session_factory(request)() as session:
            roles = await rbac_service.role_catalog(session)
        items = []
        for row in roles:
            name, description = role_display(
                row.code,
                fallback_name=row.name,
                fallback_description=row.description,
            )
            items.append(
                {
                    "code": row.code,
                    "name": name,
                    "description": description,
                    "status": row.status,
                    "is_builtin": row.is_builtin,
                }
            )
        return {"items": items}

    @app.get("/api/v1/permissions", tags=["rbac"])
    async def list_permissions(request: Request, principal: Principal):
        authorization_service.require(principal, Permission.SYSTEM_PERMISSION_READ)
        async with authorization_session_factory(request)() as session:
            permissions = await rbac_service.permission_catalog(session)
        items = []
        for row in permissions:
            name, description = permission_display(
                row.code,
                fallback_name=row.name,
                fallback_description=row.description,
            )
            items.append(
                {
                    "code": row.code,
                    "name": name,
                    "description": description,
                    "status": row.status,
                }
            )
        return {"items": items}

    @app.get("/api/v1/users/{user_id}/roles", tags=["rbac"])
    async def get_user_roles(user_id: str, request: Request, principal: Principal):
        authorization_service.require(principal, Permission.SYSTEM_ROLE_MANAGE)
        try:
            async with authorization_session_factory(request)() as session:
                roles = await rbac_service.user_role_codes(session, user_id)
        except (KeyError, PermissionError, ValueError) as error:
            raise _rbac_http_error(error) from error
        return {"user_id": user_id, "role_codes": sorted(roles)}

    @app.put("/api/v1/users/{user_id}/roles", tags=["rbac"])
    async def replace_user_roles(
        user_id: str,
        body: ReplaceUserRolesBody,
        request: Request,
        principal: Principal,
        _claims: WriteClaims,
    ):
        authorization_service.require(principal, Permission.SYSTEM_ROLE_MANAGE)
        try:
            async with authorization_session_factory(request)() as session:
                async with session.begin():
                    roles = await rbac_service.replace_user_roles(
                        session, user_id=user_id, role_codes=body.role_codes,
                        assigned_by_user_id=principal.user_id,
                    )
        except (KeyError, PermissionError, ValueError) as error:
            raise _rbac_http_error(error) from error
        await hub.close_user(user_id)
        return {"user_id": user_id, "role_codes": sorted(roles)}

    @app.get("/api/v1/system/roles/{role_code}/permissions", tags=["rbac"])
    async def get_role_permissions(role_code: str, request: Request, principal: Principal):
        authorization_service.require(principal, Permission.SYSTEM_PERMISSION_READ)
        try:
            async with authorization_session_factory(request)() as session:
                values = await rbac_service.role_permissions(session, role_code)
        except (KeyError, PermissionError, ValueError) as error:
            raise _rbac_http_error(error) from error
        return {"role_code": role_code, "permissions": {key: sorted(value) for key, value in values.items()}}

    @app.put("/api/v1/system/roles/{role_code}/permissions", tags=["rbac"])
    async def put_role_permissions(role_code: str, body: ReplaceRolePermissionsBody, request: Request, principal: Principal, _claims: WriteClaims):
        authorization_service.require(principal, Permission.SYSTEM_ROLE_MANAGE)
        try:
            async with authorization_session_factory(request)() as session:
                async with session.begin():
                    user_ids = await rbac_service.replace_role_permissions(session, role_code=role_code, permission_codes=body.permission_codes, scopes=body.scopes, actor_user_id=principal.user_id)
        except (KeyError, PermissionError, ValueError) as error:
            raise _rbac_http_error(error) from error
        for user_id in user_ids:
            await hub.close_user(user_id)
        return {"role_code": role_code, "permission_codes": sorted(body.permission_codes)}

    @app.get("/api/v1/classrooms", tags=["rbac"])
    async def list_classrooms(request: Request, principal: Principal):
        authorization_service.require(principal, Permission.LEARNING_PROGRESS_READ_CLASSROOM)
        async with authorization_session_factory(request)() as session:
            rows = await rbac_service.classrooms_for_user(session, principal.user_id)
        return {"items": [{"id": row.id, "workspace_id": row.workspace_id, "name": row.name, "status": row.status} for row in rows]}

    @app.post("/api/v1/classrooms", status_code=status.HTTP_201_CREATED, tags=["rbac"])
    async def create_classroom(body: CreateClassroomBody, request: Request, principal: Principal, _claims: WriteClaims):
        authorization_service.require(principal, Permission.CLASSROOM_CREATE, workspace_id=body.workspace_id)
        async with authorization_session_factory(request)() as session:
            async with session.begin():
                row = await rbac_service.create_classroom(session, workspace_id=body.workspace_id, name=body.name, actor_user_id=principal.user_id)
        return {"id": row.id, "workspace_id": row.workspace_id, "name": row.name, "status": row.status}

    @app.put("/api/v1/classrooms/{classroom_id}/members/{user_id}", tags=["rbac"])
    async def replace_classroom_member(classroom_id: str, user_id: str, body: ReplaceClassroomMemberBody, request: Request, principal: Principal, _claims: WriteClaims):
        async with authorization_session_factory(request)() as session:
            async with session.begin():
                classroom = await rbac_service.classroom(session, classroom_id)
                authorization_service.require_resource(principal, Permission.CLASSROOM_MEMBER_MANAGE, ResourceRef("classroom", workspace_id=classroom.workspace_id, classroom_id=classroom.id))
                await rbac_service.replace_classroom_member(session, classroom_id=classroom_id, user_id=user_id, member_role=body.member_role, status=body.status, actor_user_id=principal.user_id)
        await hub.close_user(user_id)
        return {"classroom_id": classroom_id, "user_id": user_id, "member_role": body.member_role, "status": body.status}

    @app.get("/api/v1/system/menus", tags=["rbac"])
    async def get_menus(request: Request, principal: Principal):
        authorization_service.require(principal, Permission.SYSTEM_PERMISSION_READ)
        async with authorization_session_factory(request)() as session:
            menus = await rbac_service.menus(session)
        return {"items": [
            {"id": item.id, "parent_id": item.parent_id, "type": item.menu_type,
             "name": item.name, "route_path": item.route_path,
             "component_key": item.component_key, "permission_id": item.permission_id,
             "client_scope": item.client_scope, "sort_order": item.sort_order,
             "visible": item.visible, "status": item.status}
            for item in menus
        ]}

    @app.get("/api/v1/system/menus/visible", tags=["rbac"])
    async def get_visible_menus(request: Request, principal: Principal):
        async with authorization_session_factory(request)() as session:
            menus = await rbac_service.visible_menus(session, principal)
        return {"items": [
            {"id": item.id, "parent_id": item.parent_id, "type": item.menu_type,
             "name": item.name, "route_path": item.route_path,
             "component_key": item.component_key, "permission_id": item.permission_id,
             "client_scope": item.client_scope, "sort_order": item.sort_order,
             "visible": item.visible, "status": item.status}
            for item in menus
        ]}

    @app.put("/api/v1/system/roles/{role_code}/menus", tags=["rbac"])
    async def put_role_menus(role_code: str, body: ReplaceRoleMenusBody, request: Request, principal: Principal, _claims: WriteClaims):
        authorization_service.require(principal, Permission.SYSTEM_ROLE_MANAGE)
        async with authorization_session_factory(request)() as session:
            async with session.begin():
                await rbac_service.replace_role_menus(session, role_code=role_code, menu_ids=body.menu_ids, actor_user_id=principal.user_id)
        return {"role_code": role_code, "menu_ids": sorted(body.menu_ids)}

    @app.get("/api/v1/system/roles/{role_code}/menus", tags=["rbac"])
    async def get_role_menus(role_code: str, request: Request, principal: Principal):
        authorization_service.require(principal, Permission.SYSTEM_PERMISSION_READ)
        async with authorization_session_factory(request)() as session:
            menu_ids = await rbac_service.role_menu_ids(session, role_code)
        return {"role_code": role_code, "menu_ids": sorted(menu_ids)}

    @app.get("/api/v1/audit/authorization", tags=["rbac"])
    async def list_authorization_audit(
        request: Request,
        principal: Principal,
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0, le=1_000_000),
        actor_user_id: str | None = None,
        decision: str | None = Query(default=None, pattern="^(allow|deny)$"),
        reason_code: str | None = Query(default=None, min_length=1, max_length=64),
    ):
        authorization_service.require(principal, Permission.SYSTEM_AUDIT_READ)
        async with authorization_session_factory(request)() as session:
            rows, total = await rbac_service.audit_page(
                session,
                limit=limit,
                offset=offset,
                actor_user_id=actor_user_id,
                decision=decision,
                reason_code=reason_code,
            )
        return {
            "items": [
                {
                    "id": row.id,
                    "actor_user_id": row.actor_user_id,
                    "target_user_id": row.target_user_id,
                    "decision": row.decision,
                    "reason_code": row.reason_code,
                    "permission_code": row.permission_code,
                    "resource_type": row.resource_type,
                    "resource_id": row.resource_id,
                    "detail": row.detail_json,
                    "created_at": row.created_at,
                }
                for row in rows
            ],
            "total": total,
            "offset": offset,
            "limit": limit,
            "has_more": offset + len(rows) < total,
        }

    @app.get("/api/v1/audit/authorization/stats", tags=["rbac"])
    async def authorization_audit_stats(
        request: Request,
        principal: Principal,
        days: int = Query(default=30, ge=1, le=3650),
    ):
        authorization_service.require(principal, Permission.SYSTEM_AUDIT_READ)
        since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
        async with authorization_session_factory(request)() as session:
            summary = await rbac_service.audit_summary(session, since=since)
        return {"period_days": days, "since": since, **summary}

    @app.get("/api/v1/system/sessions/{session_id}/checkpoints/{checkpoint_id}", tags=["rbac"])
    async def read_sensitive_checkpoint(session_id: str, checkpoint_id: str, request: Request, principal: Principal):
        # Runtime inspection alone is deliberately insufficient for prompt/output/checkpoint payloads.
        authorization_service.require(principal, Permission.SYSTEM_SENSITIVE_DATA_READ)
        async with authorization_session_factory(request)() as session:
            async with session.begin():
                checkpoint = await rbac_service.read_sensitive_checkpoint(
                    session, session_id=session_id, checkpoint_id=checkpoint_id,
                    actor_user_id=principal.user_id,
                    workspace_ids=principal.workspace_ids,
                )
                return {"session_id": checkpoint.session_id, "checkpoint_ns": checkpoint.checkpoint_ns,
                        "checkpoint_id": checkpoint.checkpoint_id, "checkpoint": checkpoint.checkpoint_json,
                        "metadata": checkpoint.metadata_json}

    @app.delete("/api/v1/auth/session", status_code=status.HTTP_204_NO_CONTENT, tags=["auth"])
    async def delete_auth_session(
        request: Request,
        response: Response,
        claims: WriteClaims,
    ):
        token = request.cookies.get(auth.cookie_name)
        if isinstance(claims, DatabaseSessionClaims):
            factory = getattr(request.app.state.gateway, "authorization_session_factory", None)
            if factory is None:
                raise AuthenticationError("database authentication is unavailable")
            session_fingerprint = database_auth.session_fingerprint_from_hash(
                claims.token_hash
            )
            await database_auth.revoke_token(factory, claims.token_hash)
        else:
            session_fingerprint = auth.token_fingerprint(token)
            auth.revoke(token)
        await hub.close_session(session_fingerprint)
        response.delete_cookie(auth.cookie_name, path="/", secure=cookie_secure, samesite="lax")

    @app.get("/api/v1/auth/sessions", tags=["auth"])
    async def list_auth_sessions(
        request: Request,
        claims: Annotated[SessionClaims | DatabaseSessionClaims, Depends(current_claims)],
    ):
        if not isinstance(claims, DatabaseSessionClaims):
            return {"items": []}
        factory = getattr(request.app.state.gateway, "authorization_session_factory", None)
        if factory is None:
            raise AuthenticationError("database authentication is unavailable")
        return {
            "items": await database_auth.list_user_sessions(
                factory, claims.user_id, current_session_id=claims.session_id
            )
        }

    @app.delete("/api/v1/auth/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["auth"])
    async def revoke_auth_session(
        session_id: str,
        request: Request,
        response: Response,
        claims: WriteClaims,
    ):
        if not isinstance(claims, DatabaseSessionClaims):
            raise AuthenticationError("database authentication is unavailable")
        factory = getattr(request.app.state.gateway, "authorization_session_factory", None)
        if factory is None:
            raise AuthenticationError("database authentication is unavailable")
        token_hash = await database_auth.revoke_session_id(
            factory, user_id=claims.user_id, session_id=session_id
        )
        if token_hash is None:
            raise AuthenticationError("authentication session is not owned by this user")
        await hub.close_session(database_auth.session_fingerprint_from_hash(token_hash))
        if session_id == claims.session_id:
            response.delete_cookie(auth.cookie_name, path="/", secure=cookie_secure, samesite="lax")

    @app.get("/api/v1/sessions", tags=["sessions"])
    async def list_sessions(
        request: Request,
        principal: Principal,
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0, le=1_000_000),
    ):
        authorization_service.require(principal, Permission.AGENT_SESSION_READ)
        service = request.app.state.gateway.sessions
        list_page = getattr(service, "list_page", None)
        if list_page is not None:
            return await list_page(principal, limit=limit, offset=offset)
        items = await service.list(principal)
        page = items[offset : offset + limit]
        return {
            "items": page,
            "total": len(items),
            "offset": offset,
            "limit": limit,
            "has_more": offset + len(page) < len(items),
        }

    @app.post("/api/v1/sessions", status_code=status.HTTP_201_CREATED, tags=["sessions"])
    async def create_session(
        body: CreateSessionBody,
        request: Request,
        principal: Principal,
        _claims: WriteClaims,
    ):
        session = await request.app.state.gateway.create_session(
            principal,
            workspace_id=body.workspace_id,
            channel="web",
        )
        await hub.broadcast(
            control_event(
                "session.created",
                session_id=session.session_id,
                payload={"workspace_id": session.workspace_id},
            ),
            user_id=principal.user_id,
        )
        return session.model_dump(mode="json")

    @app.get("/api/v1/sessions/{session_id}", tags=["sessions"])
    async def get_session(session_id: str, request: Request, principal: Principal):
        authorization_service.require(principal, Permission.AGENT_SESSION_READ)
        context = await request.app.state.gateway.sessions.resolve(principal, session_id)
        return context.model_dump(mode="json")

    @app.patch("/api/v1/sessions/{session_id}", tags=["sessions"])
    async def rename_session(
        session_id: str,
        body: RenameSessionBody,
        request: Request,
        principal: Principal,
        _claims: WriteClaims,
    ):
        result = await request.app.state.gateway.sessions.rename(
            principal, session_id, body.title
        )
        await hub.broadcast(
            control_event(
                "session.updated",
                session_id=session_id,
                payload={"scope": "title"},
            ),
            user_id=principal.user_id,
        )
        return result

    @app.get("/api/v1/sessions/{session_id}/messages", tags=["sessions"])
    async def get_messages(session_id: str, request: Request, principal: Principal):
        authorization_service.require(principal, Permission.AGENT_SESSION_READ)
        return {
            "items": await request.app.state.gateway.sessions.messages(principal, session_id)
        }

    @app.get("/api/v1/sessions/{session_id}/turns", tags=["sessions"])
    async def list_turns(
        session_id: str,
        request: Request,
        principal: Principal,
        limit: int = Query(default=100, ge=1, le=500),
    ):
        turns = await request.app.state.gateway.list_turns(
            principal,
            session_id,
            limit=limit,
        )
        return {"items": [turn.model_dump(mode="json") for turn in turns]}

    @app.delete("/api/v1/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["sessions"])
    async def delete_session(
        session_id: str,
        request: Request,
        principal: Principal,
        _claims: WriteClaims,
    ):
        await request.app.state.gateway.delete_session(principal, session_id)
        await hub.broadcast(
            control_event("session.deleted", session_id=session_id),
            user_id=principal.user_id,
        )

    @app.post("/api/v1/chat/turns", status_code=status.HTTP_202_ACCEPTED, tags=["chat"])
    async def submit_turn(
        body: SubmitChatBody,
        request: Request,
        principal: Principal,
        _claims: WriteClaims,
    ):
        accepted = await request.app.state.gateway.submit_turn(
            principal,
            SubmitTurnRequest(**body.model_dump()),
            auth_session_id=(
                _claims.session_id if isinstance(_claims, DatabaseSessionClaims) else None
            ),
        )
        return accepted.model_dump(mode="json")

    @app.get("/api/v1/chat/turns/{turn_id}", tags=["chat"])
    async def get_turn(turn_id: str, request: Request, principal: Principal):
        turn = await request.app.state.gateway.get_turn(principal, turn_id)
        return turn.model_dump(mode="json")

    @app.get("/api/v1/chat/turns/{turn_id}/events", tags=["chat"])
    async def replay_turn_events(
        turn_id: str,
        request: Request,
        principal: Principal,
        after_sequence: int = Query(default=0, ge=0),
        limit: int = Query(default=500, ge=1, le=2_000),
    ):
        events = await request.app.state.gateway.replay_events(
            principal,
            turn_id,
            after_sequence=after_sequence,
            limit=limit,
        )
        return {"items": [event.model_dump(mode="json") for event in events]}

    @app.post("/api/v1/chat/turns/{turn_id}/cancel", tags=["chat"])
    async def cancel_turn(
        turn_id: str,
        request: Request,
        principal: Principal,
        _claims: WriteClaims,
    ):
        turn = await request.app.state.gateway.cancel_turn(principal, turn_id)
        return turn.model_dump(mode="json")

    @app.post("/api/v1/chat/injections", status_code=status.HTTP_202_ACCEPTED, tags=["chat"])
    async def inject_message(
        body: InjectChatBody,
        request: Request,
        principal: Principal,
        _claims: WriteClaims,
    ):
        accepted = await request.app.state.gateway.inject_message(
            principal,
            InjectMessageRequest(**body.model_dump()),
        )
        return accepted.model_dump(mode="json")

    @app.post("/api/v1/tool-approvals", status_code=status.HTTP_201_CREATED, tags=["tools"])
    async def approve_tool(
        body: ToolApprovalBody,
        request: Request,
        principal: Principal,
        _claims: WriteClaims,
    ):
        return await request.app.state.gateway.grant_high_risk_tool(
            principal,
            **body.model_dump(),
        )

    @app.get("/api/v1/settings", tags=["settings"])
    async def get_settings(request: Request, principal: Principal):
        preferences = await request.app.state.gateway.get_user_settings(principal)
        return {"preferences": preferences, "runtime": _public_runtime_settings()}

    @app.post("/api/v1/feedback", status_code=status.HTTP_201_CREATED, tags=["feedback"])
    async def create_feedback(body: FeedbackBody, request: Request, principal: Principal, _claims: WriteClaims):
        authorization_service.require(principal, Permission.LEARNING_FEEDBACK_SUBMIT)
        session_factory = request.app.state.gateway.authorization_session_factory
        async with session_factory() as session:
            async with session.begin():
                try:
                    return await submit_feedback(session, principal, body.body, category=body.category)
                except ValueError as error:
                    if str(error) == "feedback_daily_limit":
                        return _problem(
                            request,
                            status_code=429,
                            code="feedback_daily_limit",
                            title="Daily feedback limit reached",
                            detail="每天最多可发送 3 条反馈，请明天再试。",
                        )
                    return _problem(
                        request,
                        status_code=422,
                        code="invalid_feedback",
                        title="反馈内容无效",
                        detail=str(error),
                    )

    @app.get("/api/v1/feedback/daily-state", tags=["feedback"])
    async def get_feedback_daily_state_route(request: Request, principal: Principal):
        authorization_service.require(principal, Permission.LEARNING_FEEDBACK_SUBMIT)
        session_factory = request.app.state.gateway.authorization_session_factory
        async with session_factory() as session:
            return await get_feedback_daily_state(session, principal)

    @app.get("/api/v1/feedback", tags=["feedback"])
    async def get_own_feedback_route(
        request: Request,
        principal: Principal,
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
    ):
        authorization_service.require(principal, Permission.LEARNING_FEEDBACK_SUBMIT)
        session_factory = request.app.state.gateway.authorization_session_factory
        async with session_factory() as session:
            return await get_own_feedback(
                session,
                principal,
                message_limit=limit,
                message_offset=offset,
            )

    @app.post("/api/v1/feedback/read", tags=["feedback"])
    async def mark_own_feedback_read_route(
        request: Request,
        principal: Principal,
        _claims: WriteClaims,
    ):
        authorization_service.require(principal, Permission.LEARNING_FEEDBACK_SUBMIT)
        session_factory = request.app.state.gateway.authorization_session_factory
        async with session_factory() as session:
            async with session.begin():
                updated = await mark_own_feedback_read(session, principal)
        return {"ok": True, "updated": updated}

    @app.get("/api/v1/developer/feedback", tags=["developer"])
    async def get_feedback_list(
        request: Request,
        principal: Principal,
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
        q: str | None = Query(default=None, max_length=64),
        status: FeedbackStatusValue | None = Query(default=None),
        category: FeedbackCategoryValue | None = Query(default=None),
        priority: FeedbackPriorityValue | None = Query(default=None),
        sort: FeedbackSortValue | None = Query(default=None),
    ):
        authorization_service.require_resource(
            principal, Permission.LEARNING_FEEDBACK_READ, ResourceRef("feedback")
        )
        session_factory = request.app.state.gateway.authorization_session_factory
        async with session_factory() as session:
            try:
                return await list_feedback_threads(
                    session,
                    limit=limit,
                    offset=offset,
                    search=q,
                    status=status,
                    category=category,
                    priority=priority,
                    sort=sort,
                )
            except ValueError as error:
                return _problem(
                    request,
                    status_code=422,
                    code="invalid_feedback_filter",
                    title="反馈筛选条件无效",
                    detail=str(error),
                )

    @app.post("/api/v1/developer/feedback/bulk-read", tags=["developer"])
    async def read_feedback_bulk(body: FeedbackBulkBody, request: Request, principal: Principal, _claims: WriteClaims):
        authorization_service.require_resource(
            principal, Permission.LEARNING_FEEDBACK_READ, ResourceRef("feedback")
        )
        session_factory = request.app.state.gateway.authorization_session_factory
        async with session_factory() as session:
            async with session.begin():
                updated = await mark_feedback_threads_read(session, body.thread_ids)
        return {"ok": True, "updated": updated}

    @app.post("/api/v1/developer/feedback/bulk-delete", tags=["developer"])
    async def delete_feedback_bulk(body: FeedbackBulkBody, request: Request, principal: Principal, _claims: WriteClaims):
        authorization_service.require_resource(
            principal, Permission.LEARNING_FEEDBACK_WRITE, ResourceRef("feedback")
        )
        session_factory = request.app.state.gateway.authorization_session_factory
        async with session_factory() as session:
            async with session.begin():
                deleted = await delete_feedback_threads(session, body.thread_ids)
        return {"ok": True, "deleted": deleted}

    @app.get("/api/v1/developer/feedback/{thread_id}", tags=["developer"])
    async def get_feedback_detail(
        thread_id: str,
        request: Request,
        principal: Principal,
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
    ):
        authorization_service.require_resource(
            principal, Permission.LEARNING_FEEDBACK_READ, ResourceRef("feedback")
        )
        session_factory = request.app.state.gateway.authorization_session_factory
        async with session_factory() as session:
            try:
                return await get_feedback_thread(
                    session,
                    thread_id,
                    message_limit=limit,
                    message_offset=offset,
                )
            except LookupError:
                return _problem(request, status_code=404, code="feedback_not_found", title="Feedback thread not found")

    @app.post("/api/v1/developer/feedback/{thread_id}/read", tags=["developer"])
    async def read_feedback(thread_id: str, body: FeedbackReadBody, request: Request, principal: Principal, _claims: WriteClaims):
        authorization_service.require_resource(
            principal, Permission.LEARNING_FEEDBACK_READ, ResourceRef("feedback")
        )
        session_factory = request.app.state.gateway.authorization_session_factory
        async with session_factory() as session:
            async with session.begin():
                try:
                    await mark_feedback_read(session, thread_id, body.read_through_message_id)
                except LookupError:
                    return _problem(request, status_code=404, code="feedback_not_found", title="Feedback thread not found")
        return {"ok": True}

    @app.delete("/api/v1/developer/feedback/{thread_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["developer"])
    async def delete_feedback(thread_id: str, request: Request, principal: Principal, _claims: WriteClaims):
        authorization_service.require_resource(
            principal, Permission.LEARNING_FEEDBACK_WRITE, ResourceRef("feedback")
        )
        session_factory = request.app.state.gateway.authorization_session_factory
        async with session_factory() as session:
            async with session.begin():
                try:
                    await delete_feedback_thread(session, thread_id)
                except LookupError:
                    return _problem(request, status_code=404, code="feedback_not_found", title="Feedback thread not found")
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.patch("/api/v1/developer/feedback/{thread_id}", tags=["developer"])
    async def patch_feedback(
        thread_id: str,
        body: FeedbackUpdateBody,
        request: Request,
        principal: Principal,
        _claims: WriteClaims,
    ):
        authorization_service.require_resource(
            principal, Permission.LEARNING_FEEDBACK_WRITE, ResourceRef("feedback")
        )
        session_factory = request.app.state.gateway.authorization_session_factory
        async with session_factory() as session:
            async with session.begin():
                try:
                    return await update_feedback_thread(
                        session,
                        thread_id,
                        status=body.status,
                        category=body.category,
                        priority=body.priority,
                    )
                except LookupError:
                    return _problem(request, status_code=404, code="feedback_not_found", title="Feedback thread not found")
                except ValueError as error:
                    return _problem(request, status_code=422, code="invalid_feedback_update", title="反馈更新无效", detail=str(error))

    @app.post("/api/v1/developer/feedback/{thread_id}/reply", tags=["developer"])
    async def reply_feedback_route(
        thread_id: str,
        body: FeedbackReplyBody,
        request: Request,
        principal: Principal,
        _claims: WriteClaims,
    ):
        authorization_service.require_resource(
            principal, Permission.LEARNING_FEEDBACK_WRITE, ResourceRef("feedback")
        )
        session_factory = request.app.state.gateway.authorization_session_factory
        async with session_factory() as session:
            async with session.begin():
                try:
                    return await reply_feedback(session, principal, thread_id, body.body)
                except LookupError:
                    return _problem(request, status_code=404, code="feedback_not_found", title="Feedback thread not found")
                except ValueError as error:
                    return _problem(request, status_code=422, code="invalid_feedback_reply", title="反馈回复无效", detail=str(error))

    @app.get("/api/v1/protocol", tags=["runtime"])
    async def get_protocol(_principal: Principal):
        return {
            "version": "1",
            "websocket_path": "/ws/v1",
            "commands": [
                "chat.send",
                "chat.inject",
                "chat.cancel",
                "session.subscribe",
                "session.unsubscribe",
                "stream.resume",
                "ping",
            ],
            "events": [
                "connection.ready",
                "command.ack",
                "command.error",
                "chat.accepted",
                "chat.started",
                "chat.delta",
                "chat.reasoning.delta",
                "chat.message.completed",
                "chat.completed",
                "chat.error",
                "chat.cancelled",
                "tool.started",
                "tool.progress",
                "tool.completed",
                "tool.error",
                "worker.started",
                "worker.progress",
                "worker.completed",
                "worker.error",
                "session.created",
                "session.updated",
                "session.deleted",
                "settings.updated",
                "usage.snapshot",
                "stream.gap",
                "pong",
                "server.shutdown",
            ],
            "limits": {
                "max_websocket_message_bytes": max_ws_message_bytes,
                "websocket_send_queue_size": ws_send_queue_size,
                "websocket_send_timeout_s": ws_send_timeout_s,
            },
        }

    @app.get("/api/v1/developer/snapshot", tags=["developer"])
    async def get_developer_snapshot(request: Request, principal: Principal):
        return await developer_snapshot(principal, request.app.state.gateway)

    @app.get("/api/v1/developer/health", tags=["developer"])
    async def get_developer_health(request: Request, principal: Principal):
        return await developer_health(principal, request.app.state.gateway)

    @app.put("/api/v1/developer/tools/policies", tags=["developer"])
    async def put_tool_policies(body: UpdateToolPoliciesBody, principal: Principal, _claims: WriteClaims):
        authorization_service.require(principal, Permission.SYSTEM_TOOL_CONFIG_MANAGE)
        return await update_tool_policies(body.policies)

    @app.put("/api/v1/developer/tools/custom", tags=["developer"])
    async def put_custom_tools(body: UpdateCustomToolsBody, principal: Principal, _claims: WriteClaims):
        authorization_service.require(principal, Permission.SYSTEM_TOOL_CONFIG_MANAGE)
        return await update_custom_tools(body.custom)

    @app.put("/api/v1/developer/mcp/{name}", tags=["developer"])
    async def put_mcp_server(name: str, body: McpServerBody, principal: Principal, _claims: WriteClaims):
        authorization_service.require(principal, Permission.SYSTEM_TOOL_CONFIG_MANAGE)
        return await upsert_mcp_server(name, body.config)

    @app.delete("/api/v1/developer/mcp/{name}", tags=["developer"])
    async def remove_mcp_server(name: str, principal: Principal, _claims: WriteClaims):
        authorization_service.require(principal, Permission.SYSTEM_TOOL_CONFIG_MANAGE)
        return await delete_mcp_server(name)

    @app.post("/api/v1/developer/mcp/{name}/test", tags=["developer"])
    async def post_mcp_test(name: str, body: McpServerBody, principal: Principal, _claims: WriteClaims):
        authorization_service.require(principal, Permission.SYSTEM_TOOL_CONFIG_MANAGE)
        return await test_mcp_server(name, body.config)

    @app.put("/api/v1/developer/skills/{name}", tags=["developer"])
    async def put_skill(name: str, body: SkillBody, principal: Principal, _claims: WriteClaims):
        authorization_service.require(principal, Permission.SYSTEM_PROMPT_TEMPLATE_MANAGE)
        return await upsert_skill(name, body.content)

    @app.get("/api/v1/developer/skills/{name}", tags=["developer"])
    async def get_skill(name: str, principal: Principal):
        authorization_service.require(principal, Permission.SYSTEM_PROMPT_TEMPLATE_MANAGE)
        return read_skill(name)

    @app.delete("/api/v1/developer/skills/{name}", tags=["developer"])
    async def remove_skill(name: str, principal: Principal, _claims: WriteClaims):
        authorization_service.require(principal, Permission.SYSTEM_PROMPT_TEMPLATE_MANAGE)
        return await delete_skill(name)

    @app.put("/api/v1/developer/worker-profiles/{name}", tags=["developer"])
    async def put_worker_profile(name: str, body: WorkerProfileBody, principal: Principal, _claims: WriteClaims):
        authorization_service.require(principal, Permission.SYSTEM_MODEL_PROFILE_MANAGE)
        return await upsert_worker_profile(name, body.profile)

    @app.delete("/api/v1/developer/worker-profiles/{name}", tags=["developer"])
    async def remove_worker_profile(name: str, principal: Principal, _claims: WriteClaims):
        authorization_service.require(principal, Permission.SYSTEM_MODEL_PROFILE_MANAGE)
        return await delete_worker_profile(name)

    @app.put("/api/v1/developer/models/providers/{name}", tags=["developer"])
    async def put_model_provider(name: str, body: ModelConfigBody, principal: Principal, _claims: WriteClaims):
        authorization_service.require(principal, Permission.SYSTEM_MODEL_PROFILE_MANAGE)
        return await upsert_model_provider(name, body.config)

    @app.put("/api/v1/developer/models/presets/{name}", tags=["developer"])
    async def put_model_preset(name: str, body: ModelConfigBody, principal: Principal, _claims: WriteClaims):
        authorization_service.require(principal, Permission.SYSTEM_MODEL_PROFILE_MANAGE)
        return await upsert_model_preset(name, body.config)

    @app.put("/api/v1/developer/models/routes/{name}", tags=["developer"])
    async def put_model_route(name: str, body: ModelConfigBody, principal: Principal, _claims: WriteClaims):
        authorization_service.require(principal, Permission.SYSTEM_MODEL_PROFILE_MANAGE)
        return await upsert_model_route(name, body.config)

    @app.put("/api/v1/developer/models/profiles/{name}", tags=["developer"])
    async def put_model_profile(name: str, body: ModelConfigBody, principal: Principal, _claims: WriteClaims):
        authorization_service.require(principal, Permission.SYSTEM_MODEL_PROFILE_MANAGE)
        return await upsert_model_profile(name, body.config)

    def _release_note_payload(row) -> dict[str, Any]:
        return {
            "id": row.id,
            "version": row.version,
            "released_at": row.released_at.isoformat() if row.released_at else None,
            "notes": row.notes_json,
            "status": row.status,
        }

    @app.get("/api/v1/developer/release-notes", tags=["developer"])
    async def list_release_notes(request: Request, principal: Principal):
        authorization_service.require(principal, Permission.SYSTEM_RELEASE_NOTES_MANAGE)
        async with authorization_session_factory(request)() as session:
            rows = await release_note_service.list(session, include_drafts=True)
        return {"items": [_release_note_payload(row) for row in rows]}

    @app.post("/api/v1/developer/release-notes", status_code=status.HTTP_201_CREATED, tags=["developer"])
    async def create_release_note(body: ReleaseNoteBody, request: Request, principal: Principal, _claims: WriteClaims):
        authorization_service.require(principal, Permission.SYSTEM_RELEASE_NOTES_MANAGE)
        try:
            async with authorization_session_factory(request)() as session:
                async with session.begin():
                    row = await release_note_service.create(
                        session, version=body.version, released_at=body.released_at,
                        notes=body.notes, status=body.status,
                    )
        except ReleaseNoteConflictError as error:
            return _problem(request, status_code=status.HTTP_409_CONFLICT, code="release_note_conflict", title="版本已存在", detail=str(error))
        return _release_note_payload(row)

    @app.put("/api/v1/developer/release-notes/{note_id}", tags=["developer"])
    async def update_release_note(note_id: str, body: ReleaseNoteBody, request: Request, principal: Principal, _claims: WriteClaims):
        authorization_service.require(principal, Permission.SYSTEM_RELEASE_NOTES_MANAGE)
        try:
            async with authorization_session_factory(request)() as session:
                async with session.begin():
                    row = await release_note_service.update(
                        session, note_id=note_id, version=body.version,
                        released_at=body.released_at, notes=body.notes, status=body.status,
                    )
        except ReleaseNoteConflictError as error:
            return _problem(request, status_code=status.HTTP_409_CONFLICT, code="release_note_conflict", title="版本已存在", detail=str(error))
        except ReleaseNoteNotFoundError as error:
            return _problem(request, status_code=status.HTTP_404_NOT_FOUND, code="release_note_not_found", title="发布说明不存在", detail=str(error))
        return _release_note_payload(row)

    @app.delete("/api/v1/developer/release-notes/{note_id}", tags=["developer"])
    async def delete_release_note(note_id: str, request: Request, principal: Principal, _claims: WriteClaims):
        authorization_service.require(principal, Permission.SYSTEM_RELEASE_NOTES_MANAGE)
        try:
            async with authorization_session_factory(request)() as session:
                async with session.begin():
                    await release_note_service.delete(session, note_id=note_id)
        except ReleaseNoteNotFoundError as error:
            return _problem(request, status_code=status.HTTP_404_NOT_FOUND, code="release_note_not_found", title="发布说明不存在", detail=str(error))
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.get("/api/v1/learning/release-notes", tags=["learning"])
    async def list_published_release_notes(request: Request, principal: Principal):
        authorization_service.require(principal, Permission.LEARNING_CONTENT_READ_PUBLIC)
        async with authorization_session_factory(request)() as session:
            rows = await release_note_service.list(session, include_drafts=False)
        return {"items": [_release_note_payload(row) for row in rows]}

    @app.get("/api/v1/teacher/overview", tags=["teacher"])
    async def teacher_overview(
        request: Request,
        principal: Principal,
        workspace_id: str = Query(default="default", min_length=1, max_length=128),
        days: int = Query(default=30, ge=1, le=365),
    ):
        analytics = await teacher_service.analytics(
            principal, request.app.state.gateway, workspace_id, days
        )
        goals = await teacher_service.goals(
            principal, request.app.state.gateway, workspace_id
        )
        annotations = await teacher_service.analysis_annotations(
            principal, request.app.state.gateway, workspace_id
        )
        return {**analytics, **goals, **annotations}

    @app.post("/api/v1/teacher/reports/ai-analysis", tags=["teacher"])
    async def teacher_ai_analysis(
        body: TeacherAIAnalysisRequest,
        request: Request,
        principal: Principal,
        _claims: WriteClaims,
    ):
        return await teacher_service.ai_analysis(
            principal,
            request.app.state.gateway,
            body.workspace_id,
            body,
        )

    @app.get("/api/v1/teacher/goals/{workspace_id}", tags=["teacher"])
    async def get_teacher_goals(workspace_id: str, request: Request, principal: Principal):
        return await teacher_service.goals(principal, request.app.state.gateway, workspace_id)

    @app.put("/api/v1/teacher/goals/{workspace_id}", tags=["teacher"])
    async def put_teacher_goals(
        workspace_id: str,
        body: UpdateTeachingGoals,
        request: Request,
        principal: Principal,
        _claims: WriteClaims,
    ):
        return await teacher_service.update_goals(
            principal, request.app.state.gateway, workspace_id, body
        )

    @app.get("/api/v1/teacher/analysis-annotations/{workspace_id}", tags=["teacher"])
    async def get_teacher_analysis_annotations(workspace_id: str, request: Request, principal: Principal):
        return await teacher_service.analysis_annotations(
            principal, request.app.state.gateway, workspace_id
        )

    @app.put("/api/v1/teacher/analysis-annotations/{workspace_id}", tags=["teacher"])
    async def put_teacher_analysis_annotations(
        workspace_id: str,
        body: UpdateTeacherAnalysisAnnotations,
        request: Request,
        principal: Principal,
        _claims: WriteClaims,
    ):
        return await teacher_service.update_analysis_annotations(
            principal, request.app.state.gateway, workspace_id, body
        )

    @app.get("/api/v1/teacher/catalog/{workspace_id}", tags=["teacher"])
    async def get_teacher_catalog(workspace_id: str, request: Request, principal: Principal):
        return await teacher_service.catalog(principal, request.app.state.gateway, workspace_id)

    @app.get("/api/v1/learning/catalog/{workspace_id}", tags=["learning"])
    async def get_learning_catalog(workspace_id: str, request: Request, principal: Principal):
        authorization_service.require(
            principal, Permission.LEARNING_CONTENT_READ_WORKSPACE, workspace_id=workspace_id
        )
        catalog = dict(
            (await request.app.state.gateway.get_teaching_catalog(principal, workspace_id))["catalog"]
        )
        catalog["topics"] = [
            _public_learning_topic(topic)
            for topic in catalog.get("topics", [])
            if topic.get("status", "enabled") == "enabled"
        ]
        enabled_topic_ids = {topic["id"] for topic in catalog["topics"]}
        catalog["exercise_blueprints"] = [
            item
            for item in catalog.get("exercise_blueprints", [])
            if item.get("status") == "enabled" and item.get("topic_id") in enabled_topic_ids
        ]
        catalog["review_blueprints"] = [
            item
            for item in catalog.get("review_blueprints", [])
            if item.get("status") == "enabled" and item.get("topic_id") in enabled_topic_ids
        ]
        catalog["guided_blueprints"] = [
            item
            for item in catalog.get("guided_blueprints", [])
            if item.get("status") == "enabled" and item.get("topic_id") in enabled_topic_ids
        ]
        return {"catalog": catalog}

    @app.get("/api/v1/teacher/book/{workspace_id}/navigation", tags=["teacher"])
    async def get_teacher_book_navigation(workspace_id: str, request: Request, principal: Principal):
        return await teacher_service.teacher_book_navigation(
            principal, request.app.state.gateway, workspace_id
        )

    @app.get("/api/v1/learning/book/{workspace_id}/navigation", tags=["learning"])
    async def get_learning_book_navigation(workspace_id: str, request: Request, principal: Principal):
        return await teacher_service.learning_book_navigation(
            principal, request.app.state.gateway, workspace_id
        )

    @app.get("/api/v1/teacher/book/{workspace_id}/pages/{knowledge_point_id}", tags=["teacher"])
    async def get_teacher_book_page(workspace_id: str, knowledge_point_id: str, request: Request, principal: Principal):
        return await teacher_service.teacher_book_page(
            principal, request.app.state.gateway, workspace_id, knowledge_point_id
        )

    @app.get("/api/v1/learning/book/{workspace_id}/pages/{knowledge_point_id}", tags=["learning"])
    async def get_learning_book_page(workspace_id: str, knowledge_point_id: str, request: Request, principal: Principal):
        return await teacher_service.learning_book_page(
            principal, request.app.state.gateway, workspace_id, knowledge_point_id
        )

    @app.put("/api/v1/teacher/book/{workspace_id}/pages/{knowledge_point_id}", tags=["teacher"])
    async def put_teacher_book_page(
        workspace_id: str,
        knowledge_point_id: str,
        body: UpdateTeacherBookPage,
        request: Request,
        principal: Principal,
        _claims: WriteClaims,
    ):
        try:
            return await teacher_service.update_teacher_book_page(
                principal, request.app.state.gateway, workspace_id, knowledge_point_id, body
            )
        except KnowledgeBookRevisionConflictError as error:
            return _problem(request, status_code=409, code="book_page_conflict", title="教材页面版本冲突", detail=str(error))
        except ValueError as error:
            return _problem(request, status_code=422, code="invalid_book_page", title="教材页面无效", detail=str(error))

    @app.post("/api/v1/teacher/book/{workspace_id}/pages/{knowledge_point_id}/publish", tags=["teacher"])
    async def publish_teacher_book_page(
        workspace_id: str,
        knowledge_point_id: str,
        body: PublishTeacherBookPage,
        request: Request,
        principal: Principal,
        _claims: WriteClaims,
    ):
        try:
            return await teacher_service.publish_teacher_book_page(
                principal, request.app.state.gateway, workspace_id, knowledge_point_id, body
            )
        except KnowledgeBookRevisionConflictError as error:
            return _problem(request, status_code=409, code="book_page_conflict", title="教材页面版本冲突", detail=str(error))
        except ValueError as error:
            return _problem(request, status_code=422, code="invalid_book_page", title="教材页面无法发布", detail=str(error))

    @app.post("/api/v1/teacher/book/{workspace_id}/imports/preview", tags=["teacher"])
    async def preview_teacher_book_import(
        workspace_id: str,
        body: TeacherBookImportPreviewRequest,
        request: Request,
        principal: Principal,
        _claims: WriteClaims,
    ):
        try:
            return await teacher_service.preview_teacher_book_import(principal, workspace_id, body)
        except ValueError as error:
            return _problem(request, status_code=422, code="invalid_book_import", title="教材导入文件无效", detail=str(error))

    @app.post("/api/v1/teacher/book/{workspace_id}/imports/apply", tags=["teacher"])
    async def apply_teacher_book_import(
        workspace_id: str,
        body: TeacherBookImportApplyRequest,
        request: Request,
        principal: Principal,
        _claims: WriteClaims,
    ):
        try:
            return await teacher_service.apply_teacher_book_import(
                principal, request.app.state.gateway, workspace_id, body
            )
        except KnowledgeBookRevisionConflictError as error:
            return _problem(request, status_code=409, code="book_page_conflict", title="教材页面版本冲突", detail=str(error))
        except ValueError as error:
            return _problem(request, status_code=422, code="invalid_book_import", title="教材导入文件无效", detail=str(error))

    @app.post("/api/v1/teacher/book/{workspace_id}/imports/archive/preview", tags=["teacher"])
    async def preview_teacher_book_archive_import(
        workspace_id: str,
        body: TeacherBookArchiveImportPreviewRequest,
        request: Request,
        principal: Principal,
        _claims: WriteClaims,
    ):
        try:
            return await teacher_service.preview_teacher_book_archive_import(
                principal, request.app.state.gateway, workspace_id, body
            )
        except ValueError as error:
            return _problem(request, status_code=422, code="invalid_book_archive", title="教材批量包无效", detail=str(error))

    @app.post("/api/v1/teacher/book/{workspace_id}/imports/archive/apply", tags=["teacher"])
    async def apply_teacher_book_archive_import(
        workspace_id: str,
        body: TeacherBookArchiveImportApplyRequest,
        request: Request,
        principal: Principal,
        _claims: WriteClaims,
    ):
        try:
            return await teacher_service.apply_teacher_book_archive_import(
                principal, request.app.state.gateway, workspace_id, body
            )
        except KnowledgeBookRevisionConflictError as error:
            return _problem(request, status_code=409, code="book_import_conflict", title="教材批量导入版本冲突", detail=str(error))
        except ValueError as error:
            return _problem(
                request,
                status_code=422,
                code="invalid_book_archive",
                title="教材批量包无效",
                detail=str(error),
            )

    @app.get("/api/v1/learning/book/{workspace_id}/assets/{asset_path:path}", tags=["learning"])
    async def get_learning_book_asset(
        workspace_id: str,
        asset_path: str,
        request: Request,
        principal: Principal,
    ):
        try:
            asset = await teacher_service.knowledge_book_asset(
                principal, request.app.state.gateway, workspace_id, asset_path
            )
        except FileNotFoundError:
            return _problem(request, status_code=404, code="book_asset_not_found", title="教材资源不存在")
        return Response(
            content=asset["content"],
            media_type=str(asset["media_type"]),
            headers={"Cache-Control": "private, max-age=300", "ETag": str(asset["sha256"])},
        )

    @app.put("/api/v1/teacher/catalog/{workspace_id}", tags=["teacher"])
    async def put_teacher_catalog(workspace_id: str, body: UpdateTeacherCatalog, request: Request, principal: Principal, _claims: WriteClaims):
        return await teacher_service.update_catalog(principal, request.app.state.gateway, workspace_id, body)

    @app.put("/api/v1/teacher/catalog/{workspace_id}/exercise-blueprints/{blueprint_id}", tags=["teacher"])
    async def put_exercise_blueprint(workspace_id: str, blueprint_id: str, body: ExerciseBlueprint, request: Request, principal: Principal, _claims: WriteClaims):
        if body.id != blueprint_id:
            return _problem(request, status_code=422, code="blueprint_id_mismatch", title="蓝图 ID 不匹配")
        try:
            return await teacher_service.upsert_exercise_blueprint(principal, request.app.state.gateway, workspace_id, body)
        except ValueError as error:
            return _problem(request, status_code=422, code="invalid_blueprint", title="出题蓝图无效", detail=str(error))

    @app.put("/api/v1/teacher/catalog/{workspace_id}/review-blueprints/{blueprint_id}", tags=["teacher"])
    async def put_review_blueprint(workspace_id: str, blueprint_id: str, body: ReviewBlueprint, request: Request, principal: Principal, _claims: WriteClaims):
        if body.id != blueprint_id:
            return _problem(request, status_code=422, code="blueprint_id_mismatch", title="蓝图 ID 不匹配")
        try:
            return await teacher_service.upsert_review_blueprint(principal, request.app.state.gateway, workspace_id, body)
        except ValueError as error:
            return _problem(request, status_code=422, code="invalid_blueprint", title="复习蓝图无效", detail=str(error))

    @app.put("/api/v1/teacher/catalog/{workspace_id}/guided-blueprints/{blueprint_id}", tags=["teacher"])
    async def put_guided_blueprint(workspace_id: str, blueprint_id: str, body: GuidedBlueprint, request: Request, principal: Principal, _claims: WriteClaims):
        if body.id != blueprint_id:
            return _problem(request, status_code=422, code="blueprint_id_mismatch", title="蓝图 ID 不匹配")
        try:
            return await teacher_service.upsert_guided_blueprint(principal, request.app.state.gateway, workspace_id, body)
        except ValueError as error:
            return _problem(request, status_code=422, code="invalid_blueprint", title="引导蓝图无效", detail=str(error))

    @app.delete("/api/v1/teacher/catalog/{workspace_id}/{kind}-blueprints/{blueprint_id}", status_code=204, tags=["teacher"])
    async def delete_blueprint(workspace_id: str, kind: str, blueprint_id: str, request: Request, principal: Principal, _claims: WriteClaims):
        if kind not in {"exercise", "review", "guided"}:
            return _problem(request, status_code=404, code="not_found", title="蓝图类型不存在")
        await teacher_service.delete_blueprint(principal, request.app.state.gateway, workspace_id, blueprint_id, kind=kind)
        return Response(status_code=204)

    @app.get("/api/v1/teacher/analytics", tags=["teacher"])
    async def teacher_analytics(
        request: Request,
        principal: Principal,
        workspace_id: str = Query(default="default", min_length=1, max_length=128),
        days: int = Query(default=30, ge=1, le=365),
    ):
        return await teacher_service.analytics(
            principal, request.app.state.gateway, workspace_id, days
        )

    @app.get("/api/v1/teacher/quota/classroom", tags=["teacher", "quota"])
    async def teacher_classroom_quota(
        request: Request,
        principal: Principal,
        classroom_id: str = Query(..., min_length=1, max_length=128),
        workspace_id: str = Query(default="default", min_length=1, max_length=128),
        days: int = Query(default=30, ge=1, le=365),
    ):
        teacher_service.require_teacher(
            principal, workspace_id, Permission.LEARNING_PROGRESS_READ_CLASSROOM
        )
        require_explicit_classroom_membership(principal, classroom_id)
        factory = getattr(request.app.state.gateway, "authorization_session_factory", None)
        if factory is None:
            if not principal.is_admin:
                raise HTTPException(status_code=503, detail="课堂权限数据暂不可用")
        else:
            async with factory() as session:
                classroom = await session.scalar(
                    select(ClassroomModel).where(
                        ClassroomModel.id == classroom_id,
                        ClassroomModel.status == "active",
                    )
                )
            if classroom is None:
                raise HTTPException(status_code=404, detail="课堂不存在")
            if classroom.workspace_id != workspace_id:
                raise HTTPException(status_code=403, detail="课堂不属于当前工作区")
        operations = quota_operations_for(request)
        end = datetime.now(timezone.utc)
        return await asyncio.to_thread(
            operations.classroom_usage,
            classroom_id,
            workspace_id=workspace_id,
            start=end - timedelta(days=days),
            end=end,
        )

    @app.get("/api/v1/teacher/{resource}", tags=["teacher"])
    async def teacher_placeholder(
        resource: str,
        principal: Principal,
        workspace_id: str = Query(default="default", min_length=1, max_length=128),
    ):
        if resource not in {"courses", "prompts", "reports"}:
            raise FileNotFoundError(resource)
        teacher_service.require_teacher(principal, workspace_id)
        return {
            "items": [],
            "resource": resource,
            "workspace_id": workspace_id,
            "status": "interface_reserved",
        }

    @app.patch("/api/v1/settings", tags=["settings"])
    async def update_settings(
        body: UpdateSettingsBody,
        request: Request,
        principal: Principal,
        _claims: WriteClaims,
    ):
        changes = body.model_dump(exclude_none=True)
        if "model_profile" in changes:
            from fastapi import HTTPException
            from core.model_runtime.factory import get_global_model_factory

            factory = get_global_model_factory()
            profile_name = changes["model_profile"]
            try:
                factory.config.profile(profile_name)
                if not factory.profile_available(profile_name):
                    raise HTTPException(400, f"Model profile {profile_name!r} is currently unavailable (missing credentials)")
            except KeyError:
                raise HTTPException(400, f"Unknown model profile {profile_name!r}")
        if "default_workspace_id" in changes:
            principal.require_workspace(changes["default_workspace_id"])
        updated = await request.app.state.gateway.update_user_settings(principal, changes)
        await hub.broadcast(
            control_event("settings.updated", payload=updated),
            user_id=principal.user_id,
        )
        return updated

    @app.get("/api/v1/usage/me", tags=["usage"])
    async def usage_me(
        request: Request,
        principal: Principal,
        workspace_id: str | None = Query(default=None, max_length=128),
        days: int = Query(30, ge=1, le=365),
        granularity: Literal["day", "week"] = Query("day"),
    ):
        authorization_service.require(principal, Permission.QUOTA_USAGE_READ_SELF)
        if workspace_id is not None:
            principal.require_workspace(workspace_id)
        reader = getattr(request.app.state, "quota_usage_reader", None)
        if reader is None:
            return _problem(
                request,
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                code="usage_unavailable",
                title="Usage persistence is unavailable",
            )
        return await asyncio.to_thread(
            reader.user_snapshot,
            principal.user_id,
            workspace_id=workspace_id,
            days=days,
            granularity=granularity,
        )

    @app.get("/api/v1/quota/me", tags=["quota"])
    async def quota_me(
        request: Request,
        principal: Principal,
        workspace_id: str | None = Query(default=None, max_length=128),
    ):
        authorization_service.require(principal, Permission.QUOTA_USAGE_READ_SELF)
        if workspace_id is not None:
            principal.require_workspace(workspace_id)
        quota_service = getattr(request.app.state, "quota_read_service", None)
        if quota_service is None:
            quota_service = getattr(request.app.state.gateway, "quota_service", None)
        if quota_service is None:
            return _problem(
                request,
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                code="quota_unavailable",
                title="Quota persistence is unavailable",
            )
        classroom_ids = tuple(principal.classroom_ids)
        if workspace_id is not None and classroom_ids:
            factory = getattr(request.app.state.gateway, "authorization_session_factory", None)
            if factory is None:
                # A workspace-scoped snapshot must not trust an unscoped
                # classroom id list when the authorization store is absent.
                classroom_ids = ()
            else:
                async with factory() as session:
                    classroom_ids = tuple(
                        (
                            await session.scalars(
                                select(ClassroomModel.id).where(
                                    ClassroomModel.id.in_(classroom_ids),
                                    ClassroomModel.workspace_id == workspace_id,
                                    ClassroomModel.status == "active",
                                )
                            )
                        ).all()
                    )
        try:
            snapshot = await asyncio.to_thread(
                quota_service.snapshot,
                user_id=principal.user_id,
                workspace_id=workspace_id,
                classroom_ids=classroom_ids,
            )
            explanation = None
            management = getattr(request.app.state, "quota_management", None)
            if management is not None:
                try:
                    explanation = await asyncio.to_thread(
                        management.explain_policy,
                        user_id=principal.user_id,
                        workspace_id=workspace_id,
                        role_codes=tuple(principal.roles),
                        classroom_ids=classroom_ids,
                        at=datetime.now(timezone.utc),
                    )
                except QuotaDomainError:
                    # A new account may not have a policy yet; the balance itself
                    # remains useful and the developer can attach a policy later.
                    explanation = None
        except OperationalError as error:
            if not _is_quota_schema_mismatch(error):
                raise
            logger.error("Quota database schema is out of date: %s", error)
            return _problem(
                request,
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                code="quota_schema_outdated",
                title="Quota database schema is outdated",
                detail="请先执行 alembic upgrade head，再重启服务。",
            )
        return {"quota": snapshot, "policy": explanation}

    @app.exception_handler(QuotaRejectedError)
    async def quota_rejected_error(request: Request, error: QuotaRejectedError):
        problem = error.problem
        status_code = (
            status.HTTP_403_FORBIDDEN
            if problem.code == "quota_model_not_allowed"
            else status.HTTP_429_TOO_MANY_REQUESTS
        )
        return _problem(
            request,
            status_code=status_code,
            code=problem.code.value,
            title="Quota admission rejected",
            detail=problem.reason,
            extra={
                "remaining_micro": problem.remaining_micro,
                "reset_at": problem.reset_at.isoformat() if problem.reset_at else None,
                "allowed_model_profiles": list(problem.allowed_model_profiles),
                "retryable": problem.retryable,
            },
        )

    @app.websocket("/ws/v1")
    async def websocket_route(websocket: WebSocket):
        await websocket_endpoint(
            websocket,
            gateway=websocket.app.state.gateway,
            auth=auth,
            hub=hub,
            max_message_bytes=max_ws_message_bytes,
            max_queue=stream_queue_size,
            send_queue_size=ws_send_queue_size,
            send_timeout_s=ws_send_timeout_s,
            principal_resolver=lambda claims: resolve_principal(websocket, claims),
            database_auth=database_auth if not app.state.auth_injected else None,
            authorization_session_factory=getattr(
                websocket.app.state.gateway, "authorization_session_factory", None
            ),
        )

    # User-management vertical modules (chained fix: backend-user-modules).
    # Registered before the SPA static mount so /api routes always win.
    from server.user.controller import router as user_router
    from server.workspace.controller import router as workspace_router
    from server.classroom_join import router as classroom_join_router
    from server.sandbox.controller import router as sandbox_router
    from server.sandbox.artifact_controller import router as sandbox_artifact_router

    app.include_router(user_router)
    app.include_router(workspace_router)
    app.include_router(classroom_join_router)
    app.include_router(sandbox_router)
    app.include_router(sandbox_artifact_router)

    static_dir_value = str(web_config.get("static_dir", "")).strip()
    static_dir = Path(static_dir_value).expanduser() if static_dir_value else None
    if static_dir is not None and not static_dir.is_absolute():
        static_dir = Path(__file__).resolve().parents[2] / static_dir
    # Image upload endpoints (registered before the SPA mount so /api routes win).
    from server.uploads import router as uploads_router
    app.include_router(uploads_router)

    if static_dir is not None and static_dir.is_dir():
        app.mount("/", SpaStaticFiles(directory=static_dir, html=True), name="webui")
    else:
        @app.get("/", include_in_schema=False)
        async def api_root():
            return {
                "name": "NLP Agent Backend",
                "api": "/api/v1",
                "websocket": "/ws/v1",
                "docs": "/api/docs",
            }

    return app


app = create_app()
