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
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from server.infrastructure.mysql.models import AuthCodeModel

# ---------------------------------------------------------------------------
#  Policy constants
# ---------------------------------------------------------------------------

SMS_CODE_TTL_S = 120          # 短信验证码 2 分钟内有效
CAPTCHA_TTL_S = 120           # 图形验证码 2 分钟内有效
SMS_RESEND_COOLDOWN_S = 60    # 同一手机号两次发送至少间隔 60 秒
SMS_MAX_PER_PHONE_HOUR = 10   # 同一手机号每小时最多 10 条
SMS_MAX_PER_IP_HOUR = 30      # 同一 IP 每小时最多 30 条


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
    # 先清掉该 subject 的旧码（单码语义：重发即作废旧码）。
    await session.execute(
        delete(AuthCodeModel).where(
            AuthCodeModel.kind == kind, AuthCodeModel.subject == subject
        )
    )
    session.add(
        AuthCodeModel(
            id=str(uuid.uuid4()),
            kind=kind,
            subject=subject,
            code_hash=_code_hash(code),
            expires_at=now + timedelta(seconds=ttl_s),
            client_ip=client_ip,
        )
    )
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
        select(func.max(AuthCodeModel.created_at)).where(
            AuthCodeModel.kind == "sms", AuthCodeModel.subject == phone
        )
    )
    if last_send_at is not None and last_send_at > cooldown_floor:
        return False, "sms_send_too_frequent"

    phone_count = await session.scalar(
        select(func.count(AuthCodeModel.id)).where(
            AuthCodeModel.kind == "sms",
            AuthCodeModel.subject == phone,
            AuthCodeModel.created_at > hour_ago,
        )
    )
    if int(phone_count or 0) >= SMS_MAX_PER_PHONE_HOUR:
        return False, "sms_send_phone_limit"

    if client_ip:
        ip_count = await session.scalar(
            select(func.count(AuthCodeModel.id)).where(
                AuthCodeModel.kind == "sms",
                AuthCodeModel.client_ip == client_ip,
                AuthCodeModel.created_at > hour_ago,
            )
        )
        if int(ip_count or 0) >= SMS_MAX_PER_IP_HOUR:
            return False, "sms_send_ip_limit"

    return True, ""


async def purge_expired(session: AsyncSession) -> None:
    """Best-effort cleanup of stale rows (called opportunistically).

    Rows are kept for a full hour after creation so the hourly send-rate
    counters (per phone / per IP) stay accurate; only rows that are both
    expired and older than the rate-limit window are removed.
    """
    cutoff = _utc_now() - timedelta(hours=1)
    await session.execute(
        delete(AuthCodeModel).where(AuthCodeModel.created_at < cutoff)
    )
    await session.flush()
