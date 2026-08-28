"""Bounded Artifact Store cleanup for the Phase 4 TTL contract."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from server.infrastructure.mysql.models import SandboxArtifactModel

from .artifacts import resolve_artifact_path
from .faults import SandboxFaultInjector


async def purge_expired_artifacts(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    store_root: Path,
    limit: int = 100,
    now: datetime | None = None,
    fault_injector: SandboxFaultInjector | None = None,
) -> int:
    """Delete expired metadata and its safe file locator in bounded batches."""
    current = now or datetime.now(UTC)
    faults = fault_injector or SandboxFaultInjector.from_env()
    async with session_factory.begin() as session:
        rows = list(
            (
                await session.scalars(
                    select(SandboxArtifactModel)
                    .where(SandboxArtifactModel.expires_at.is_not(None))
                    .where(SandboxArtifactModel.expires_at <= current.replace(tzinfo=None))
                    .order_by(SandboxArtifactModel.created_at)
                    .limit(max(1, min(limit, 1_000)))
                    .with_for_update(skip_locked=True)
                )
            ).all()
        )
        for artifact in rows:
            try:
                faults.fail_if_configured("artifact.cleanup")
                path = resolve_artifact_path(store_root, artifact.locator)
                path.unlink(missing_ok=True)
            except (FileNotFoundError, PermissionError, OSError, ValueError):
                # Metadata expiry remains authoritative even if a prior cleanup
                # or a broken locator already removed the file.
                pass
            await session.delete(artifact)
        return len(rows)
