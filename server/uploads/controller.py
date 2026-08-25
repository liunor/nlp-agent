"""Image upload endpoints (phase-one local-file workflow)."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse

from core.identity import AuthenticatedPrincipal
from core.session_context import SessionContext
from server.rbac.service import rbac_service
from server.uploads.schemas import UploadResponse
from server.web.auth import AuthenticationError, SessionClaims
from server.web.database_auth import DatabaseSessionClaims
from server.tools.vision.contracts import VisionError
from server.tools.vision.input_resolver import session_uploads_root
from server.tools.vision.safety import (
    ImageSafetyLimits,
    load_validated_image,
)

router = APIRouter(prefix="/api/v1/uploads", tags=["uploads"])

_LIMITS = ImageSafetyLimits()
_MEDIA_TYPE_TO_EXT: dict[str, str] = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}


async def _current_claims(
    request: Request,
) -> SessionClaims | DatabaseSessionClaims | None:
    """Use the same browser-auth mode as the primary Web API routes."""
    auth = getattr(request.app.state, "auth", None)
    if auth is None:
        return None
    token = request.cookies.get(auth.cookie_name)
    session_factory = getattr(
        getattr(request.app.state, "gateway", None), "authorization_session_factory", None
    )
    if not getattr(request.app.state, "auth_injected", True):
        database_auth = getattr(request.app.state, "database_auth", None)
        if database_auth is None or session_factory is None:
            raise AuthenticationError("database authentication is unavailable")
        return await database_auth.authenticate(session_factory, token)
    return auth.authenticate(token)


CurrentClaims = Annotated[
    SessionClaims | DatabaseSessionClaims | None, Depends(_current_claims)
]


async def get_current_principal(
    request: Request, claims: CurrentClaims
) -> AuthenticatedPrincipal:
    """Extract an authenticated principal from the configured browser session."""
    if claims is None:
        return AuthenticatedPrincipal(
            user_id="local",
            workspace_ids=frozenset({"default"}),
            roles=frozenset({"admin"}),
        )
    session_factory = getattr(
        getattr(request.app.state, "gateway", None), "authorization_session_factory", None
    )
    if isinstance(claims, DatabaseSessionClaims):
        if session_factory is None:
            raise AuthenticationError("database authentication is unavailable")
        async with session_factory() as session:
            return await rbac_service.principal_for_user_id(session, claims.user_id)
    if session_factory is not None and claims.roles != frozenset({"guest"}):
        async with session_factory() as session:
            return await rbac_service.principal_for_username(session, claims.user_id)
    return claims.principal()


async def get_write_access(
    request: Request,
    claims: CurrentClaims,
    csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
) -> None:
    """Validate CSRF and origin headers for mutating upload requests."""
    if claims is None:
        return
    if isinstance(claims, DatabaseSessionClaims):
        database_auth = getattr(request.app.state, "database_auth", None)
        if database_auth is None:
            raise AuthenticationError("database authentication is unavailable")
        database_auth.require_same_origin(request.headers.get("origin"), request.headers.get("host"))
        database_auth.require_csrf(claims, csrf_token)
    else:
        auth = request.app.state.auth
        auth.require_same_origin(request.headers.get("origin"), request.headers.get("host"))
        auth.require_csrf(claims, csrf_token)


async def _resolve_session_context(
    request: Request,
    principal: AuthenticatedPrincipal,
    session_id: str,
) -> SessionContext:
    gateway = getattr(request.app.state, "gateway", None)
    sessions = getattr(gateway, "sessions", None)
    if sessions is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="session service is unavailable",
        )
    try:
        return await sessions.resolve(principal, session_id)
    except (FileNotFoundError, PermissionError, ValueError) as exc:
        # Do not reveal whether a session exists outside this principal's scope.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND) from exc


@router.post("", response_model=UploadResponse, status_code=status.HTTP_201_CREATED)
async def upload_image(
    request: Request,
    session_id: str = Form(..., min_length=1, max_length=128),
    file: UploadFile = File(...),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _write: None = Depends(get_write_access),
) -> UploadResponse:
    context = await _resolve_session_context(request, principal, session_id)
    data = await file.read(_LIMITS.max_file_bytes + 1)
    if len(data) > _LIMITS.max_file_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"文件超过 {_LIMITS.max_file_bytes} 字节上限",
        )

    uploads_dir = session_uploads_root(context)
    uploads_dir.mkdir(parents=True, exist_ok=True)

    temp_name = f"_tmp_{uuid.uuid4().hex}"
    temp_path = uploads_dir / temp_name
    try:
        temp_path.write_bytes(data)
        try:
            asset = load_validated_image(temp_path, _LIMITS)
        except VisionError as exc:
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail=exc.message,
            ) from exc
        ext = _MEDIA_TYPE_TO_EXT.get(asset.reference.media_type, ".bin")
        safe_name = f"{uuid.uuid4().hex}{ext}"
        final_path = uploads_dir / safe_name
        temp_path.rename(final_path)
    except HTTPException:
        temp_path.unlink(missing_ok=True)
        raise
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise

    return UploadResponse(
        file_name=safe_name,
        url=f"/api/v1/uploads/{session_id}/{safe_name}",
        media_type=asset.reference.media_type,
        size_bytes=asset.reference.size_bytes,
        width=asset.reference.width,
        height=asset.reference.height,
        sha256=asset.reference.sha256,
    )


@router.get("/{session_id}/{file_name}")
async def get_upload(
    request: Request,
    session_id: str,
    file_name: str,
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
) -> FileResponse:
    if "/" in file_name or "\\" in file_name or ".." in file_name:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    context = await _resolve_session_context(request, principal, session_id)
    uploads_dir = session_uploads_root(context)
    file_path = uploads_dir / file_name
    if not file_path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return FileResponse(
        path=file_path,
        headers={"X-Content-Type-Options": "nosniff"},
    )
