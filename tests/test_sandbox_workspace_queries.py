from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import sqlalchemy as sa
from sqlalchemy.orm import Session

from server.infrastructure.mysql.models import SandboxEnvironmentModel, SandboxRuntimeInstanceModel
from server.sandbox.contracts import SandboxScope
from server.sandbox.service import SandboxLifecycleService
from server.sandbox.warm_pool import WarmPoolService


async def test_describe_and_claim_choose_current_workspace_with_two_environments(monkeypatch):
    engine = sa.create_engine("sqlite:///:memory:")
    metadata = sa.MetaData()
    # Execute the real ORM queries against a small portable schema. No MySQL
    # defaults or foreign keys are needed to exercise scope selection.
    for model in (SandboxEnvironmentModel, SandboxRuntimeInstanceModel):
        columns = []
        for column in model.__table__.columns:
            if isinstance(column.type, sa.DateTime):
                type_ = sa.DateTime()
            elif isinstance(column.type, sa.Integer):
                type_ = sa.Integer()
            else:
                type_ = sa.String()
            columns.append(sa.Column(column.name, type_, primary_key=column.primary_key))
        sa.Table(model.__tablename__, metadata, *columns)
    metadata.create_all(engine)
    now = datetime.now(UTC).replace(tzinfo=None)
    with Session(engine, autoflush=False) as db:
        for number in (1, 2):
            db.add(SandboxEnvironmentModel(id=f"env-{number}", owner_user_id="user",
                workspace_id=f"ws-{number}", generation=4, resource_profile_id="python-base",
                profile_revision=1, status="ready", active_runtime_id=f"runtime-{number}",
                created_at=now, updated_at=now))
            db.add(SandboxRuntimeInstanceModel(id=f"runtime-{number}", environment_id=f"env-{number}",
                state="assigned", generation=4, resource_profile_id="python-base",
                runtime_kind="docker", created_at=now, updated_at=now))
        db.commit()

        class AsyncSession:
            async def scalar(self, statement):
                return db.scalar(statement)

        scope = SandboxScope("user", "session", "ws-2", 4, now + timedelta(minutes=5))
        session = AsyncSession()
        claim = await WarmPoolService().claim(session, scope)
        assert claim.runtime.id == "runtime-2"
        service = SandboxLifecycleService()
        monkeypatch.setattr(service, "revoke_expired_leases", AsyncMock())
        # Describe performs a second lease query only after selecting the
        # environment; capture that separately to avoid adding auth fixtures.
        captured = []
        original_scalar = session.scalar

        async def scalar(statement):
            if "nlp_sandbox_leases" in str(statement):
                return None
            environment = await original_scalar(statement)
            captured.append(environment.id)
            return environment

        session.scalar = scalar
        await service.describe(session, scope)
        assert captured == ["env-2"]
    engine.dispose()
