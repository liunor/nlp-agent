from __future__ import annotations

from pathlib import Path
from typing import Protocol
import os

from fastapi import Response

from .artifacts import (
    ArtifactAccessSigner,
    artifact_access_url,
    artifact_expired,
    artifact_security_headers,
    resolve_artifact_path,
    validate_artifact_origin,
)

MAX_ARTIFACT_BYTES = 16 * 1024 * 1024


class ArtifactMetadata(Protocol):
    id: str
    owner_user_id: str
    locator: str
    mime_type: str
    expires_at: object | None


def _read_artifact_bytes(store_root: Path, locator: str) -> bytes:
    """Open a locator with no-follow semantics for every path component."""
    path = resolve_artifact_path(store_root, locator)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    directory = getattr(os, "O_DIRECTORY", 0)
    if directory and os.open in getattr(os, "supports_dir_fd", set()):
        root_fd = os.open(str(store_root.resolve()), os.O_RDONLY | directory | nofollow)
        current_fd = root_fd
        try:
            relative_parts = Path(locator).parts
            for index, component in enumerate(relative_parts):
                flags = os.O_RDONLY | nofollow
                if index < len(relative_parts) - 1:
                    flags |= directory
                next_fd = os.open(component, flags, dir_fd=current_fd)
                if current_fd != root_fd:
                    os.close(current_fd)
                current_fd = next_fd
            with os.fdopen(current_fd, "rb") as stream:
                current_fd = -1
                return stream.read(MAX_ARTIFACT_BYTES + 1)
        finally:
            if current_fd >= 0:
                os.close(current_fd)
            os.close(root_fd)
    flags = os.O_RDONLY | nofollow
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, "rb") as stream:
        return stream.read(MAX_ARTIFACT_BYTES + 1)


def build_artifact_response(
    artifact: ArtifactMetadata,
    *,
    ticket: str,
    signer: ArtifactAccessSigner,
    store_root: Path,
    application_origin: str | None = None,
) -> Response:
    if artifact_expired(getattr(artifact, "expires_at", None)):
        raise PermissionError("sandbox artifact expired")
    signer.verify(ticket, artifact_id=artifact.id, owner_user_id=artifact.owner_user_id)
    frame_ancestors = "'none'"
    if application_origin:
        parsed = validate_artifact_origin(application_origin, application_origin="https://artifact-origin.invalid")
        frame_ancestors = parsed
    headers = artifact_security_headers(artifact.mime_type, frame_ancestors=frame_ancestors)
    if artifact.mime_type == "image/svg+xml":
        headers["Content-Disposition"] = "attachment"
    try:
        content = _read_artifact_bytes(store_root, artifact.locator)
    except OSError as error:
        raise PermissionError("sandbox artifact could not be opened safely") from error
    if len(content) > MAX_ARTIFACT_BYTES:
        raise PermissionError("sandbox artifact exceeds the delivery size limit")
    return Response(content=content, media_type=artifact.mime_type, headers=headers)


def issue_artifact_access_url(artifact: ArtifactMetadata, *, requester_user_id: str, signer: ArtifactAccessSigner, artifact_origin: str, application_origin: str) -> str:
    if artifact.owner_user_id != requester_user_id:
        raise PermissionError("sandbox artifact does not belong to the current user")
    origin = validate_artifact_origin(artifact_origin, application_origin=application_origin)
    ticket = signer.issue(artifact_id=artifact.id, owner_user_id=artifact.owner_user_id)
    return artifact_access_url(origin, artifact_id=artifact.id, ticket=ticket)
