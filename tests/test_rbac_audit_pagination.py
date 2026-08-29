from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from server.infrastructure.mysql.models import AuthorizationAuditLogModel
from server.rbac.service import rbac_service


@pytest.mark.asyncio
async def test_audit_page_is_stable_and_summary_is_aggregated() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        metadata = sa.MetaData()
        sa.Table(
            "nlp_authorization_audit_logs",
            metadata,
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("actor_user_id", sa.String(36)),
            sa.Column("target_user_id", sa.String(36)),
            sa.Column("decision", sa.String(16), nullable=False),
            sa.Column("reason_code", sa.String(64), nullable=False),
            sa.Column("permission_code", sa.String(128)),
            sa.Column("resource_type", sa.String(64)),
            sa.Column("resource_id", sa.String(128)),
            sa.Column("detail_json", sa.JSON, nullable=False),
            sa.Column("created_at", sa.DateTime, nullable=False),
        )
        async with engine.begin() as connection:
            await connection.run_sync(metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        async with factory.begin() as session:
            session.add_all(
                [
                    AuthorizationAuditLogModel(
                        id="audit-1",
                        actor_user_id="user-1",
                        decision="allow",
                        reason_code="role_created",
                        detail_json={},
                        created_at=now - timedelta(minutes=2),
                    ),
                    AuthorizationAuditLogModel(
                        id="audit-2",
                        actor_user_id="user-1",
                        decision="deny",
                        reason_code="authorization_required",
                        detail_json={},
                        created_at=now - timedelta(minutes=1),
                    ),
                    AuthorizationAuditLogModel(
                        id="audit-3",
                        actor_user_id="user-2",
                        decision="allow",
                        reason_code="role_created",
                        detail_json={},
                        created_at=now,
                    ),
                ]
            )

        async with factory() as session:
            rows, total = await rbac_service.audit_page(
                session, limit=1, offset=1, actor_user_id="user-1"
            )
            summary = await rbac_service.audit_summary(
                session, since=now - timedelta(hours=1)
            )

        assert total == 2
        assert [row.id for row in rows] == ["audit-1"]
        assert summary == {
            "total": 3,
            "by_decision": {"allow": 2, "deny": 1},
            "top_reasons": [
                {"reason_code": "role_created", "count": 2},
                {"reason_code": "authorization_required", "count": 1},
            ],
        }
    finally:
        await engine.dispose()
