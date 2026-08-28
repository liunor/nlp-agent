"""Persist only runtime-returned files from the explicit sandbox artifacts directory."""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
import hashlib
from pathlib import Path, PurePosixPath
from uuid import uuid4

from server.infrastructure.mysql.models import SandboxArtifactModel

MAX_ARTIFACT_BYTES = 8 * 1024 * 1024


def normalized_runtime_artifacts(payload: object) -> list[tuple[str, str, bytes]]:
    if not isinstance(payload, list):
        return []
    accepted: list[tuple[str, str, bytes]] = []
    used = 0
    for item in payload[:16]:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        mime_type = item.get("mime_type")
        encoded = item.get("content_b64")
        path = PurePosixPath(name) if isinstance(name, str) else None
        if path is None or path.is_absolute() or ".." in path.parts or not path.parts or not isinstance(mime_type, str) or not isinstance(encoded, str):
            continue
        try:
            data = base64.b64decode(encoded, validate=True)
        except Exception:
            continue
        if len(data) > MAX_ARTIFACT_BYTES or used + len(data) > MAX_ARTIFACT_BYTES:
            continue
        used += len(data)
        accepted.append((path.as_posix(), mime_type[:128], data))
    return accepted


def persist_runtime_artifacts(*, db, execution_id: str, owner_user_id: str, payload: object, store_root: Path, ttl_seconds: int) -> list[SandboxArtifactModel]:
    records: list[SandboxArtifactModel] = []
    for name, mime_type, data in normalized_runtime_artifacts(payload):
        artifact_id = str(uuid4())
        locator = f"{execution_id}/{artifact_id}/{name}"
        destination = store_root / locator
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        record = SandboxArtifactModel(
            id=artifact_id, execution_id=execution_id, owner_user_id=owner_user_id,
            kind="file", mime_type=mime_type, locator=locator,
            sha256=hashlib.sha256(data).hexdigest(), size_bytes=len(data),
            expires_at=datetime.now(UTC).replace(tzinfo=None) + timedelta(seconds=max(1, ttl_seconds)),
        )
        db.add(record)
        records.append(record)
    return records
