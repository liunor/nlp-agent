"""DB-backed one-time verification code store with server-side rate limits.

Verification codes (image CAPTCHA answers and email codes) used to live in
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

from server.infrastructure.mysql.models import AuthCodeModel, EmailSendAuditModel

# ---------------------------------------------------------------------------
#  Policy constants
# ---------------------------------------------------------------------------

EMAIL_CODE_TTL_S = 120        # 邮箱验证码 2 分钟内有效
CAPTCHA_TTL_S = 120           # 图形验证码 2 分钟内有效
EMAIL_RESEND_COOLDOWN_S = 60  # 同一邮箱两次发送至少间隔 60 秒
EMAIL_MAX_PER_EMAIL_HOUR = 10 # 同一邮箱每小时最多 10 封
EMAIL_MAX_PER_IP_HOUR = 30    # 同一 IP 每小时最多 30 封


@asynccontextmanager
async def email_send_lock(session: AsyncSession, email: str):
    """Serialize email rate checks with a transaction-scoped MySQL row lock.

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
            "INSERT INTO nlp_email_send_locks (email, locked_at) "
            "VALUES (:email, UTC_TIMESTAMP(6)) "
            "ON DUPLICATE KEY UPDATE locked_at = UTC_TIMESTAMP(6)"
        ),
        {"email": email},
    )
    await session.execute(
        text(
            "SELECT email FROM nlp_email_send_locks "
            "WHERE email = :email FOR UPDATE"
        ),
        {"email": email},
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


async def email_send_allowed(
    session: AsyncSession, *, email: str, client_ip: str | None
) -> tuple[bool, str]:
    """Server-side rate limits for verification-email sending.

    Returns ``(allowed, reason)``; ``reason`` is a stable machine-readable
    code when denied.
    """
    now = _utc_now()
    hour_ago = now - timedelta(hours=1)
    cooldown_floor = now - timedelta(seconds=EMAIL_RESEND_COOLDOWN_S)

    last_send_at = await session.scalar(
        select(func.max(EmailSendAuditModel.created_at)).where(
            EmailSendAuditModel.email == email
        )
    )
    if last_send_at is not None and last_send_at > cooldown_floor:
        return False, "email_send_too_frequent"

    email_count = await session.scalar(
        select(func.count(EmailSendAuditModel.id)).where(
            EmailSendAuditModel.email == email,
            EmailSendAuditModel.created_at > hour_ago,
        )
    )
    if int(email_count or 0) >= EMAIL_MAX_PER_EMAIL_HOUR:
        return False, "email_send_email_limit"

    if client_ip:
        ip_count = await session.scalar(
            select(func.count(EmailSendAuditModel.id)).where(
                EmailSendAuditModel.client_ip == client_ip,
                EmailSendAuditModel.created_at > hour_ago,
            )
        )
        if int(ip_count or 0) >= EMAIL_MAX_PER_IP_HOUR:
            return False, "email_send_ip_limit"

    return True, ""


async def purge_expired(session: AsyncSession) -> None:
    """Best-effort cleanup of stale rows (called opportunistically).

    Verification rows and email audit rows are kept for a full hour after
    creation so the hourly send-rate counters stay accurate.
    """
    cutoff = _utc_now() - timedelta(hours=1)
    await session.execute(delete(AuthCodeModel).where(AuthCodeModel.created_at < cutoff))
    await session.execute(delete(EmailSendAuditModel).where(EmailSendAuditModel.created_at < cutoff))
    await session.flush()


async def record_email_send(
    session: AsyncSession,
    *,
    email: str,
    client_ip: str | None,
    outcome: str = "sent",
) -> str:
    """Record an email attempt independently from the consumable code row."""
    row = EmailSendAuditModel(
        id=str(uuid.uuid4()),
        email=email,
        client_ip=client_ip,
        outcome=outcome,
    )
    session.add(row)
    await session.flush()
    return row.id
