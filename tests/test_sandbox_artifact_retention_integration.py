"""Opt-in MySQL + filesystem proof for bounded Artifact TTL cleanup."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_SANDBOX_ARTIFACT_INTEGRATION") != "1"
    or not os.getenv("NLP_AGENT_DATABASE_URL"),
    reason="Artifact MySQL integration is enabled in Linux CI only",
)


@pytest.mark.asyncio
async def test_expired_artifact_is_removed_from_mysql_and_store(tmp_path: Path) -> None:
    from sqlalchemy import select

    from server.infrastructure.mysql import DatabaseConfig, create_engine, create_session_factory
    from server.infrastructure.mysql.models import (
        SandboxArtifactModel,
        SandboxEnvironmentModel,
        SandboxExecutionModel,
        WorkspaceMemberModel,
    )
    from server.sandbox.artifact_retention import purge_expired_artifacts
    from server.user.schemas import UserCreate
    from server.user.service import UserService

    engine = create_engine(
        DatabaseConfig(os.environ["NLP_AGENT_DATABASE_URL"], pool_size=2, max_overflow=0)
    )
    factory = create_session_factory(engine)
    now = datetime.now(UTC).replace(tzinfo=None)
    artifact_id = str(uuid4())
    locator = "expired/output.html"
    artifact_path = tmp_path / locator
    artifact_path.parent.mkdir(parents=True)
    artifact_path.write_text("expired", encoding="utf-8")
    try:
        async with factory.begin() as session:
            user = await UserService(session).create_user(
                UserCreate(
                    username=f"artifact{uuid4().hex[:12]}",
                    display_name="Artifact integration",
                    password="InitialPw0rd1",
                )
            )
            workspace_id = await session.scalar(
                select(WorkspaceMemberModel.workspace_id).where(
                    WorkspaceMemberModel.user_id == user.id
                )
            )
            environment_id = str(uuid4())
            execution_id = str(uuid4())
            session.add(
                SandboxEnvironmentModel(
                    id=environment_id,
                    owner_user_id=user.id,
                    resource_profile_id="python-base",
                    generation=1,
                )
            )
            await session.flush()
            session.add(
                SandboxExecutionModel(
                    id=execution_id,
                    environment_id=environment_id,
                    owner_user_id=user.id,
                    workspace_id=workspace_id,
                    actor_type="model",
                    request_id=execution_id,
                    code_hash="0" * 64,
                    status="completed",
                    generation=1,
                    started_at=now - timedelta(minutes=1),
                    completed_at=now,
                )
            )
            await session.flush()
            session.add(
                SandboxArtifactModel(
                    id=artifact_id,
                    execution_id=execution_id,
                    owner_user_id=user.id,
                    kind="html",
                    mime_type="text/html",
                    locator=locator,
                    size_bytes=7,
                    expires_at=now - timedelta(seconds=1),
                )
            )
        removed = await purge_expired_artifacts(factory, store_root=tmp_path)
        assert removed == 1
        assert not artifact_path.exists()
        async with factory() as session:
            assert await session.get(SandboxArtifactModel, artifact_id) is None
    finally:
        await engine.dispose()
