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
import math
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
from server.user.phone import normalize_phone_number
from server.sandbox.service import sandbox_lifecycle_service
from server.web.auth import (
    AuthenticationError,
    AuthorizationChangedError,
    CsrfRejectedError,
    LoginRateLimitError,
    OriginRejectedError,
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


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
        return self.retry_after(key) == 0

    def retry_after(self, key: str) -> int:
        now = monotonic()
        failures = self._failures[key]
        while failures and now - failures[0] >= self.window_s:
            failures.popleft()
        if len(failures) < self.max_attempts:
            return 0
        # Concurrent attempts can overshoot the threshold. Wait until enough
        # failures, rather than just the oldest one, leave the sliding window.
        blocked_until = failures[len(failures) - self.max_attempts] + self.window_s
        return max(1, math.ceil(blocked_until - now))

    def record_failure(self, key: str) -> None:
        self._failures[key].append(monotonic())

    def clear(self, key: str) -> None:
        self._failures.pop(key, None)


# Keep increments and TTL assignment atomic. An interrupted process must not
# leave a permanent lockout counter behind. Status also repairs old counters
# that lack a TTL, without extending the window on every blocked request.
_LOGIN_RATE_STATUS = """
local retry = 0
for i = 1, 2 do
    local count = tonumber(redis.call('GET', KEYS[i]) or '0')
    if count >= tonumber(ARGV[i]) then
        local ttl = redis.call('TTL', KEYS[i])
        if ttl < 0 then
            ttl = tonumber(ARGV[i + 2])
            redis.call('EXPIRE', KEYS[i], ttl)
        end
        retry = math.max(retry, math.max(1, ttl))
    end
end
return retry
"""

_LOGIN_RATE_FAILURE = """
for i = 1, 2 do
    local count = redis.call('INCR', KEYS[i])
    if count == 1 or redis.call('TTL', KEYS[i]) < 0 then
        redis.call('EXPIRE', KEYS[i], tonumber(ARGV[i]))
    end
end
return 1
"""


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
        max_ip_login_attempts: int = 100,
        ip_rate_window_s: int | None = None,
    ) -> None:
        self.cookie_name = cookie_name
        self.ttl_s = min(max(int(ttl_s), 300), 604_800)
        self.secure = secure
        self.idle_timeout_s = min(max(int(idle_timeout_s), 60), self.ttl_s)
        self.allowed_origins = {
            item.rstrip("/").lower() for item in (allowed_origins or []) if item
        }
        self._hasher = PasswordHasherSingleton.get()
        # Password hashing uses substantial CPU/memory. Keep those jobs outside
        # DB transactions and bound them independently of database connections.
        self._password_verifications = asyncio.Semaphore(2)
        self._username_rate_limiter = _RateLimiter(max_login_attempts, rate_window_s)
        self._client_rate_limiter = _RateLimiter(
            max_ip_login_attempts,
            rate_window_s if ip_rate_window_s is None else ip_rate_window_s,
        )
        self._redis = None
        self._rate_prefix = "nlp-agent:auth-login:"

    def set_redis_client(self, redis_client: Any | None) -> None:
        """Use the shared Redis limiter when the deployment provides Redis."""
        self._redis = redis_client

    async def _rate_allowed(self, username: str, client_key: str) -> bool:
        return await self._rate_retry_after(username, client_key) == 0

    def _rate_keys(self, username: str, client_key: str) -> tuple[str, str]:
        return f"{self._rate_prefix}user:{username}", f"{self._rate_prefix}ip:{client_key}"

    async def _rate_retry_after(self, username: str, client_key: str) -> int:
        if self._redis is None:
            return max(self._username_rate_limiter.retry_after(username),
                       self._client_rate_limiter.retry_after(client_key))
        return int(await self._redis.eval(
            _LOGIN_RATE_STATUS, 2, *self._rate_keys(username, client_key),
            self._username_rate_limiter.max_attempts,
            self._client_rate_limiter.max_attempts,
            self._username_rate_limiter.window_s,
            self._client_rate_limiter.window_s,
        ))

    async def _rate_failure(self, username: str, client_key: str) -> None:
        if self._redis is None:
            self._username_rate_limiter.record_failure(username)
            self._client_rate_limiter.record_failure(client_key)
            return
        await self._redis.eval(
            _LOGIN_RATE_FAILURE, 2, *self._rate_keys(username, client_key),
            self._username_rate_limiter.window_s,
            self._client_rate_limiter.window_s,
        )

    async def _rate_clear(self, username: str, client_key: str) -> None:
        if self._redis is None:
            self._username_rate_limiter.clear(username)
            return
        # One valid account must not reset failures belonging to every other
        # account on a shared IP (or allow an attacker to reset the IP budget).
        await self._redis.delete(self._rate_keys(username, client_key)[0])

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
            max_ip_login_attempts=auth_env_int("NLP_AGENT_AUTH_MAX_IP_LOGIN_ATTEMPTS", 100),
            ip_rate_window_s=auth_env_int(
                "NLP_AGENT_AUTH_IP_RATE_WINDOW_S",
                auth_env_int("NLP_AGENT_AUTH_RATE_WINDOW_S", 300),
            ),
        )

    @staticmethod
    def token_hash(token: str) -> str:
        return hashlib.sha256(token.encode("ascii")).hexdigest()

    @staticmethod
    def csrf_hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    @staticmethod
    def csrf_token_for_session(token: str) -> str:
        """Derive a stable, domain-separated CSRF token for one browser session."""

        return hashlib.sha256(f"nlp-agent:csrf:{token}".encode("ascii")).hexdigest()

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

    async def _verify_login_password(self, password_hash: str, password: str) -> bool:
        await self._password_verifications.acquire()
        try:
            task = asyncio.create_task(asyncio.to_thread(
                self._verify_password, password_hash, password, self._hasher
            ))
        except BaseException:
            self._password_verifications.release()
            raise

        def finished(result: asyncio.Task[bool]) -> None:
            # Cancelling an HTTP request cannot cancel a running hash thread.
            # Hold its slot until the actual job finishes, including on errors.
            self._password_verifications.release()
            if not result.cancelled():
                result.exception()

        task.add_done_callback(finished)
        return await asyncio.shield(task)

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
        retry_after_s = await self._rate_retry_after(normalized, client_key)
        if retry_after_s:
            raise LoginRateLimitError(retry_after_s)

        # Login accepts usernames, normalized email, and canonical phone
        # identity; manually provisioned accounts keep username login.
        identity_criteria = (
            (UserModel.username_lower == normalized)
            | (UserModel.email_normalized == normalized)
        )
        try:
            phone_identity = normalize_phone_number(username)
        except ValueError:
            phone_identity = None
        if phone_identity is not None:
            identity_criteria = identity_criteria | (
                UserModel.phone_number_normalized == phone_identity
            )
        async with factory.begin() as session:
            candidate = await session.scalar(
                select(UserModel).where(identity_criteria, UserModel.deleted_at.is_(None))
            )
            snapshot = ((candidate.id, candidate.password_hash)
                        if candidate is not None and candidate.status == "active" else None)

        # No DB connection or row lock is held while hashing or waiting for CPU.
        if snapshot is None or not await self._verify_login_password(snapshot[1], password):
            await self._rate_failure(normalized, client_key)
            raise AuthenticationError("invalid credentials")

        async with factory.begin() as session:
            user = await session.scalar(
                select(UserModel)
                .where(
                    UserModel.id == snapshot[0],
                    identity_criteria,
                    UserModel.deleted_at.is_(None),
                )
                .with_for_update()
            )
            valid = (
                user is not None
                and user.status == "active"
                and hmac.compare_digest(user.password_hash, snapshot[1])
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
            csrf_token = self.csrf_token_for_session(token)
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
        async with factory.begin() as session:
            # Load the row even when it is already expired or revoked so the
            # client receives the stable reason (session expiry versus an
            # authorization change) instead of a generic invalid-cookie code.
            row = await session.scalar(
                select(SessionModel)
                .options(joinedload(SessionModel.user))
                .where(SessionModel.token_hash == digest)
            )
            claims, failure = await self._validate_session_row(
                session, row, touch=touch, credential="cookie"
            )
        if failure is not None:
            raise failure
        assert claims is not None
        return claims

    async def _validate_session_row(
        self, session: AsyncSession, row: SessionModel | None, *, touch: bool, credential: str
    ) -> tuple[DatabaseSessionClaims | None, AuthenticationError | None]:
        """Apply the same expiry policy; callers commit cleanup before raising."""
        if row is None:
            message = "authentication cookie is invalid" if credential == "cookie" else "authentication session is expired"
            return None, AuthenticationError(message)
        failure: AuthenticationError | None = None
        now = _utc_now()
        prefix = f"authentication {credential}"
        if row.expires_at <= now:
            failure = AuthenticationError(f"{prefix} has expired")
        elif row.revoked_at is not None:
            if row.authorization_version != row.user.authorization_version:
                failure = AuthorizationChangedError("authorization changed")
            else:
                failure = AuthenticationError(f"{prefix} is invalid" if credential == "cookie" else f"{prefix} is revoked")
        elif row.last_seen_at is not None and (
            now - row.last_seen_at
        ).total_seconds() > self.idle_timeout_s:
            row.revoked_at = now
            failure = AuthenticationError(f"{prefix} has expired")
        elif row.authorization_version != row.user.authorization_version:
            row.revoked_at = now
            failure = AuthorizationChangedError("authorization changed")
        elif row.user.status != "active" or row.user.deleted_at is not None:
            row.revoked_at = now
            failure = AuthenticationError(f"{prefix} has expired")
        elif touch:
            row.last_seen_at = now
            if credential == "cookie":
                # Preserve the HTTP sliding cap; WebSocket activity never extends expiry.
                max_absolute_expiry = row.issued_at + timedelta(seconds=self.ttl_s)
                row.expires_at = min(now + timedelta(seconds=self.ttl_s), max_absolute_expiry)
        if failure is not None:
            await sandbox_lifecycle_service.release_auth_session_in_transaction(
                session,
                user_id=row.user_id,
                auth_session_id=row.id,
                reason="auth.session.expired",
            )
            return None, failure
        return self._claims(row), None

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

    async def restore_csrf(
        self,
        factory: async_sessionmaker[AsyncSession],
        claims: DatabaseSessionClaims,
        session_token: str | None,
    ) -> str:
        if not session_token or not hmac.compare_digest(
            self.token_hash(session_token), claims.token_hash
        ):
            raise AuthenticationError("authentication cookie is invalid")
        csrf_token = self.csrf_token_for_session(session_token)
        csrf_hash = self.csrf_hash(csrf_token)
        async with factory.begin() as session:
            row = await self._active_row(session, claims.token_hash)
            if row is None or row.id != claims.session_id:
                raise AuthenticationError("authentication cookie is invalid")
            # Upgrade sessions issued before stable per-session CSRF tokens;
            # repeated restores and other tabs must not invalidate each other.
            if not hmac.compare_digest(row.csrf_hash, csrf_hash):
                row.csrf_hash = csrf_hash
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
            if auth_session is None:
                raise AuthenticationError("authentication session is revoked")
            if auth_session.revoked_at is not None:
                if auth_session.authorization_version != auth_session.user.authorization_version:
                    raise AuthorizationChangedError("authorization changed")
                raise AuthenticationError("authentication session is revoked")
            if auth_session.expires_at <= now or auth_session.user.status != "active" or auth_session.user.deleted_at is not None:
                raise AuthenticationError("authentication session is expired")
            if auth_session.authorization_version != auth_session.user.authorization_version:
                raise AuthorizationChangedError("authorization changed")
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
            claims, failure = await self._validate_session_row(
                session, row, touch=touch, credential="session"
            )
        if failure is not None:
            raise failure
        assert claims is not None
        return claims

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
