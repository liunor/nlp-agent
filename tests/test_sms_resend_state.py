"""SQL state regressions for resend; MySQL locking is verified separately."""

from datetime import datetime, timedelta, timezone
from copy import copy

import pytest
from sqlalchemy import MetaData, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from server.auth import code_store
from server.infrastructure.mysql.models import AuthCodeModel


@pytest.mark.asyncio
@pytest.mark.parametrize("old_send_completes_late", [False, True])
async def test_resend_accepts_only_new_code_and_retains_both_send_records(old_send_completes_late):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as connection:
            table = AuthCodeModel.__table__.to_metadata(MetaData())
            for column in table.c:
                if getattr(column.type, "collation", None):
                    column.type = copy(column.type)
                    column.type.collation = None
            await connection.run_sync(table.create)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        async with factory.begin() as session:
            session.add_all([
                AuthCodeModel(id="older", kind="sms_attempt" if old_send_completes_late else "sms", subject="13800138000", code_hash=code_store._code_hash("111111"), created_at=now - timedelta(seconds=61), expires_at=now + timedelta(seconds=120)),
                AuthCodeModel(id="newer", kind="sms_attempt", subject="13800138000", code_hash="unusable", created_at=now, expires_at=now),
            ])
        async with factory.begin() as session:
            await code_store.complete_sms_attempt(session, attempt_id="newer", code="222222", ttl_s=120)
        if old_send_completes_late:
            async with factory.begin() as session:
                await code_store.complete_sms_attempt(session, attempt_id="older", code="111111", ttl_s=120)
        async with factory.begin() as session:
            assert await code_store.consume_code(session, kind="sms", subject="13800138000", code="222222")
            assert not await code_store.consume_code(session, kind="sms", subject="13800138000", code="111111")
            assert await code_store.sms_send_allowed(session, phone="13800138000", client_ip=None) == (False, "sms_send_too_frequent")
            records = (await session.scalars(select(AuthCodeModel))).all()
            assert len(records) == 2
            assert {record.kind for record in records} == {"sms_history"}
    finally:
        await engine.dispose()
