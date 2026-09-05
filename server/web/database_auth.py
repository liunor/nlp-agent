"""Database-backed browser authentication for the production Web API.

The browser only receives an opaque random token.  The token, CSRF secret and
the authorization version are checked against MySQL on every request, so
password changes, account lifecycle changes and explicit revocation take
effect immediately across processes.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import secrets
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from time import monotonic
from typing import Any
from urllib.parse import urlsplit

from argon2.exceptions import VerificationError, VerifyMismatchError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import joinedload

from configs.settings import auth_env_bool, auth_env_int, auth_session_ttl_s
from server.infrastructure.mysql.models import (
    SessionModel,
    UserModel,
    WorkspaceMemberModel,
    WsTicketModel,
)
from server.user.service import PasswordHasherSingleton
from server.user.phone import InvalidPhoneNumberError, normalize_phone_number
from server.sandbox.service import sandbox_lifecycle_service
from server.web.auth import AuthenticationError, CsrfRejectedError, OriginRejectedError


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _phone_variants(identifier: str) -> list[str]:
    """Plausible stored forms of *identifier* when it looks like a phone number.

    Registration stores one canonical E.164 value, so login uses the same
    identity regardless of spacing, punctuation or domestic ``+86`` prefix.
    Returns ``[]`` for inputs that are not phone-shaped.
    """
    try:
        return [normalize_phone_number(identifier)]
    except InvalidPhoneNumberError:
        return []


@dataclass(frozen=True)
class DatabaseSessionClaims:
    user_id: str
    workspace_id: str
    session_id: str
    token_hash_value: str
    csrf_hash_value: str
    expires_at: datetime
    authorization_version: int
    csrf_token: str | None = None

    @property
    def token_hash(self) -> str:
        return self.token_hash_value

    @property
    def csrf_hash(self) -> str:
        return self.csrf_hash_value

    @property
    def expires_at_epoch(self) -> int:
        return int(self.expires_at.replace(tzinfo=timezone.utc).timestamp())


class _RateLimiter:
    def __init__(self, max_attempts: int, window_s: int) -> None:
        self.max_attempts = max(1, int(max_attempts))
        self.window_s = max(1, int(window_s))
        self._failures: dict[str, deque[float]] = defaultdict(deque)

    def allowed(self, key: str) -> bool:
        now = monotonic()
        failures = self._failures[key]
        while failures and now - failures[0] >= self.window_s:
            failures.popleft()
        return len(failures) < self.max_attempts

    def record_failure(self, key: str) -> None:
        self._failures[key].append(monotonic())

    def clear(self, key: str) -> None:
        self._failures.pop(key, None)


class DatabaseSessionAuth:
    """MySQL-backed authentication and same-origin/CSRF policy."""

    def __init__(
        self,
        *,
        cookie_name: str = "nlp_session",
        ttl_s: int = 86_400,
        secure: bool = False,
        allowed_origins: list[str] | None = None,
        idle_timeout_s: int = 900,
        max_login_attempts: int = 5,
        rate_window_s: int = 300,
    ) -> None:
        self.cookie_name = cookie_name
        self.ttl_s = min(max(int(ttl_s), 300), 604_800)
        self.secure = secure
        self.idle_timeout_s = min(max(int(idle_timeout_s), 60), self.ttl_s)
        self.allowed_origins = {
            item.rstrip("/").lower() for item in (allowed_origins or []) if item
        }
        self._hasher = PasswordHasherSingleton.get()
        self._username_rate_limiter = _RateLimiter(max_login_attempts, rate_window_s)
        self._client_rate_limiter = _RateLimiter(max_login_attempts, rate_window_s)
        self._redis = None
        self._rate_prefix = "nlp-agent:auth-login:"

    def set_redis_client(self, redis_client: Any | None) -> None:
        """Use the shared Redis limiter when the deployment provides Redis."""
        self._redis = redis_client

    async def _rate_allowed(self, username: str, client_key: str) -> bool:
        if self._redis is None:
            return self._username_rate_limiter.allowed(username) and self._client_rate_limiter.allowed(client_key)
        for key in (f"{self._rate_prefix}user:{username}", f"{self._rate_prefix}ip:{client_key}"):
            value = await self._redis.get(key)
            if value is not None and int(value) >= self._username_rate_limiter.max_attempts:
                return False
        return True

    async def _rate_failure(self, username: str, client_key: str) -> None:
        if self._redis is None:
            self._username_rate_limiter.record_failure(username)
            self._client_rate_limiter.record_failure(client_key)
            return
        for key in (f"{self._rate_prefix}user:{username}", f"{self._rate_prefix}ip:{client_key}"):
            count = await self._redis.incr(key)
            if int(count) == 1:
                await self._redis.expire(key, self._username_rate_limiter.window_s)

    async def _rate_clear(self, username: str, client_key: str) -> None:
        if self._redis is None:
            self._username_rate_limiter.clear(username)
            self._client_rate_limiter.clear(client_key)
            return
        await self._redis.delete(f"{self._rate_prefix}user:{username}", f"{self._rate_prefix}ip:{client_key}")

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "DatabaseSessionAuth":
        return cls(
            cookie_name=str(config.get("cookie_name", "nlp_session")),
            ttl_s=auth_session_ttl_s(86_400),
            # Secure is the production-safe default.  Local HTTP development
            # may explicitly opt out through NLP_AGENT_AUTH_COOKIE_SECURE=false.
            secure=auth_env_bool("NLP_AGENT_AUTH_COOKIE_SECURE", bool(config.get("cookie_secure", True))),
            allowed_origins=list(config.get("allowed_origins", [])),
            idle_timeout_s=auth_env_int("NLP_AGENT_AUTH_IDLE_TIMEOUT_S", 900),
            max_login_attempts=auth_env_int("NLP_AGENT_AUTH_MAX_LOGIN_ATTEMPTS", 5),
            rate_window_s=auth_env_int("NLP_AGENT_AUTH_RATE_WINDOW_S", 300),
        )

    @staticmethod
    def token_hash(token: str) -> str:
        return hashlib.sha256(token.encode("ascii")).hexdigest()

    @staticmethod
    def csrf_hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    @classmethod
    def token_fingerprint(cls, token: str | None) -> bytes | None:
        if not token:
            return None
        return hashlib.sha256(token.encode("ascii")).digest()

    @staticmethod
    def session_fingerprint_from_hash(token_hash: str) -> bytes:
        """Return the stable in-process key used to close a DB-backed socket."""
        try:
            return bytes.fromhex(token_hash)
        except ValueError as error:
            raise AuthenticationError("authentication session hash is invalid") from error

    @staticmethod
    def _verify_password(password_hash: str, password: str, hasher: Any) -> bool:
        try:
            return bool(hasher.verify(password_hash, password))
        except (VerifyMismatchError, VerificationError, ValueError, TypeError):
            return False

    async def login(
        self,
        factory: async_sessionmaker[AsyncSession],
        username: str,
        password: str,
        *,
        client_key: str = "unknown",
        previous_token: str | None = None,
        workspace_id: str | None = None,
    ) -> tuple[str, DatabaseSessionClaims]:
        normalized = username.casefold()
        if not await self._rate_allowed(normalized, client_key):
            raise AuthenticationError("too many login attempts")

        async with factory.begin() as session:
            # 主登录入口同时接受用户名与规范化手机号。手机号命中唯一的
            # ``phone_number_normalized`` 索引，避免多种原始格式产生歧义。
            identity_criteria = UserModel.username_lower == normalized
            phone_variants = _phone_variants(username)
            if phone_variants:
                identity_criteria = identity_criteria | UserModel.phone_number_normalized.in_(phone_variants)
            user = await session.scalar(
                select(UserModel)
                .where(
                    identity_criteria,
                    UserModel.deleted_at.is_(None),
                )
                .with_for_update()
            )
            valid = (
                user is not None
                and user.status == "active"
                and await asyncio.to_thread(
                    self._verify_password, user.password_hash, password, self._hasher
                )
            )
            if not valid:
                await self._rate_failure(normalized, client_key)
                raise AuthenticationError("invalid credentials")

            await self._rate_clear(normalized, client_key)
            now = _utc_now()
            user.last_login_at = now
            selected_workspace = await self._select_workspace(
                session, user.id, workspace_id
            )
            if previous_token:
                await session.execute(
                    update(SessionModel)
                    .where(
                        SessionModel.token_hash == self.token_hash(previous_token),
                        SessionModel.revoked_at.is_(None),
                    )
                    .values(revoked_at=_utc_now())
                )
            token = secrets.token_urlsafe(32)
            csrf_token = secrets.token_urlsafe(32)
            expires_at = now + timedelta(seconds=self.ttl_s)
            row = SessionModel(
                id=str(uuid.uuid4()),
                user_id=user.id,
                workspace_id=selected_workspace,
                token_hash=self.token_hash(token),
                csrf_hash=self.csrf_hash(csrf_token),
                expires_at=expires_at,
                issued_at=now,
                last_seen_at=now,
                authorization_version=user.authorization_version,
            )
            session.add(row)
            await session.flush()
            return token, self._claims(row, csrf_token=csrf_token)

    async def _select_workspace(
        self,
        session: AsyncSession,
        user_id: str,
        workspace_id: str | None,
    ) -> str:
        statement = select(WorkspaceMemberModel.workspace_id).where(
            WorkspaceMemberModel.user_id == user_id,
            WorkspaceMemberModel.status == "active",
        )
        if workspace_id:
            statement = statement.where(WorkspaceMemberModel.workspace_id == workspace_id)
        statement = statement.order_by(WorkspaceMemberModel.created_at).limit(1)
        selected = await session.scalar(statement)
        if selected is None:
            raise AuthenticationError("user has no active workspace")
        return str(selected)

    async def authenticate(
        self,
        factory: async_sessionmaker[AsyncSession],
        token: str | None,
        *,
        touch: bool = True,
    ) -> DatabaseSessionClaims:
        if not token:
            raise AuthenticationError("authentication cookie is missing")
        digest = self.token_hash(token)
        failure: str | None = None
        claims: DatabaseSessionClaims | None = None
        async with factory.begin() as session:
            row = await self._active_row(session, digest)
            if row is None:
                raise AuthenticationError("authentication cookie is invalid")
            now = _utc_now()
            if row.last_seen_at is not None and (
                now - row.last_seen_at
            ).total_seconds() > self.idle_timeout_s:
                row.revoked_at = now
                failure = "authentication cookie has expired"
            elif (
                row.authorization_version != row.user.authorization_version
                or row.user.status != "active"
                or row.user.deleted_at is not None
            ):
                row.revoked_at = now
                failure = "authentication cookie has expired"
            elif touch:
                row.last_seen_at = now
                # Limit sliding TTL: extend the absolute expiry up to a maximum of the original
                # session TTL (8 hours), not indefinitely. This prevents sessions from lasting forever
                # by sliding beyond the initial intended session lifetime.
                max_absolute_expiry = row.issued_at + timedelta(seconds=self.ttl_s)
                row.expires_at = min(now + timedelta(seconds=self.ttl_s), max_absolute_expiry)
            if failure is not None:
                await sandbox_lifecycle_service.release_auth_session_in_transaction(
                    session,
                    user_id=row.user_id,
                    auth_session_id=row.id,
                    reason="auth.session.expired",
                )
            if failure is None:
                claims = self._claims(row)
        if failure is not None:
            raise AuthenticationError(failure)
        assert claims is not None
        return claims

    async def _active_row(
        self, session: AsyncSession, token_hash: str
    ) -> SessionModel | None:
        return await session.scalar(
            select(SessionModel)
            .options(joinedload(SessionModel.user))
            .where(
                SessionModel.token_hash == token_hash,
                SessionModel.revoked_at.is_(None),
                SessionModel.expires_at > _utc_now(),
            )
        )

    async def rotate_csrf(
        self,
        factory: async_sessionmaker[AsyncSession],
        claims: DatabaseSessionClaims,
    ) -> str:
        async with factory.begin() as session:
            row = await self._active_row(session, claims.token_hash)
            if row is None or row.id != claims.session_id:
                raise AuthenticationError("authentication cookie is invalid")
            csrf_token = secrets.token_urlsafe(32)
            row.csrf_hash = self.csrf_hash(csrf_token)
            row.last_seen_at = _utc_now()
            return csrf_token

    async def revoke_token(
        self,
        factory: async_sessionmaker[AsyncSession],
        token_hash: str,
    ) -> None:
        async with factory.begin() as session:
            row = await session.scalar(
                select(SessionModel)
                .where(SessionModel.token_hash == token_hash)
                .with_for_update()
            )
            if row is not None and row.revoked_at is None:
                row.revoked_at = _utc_now()
                await sandbox_lifecycle_service.release_auth_session_in_transaction(
                    session,
                    user_id=row.user_id,
                    auth_session_id=row.id,
                    reason="auth.session.logged_out",
                )

    async def list_user_sessions(
        self,
        factory: async_sessionmaker[AsyncSession],
        user_id: str,
        *,
        current_session_id: str | None = None,
    ) -> list[dict[str, Any]]:
        async with factory() as session:
            rows = list(
                (
                    await session.scalars(
                        select(SessionModel)
                        .where(SessionModel.user_id == user_id)
                        .order_by(SessionModel.issued_at.desc())
                    )
                ).all()
            )
        return [
            {
                "session_id": row.id,
                "workspace_id": row.workspace_id,
                "issued_at": row.issued_at,
                "last_seen_at": row.last_seen_at,
                "expires_at": row.expires_at,
                "revoked_at": row.revoked_at,
                "current": row.id == current_session_id,
                "active": row.revoked_at is None and row.expires_at > _utc_now(),
            }
            for row in rows
        ]

    async def revoke_session_id(
        self,
        factory: async_sessionmaker[AsyncSession],
        *,
        user_id: str,
        session_id: str,
    ) -> str | None:
        """Revoke exactly one session owned by the authenticated user."""
        async with factory.begin() as session:
            row = await session.scalar(
                select(SessionModel)
                .where(SessionModel.id == session_id, SessionModel.user_id == user_id)
                .with_for_update()
            )
            if row is None:
                return None
            if row.revoked_at is None:
                row.revoked_at = _utc_now()
                await sandbox_lifecycle_service.release_auth_session_in_transaction(
                    session,
                    user_id=row.user_id,
                    auth_session_id=row.id,
                    reason="auth.session.revoked",
                )
            return row.token_hash

    async def issue_ws_ticket(
        self,
        factory: async_sessionmaker[AsyncSession],
        claims: DatabaseSessionClaims,
        *,
        origin: str,
        host: str | None = None,
        ttl_s: int = 60,
    ) -> str:
        self.require_same_origin(origin, host)
        ticket = secrets.token_urlsafe(32)
        async with factory.begin() as session:
            row = await self._active_row(session, claims.token_hash)
            if row is None or row.id != claims.session_id:
                raise AuthenticationError("authentication cookie is invalid")
            session.add(
                WsTicketModel(
                    id=str(uuid.uuid4()),
                    auth_session_id=row.id,
                    user_id=row.user_id,
                    workspace_id=row.workspace_id,
                    ticket_hash=self.token_hash(ticket),
                    origin=origin.rstrip("/").lower(),
                    expires_at=_utc_now() + timedelta(seconds=max(5, min(ttl_s, 300))),
                )
            )
        return ticket

    async def consume_ws_ticket(
        self,
        factory: async_sessionmaker[AsyncSession],
        ticket: str | None,
        *,
        origin: str | None,
        host: str | None = None,
    ) -> DatabaseSessionClaims:
        if not ticket or not origin:
            raise AuthenticationError("WebSocket ticket is missing")
        self.require_same_origin(origin, host)
        async with factory.begin() as session:
            now = _utc_now()
            row = await session.scalar(
                select(WsTicketModel)
                .where(
                    WsTicketModel.ticket_hash == self.token_hash(ticket),
                    WsTicketModel.used_at.is_(None),
                    WsTicketModel.expires_at > now,
                    WsTicketModel.origin == origin.rstrip("/").lower(),
                )
                .with_for_update()
            )
            if row is None:
                raise AuthenticationError("WebSocket ticket is invalid or expired")
            auth_session = await session.scalar(
                select(SessionModel)
                .options(joinedload(SessionModel.user))
                .where(SessionModel.id == row.auth_session_id)
                .with_for_update()
            )
            if auth_session is None or auth_session.revoked_at is not None:
                raise AuthenticationError("authentication session is revoked")
            if auth_session.expires_at <= now or auth_session.user.status != "active" or auth_session.user.deleted_at is not None:
                raise AuthenticationError("authentication session is expired")
            if auth_session.authorization_version != auth_session.user.authorization_version:
                raise AuthenticationError("authentication session is expired")
            row.used_at = now
            return self._claims(auth_session)

    async def authenticate_session_id(
        self,
        factory: async_sessionmaker[AsyncSession],
        session_id: str,
        *,
        touch: bool = False,
    ) -> DatabaseSessionClaims:
        async with factory.begin() as session:
            row = await session.scalar(
                select(SessionModel)
                .options(joinedload(SessionModel.user))
                .where(SessionModel.id == session_id)
            )
            now = _utc_now()
            if row is None or row.revoked_at is not None or row.expires_at <= now:
                raise AuthenticationError("authentication session is expired")
            if row.user.status != "active" or row.user.deleted_at is not None:
                raise AuthenticationError("authentication session is expired")
            if row.authorization_version != row.user.authorization_version:
                raise AuthenticationError("authentication session is expired")
            if touch:
                row.last_seen_at = now
            return self._claims(row)

    def require_csrf(
        self, claims: DatabaseSessionClaims, supplied: str | None
    ) -> None:
        if not supplied or not hmac.compare_digest(self.csrf_hash(supplied), claims.csrf_hash):
            raise CsrfRejectedError("CSRF token is missing or invalid")

    def require_same_origin(self, origin: str | None, host: str | None) -> None:
        if not origin:
            raise OriginRejectedError("Origin header is required")
        normalized = origin.rstrip("/").lower()
        if normalized in self.allowed_origins:
            return
        try:
            origin_host = urlsplit(origin).netloc.lower()
        except ValueError as error:
            raise OriginRejectedError("Origin header is invalid") from error
        if host and origin_host == host.lower():
            return
        raise OriginRejectedError("cross-origin access is not allowed")

    @staticmethod
    def _claims(row: SessionModel, *, csrf_token: str | None = None) -> DatabaseSessionClaims:
        return DatabaseSessionClaims(
            user_id=row.user_id,
            workspace_id=row.workspace_id,
            session_id=row.id,
            token_hash_value=row.token_hash,
            csrf_hash_value=row.csrf_hash,
            expires_at=row.expires_at,
            authorization_version=row.authorization_version,
            csrf_token=csrf_token,
        )
