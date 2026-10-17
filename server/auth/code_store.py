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

from sqlalchemy import delete, func, select, text, update, or_, and_
from sqlalchemy.ext.asyncio import AsyncSession

from server.infrastructure.mysql.models import AuthCodeModel, EmailSendAuditModel
from server.auth.verification_policy import EMAIL_CODE_TTL_S, EMAIL_RESEND_COOLDOWN_S

# ---------------------------------------------------------------------------
#  Policy constants
# ---------------------------------------------------------------------------

CAPTCHA_TTL_S = 120           # 图形验证码 2 分钟内有效
SMS_CODE_TTL_S = 300          # 手机验证码 5 分钟内有效
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
SMS_RESEND_COOLDOWN_S = 60    # 同一手机号两次发送至少间隔 60 秒
SMS_MAX_PER_PHONE_HOUR = 10   # 同一手机号每小时最多 10 条
SMS_MAX_PER_IP_HOUR = 30      # 同一 IP 每小时最多 30 条
_SMS_HISTORY_KINDS = ("sms", "sms_attempt", "sms_history")


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
    """Single-use verification: retire the row and check hash + expiry.

    CAPTCHA rows are deleted; SMS rows become unusable send history, preserving
    hourly rate-limit accounting. Both paths lock the row before checking it.
    """
    row = await session.scalar(
        select(AuthCodeModel)
        .where(AuthCodeModel.kind == kind, AuthCodeModel.subject == subject)
        .order_by(AuthCodeModel.created_at.desc(), AuthCodeModel.id.desc())
        .limit(1)
        .with_for_update()
    )
    if row is None:
        return False
    valid = row.expires_at > _utc_now() and secrets.compare_digest(row.code_hash, _code_hash(code))
    if kind == "sms":
        row.kind = "sms_history"
        row.expires_at = _utc_now()
    else:
        await session.execute(delete(AuthCodeModel).where(AuthCodeModel.id == row.id))
    await session.flush()
    return valid


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
        select(func.max(AuthCodeModel.created_at)).where(
            AuthCodeModel.kind.in_(_SMS_HISTORY_KINDS),
            AuthCodeModel.subject == phone,
        )
    )
    if last_send_at is not None and last_send_at > cooldown_floor:
        return False, "sms_send_too_frequent"

    phone_count = await session.scalar(
        select(func.count(AuthCodeModel.id)).where(
            AuthCodeModel.kind.in_(_SMS_HISTORY_KINDS),
            AuthCodeModel.subject == phone,
            AuthCodeModel.created_at > hour_ago,
        )
    )
    if int(phone_count or 0) >= SMS_MAX_PER_PHONE_HOUR:
        return False, "sms_send_phone_limit"

    if client_ip:
        ip_count = await session.scalar(
            select(func.count(AuthCodeModel.id)).where(
                AuthCodeModel.kind.in_(_SMS_HISTORY_KINDS),
                AuthCodeModel.client_ip == client_ip,
                AuthCodeModel.created_at > hour_ago,
            )
        )
        if int(ip_count or 0) >= SMS_MAX_PER_IP_HOUR:
            return False, "sms_send_ip_limit"

    return True, ""


async def reserve_sms_attempt(
    session: AsyncSession, *, phone: str, client_ip: str | None
) -> str:
    """Persist an unusable attempt before external I/O, for rate-limit accounting."""
    attempt_id = str(uuid.uuid4())
    now = _utc_now()
    session.add(
        AuthCodeModel(
            id=attempt_id,
            kind="sms_attempt",
            subject=phone,
            code_hash=_code_hash(secrets.token_urlsafe(32)),
            expires_at=now,
            client_ip=client_ip,
        )
    )
    await session.flush()
    return attempt_id


async def complete_sms_attempt(
    session: AsyncSession, *, attempt_id: str, code: str, ttl_s: int
) -> None:
    """Turn a persisted attempt into a usable one-time code after delivery succeeds."""
    row = await session.scalar(
        select(AuthCodeModel).where(AuthCodeModel.id == attempt_id).with_for_update()
    )
    if row is None or row.kind != "sms_attempt":
        raise RuntimeError("SMS delivery attempt is no longer available")
    # A slow, older send may finish after a newer code was delivered. It must
    # not revoke that newer code or become the selected usable code itself.
    newer = await session.scalar(select(AuthCodeModel.id).where(
        AuthCodeModel.subject == row.subject,
        AuthCodeModel.kind.in_(("sms", "sms_history")),
        or_(AuthCodeModel.created_at > row.created_at, and_(
            AuthCodeModel.created_at == row.created_at, AuthCodeModel.id > row.id
        )),
    ).limit(1).with_for_update())
    if newer is not None:
        row.kind = "sms_history"
        row.expires_at = _utc_now()
        await session.flush()
        return
    await session.execute(update(AuthCodeModel).where(
        AuthCodeModel.subject == row.subject, AuthCodeModel.kind == "sms"
    ).values(kind="sms_history", expires_at=_utc_now()))
    row.kind = "sms"
    row.code_hash = _code_hash(code)
    row.expires_at = _utc_now() + timedelta(seconds=ttl_s)
    await session.flush()


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
