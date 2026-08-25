"""Resolve phase-one image inputs from the controlled uploads directory."""

from __future__ import annotations

import shutil
from pathlib import Path

from core.session_context import SessionContext
from server.tools.vision.contracts import ImageAsset
from server.tools.vision.safety import (
    ImageSafetyLimits,
    load_validated_image,
    resolve_controlled_path,
)


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_UPLOADS_ROOT = PROJECT_ROOT / ".data" / "uploads"


def session_uploads_root(
    context: SessionContext, *, uploads_root: Path | None = None
) -> Path:
    """Return the non-guessable identity namespace used by upload and Worker code."""

    root = Path(uploads_root or DEFAULT_UPLOADS_ROOT)
    return root / context.workspace_id / context.user_id / context.session_id


def delete_session_uploads(
    context: SessionContext, *, uploads_root: Path | None = None
) -> bool:
    """Delete only one validated session's local upload namespace."""

    path = session_uploads_root(context, uploads_root=uploads_root)
    if not path.exists():
        return False
    shutil.rmtree(path)
    return True


class ImageInputResolver:
    def __init__(
        self,
        *,
        uploads_root: Path | None = None,
        project_root: Path | None = None,
        limits: ImageSafetyLimits | None = None,
    ) -> None:
        self.uploads_root = Path(uploads_root or DEFAULT_UPLOADS_ROOT)
        self.project_root = Path(project_root or PROJECT_ROOT)
        self.limits = limits or ImageSafetyLimits()

    def resolve(self, image: str) -> ImageAsset:
        path = resolve_controlled_path(
            image,
            uploads_root=self.uploads_root,
            project_root=self.project_root,
        )
        return load_validated_image(path, self.limits)
