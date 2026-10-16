"""Real isolated SQL storage checks with controlled time; no email/network calls."""
from datetime import datetime, timedelta
from email import message_from_string
from copy import copy

import pytest
import pytest_asyncio
from sqlalchemy import MetaData, event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from server.auth import code_store
from server.auth.verification_policy import EMAIL_CODE_TTL_S
from server.infrastructure.mysql.models import AuthCodeModel
from server.user import email_provider


@pytest_asyncio.fixture
async def codes(monkeypatch):
    clock = [datetime(2026, 10, 15, 12, 0)]
    monkeypatch.setattr(code_store, '_utc_now', lambda: clock[0])
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')

    @event.listens_for(engine.sync_engine, 'connect')
    def sqlite_utc_timestamp(connection, _record):
        connection.create_function('utc_timestamp', 1, lambda _precision: clock[0].isoformat(' '))

    try:
        async with engine.begin() as connection:
            # SQLite has binary text comparison by default but does not know
            # MySQL's ascii_bin collation name. Adapt test DDL, not the model.
            table = AuthCodeModel.__table__.to_metadata(MetaData())
            for column in table.c:
                if getattr(column.type, 'collation', None):
                    column.type = copy(column.type)
                    column.type.collation = None
            await connection.run_sync(table.create)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            yield session, clock
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('elapsed,valid', [(119, True), (121, True), (299.999, True), (300, False), (300.001, False)])
async def test_five_minute_boundary_and_single_use(codes, elapsed, valid):
    session, clock = codes
    await code_store.put_code(session, kind='email', subject='fixture@example.com',
                              code='123456', ttl_s=EMAIL_CODE_TTL_S)
    clock[0] += timedelta(seconds=elapsed)
    assert await code_store.consume_code(session, kind='email', subject='fixture@example.com', code='123456') is valid
    assert await code_store.consume_code(session, kind='email', subject='fixture@example.com', code='123456') is False


@pytest.mark.asyncio
async def test_reissuing_replaces_code_and_expiry(codes):
    session, clock = codes
    await code_store.put_code(session, kind='email', subject='fixture@example.com', code='111111', ttl_s=EMAIL_CODE_TTL_S)
    clock[0] += timedelta(seconds=200)
    await code_store.put_code(session, kind='email', subject='fixture@example.com', code='222222', ttl_s=EMAIL_CODE_TTL_S)
    clock[0] += timedelta(seconds=290)
    assert await code_store.consume_code(session, kind='email', subject='fixture@example.com', code='222222') is True


@pytest.mark.asyncio
async def test_captcha_keeps_its_own_two_minute_boundary(codes):
    session, clock = codes
    assert code_store.CAPTCHA_TTL_S == 120
    await code_store.put_code(session, kind='captcha', subject='fixture-captcha', code='ABCD', ttl_s=code_store.CAPTCHA_TTL_S)
    clock[0] += timedelta(seconds=120)
    assert await code_store.consume_code(session, kind='captcha', subject='fixture-captcha', code='ABCD') is False


def test_smtp_copy_matches_the_storage_duration(monkeypatch):
    sent = []

    class Mailbox:
        def __init__(self, *args, **kwargs):
            pass

        def sendmail(self, _sender, _recipients, message):
            sent.append(message_from_string(message))

        def quit(self):
            pass

    monkeypatch.setattr(email_provider.smtplib, 'SMTP_SSL', Mailbox)
    provider = email_provider.SmtpEmailProvider('fixture.invalid', 465, '', '', 'sender@example.com')
    provider._send_sync('fixture@example.com', '123456')
    text = sent[0].get_payload(decode=True).decode('utf-8')
    assert '5 分钟内有效' in text
    assert EMAIL_CODE_TTL_S == code_store.EMAIL_CODE_TTL_S == 300
