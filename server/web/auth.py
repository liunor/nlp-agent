"""Legacy same-origin authentication adapter used only by injected tests."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from urllib.parse import urlsplit

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, VerifyMismatchError
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from core.identity import AuthenticatedPrincipal


class AuthenticationError(PermissionError):
    pass


class OriginRejectedError(PermissionError):
    pass


class CsrfRejectedError(PermissionError):
    pass


class SessionClaims(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    user_id: str
    workspace_ids: frozenset[str] = Field(default_factory=lambda: frozenset({"default"}))
    roles: frozenset[str] = Field(default_factory=frozenset)
    csrf_token: str
    issued_at: int
    expires_at: int

    def principal(self) -> AuthenticatedPrincipal:
        return AuthenticatedPrincipal(
            user_id=self.user_id,
            workspace_ids=self.workspace_ids,
            roles=self.roles,
        )


@dataclass
class _StatefulSession:
    claims: SessionClaims
    last_seen_at: float


class _LoginRateLimiter:
    def __init__(self, max_attempts: int, window_s: int) -> None:
        self.max_attempts = max(1, int(max_attempts))
        self.window_s = max(1, int(window_s))
        self._failures: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allowed(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            failures = self._failures[key]
            while failures and now - failures[0] >= self.window_s:
                failures.popleft()
            return len(failures) < self.max_attempts

    def record_failure(self, key: str) -> None:
        with self._lock:
            self._failures[key].append(time.monotonic())

    def clear(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(raw: str) -> bytes:
    return base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))


class SameOriginSessionAuth:
    """Issues stateless HMAC-signed sessions; no credential is placed in a URL."""

    def __init__(
        self,
        *,
        secret: str = "",
        cookie_name: str = "nlp_session",
        ttl_s: int = 86_400,
        secure: bool = False,
        allowed_origins: list[str] | None = None,
        username: str = "",
        password_hash: str = "",
        roles: frozenset[str] | None = None,
        idle_timeout_s: int = 900,
        max_login_attempts: int = 5,
        rate_window_s: int = 300,
    ) -> None:
        self.ephemeral_secret = not bool(secret)
        self._secret = (secret.encode("utf-8") if secret else secrets.token_bytes(32))
        self.cookie_name = cookie_name
        self.ttl_s = min(max(int(ttl_s), 300), 604_800)
        self.secure = secure
        self.username = username
        self.password_hash = password_hash
        self.roles = roles or frozenset({"student", "teacher", "admin"})
        self.idle_timeout_s = min(max(int(idle_timeout_s), 60), self.ttl_s)
        self._password_hasher = PasswordHasher()
        self._username_rate_limiter = _LoginRateLimiter(max_login_attempts, rate_window_s)
        self._client_rate_limiter = _LoginRateLimiter(max_login_attempts, rate_window_s)
        self._sessions: dict[bytes, _StatefulSession] = {}
        self._sessions_lock = threading.Lock()
        self.allowed_origins = {
            item.rstrip("/").lower() for item in (allowed_origins or []) if item
        }

    @classmethod
    def from_config(
        cls, config: dict, *, include_credentials: bool = True
    ) -> "SameOriginSessionAuth":
        return cls(
            secret=str(config.get("auth_secret", "")),
            cookie_name=str(config.get("cookie_name", "nlp_session")),
            ttl_s=int(config.get("cookie_ttl_s", 86_400)),
            secure=bool(config.get("cookie_secure", False)),
            allowed_origins=list(config.get("allowed_origins", [])),
            username=str(config.get("auth_username", "")) if include_credentials else "",
            password_hash=str(config.get("auth_password_hash", "")) if include_credentials else "",
            roles=frozenset(
                item.strip()
                for item in str(
                    config.get("auth_roles", "student,teacher,admin")
                ).split(",")
                if item.strip()
            ),
            idle_timeout_s=int(config.get("auth_idle_timeout_s", 900)),
            max_login_attempts=int(config.get("auth_max_login_attempts", 5)),
            rate_window_s=int(config.get("auth_rate_window_s", 300)),
        )

    @property
    def credentials_configured(self) -> bool:
        return bool(self.username and self.password_hash)

    def login(
        self,
        username: str,
        password: str,
        *,
        client_key: str = "unknown",
        previous_token: str | None = None,
    ) -> tuple[str, SessionClaims]:
        if not self.credentials_configured:
            raise AuthenticationError("authentication credentials are not configured")
        username_key = username.casefold()
        if not (
            self._username_rate_limiter.allowed(username_key)
            and self._client_rate_limiter.allowed(client_key)
        ):
            raise AuthenticationError("too many login attempts")
        try:
            valid_password = self._password_hasher.verify(self.password_hash, password)
        except (VerifyMismatchError, VerificationError, ValueError):
            valid_password = False
        valid_username = hmac.compare_digest(
            username.encode("utf-8"), self.username.encode("utf-8")
        )
        if not valid_username or not valid_password:
            self._username_rate_limiter.record_failure(username_key)
            self._client_rate_limiter.record_failure(client_key)
            raise AuthenticationError("invalid credentials")
        self._username_rate_limiter.clear(username_key)
        self._client_rate_limiter.clear(client_key)
        self.revoke(previous_token)
        return self.issue(
            AuthenticatedPrincipal(
                user_id=self.username,
                workspace_ids=frozenset({"default"}),
                roles=self.roles,
            )
        )

    def issue_guest(self, *, previous_token: str | None = None) -> tuple[str, SessionClaims]:
        """Create a limited session for public, read-only guest capabilities."""
        self.revoke(previous_token)
        return self.issue(
            AuthenticatedPrincipal(
                user_id="guest",
                workspace_ids=frozenset(),
                roles=frozenset({"guest"}),
            )
        )

    def issue(
        self,
        principal: AuthenticatedPrincipal | None = None,
    ) -> tuple[str, SessionClaims]:
        now = int(time.time())
        principal = principal or AuthenticatedPrincipal(
            user_id="local",
            workspace_ids=frozenset({"default"}),
            roles=frozenset({"admin"}),
        )
        claims = SessionClaims(
            user_id=principal.user_id,
            workspace_ids=principal.workspace_ids,
            roles=principal.roles,
            csrf_token=secrets.token_urlsafe(32),
            issued_at=now,
            expires_at=now + self.ttl_s,
        )
        payload = _b64encode(
            json.dumps(
                claims.model_dump(mode="json"),
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        )
        signature = _b64encode(hmac.new(self._secret, payload.encode("ascii"), hashlib.sha256).digest())
        if not self.credentials_configured:
            return f"{payload}.{signature}", claims
        token = _b64encode(secrets.token_bytes(32))
        digest = hashlib.sha256(token.encode("ascii")).digest()
        with self._sessions_lock:
            self._sessions[digest] = _StatefulSession(
                claims=claims,
                last_seen_at=time.monotonic(),
            )
        return token, claims

    def token_fingerprint(self, token: str | None) -> bytes | None:
        """Return an opaque token identifier suitable for in-process lookup only."""
        if not token:
            return None
        return hashlib.sha256(token.encode("ascii")).digest()

    def authenticate(self, token: str | None, *, touch: bool = True) -> SessionClaims:
        if not token:
            raise AuthenticationError("authentication cookie is missing")
        if self.credentials_configured:
            digest = self.token_fingerprint(token)
            assert digest is not None
            with self._sessions_lock:
                session = self._sessions.get(digest)
                now = time.monotonic()
                if session is None:
                    raise AuthenticationError("authentication cookie is invalid")
                if (
                    session.claims.expires_at <= int(time.time())
                    or now - session.last_seen_at > self.idle_timeout_s
                ):
                    self._sessions.pop(digest, None)
                    raise AuthenticationError("authentication cookie has expired")
                if touch:
                    session.last_seen_at = now
                return session.claims
        try:
            payload, supplied_signature = token.split(".", 1)
            expected = _b64encode(
                hmac.new(self._secret, payload.encode("ascii"), hashlib.sha256).digest()
            )
            if not hmac.compare_digest(supplied_signature, expected):
                raise AuthenticationError("authentication cookie signature is invalid")
            claims = SessionClaims.model_validate_json(_b64decode(payload))
        except AuthenticationError:
            raise
        except (ValueError, UnicodeError, ValidationError) as error:
            raise AuthenticationError("authentication cookie is invalid") from error
        if claims.expires_at <= int(time.time()):
            raise AuthenticationError("authentication cookie has expired")
        return claims

    def revoke(self, token: str | None) -> None:
        if not token or not self.credentials_configured:
            return
        digest = self.token_fingerprint(token)
        assert digest is not None
        with self._sessions_lock:
            self._sessions.pop(digest, None)

    def require_csrf(self, claims: SessionClaims, supplied: str | None) -> None:
        if not supplied or not hmac.compare_digest(claims.csrf_token, supplied):
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
