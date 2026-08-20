"""FastAPI monitor plane on a port isolated from student chat traffic."""

import asyncio
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, Query, Request, Response, Security, WebSocket
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import APIKeyCookie
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.websockets import WebSocketDisconnect

from configs.settings import settings
from core.identity import AccessDeniedError, AuthenticatedPrincipal
from core.rbac import Permission, authorization_service
from core.observability.runtime import TelemetryRuntime
from core.observability.service import ObservabilityService
from server.infrastructure.mysql import MySQLRuntime
from server.rbac.service import rbac_service
from server.web.auth import AuthenticationError, CsrfRejectedError, OriginRejectedError, SameOriginSessionAuth, SessionClaims
from server.web.database_auth import DatabaseSessionAuth, DatabaseSessionClaims
from server.monitor.reset import LocalRuntimeResetter


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
    # accepts the control-plane browser session and only applies its own
    # origin allow-list to ticket issuance. Keeping the cookie name and TTL in
    # the web runtime prevents a user from logging in on 8765 and silently
    # becoming unauthenticated on 8766.
    database_config = dict(settings.web_runtime)
    database_config["allowed_origins"] = list(config.get("allowed_origins", []))
    database_auth = DatabaseSessionAuth.from_config(database_config)
    cookie_secure = database_auth.secure if not auth_injected else auth.secure
    resetter = resetter or LocalRuntimeResetter(runtime)
    rbac_runtime = MySQLRuntime.from_runtime(settings.database_runtime)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.runtime = runtime
        app.state.observability = service
        app.state.rbac_runtime = rbac_runtime
        await rbac_runtime.start()
        try:
            yield
        finally:
            await rbac_runtime.close()
            await runtime.close()

    app = FastAPI(
        title="NLP Agent Observability Monitor",
        version="1.0.0",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
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
        request.state.request_id = request.headers.get("x-request-id") or secrets.token_hex(16)
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Cache-Control"] = "no-store"
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
        if isinstance(session, DatabaseSessionClaims):
            database_auth.require_same_origin(
                request.headers.get("origin"), request.headers.get("host")
            )
            database_auth.require_csrf(session, csrf)
        else:
            auth.require_same_origin(request.headers.get("origin"), request.headers.get("host"))
            auth.require_csrf(session, csrf)
        authorization_service.require(identity, Permission.SYSTEM_RUNTIME_MONITOR)
        return session

    Principal = Annotated[AuthenticatedPrincipal, Depends(principal)]
    WriteClaims = Annotated[
        SessionClaims | DatabaseSessionClaims, Depends(write_access)
    ]

    @app.exception_handler(AuthenticationError)
    async def auth_error(_request: Request, _error: AuthenticationError):
        return _problem(401, "authentication_required", "Authentication required")

    @app.exception_handler(AccessDeniedError)
    async def access_error(_request: Request, _error: AccessDeniedError):
        return _problem(403, "forbidden", "Developer monitor permission required")

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
        return {"status": "ready", **runtime.health()}

    @app.post("/api/v1/auth/session", status_code=201, tags=["auth"])
    async def create_session(request: Request, response: Response):
        if not auth_injected:
            raise AuthenticationError(
                "monitor sessions are created through the control-plane login"
            )
        auth.require_same_origin(request.headers.get("origin"), request.headers.get("host"))
        token, session = auth.issue()
        response.set_cookie(auth.cookie_name, token, max_age=auth.ttl_s, httponly=True, secure=cookie_secure, samesite="lax", path="/")
        return {"user_id": session.user_id, "roles": sorted(session.roles), "csrf_token": session.csrf_token, "expires_at": session.expires_at}

    @app.get("/api/v1/auth/session", tags=["auth"])
    async def get_session(
        session: Annotated[SessionClaims | DatabaseSessionClaims, Depends(claims)],
        identity: Principal,
    ):
        if isinstance(session, DatabaseSessionClaims):
            csrf_token = await database_auth.rotate_csrf(
                rbac_runtime.session_factory, session
            )
            session = DatabaseSessionClaims(
                **{**session.__dict__, "csrf_token": csrf_token}
            )
        return {
            "user_id": identity.user_id,
            "roles": sorted(identity.roles),
            "permissions": sorted(identity.permissions),
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

    @app.get("/api/v1/observability/overview", tags=["observability"])
    async def overview(identity: Principal, days: int = Query(30, ge=1, le=365)):
        return await service.overview(identity, days)

    @app.get("/api/v1/observability/traces", tags=["observability"])
    async def traces(identity: Principal, limit: int = Query(100, ge=1, le=500), session_id: str | None = None, status: str | None = None):
        return {"items": await service.traces(identity, limit=limit, session_id=session_id, status=status)}

    @app.get("/api/v1/observability/traces/{trace_id}", tags=["observability"])
    async def trace(trace_id: str, identity: Principal):
        detail = await service.trace(identity, trace_id)
        if detail is None:
            return _problem(404, "trace_not_found", "Trace not found")
        return detail

    @app.get("/api/v1/observability/usage", tags=["observability"])
    async def usage(identity: Principal, days: int = Query(30, ge=1, le=365)):
        return {"items": await service.usage(identity, days)}

    @app.get("/api/v1/observability/sessions", tags=["observability"])
    async def sessions(identity: Principal, days: int = Query(30, ge=1, le=365), limit: int = Query(100, ge=1, le=500)):
        return {"items": await service.sessions(identity, days, limit)}

    @app.get("/api/v1/observability/events", tags=["observability"])
    async def events(identity: Principal, limit: int = Query(200, ge=1, le=1000), level: str | None = None, trace_id: str | None = None):
        return {"items": await service.events(identity, limit=limit, level=level, trace_id=trace_id)}

    @app.get("/api/v1/observability/errors", tags=["observability"])
    async def errors(identity: Principal, days: int = Query(30, ge=1, le=365), limit: int = Query(100, ge=1, le=500)):
        return {"items": await service.errors(identity, days, limit)}

    @app.get("/api/v1/observability/storage", tags=["observability"])
    async def storage(identity: Principal):
        return await service.health(identity)

    @app.post("/api/v1/observability/storage/prune", tags=["observability"])
    async def prune(_identity: Principal, _write: WriteClaims, trace_days: int = Query(30, ge=1, le=365), event_days: int = Query(30, ge=1, le=365)):
        await asyncio.to_thread(runtime.repository.prune, trace_days, event_days)
        return runtime.health()

    @app.post("/api/v1/observability/storage/reset", tags=["observability"])
    async def reset(_identity: Principal, _write: WriteClaims):
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
        @app.get("/monitor/{path:path}", include_in_schema=False)
        async def monitor_spa(path: str = ""):
            return FileResponse(static_dir / "index.html")
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="monitor-ui")
    else:
        @app.get("/", include_in_schema=False)
        async def root():
            return {"name": "NLP Agent Observability Monitor", "api": "/api/v1/observability", "docs": "/api/docs"}
    return app


app = create_monitor_app()
