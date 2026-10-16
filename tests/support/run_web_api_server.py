from __future__ import annotations

import asyncio
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import delete, select
import uvicorn


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from gateway.core import BackendGateway  # noqa: E402
from configs.settings import settings  # noqa: E402
from server.infrastructure.mysql import MySQLRuntime  # noqa: E402
from server.infrastructure.mysql.models import (  # noqa: E402
    RoleModel,
    UserModel,
    UserRoleModel,
    WorkspaceMemberModel,
    WorkspaceModel,
)
from server.quota.models import PolicyBindingModel, QuotaPolicyModel  # noqa: E402
from server.user.schemas import UserCreate  # noqa: E402
from server.user.service import UserService  # noqa: E402
from server.web.app import create_app  # noqa: E402
from test_web_api import FakeEngine  # noqa: E402


INTEGRATION_USERNAME = os.environ.get(
    "PRO_NLP_INTEGRATION_USERNAME", f"integrationtest{uuid.uuid4().hex[:24]}"
)
INTEGRATION_PASSWORD = os.environ.get(
    "PRO_NLP_INTEGRATION_PASSWORD", f"Integration-{uuid.uuid4().hex}!"
)


async def seed_integration_user() -> None:
    runtime = MySQLRuntime.from_runtime(settings.database_runtime)
    await runtime.start()
    try:
        async with runtime.session_factory.begin() as session:
            service = UserService(session)
            user = await session.scalar(
                select(UserModel).where(UserModel.username == INTEGRATION_USERNAME)
            )
            if user is None:
                user = await service.create_user(
                    UserCreate(
                        username=INTEGRATION_USERNAME,
                        display_name="Integration User",
                        password=INTEGRATION_PASSWORD,
                    )
                )
            else:
                user.password_hash = service.hasher.hash(INTEGRATION_PASSWORD)
                user.status = "active"
                user.deleted_at = None
                user.authorization_version += 1
                await service._revoke_user_sessions(user.id)
            developer_role = await session.scalar(
                select(RoleModel).where(
                    RoleModel.code == "developer", RoleModel.status == "active"
                )
            )
            if developer_role is None:
                raise RuntimeError("developer role is not seeded")
            await session.execute(
                delete(UserRoleModel).where(UserRoleModel.user_id == user.id)
            )
            session.add(
                UserRoleModel(
                    user_id=user.id,
                    role_id=developer_role.id,
                )
            )
            workspace = await session.scalar(
                select(WorkspaceModel).where(WorkspaceModel.slug == "default")
            )
            if workspace is None:
                workspace = WorkspaceModel(
                    id=str(uuid.uuid4()),
                    slug="default",
                    name="Default Workspace",
                    status="active",
                )
                session.add(workspace)
                await session.flush()
            member = await session.scalar(
                select(WorkspaceMemberModel).where(
                    WorkspaceMemberModel.workspace_id == workspace.id,
                    WorkspaceMemberModel.user_id == user.id,
                )
            )
            if member is None:
                session.add(
                    WorkspaceMemberModel(
                        workspace_id=workspace.id,
                        user_id=user.id,
                        member_type="member",
                        status="active",
                    )
                )
            policy_code = f"integration-unlimited-{INTEGRATION_USERNAME}"
            policy = await session.scalar(
                select(QuotaPolicyModel).where(
                    QuotaPolicyModel.code == policy_code,
                    QuotaPolicyModel.version == "1",
                )
            )
            if policy is None:
                policy = QuotaPolicyModel(
                    id=str(uuid.uuid4()),
                    code=policy_code,
                    version="1",
                    name="Integration test unlimited policy",
                    status="active",
                    request_limit_micro=None,
                    daily_limit_micro=None,
                    weekly_limit_micro=None,
                    concurrency_limit=None,
                    max_overdraft_micro=0,
                    allowed_model_profiles=[],
                    unlimited=True,
                    effective_from=datetime.now(timezone.utc).replace(tzinfo=None),
                    effective_until=None,
                    created_by=user.id,
                )
                session.add(policy)
                await session.flush()
            binding = await session.scalar(
                select(PolicyBindingModel).where(
                    PolicyBindingModel.subject_type == "user",
                    PolicyBindingModel.subject_id == user.id,
                )
            )
            if binding is None:
                session.add(
                    PolicyBindingModel(
                        id=str(uuid.uuid4()),
                        subject_type="user",
                        subject_id=user.id,
                        policy_id=policy.id,
                        priority=100,
                        status="active",
                        effective_from=datetime.now(timezone.utc).replace(tzinfo=None),
                        effective_until=None,
                    )
                )
            else:
                binding.policy_id = policy.id
                binding.status = "active"
                binding.effective_until = None
    finally:
        await runtime.close()


def main() -> None:
    port = int(sys.argv[1])
    engine = FakeEngine()

    asyncio.run(seed_integration_user())

    app = create_app(
        gateway_factory=lambda: BackendGateway(engine=engine),
        allowed_hosts=["127.0.0.1"],
    )
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
