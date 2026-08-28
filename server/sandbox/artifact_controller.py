from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from configs.settings import settings
from server.auth.dependencies import Principal, get_database_session_claims, get_db_session
from server.infrastructure.mysql.models import SandboxArtifactModel
from server.web.database_auth import DatabaseSessionClaims

from .artifact_delivery import build_artifact_response, issue_artifact_access_url
from .artifacts import ArtifactAccessSigner, artifact_expired, artifact_request_origin_matches

router = APIRouter(prefix="/api/v1/sandbox/artifacts", tags=["sandbox-artifacts"])
DbSession = Annotated[AsyncSession, Depends(get_db_session)]
DatabaseClaims = Annotated[DatabaseSessionClaims, Depends(get_database_session_claims)]


def _signer() -> ArtifactAccessSigner:
    secret = settings.NLP_AGENT_WEB_SECRET.strip()
    if not secret:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Sandbox artifact signing is not configured.")
    return ArtifactAccessSigner(secret)


@router.get("/{artifact_id}/access")
async def issue_artifact_access(artifact_id: str, request: Request, db: DbSession, principal: Principal, claims: DatabaseClaims) -> dict[str, str]:
    artifact = await db.get(SandboxArtifactModel, artifact_id)
    expires_at = getattr(artifact, "expires_at", None)
    if artifact is None or artifact.owner_user_id != claims.user_id or principal.user_id != claims.user_id or artifact_expired(expires_at):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    origin = settings.NLP_AGENT_SANDBOX_ARTIFACT_ORIGIN.strip()
    if not origin:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Sandbox artifact origin is not configured.")
    try:
        return {"url": issue_artifact_access_url(artifact, requester_user_id=claims.user_id, signer=_signer(), artifact_origin=origin, application_origin=str(request.base_url).rstrip("/"))}
    except (PermissionError, ValueError) as error:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)) from error


@router.get("/{artifact_id}/content")
async def get_artifact_content(artifact_id: str, ticket: str, request: Request, db: DbSession):
    artifact_origin = settings.NLP_AGENT_SANDBOX_ARTIFACT_ORIGIN.strip()
    forwarded_scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    request_origin = f"{forwarded_scheme}://{request.headers.get('host', '')}"
    if (
        request.headers.get("x-nova-artifact-delivery") != "1"
        or not artifact_request_origin_matches(request_origin, configured_origin=artifact_origin)
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    artifact = await db.get(SandboxArtifactModel, artifact_id)
    if artifact is None or artifact_expired(getattr(artifact, "expires_at", None)):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    root = settings.NLP_AGENT_SANDBOX_ARTIFACT_STORE_ROOT.strip()
    if not root:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Sandbox artifact store is not configured.")
    try:
        application_origin = settings.NLP_AGENT_SANDBOX_APPLICATION_ORIGIN.strip() or None
        return build_artifact_response(
            artifact,
            ticket=ticket,
            signer=_signer(),
            store_root=Path(root),
            application_origin=application_origin,
        )
    except (FileNotFoundError, PermissionError, ValueError) as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND) from error
