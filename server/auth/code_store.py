"""DB-backed one-time verification code store with server-side rate limits.

Verification codes (image CAPTCHA answers and SMS codes) used to live in
per-process dicts, which breaks as soon as more than one server instance is
running: the instance that generated the code may not be the one asked to
verify it.  This module stores codes in ``nlp_auth_codes`` so every instance
shares one source of truth, and it enforces send-rate limits on the server
(the frontend 60s countdown is UX only and never a security control).

Codes are stored as sha256 hashes; rows are single-use and expire via
``expires_at``.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from server.infrastructure.mysql.models import AuthCodeModel, SmsSendAuditModel

# ---------------------------------------------------------------------------
#  Policy constants
# ---------------------------------------------------------------------------

SMS_CODE_TTL_S = 120          # 短信验证码 2 分钟内有效
CAPTCHA_TTL_S = 120           # 图形验证码 2 分钟内有效
SMS_RESEND_COOLDOWN_S = 60    # 同一手机号两次发送至少间隔 60 秒
SMS_MAX_PER_PHONE_HOUR = 10   # 同一手机号每小时最多 10 条
SMS_MAX_PER_IP_HOUR = 30      # 同一 IP 每小时最多 30 条


@asynccontextmanager
async def sms_send_lock(session: AsyncSession, phone: str):
    """Serialize SMS rate checks with a transaction-scoped MySQL row lock.

    A connection-scoped ``GET_LOCK`` is not sufficient here: releasing it in
    the endpoint before the request transaction commits lets the next replica
    miss the still-uncommitted audit row. The lock row is held by ``SELECT FOR
    UPDATE`` until the request-scoped transaction commits or rolls back.
    """
    bind = session.get_bind()
    if bind is None or bind.dialect.name != "mysql":
        yield
        return
    await session.execute(
        text(
            "INSERT INTO nlp_sms_send_locks (phone_number, locked_at) "
            "VALUES (:phone, UTC_TIMESTAMP(6)) "
            "ON DUPLICATE KEY UPDATE locked_at = UTC_TIMESTAMP(6)"
        ),
        {"phone": phone},
    )
    await session.execute(
        text(
            "SELECT phone_number FROM nlp_sms_send_locks "
            "WHERE phone_number = :phone FOR UPDATE"
        ),
        {"phone": phone},
    )
    yield


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _code_hash(code: str) -> str:
    return hashlib.sha256(code.strip().casefold().encode("utf-8")).hexdigest()


async def put_code(
    session: AsyncSession,
    *,
    kind: str,
    subject: str,
    code: str,
    ttl_s: int,
    client_ip: str | None = None,
) -> None:
    """Store a fresh code, replacing any previous one for the same subject."""
    now = _utc_now()
    # Lock and update the single row in place. This preserves one-code
    # semantics under concurrent requests and avoids delete/insert races.
    row = await session.scalar(
        select(AuthCodeModel)
        .where(AuthCodeModel.kind == kind, AuthCodeModel.subject == subject)
        .with_for_update()
    )
    if row is None:
        session.add(AuthCodeModel(
            id=str(uuid.uuid4()), kind=kind, subject=subject,
            code_hash=_code_hash(code), expires_at=now + timedelta(seconds=ttl_s),
            client_ip=client_ip,
        ))
    else:
        row.code_hash = _code_hash(code)
        row.expires_at = now + timedelta(seconds=ttl_s)
        row.client_ip = client_ip
    await session.flush()


async def consume_code(
    session: AsyncSession, *, kind: str, subject: str, code: str
) -> bool:
    """Single-use verification: delete the row and check hash + expiry.

    The row is removed regardless of the outcome so a code can never be
    replayed.  Returns ``True`` only when the code matches and has not
    expired.  MySQL has no ``DELETE ... RETURNING``, so the row is locked,
    read, and then deleted.
    """
    row = await session.scalar(
        select(AuthCodeModel)
        .where(AuthCodeModel.kind == kind, AuthCodeModel.subject == subject)
        .with_for_update()
    )
    if row is None:
        return False
    await session.execute(
        delete(AuthCodeModel).where(AuthCodeModel.id == row.id)
    )
    await session.flush()
    if row.expires_at < _utc_now():
        return False
    return secrets.compare_digest(row.code_hash, _code_hash(code))


async def sms_send_allowed(
    session: AsyncSession, *, phone: str, client_ip: str | None
) -> tuple[bool, str]:
    """Server-side rate limits for SMS code sending.

    Returns ``(allowed, reason)``; ``reason`` is a stable machine-readable
    code when denied.
    """
    now = _utc_now()
    hour_ago = now - timedelta(hours=1)
    cooldown_floor = now - timedelta(seconds=SMS_RESEND_COOLDOWN_S)

    last_send_at = await session.scalar(
        select(func.max(SmsSendAuditModel.created_at)).where(
            SmsSendAuditModel.phone_number == phone
        )
    )
    if last_send_at is not None and last_send_at > cooldown_floor:
        return False, "sms_send_too_frequent"

    phone_count = await session.scalar(
        select(func.count(SmsSendAuditModel.id)).where(
            SmsSendAuditModel.phone_number == phone,
            SmsSendAuditModel.created_at > hour_ago,
        )
    )
    if int(phone_count or 0) >= SMS_MAX_PER_PHONE_HOUR:
        return False, "sms_send_phone_limit"

    if client_ip:
        ip_count = await session.scalar(
            select(func.count(SmsSendAuditModel.id)).where(
                SmsSendAuditModel.client_ip == client_ip,
                SmsSendAuditModel.created_at > hour_ago,
            )
        )
        if int(ip_count or 0) >= SMS_MAX_PER_IP_HOUR:
            return False, "sms_send_ip_limit"

    return True, ""


async def purge_expired(session: AsyncSession) -> None:
    """Best-effort cleanup of stale rows (called opportunistically).

    Verification rows and SMS audit rows are kept for a full hour after
    creation so the hourly send-rate counters stay accurate.
    """
    cutoff = _utc_now() - timedelta(hours=1)
    await session.execute(delete(AuthCodeModel).where(AuthCodeModel.created_at < cutoff))
    await session.execute(delete(SmsSendAuditModel).where(SmsSendAuditModel.created_at < cutoff))
    await session.flush()


async def record_sms_send(
    session: AsyncSession,
    *,
    phone: str,
    client_ip: str | None,
    outcome: str = "sent",
) -> str:
    """Record an SMS attempt independently from the consumable code row."""
    row = SmsSendAuditModel(
        id=str(uuid.uuid4()),
        phone_number=phone,
        client_ip=client_ip,
        outcome=outcome,
    )
    session.add(row)
    await session.flush()
    return row.id
