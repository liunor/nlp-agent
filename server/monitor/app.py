"""FastAPI monitor plane on a port isolated from student chat traffic."""

import asyncio
import logging
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Header, Query, Request, Response, Security, WebSocket
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import APIKeyCookie
from fastapi.staticfiles import StaticFiles
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.websockets import WebSocketDisconnect

from configs.settings import settings
from core.authorization_audit import begin as begin_authorization_audit
from core.authorization_audit import end as end_authorization_audit
from core.identity import AccessDeniedError, AuthenticatedPrincipal
from core.rbac import Permission, authorization_service
from core.observability.runtime import TelemetryRuntime
from core.observability.service import ObservabilityService
from server.infrastructure.mysql import MySQLRuntime
from server.rbac.service import rbac_service
from server.web.auth import AuthenticationError, CsrfRejectedError, OriginRejectedError, SameOriginSessionAuth, SessionClaims
from server.web.contracts import LoginBody
from server.web.database_auth import DatabaseSessionAuth, DatabaseSessionClaims
from server.monitor.reset import LocalRuntimeResetter
from server.monitor.catalog import monitor_model_catalog
from server.monitor.retention import enforce_manual_retention, monitor_retention_settings, run_monitor_retention
from server.quota.usage import UsageReadService

MAX_PENDING_AUTHORIZATION_AUDIT_TASKS = 256


def _problem(status_code: int, code: str, title: str) -> JSONResponse:
    return JSONResponse(
        {"type": f"urn:nlp-agent:monitor:{code}", "status": status_code, "code": code, "title": title},
        status_code=status_code,
        media_type="application/problem+json",
    )


def create_monitor_app(
    *,
    runtime: TelemetryRuntime | None = None,
    auth: SameOriginSessionAuth | None = None,
    resetter: LocalRuntimeResetter | None = None,
    usage_reader: UsageReadService | None = None,
    allowed_hosts: list[str] | None = None,
) -> FastAPI:
    # An explicitly injected auth adapter is a self-contained/test deployment
    # seam. Production construction uses the configured adapter and resolves
    # roles from MySQL on every request.
    auth_injected = auth is not None
    config = settings.monitor_runtime
    runtime = runtime or TelemetryRuntime()
    service = ObservabilityService(runtime)
    auth = auth or SameOriginSessionAuth.from_config(config, include_credentials=False)
    # The monitor is a separate process, not a separate identity system. It
    # reuses the same account/password tables, but keeps an independent
    # browser session. Sharing the session cookie would make the two apps
    # rotate one CSRF token and invalidate each other's write requests.
    database_config = dict(config)
    database_config["allowed_origins"] = list(config.get("allowed_origins", []))
    database_auth = DatabaseSessionAuth.from_config(database_config)
    cookie_secure = database_auth.secure if not auth_injected else auth.secure
    resetter = resetter or LocalRuntimeResetter(runtime)
    rbac_runtime = MySQLRuntime.from_runtime(settings.database_runtime)
    usage_reader = usage_reader or (
        UsageReadService(settings.NLP_AGENT_DATABASE_URL.strip())
        if settings.NLP_AGENT_DATABASE_URL.strip()
        else None
    )
    retention = monitor_retention_settings(config)
    pending_audit_tasks: set[asyncio.Task[None]] = set()

    async def monitor_db_session() -> AsyncIterator[AsyncSession]:
        async with rbac_runtime.session_factory() as db_session:
            yield db_session

    async def cleanup_authorization_audit() -> dict[str, int]:
        async with rbac_runtime.session_factory() as db_session:
            async with db_session.begin():
                removed = await rbac_service.prune_audit(
                    db_session, retention_days=int(retention["audit_days"])
                )
        return {"audit_logs": removed}

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.runtime = runtime
        app.state.observability = service
        app.state.rbac_runtime = rbac_runtime
        app.state.quota_usage_reader = usage_reader
        app.state.monitor_retention = retention
        monitor_redis = None
        set_auth_redis_client = getattr(database_auth, "set_redis_client", None)
        redis_url = settings.NLP_AGENT_REDIS_URL.strip()
        if redis_url:
            try:
                import redis.asyncio as redis_async

                monitor_redis = redis_async.from_url(
                    redis_url, decode_responses=True
                )
                if callable(set_auth_redis_client):
                    set_auth_redis_client(monitor_redis)
            except (ImportError, ValueError):
                # Keep the local limiter as a safe compatibility fallback for
                # development images that do not include Redis support.
                if callable(set_auth_redis_client):
                    set_auth_redis_client(None)
        else:
            if callable(set_auth_redis_client):
                set_auth_redis_client(None)
        await rbac_runtime.start()
        retention_task = None
        repository = getattr(runtime, "repository", None)
        if retention["enabled"] and repository is not None:
            retention_task = asyncio.create_task(
                run_monitor_retention(
                    repository,
                    trace_days=int(retention["trace_days"]),
                    event_days=int(retention["event_days"]),
                    initial_delay_s=int(retention["initial_delay_s"]),
                    interval_s=int(retention["interval_s"]),
                    audit_cleanup=cleanup_authorization_audit,
                ),
                name="monitor-telemetry-retention",
            )
        try:
            yield
        finally:
            if retention_task is not None:
                retention_task.cancel()
                with suppress(asyncio.CancelledError):
                    await retention_task
            if usage_reader is not None:
                usage_reader.close()
            if pending_audit_tasks:
                await asyncio.gather(*pending_audit_tasks, return_exceptions=True)
            if monitor_redis is not None:
                await monitor_redis.aclose()
            await rbac_runtime.close()
            await runtime.close()

    app = FastAPI(
        title="NLP Agent Observability Monitor",
        version="1.0.0",
        # The monitor exposes user- and provider-level operational data. Keep
        # the schema and interactive docs out of the public surface; operators
        # can still inspect the source or mount a separately protected docs
        # endpoint in an internal deployment.
        docs_url=None,
        openapi_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )
    cookie_auth = APIKeyCookie(
        name=auth.cookie_name if auth_injected else database_auth.cookie_name,
        auto_error=False,
    )
    # An explicit allowed_hosts override (tests/local deployments) wins over the
    # config-derived whitelist so the app never depends on a gitignored .env
    # override of NLP_AGENT_MONITOR_ALLOWED_HOSTS; otherwise fall back to the
    # configured list and finally the loopback default.
    middleware_hosts = (
        allowed_hosts
        if allowed_hosts is not None
        else list(config.get("allowed_hosts", ["127.0.0.1", "localhost"]))
    )
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=middleware_hosts,
    )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        request.state.request_id = getattr(request.state, "request_id", None) or request.headers.get("x-request-id") or secrets.token_hex(16)
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; base-uri 'self'; frame-ancestors 'none'; "
            "form-action 'self'; script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
            "connect-src 'self' ws: wss:"
        )
        return response

    @app.middleware("http")
    async def authorization_audit(request: Request, call_next):
        """Persist denied and state-changing monitor authorization decisions.

        Monitor polling is intentionally not written by default because it can
        generate more audit rows than useful operator actions.  Deployments
        that require successful-read evidence can opt in with the same
        ``audit_successful_reads`` setting used by the main web plane.
        """
        audit_token, decisions = begin_authorization_audit()
        response = None
        try:
            response = await call_next(request)
        finally:
            end_authorization_audit(audit_token)
        if (
            response is not None
            and not auth_injected
            and decisions
            and response.status_code != 401
        ):
            audit_successful_reads = bool(config.get("audit_successful_reads", False))
            retained = [
                decision
                for decision in decisions
                if not (
                    decision.decision == "allow"
                    and request.method in {"GET", "HEAD"}
                    and not audit_successful_reads
                )
            ]
            if retained:
                request_id = request.state.request_id

                async def flush_authorization_audit(
                    decisions_to_write=retained,
                    rid=request_id,
                ) -> None:
                    try:
                        async with rbac_runtime.session_factory() as session:
                            async with session.begin():
                                for decision in decisions_to_write:
                                    await rbac_service.audit(
                                        session,
                                        actor_user_id=decision.actor_user_id,
                                        target_user_id=None,
                                        decision=decision.decision,
                                        reason_code="monitor_authorization",
                                        permission_code=decision.permission_code,
                                        resource_type=decision.resource_type or "monitor",
                                        resource_id=decision.resource_id,
                                        detail={
                                            "workspace_id": decision.workspace_id,
                                            "request_id": rid,
                                            "method": request.method,
                                            "path": request.url.path,
                                        },
                                    )
                    except Exception as audit_exc:
                        logging.getLogger("audit").warning(
                            "monitor authorization audit flush failed: %s", audit_exc
                        )

                # Keep the best-effort audit writer bounded. Once the database
                # is slower than incoming state-changing requests, drain one
                # record in the request coroutine instead of creating an
                # unbounded task backlog and losing evidence to memory pressure.
                if len(pending_audit_tasks) >= MAX_PENDING_AUTHORIZATION_AUDIT_TASKS:
                    await flush_authorization_audit()
                else:
                    audit_task = asyncio.get_running_loop().create_task(
                        flush_authorization_audit(), name="monitor-authorization-audit"
                    )
                    pending_audit_tasks.add(audit_task)
                    audit_task.add_done_callback(pending_audit_tasks.discard)
        return response

    async def claims(
        token: Annotated[str | None, Security(cookie_auth)],
    ) -> SessionClaims | DatabaseSessionClaims:
        if auth_injected:
            return auth.authenticate(token)
        return await database_auth.authenticate(rbac_runtime.session_factory, token)

    async def principal(
        session: Annotated[SessionClaims | DatabaseSessionClaims, Depends(claims)]
    ) -> AuthenticatedPrincipal:
        if isinstance(session, DatabaseSessionClaims):
            async with rbac_runtime.session_factory() as db_session:
                identity = await rbac_service.principal_for_user_id(
                    db_session, session.user_id
                )
        else:
            # An injected SameOriginSessionAuth is the self-contained adapter
            # used by local/test monitor deployments.  Its signed claims are
            # already the authoritative identity for that deployment; trying
            # to reload a synthetic user such as ``local`` from MySQL makes
            # the compatibility seam unusable.  Production always arrives as
            # DatabaseSessionClaims and takes the branch above.
            identity = session.principal()
        authorization_service.require(identity, Permission.SYSTEM_RUNTIME_MONITOR)
        return identity

    async def write_access(
        request: Request,
        session: Annotated[SessionClaims | DatabaseSessionClaims, Depends(claims)],
        identity: Annotated[AuthenticatedPrincipal, Depends(principal)],
        csrf: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    ) -> SessionClaims | DatabaseSessionClaims:
        _require_write_security(request, session, csrf)
        authorization_service.require(identity, Permission.SYSTEM_RUNTIME_MONITOR)
        return session

    def _require_write_security(
        request: Request,
        session: SessionClaims | DatabaseSessionClaims,
        csrf: str | None,
    ) -> None:
        if isinstance(session, DatabaseSessionClaims):
            database_auth.require_same_origin(
                request.headers.get("origin"), request.headers.get("host")
            )
            database_auth.require_csrf(session, csrf)
        else:
            auth.require_same_origin(request.headers.get("origin"), request.headers.get("host"))
            auth.require_csrf(session, csrf)

    async def reset_write_access(
        request: Request,
        session: Annotated[SessionClaims | DatabaseSessionClaims, Depends(claims)],
        identity: Annotated[AuthenticatedPrincipal, Depends(principal)],
        csrf: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    ) -> SessionClaims | DatabaseSessionClaims:
        _require_write_security(request, session, csrf)
        authorization_service.require(identity, Permission.SYSTEM_RUNTIME_RESET)
        return session

    Principal = Annotated[AuthenticatedPrincipal, Depends(principal)]
    WriteClaims = Annotated[
        SessionClaims | DatabaseSessionClaims, Depends(write_access)
    ]
    ResetWriteClaims = Annotated[
        SessionClaims | DatabaseSessionClaims, Depends(reset_write_access)
    ]

    from server.sandbox.monitor_controller import create_sandbox_monitor_router

    app.include_router(
        create_sandbox_monitor_router(
            db_session_dependency=monitor_db_session,
            principal_dependency=principal,
            write_access_dependency=write_access,
        )
    )

    @app.exception_handler(AuthenticationError)
    async def auth_error(_request: Request, _error: AuthenticationError):
        return _problem(401, "authentication_required", "Authentication required")

    @app.exception_handler(AccessDeniedError)
    async def access_error(_request: Request, _error: AccessDeniedError):
        return _problem(403, "forbidden", "Monitor permission required")

    @app.exception_handler(OriginRejectedError)
    async def origin_error(_request: Request, _error: OriginRejectedError):
        return _problem(403, "origin_rejected", "Origin rejected")

    @app.exception_handler(CsrfRejectedError)
    async def csrf_error(_request: Request, _error: CsrfRejectedError):
        return _problem(403, "csrf_rejected", "CSRF validation failed")

    @app.get("/health/live", tags=["health"])
    async def health_live():
        return {"status": "ok", "plane": "observability"}

    @app.get("/health/ready", tags=["health"])
    async def health_ready():
        # Public readiness probes must not disclose database paths, row counts,
        # or storage usage. Authenticated monitor callers get the detailed
        # health payload through /api/v1/observability/storage.
        return {"status": "ready", "plane": "observability"}

    @app.post("/api/v1/auth/login", tags=["auth"])
    async def login(body: LoginBody, request: Request, response: Response):
        # The monitor has its own login screen, but deliberately reuses the
        # control-plane account/session store. Do not rotate an existing
        # cookie until monitor permission has been checked: a student opening
        # the monitor must not be logged out of the control plane when the
        # attempt is rejected.
        if auth_injected:
            auth.require_same_origin(request.headers.get("origin"), request.headers.get("host"))
            token, claims = auth.login(
                body.username,
                body.password,
                client_key=request.client.host if request.client else "unknown",
                previous_token=None,
            )
            identity = claims.principal()
            try:
                authorization_service.require(identity, Permission.SYSTEM_RUNTIME_MONITOR)
            except AccessDeniedError:
                auth.revoke(token)
                raise
            cookie_name = auth.cookie_name
            ttl_s = auth.ttl_s
            expires_at = claims.expires_at
            csrf_token = claims.csrf_token
        else:
            database_auth.require_same_origin(request.headers.get("origin"), request.headers.get("host"))
            token, claims = await database_auth.login(
                rbac_runtime.session_factory,
                body.username,
                body.password,
                client_key=request.client.host if request.client else "unknown",
                previous_token=None,
                workspace_id=body.workspace_id,
            )
            async with rbac_runtime.session_factory() as db_session:
                identity = await rbac_service.principal_for_user_id(db_session, claims.user_id)
            try:
                authorization_service.require(identity, Permission.SYSTEM_RUNTIME_MONITOR)
            except AccessDeniedError:
                await database_auth.revoke_token(rbac_runtime.session_factory, claims.token_hash)
                raise
            cookie_name = database_auth.cookie_name
            ttl_s = database_auth.ttl_s
            expires_at = claims.expires_at_epoch
            csrf_token = claims.csrf_token

        response.set_cookie(
            cookie_name,
            token,
            max_age=ttl_s,
            httponly=True,
            secure=cookie_secure,
            samesite="lax",
            path="/",
        )
        return {
            "user_id": identity.user_id,
            "roles": sorted(identity.roles),
            "permissions": sorted(authorization_service.permissions_for(identity)),
            "csrf_token": csrf_token,
            "expires_at": expires_at,
        }

    @app.post("/api/v1/auth/session", status_code=201, tags=["auth"])
    async def create_session(request: Request, response: Response):
        if not auth_injected:
            raise AuthenticationError(
                "monitor sessions are created through the control-plane login"
            )
        auth.require_same_origin(request.headers.get("origin"), request.headers.get("host"))
        token, session = auth.issue()
        response.set_cookie(auth.cookie_name, token, max_age=auth.ttl_s, httponly=True, secure=cookie_secure, samesite="lax", path="/")
        identity = session.principal()
        return {"user_id": session.user_id, "roles": sorted(session.roles), "permissions": sorted(authorization_service.permissions_for(identity)), "csrf_token": session.csrf_token, "expires_at": session.expires_at}

    @app.get("/api/v1/auth/session", tags=["auth"])
    async def get_session(
        request: Request,
        session: Annotated[SessionClaims | DatabaseSessionClaims, Depends(claims)],
        identity: Principal,
    ):
        if isinstance(session, DatabaseSessionClaims):
            csrf_token = await database_auth.restore_csrf(
                rbac_runtime.session_factory,
                session,
                request.cookies.get(database_auth.cookie_name),
            )
            session = DatabaseSessionClaims(
                **{**session.__dict__, "csrf_token": csrf_token}
            )
        return {
            "user_id": identity.user_id,
            "roles": sorted(identity.roles),
            "permissions": sorted(authorization_service.permissions_for(identity)),
            "csrf_token": session.csrf_token,
            "expires_at": session.expires_at_epoch
            if isinstance(session, DatabaseSessionClaims)
            else session.expires_at,
        }

    @app.post("/api/v1/auth/ws-ticket", tags=["auth"])
    async def create_ws_ticket(
        request: Request,
        session: WriteClaims,
    ):
        if not isinstance(session, DatabaseSessionClaims):
            return {"ticket": "legacy-injected-session", "expires_in": 0}
        origin = request.headers.get("origin")
        if not origin:
            raise OriginRejectedError("Origin header is required")
        ticket = await database_auth.issue_ws_ticket(
            rbac_runtime.session_factory,
            session,
            origin=origin,
            host=request.headers.get("host"),
        )
        return {"ticket": ticket, "expires_in": 60}

    @app.get("/api/v1/audit/authorization", tags=["audit"])
    async def list_authorization_audit(
        identity: Principal,
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0, le=1_000_000),
        actor_user_id: str | None = None,
        decision: str | None = Query(default=None, pattern="^(allow|deny)$"),
        reason_code: str | None = Query(default=None, min_length=1, max_length=64),
    ):
        authorization_service.require(identity, Permission.SYSTEM_AUDIT_READ)
        async with rbac_runtime.session_factory() as session:
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

    @app.get("/api/v1/audit/authorization/stats", tags=["audit"])
    async def authorization_audit_stats(
        identity: Principal,
        days: int = Query(default=30, ge=1, le=3650),
    ):
        authorization_service.require(identity, Permission.SYSTEM_AUDIT_READ)
        since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
        async with rbac_runtime.session_factory() as session:
            summary = await rbac_service.audit_summary(session, since=since)
        return {"period_days": days, "since": since, **summary}

    @app.get("/api/v1/observability/overview", tags=["observability"])
    async def overview(identity: Principal, days: int = Query(30, ge=1, le=365)):
        return await service.overview(identity, days)

    @app.get("/api/v1/observability/dependencies", tags=["observability"])
    async def dependencies(
        identity: Principal,
        days: int = Query(30, ge=1, le=365),
        window_minutes: int = Query(120, ge=5, le=1440),
        bucket_minutes: int = Query(5, ge=1, le=60),
    ):
        if bucket_minutes > window_minutes:
            return _problem(422, "invalid_dependency_window", "bucket_minutes must not exceed window_minutes")
        payload = await service.dependency_health(
            identity,
            days=days,
            window_minutes=window_minutes,
            bucket_minutes=bucket_minutes,
        )
        payload.setdefault("catalog", monitor_model_catalog())
        return payload

    @app.get("/api/v1/observability/traces", tags=["observability"])
    async def traces(identity: Principal, limit: int = Query(100, ge=1, le=500), session_id: str | None = None, status: str | None = None):
        return {"items": await service.traces(identity, limit=limit, session_id=session_id, status=status)}

    @app.get("/api/v1/observability/traces/groups", tags=["observability"])
    async def trace_groups(
        identity: Principal,
        days: int = Query(30, ge=1, le=365),
        limit: int = Query(24, ge=1, le=100),
        offset: int = Query(0, ge=0),
        query: str | None = Query(default=None, max_length=128),
        focus: str = Query("all", pattern="^(all|errors|slow)$"),
    ):
        return await service.trace_groups(
            identity,
            days=days,
            limit=limit,
            offset=offset,
            query=query,
            focus=focus,
        )

    @app.get("/api/v1/observability/traces/chains/{chain_id:path}", tags=["observability"])
    async def trace_group(chain_id: str, identity: Principal):
        detail = await service.trace_group(identity, chain_id)
        if detail is None:
            return _problem(404, "trace_group_not_found", "Trace chain not found")
        return detail

    @app.get("/api/v1/observability/traces/{trace_id}", tags=["observability"])
    async def trace(trace_id: str, identity: Principal):
        detail = await service.trace(identity, trace_id)
        if detail is None:
            return _problem(404, "trace_not_found", "Trace not found")
        return detail

    @app.get("/api/v1/observability/usage", tags=["observability"])
    async def usage(identity: Principal, days: int = Query(30, ge=1, le=365)):
        return {"items": await service.usage(identity, days)}

    @app.get("/api/v1/observability/usage/system", tags=["observability"])
    async def system_usage(
        identity: Principal,
        days: int = Query(30, ge=1, le=365),
        include_users: bool = Query(False),
    ):
        if include_users:
            return _problem(
                422,
                "user_breakdown_requires_pagination",
                "Use /usage/system/users for paged user breakdowns",
            )
        reader = getattr(app.state, "quota_usage_reader", None)
        if reader is None:
            return _problem(503, "usage_unavailable", "Quota usage persistence is unavailable")
        payload = await service.system_usage(
            identity, reader, days, include_users=include_users
        )
        payload.setdefault("catalog", monitor_model_catalog())
        return payload

    @app.get("/api/v1/observability/usage/system/users", tags=["observability"])
    async def system_usage_users(
        identity: Principal,
        days: int = Query(30, ge=1, le=365),
        limit: int = Query(12, ge=1, le=100),
        offset: int = Query(0, ge=0),
    ):
        reader = getattr(app.state, "quota_usage_reader", None)
        if reader is None:
            return _problem(503, "usage_unavailable", "Quota usage persistence is unavailable")
        return await service.system_usage_users(
            identity, reader, days=days, limit=limit, offset=offset
        )

    @app.get("/api/v1/observability/usage/system/dimensions", tags=["observability"])
    async def system_usage_dimensions(
        identity: Principal,
        dimension: str = Query(
            "providers",
            pattern="^(users|workspaces|providers|purposes|models)$",
        ),
        days: int = Query(30, ge=1, le=365),
        limit: int = Query(12, ge=1, le=100),
        offset: int = Query(0, ge=0),
    ):
        reader = getattr(app.state, "quota_usage_reader", None)
        if reader is None:
            return _problem(503, "usage_unavailable", "Quota usage persistence is unavailable")
        return await service.system_usage_dimension(
            identity,
            reader,
            dimension=dimension,
            days=days,
            limit=limit,
            offset=offset,
        )

    @app.get("/api/v1/observability/usage/system/trend", tags=["observability"])
    async def system_usage_trend(
        identity: Principal,
        window_minutes: int = Query(120, ge=5, le=1440),
        bucket_minutes: int = Query(5, ge=1, le=60),
    ):
        reader = getattr(app.state, "quota_usage_reader", None)
        if reader is None:
            return _problem(503, "usage_unavailable", "Quota usage persistence is unavailable")
        if bucket_minutes > window_minutes:
            return _problem(422, "invalid_usage_window", "bucket_minutes must not exceed window_minutes")
        return await service.system_usage_trend(
            identity,
            reader,
            window_minutes=window_minutes,
            bucket_minutes=bucket_minutes,
        )

    @app.get("/api/v1/observability/usage-shadow", tags=["observability"])
    async def usage_shadow(
        identity: Principal,
        days: int = Query(30, ge=1, le=365),
    ):
        reader = getattr(app.state, "quota_usage_reader", None)
        if reader is None:
            return _problem(503, "usage_unavailable", "Quota usage persistence is unavailable")
        authorization_service.require(identity, Permission.SYSTEM_RUNTIME_MONITOR)
        return await asyncio.to_thread(reader.shadow_comparison, days=days)

    @app.get("/api/v1/observability/events", tags=["observability"])
    async def events(identity: Principal, limit: int = Query(200, ge=1, le=1000), level: str | None = None, trace_id: str | None = None):
        return {"items": await service.events(identity, limit=limit, level=level, trace_id=trace_id)}

    @app.get("/api/v1/observability/errors", tags=["observability"])
    async def errors(
        identity: Principal,
        days: int = Query(30, ge=1, le=365),
        limit: int = Query(100, ge=1, le=500),
        offset: int = Query(0, ge=0),
        window_minutes: int = Query(120, ge=5, le=1440),
        bucket_minutes: int = Query(5, ge=1, le=60),
    ):
        if bucket_minutes > window_minutes:
            return _problem(422, "invalid_error_window", "bucket_minutes must not exceed window_minutes")
        return await service.error_analysis(
            identity,
            days=days,
            limit=limit,
            offset=offset,
            window_minutes=window_minutes,
            bucket_minutes=bucket_minutes,
        )

    @app.get("/api/v1/observability/storage", tags=["observability"])
    async def storage(identity: Principal):
        return {**(await service.health(identity)), "retention": app.state.monitor_retention}

    @app.post("/api/v1/observability/storage/prune", tags=["observability"])
    async def prune(
        _identity: Principal,
        _write: WriteClaims,
        trace_days: int | None = Query(None, ge=1, le=365),
        event_days: int | None = Query(None, ge=1, le=365),
    ):
        configured_trace_days = int(retention["trace_days"])
        configured_event_days = int(retention["event_days"])
        trace_days, event_days = enforce_manual_retention(
            configured_trace_days=configured_trace_days,
            configured_event_days=configured_event_days,
            requested_trace_days=trace_days or configured_trace_days,
            requested_event_days=event_days or configured_event_days,
        )
        await asyncio.to_thread(runtime.repository.prune, trace_days, event_days)
        return runtime.health()

    @app.post("/api/v1/observability/storage/reset", tags=["observability"])
    async def reset(_identity: Principal, _write: ResetWriteClaims):
        return await resetter.reset()

    @app.websocket("/ws/observability")
    async def live_events(websocket: WebSocket):
        try:
            if not auth_injected:
                origin = websocket.headers.get("origin")
                database_auth.require_same_origin(origin, websocket.headers.get("host"))
                session = await database_auth.consume_ws_ticket(
                    rbac_runtime.session_factory,
                    websocket.query_params.get("ticket"),
                    origin=origin,
                    host=websocket.headers.get("host"),
                )
                async with rbac_runtime.session_factory() as db_session:
                    identity = await rbac_service.principal_for_user_id(
                        db_session, session.user_id
                    )
            else:
                auth.require_same_origin(websocket.headers.get("origin"), websocket.headers.get("host"))
                session = auth.authenticate(websocket.cookies.get(auth.cookie_name))
                identity = session.principal()
            authorization_service.require(identity, Permission.SYSTEM_RUNTIME_MONITOR)
        except AuthenticationError:
            await websocket.close(code=4401, reason="authentication required"); return
        except (OriginRejectedError, AccessDeniedError):
            await websocket.close(code=4403, reason="access rejected"); return
        await websocket.accept()
        queue = service.subscribe(identity)
        try:
            for row in reversed(await asyncio.to_thread(runtime.repository.recent_events, limit=100)):
                await asyncio.wait_for(websocket.send_json({"type": "telemetry.event", "payload": row}), timeout=5)
            while True:
                try:
                    envelope = await asyncio.wait_for(queue.get(), timeout=1)
                except asyncio.TimeoutError:
                    await asyncio.wait_for(websocket.send_json({"type": "monitor.heartbeat", "payload": runtime.health()}), timeout=5)
                    continue
                if envelope["kind"] == "event":
                    await asyncio.wait_for(websocket.send_json({"type": "telemetry.event", "payload": envelope["payload"]}), timeout=5)
        except (asyncio.CancelledError, asyncio.TimeoutError, RuntimeError, WebSocketDisconnect):
            return
        finally:
            service.unsubscribe(queue)

    static_value = str(config.get("static_dir", "")).strip()
    static_dir = Path(static_value).expanduser() if static_value else None
    if static_dir is not None and not static_dir.is_absolute():
        static_dir = Path(__file__).resolve().parents[2] / static_dir
    if static_dir is not None and static_dir.is_dir():
        @app.get("/monitor", include_in_schema=False)
        async def monitor_root():
            return FileResponse(static_dir / "index.html")

        @app.get("/monitor/{path:path}", include_in_schema=False)
        async def monitor_spa(path: str = ""):
            return FileResponse(static_dir / "index.html")
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="monitor-ui")
    else:
        @app.get("/", include_in_schema=False)
        async def root():
            return {"name": "NLP Agent Observability Monitor", "api": "/api/v1/observability"}
    return app


app = create_monitor_app()
