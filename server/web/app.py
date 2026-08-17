"""FastAPI same-origin adapter for the lifecycle-owning BackendGateway."""

import asyncio
import json
import secrets
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, Query, Request, Response, Security, WebSocket, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.security import APIKeyCookie
from starlette.middleware.trustedhost import TrustedHostMiddleware

from configs.settings import settings
from core.identity import AccessDeniedError, AuthenticatedPrincipal
from core.rbac import Permission, ResourceRef, authorization_service
from core.authorization_audit import begin as begin_authorization_audit, end as end_authorization_audit
from gateway.contracts import (
    GatewayNotStartedError,
    InjectMessageRequest,
    ResourceNotFoundError,
    SubmitTurnRequest,
    TurnConflictError,
)
from gateway.core import BackendGateway
from server.web.auth import (
    AuthenticationError,
    CsrfRejectedError,
    OriginRejectedError,
    SameOriginSessionAuth,
    SessionClaims,
)
from server.web.contracts import (
    CreateSessionBody,
    LoginBody,
    ReplaceUserRolesBody,
    ReplaceRolePermissionsBody,
    ReplaceRoleMenusBody,
    CreateRoleBody,
    UpdateRoleStatusBody,
    CreateClassroomBody,
    ReplaceClassroomMemberBody,
    InjectChatBody,
    SubmitChatBody,
    ToolApprovalBody,
    UpdateCustomToolsBody,
    UpdateToolPoliciesBody,
    UpdateSettingsBody,
    McpServerBody,
    SkillBody,
    WorkerProfileBody,
    ReleaseNoteBody,
)
from server.web.protocol import control_event
from server.web.developer import developer_snapshot
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
    upsert_skill,
    upsert_worker_profile,
)
from server.teacher.models import ExerciseBlueprint, GuidedBlueprint, ReviewBlueprint, UpdateTeacherCatalog, UpdateTeachingGoals
from server.teacher.service import teacher_service
from server.rbac.service import rbac_service
from server.release_notes.service import (
    ReleaseNoteConflictError,
    ReleaseNoteNotFoundError,
    release_note_service,
)
from server.web.websocket import WebSocketHub, websocket_endpoint


GatewayFactory = Callable[[], BackendGateway]


def _problem(
    request: Request,
    *,
    status_code: int,
    code: str,
    title: str,
    detail: str | None = None,
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
    return JSONResponse(body, status_code=status_code, media_type="application/problem+json")


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


def create_app(
    *,
    gateway_factory: GatewayFactory = BackendGateway,
    auth: SameOriginSessionAuth | None = None,
    allowed_hosts: list[str] | None = None,
) -> FastAPI:
    web_config = settings.web_runtime
    auth = auth or SameOriginSessionAuth.from_config(web_config)
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
        app.state.gateway = gateway
        await gateway.start()
        redis_client = getattr(getattr(gateway, "dispatcher", None), "client", None)
        authorization_channel = str(settings.gateway_runtime.get("redis_authorization_channel", "nlp-agent:authorization"))

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
        try:
            yield
        finally:
            if authorization_listener is not None:
                authorization_listener.cancel()
                await asyncio.gather(authorization_listener, return_exceptions=True)
            await gateway.begin_shutdown()
            await hub.close()
            await gateway.close()

    app = FastAPI(
        title="NLP Agent Web API",
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url=None,
    )
    app.state.auth = auth
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
        if session_factory is not None and decisions:
            async with session_factory() as session:
                async with session.begin():
                    for decision in decisions:
                        await rbac_service.audit(
                            session, actor_user_id=decision.actor_user_id, target_user_id=None,
                            decision=decision.decision, reason_code="authorization_required",
                            permission_code=decision.permission_code, resource_type=decision.resource_type,
                            resource_id=decision.resource_id,
                            detail={"workspace_id": decision.workspace_id, "request_id": request.state.request_id},
                        )
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Cache-Control"] = "no-store"
        return response

    def current_claims(
        token: Annotated[str | None, Security(cookie_auth)],
    ) -> SessionClaims:
        return auth.authenticate(token)

    async def resolve_principal(
        request: Request, claims: SessionClaims
    ) -> AuthenticatedPrincipal:
        """Resolve roles and workspace membership from MySQL in production."""
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
        claims: Annotated[SessionClaims, Depends(current_claims)],
    ) -> AuthenticatedPrincipal:
        return await resolve_principal(request, claims)

    def write_access(
        request: Request,
        claims: Annotated[SessionClaims, Depends(current_claims)],
        csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    ) -> SessionClaims:
        auth.require_same_origin(request.headers.get("origin"), request.headers.get("host"))
        auth.require_csrf(claims, csrf_token)
        return claims

    Principal = Annotated[AuthenticatedPrincipal, Depends(current_principal)]
    WriteClaims = Annotated[SessionClaims, Depends(write_access)]

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

    @app.post("/api/v1/auth/login", status_code=status.HTTP_200_OK, tags=["auth"])
    async def login(body: LoginBody, request: Request, response: Response):
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
            secure=auth.secure,
            samesite="strict",
            path="/",
        )
        return {
            "user_id": claims.user_id,
            "workspace_ids": sorted(claims.workspace_ids),
            "roles": sorted(claims.roles),
            "csrf_token": claims.csrf_token,
            "expires_at": claims.expires_at,
            "ephemeral_secret": auth.ephemeral_secret,
        }

    @app.get("/api/v1/auth/session", tags=["auth"])
    async def get_auth_session(
        request: Request, claims: Annotated[SessionClaims, Depends(current_claims)]
    ):
        principal = await resolve_principal(request, claims)
        return {
            "user_id": principal.user_id,
            "workspace_ids": sorted(principal.workspace_ids),
            "roles": sorted(principal.roles),
            "csrf_token": claims.csrf_token,
            "expires_at": claims.expires_at,
        }

    @app.post("/api/v1/auth/guest", status_code=status.HTTP_200_OK, tags=["auth"])
    async def guest_login(request: Request, response: Response):
        auth.require_same_origin(request.headers.get("origin"), request.headers.get("host"))
        token, claims = auth.issue_guest(
            previous_token=request.cookies.get(auth.cookie_name)
        )
        response.set_cookie(
            auth.cookie_name, token, max_age=auth.ttl_s, httponly=True,
            secure=auth.secure, samesite="strict", path="/",
        )
        return {
            "user_id": claims.user_id, "workspace_ids": [], "roles": ["guest"],
            "csrf_token": claims.csrf_token, "expires_at": claims.expires_at,
        }

    def authorization_session_factory(request: Request):
        factory = getattr(request.app.state.gateway, "authorization_session_factory", None)
        if factory is None:
            raise RuntimeError("RBAC administration requires MySQL persistence")
        return factory

    @app.get("/api/v1/roles", tags=["rbac"])
    async def list_roles(request: Request, principal: Principal):
        authorization_service.require(principal, Permission.SYSTEM_ROLE_MANAGE)
        async with authorization_session_factory(request)() as session:
            roles = await rbac_service.role_catalog(session)
        return {"items": [
            {"code": row.code, "name": row.name, "description": row.description,
             "status": row.status, "is_builtin": row.is_builtin}
            for row in roles
        ]}

    @app.post("/api/v1/system/roles", status_code=status.HTTP_201_CREATED, tags=["rbac"])
    async def create_role(body: CreateRoleBody, request: Request, principal: Principal, _claims: WriteClaims):
        authorization_service.require(principal, Permission.SYSTEM_ROLE_MANAGE)
        async with authorization_session_factory(request)() as session:
            async with session.begin():
                role = await rbac_service.create_role(session, code=body.code, name=body.name, description=body.description, actor_user_id=principal.user_id)
        return {"code": role.code, "name": role.name, "description": role.description, "status": role.status, "is_builtin": role.is_builtin}

    @app.patch("/api/v1/system/roles/{role_code}/status", tags=["rbac"])
    async def update_role_status(role_code: str, body: UpdateRoleStatusBody, request: Request, principal: Principal, _claims: WriteClaims):
        authorization_service.require(principal, Permission.SYSTEM_ROLE_MANAGE)
        async with authorization_session_factory(request)() as session:
            async with session.begin():
                user_ids = await rbac_service.update_role_status(session, role_code=role_code, status=body.status, actor_user_id=principal.user_id)
        for user_id in user_ids:
            await hub.close_user(user_id)
        return {"role_code": role_code, "status": body.status}

    @app.get("/api/v1/permissions", tags=["rbac"])
    async def list_permissions(request: Request, principal: Principal):
        authorization_service.require(principal, Permission.SYSTEM_PERMISSION_READ)
        async with authorization_session_factory(request)() as session:
            permissions = await rbac_service.permission_catalog(session)
        return {"items": [
            {"code": row.code, "name": row.name, "description": row.description,
             "status": row.status}
            for row in permissions
        ]}

    @app.get("/api/v1/users/{user_id}/roles", tags=["rbac"])
    async def get_user_roles(user_id: str, request: Request, principal: Principal):
        authorization_service.require(principal, Permission.SYSTEM_ROLE_MANAGE)
        async with authorization_session_factory(request)() as session:
            roles = await rbac_service.user_role_codes(session, user_id)
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
        async with authorization_session_factory(request)() as session:
            async with session.begin():
                roles = await rbac_service.replace_user_roles(
                    session, user_id=user_id, role_codes=body.role_codes,
                    assigned_by_user_id=principal.user_id,
                )
        await hub.close_user(user_id)
        return {"user_id": user_id, "role_codes": sorted(roles)}

    @app.get("/api/v1/system/roles/{role_code}/permissions", tags=["rbac"])
    async def get_role_permissions(role_code: str, request: Request, principal: Principal):
        authorization_service.require(principal, Permission.SYSTEM_PERMISSION_READ)
        async with authorization_session_factory(request)() as session:
            values = await rbac_service.role_permissions(session, role_code)
        return {"role_code": role_code, "permissions": {key: sorted(value) for key, value in values.items()}}

    @app.put("/api/v1/system/roles/{role_code}/permissions", tags=["rbac"])
    async def put_role_permissions(role_code: str, body: ReplaceRolePermissionsBody, request: Request, principal: Principal, _claims: WriteClaims):
        authorization_service.require(principal, Permission.SYSTEM_ROLE_MANAGE)
        async with authorization_session_factory(request)() as session:
            async with session.begin():
                user_ids = await rbac_service.replace_role_permissions(session, role_code=role_code, permission_codes=body.permission_codes, scopes=body.scopes, actor_user_id=principal.user_id)
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

    @app.put("/api/v1/system/roles/{role_code}/menus", tags=["rbac"])
    async def put_role_menus(role_code: str, body: ReplaceRoleMenusBody, request: Request, principal: Principal, _claims: WriteClaims):
        authorization_service.require(principal, Permission.SYSTEM_ROLE_MANAGE)
        async with authorization_session_factory(request)() as session:
            async with session.begin():
                await rbac_service.replace_role_menus(session, role_code=role_code, menu_ids=body.menu_ids, actor_user_id=principal.user_id)
        return {"role_code": role_code, "menu_ids": sorted(body.menu_ids)}

    @app.get("/api/v1/audit/authorization", tags=["rbac"])
    async def list_authorization_audit(
        request: Request,
        principal: Principal,
        limit: int = Query(default=100, ge=1, le=500),
        actor_user_id: str | None = None,
    ):
        authorization_service.require(principal, Permission.SYSTEM_AUDIT_READ)
        async with authorization_session_factory(request)() as session:
            rows = await rbac_service.audit_records(
                session, limit=limit, actor_user_id=actor_user_id
            )
        return {"items": [
            {
                "id": row.id, "actor_user_id": row.actor_user_id,
                "target_user_id": row.target_user_id, "decision": row.decision,
                "reason_code": row.reason_code, "permission_code": row.permission_code,
                "resource_type": row.resource_type, "resource_id": row.resource_id,
                "detail": row.detail_json, "created_at": row.created_at,
            }
            for row in rows
        ]}

    @app.get("/api/v1/system/sessions/{session_id}/checkpoints/{checkpoint_id}", tags=["rbac"])
    async def read_sensitive_checkpoint(session_id: str, checkpoint_id: str, request: Request, principal: Principal):
        # Runtime inspection alone is deliberately insufficient for prompt/output/checkpoint payloads.
        authorization_service.require(principal, Permission.SYSTEM_SENSITIVE_DATA_READ)
        async with authorization_session_factory(request)() as session:
            async with session.begin():
                checkpoint = await rbac_service.read_sensitive_checkpoint(
                    session, session_id=session_id, checkpoint_id=checkpoint_id,
                    actor_user_id=principal.user_id,
                )
                return {"session_id": checkpoint.session_id, "checkpoint_ns": checkpoint.checkpoint_ns,
                        "checkpoint_id": checkpoint.checkpoint_id, "checkpoint": checkpoint.checkpoint_json,
                        "metadata": checkpoint.metadata_json}

    @app.delete("/api/v1/auth/session", status_code=status.HTTP_204_NO_CONTENT, tags=["auth"])
    async def delete_auth_session(
        request: Request,
        response: Response,
        _claims: WriteClaims,
    ):
        token = request.cookies.get(auth.cookie_name)
        session_fingerprint = auth.token_fingerprint(token)
        auth.revoke(token)
        await hub.close_session(session_fingerprint)
        response.delete_cookie(auth.cookie_name, path="/", samesite="strict")

    @app.get("/api/v1/sessions", tags=["sessions"])
    async def list_sessions(request: Request, principal: Principal):
        authorization_service.require(principal, Permission.AGENT_SESSION_READ)
        return {"items": await request.app.state.gateway.sessions.list(principal)}

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
        return {**analytics, **goals}

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

    @app.get("/api/v1/teacher/catalog/{workspace_id}", tags=["teacher"])
    async def get_teacher_catalog(workspace_id: str, request: Request, principal: Principal):
        return await teacher_service.catalog(principal, request.app.state.gateway, workspace_id)

    @app.get("/api/v1/learning/catalog/{workspace_id}", tags=["learning"])
    async def get_learning_catalog(workspace_id: str, request: Request, principal: Principal):
        authorization_service.require(
            principal, Permission.LEARNING_CONTENT_READ_WORKSPACE, workspace_id=workspace_id
        )
        catalog = (await request.app.state.gateway.get_teaching_catalog(principal, workspace_id))["catalog"]
        catalog["topics"] = [
            {
                **topic,
                "knowledge_points": [
                    point
                    for point in topic.get("knowledge_points", [])
                    if point.get("status", "enabled") == "enabled"
                ],
            }
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

    @app.get("/api/v1/teacher/questions", tags=["teacher"])
    async def teacher_questions(
        request: Request,
        principal: Principal,
        workspace_id: str = Query(default="default", min_length=1, max_length=128),
        days: int = Query(default=30, ge=1, le=365),
        limit: int = Query(default=500, ge=1, le=2_000),
    ):
        result = await teacher_service.analytics(
            principal, request.app.state.gateway, workspace_id, days, limit
        )
        return {"items": result["questions"], "period_days": days}

    @app.get("/api/v1/teacher/analytics", tags=["teacher"])
    async def teacher_analytics(
        request: Request,
        principal: Principal,
        workspace_id: str = Query(default="default", min_length=1, max_length=128),
        days: int = Query(default=30, ge=1, le=365),
    ):
        result = await teacher_service.analytics(
            principal, request.app.state.gateway, workspace_id, days
        )
        result.pop("questions", None)
        return result

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
        )

    static_dir_value = str(web_config.get("static_dir", "")).strip()
    static_dir = Path(static_dir_value).expanduser() if static_dir_value else None
    if static_dir is not None and not static_dir.is_absolute():
        static_dir = Path(__file__).resolve().parents[2] / static_dir
    if static_dir is not None and static_dir.is_dir():
        @app.get("/developer", include_in_schema=False)
        @app.get("/developer/{developer_path:path}", include_in_schema=False)
        async def developer_spa(developer_path: str = ""):
            return FileResponse(static_dir / "index.html")

        @app.get("/teacher", include_in_schema=False)
        @app.get("/teacher/{teacher_path:path}", include_in_schema=False)
        async def teacher_spa(teacher_path: str = ""):
            return FileResponse(static_dir / "index.html")

        app.mount("/", StaticFiles(directory=static_dir, html=True), name="webui")
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
