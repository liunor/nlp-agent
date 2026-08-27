"""Safe delivery contract for untrusted sandbox artifacts on a separate origin."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import quote, urlsplit


class ArtifactAccessSigner:
    """Issue short-lived, owner-bound access tickets for the artifact origin."""

    def __init__(self, secret: str, *, lifetime: timedelta = timedelta(minutes=5)) -> None:
        self._secret = secret.encode("utf-8")
        self._lifetime = lifetime

    def issue(self, *, artifact_id: str, owner_user_id: str) -> str:
        payload = json.dumps(
            {
                "a": artifact_id,
                "u": owner_user_id,
                "e": int(time.time() + self._lifetime.total_seconds()),
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        encoded = base64.urlsafe_b64encode(payload).rstrip(b"=")
        signature = hmac.new(self._secret, encoded, hashlib.sha256).hexdigest()
        return f"{encoded.decode('ascii')}.{signature}"

    def verify(self, ticket: str, *, artifact_id: str, owner_user_id: str) -> None:
        try:
            encoded, signature = ticket.split(".", maxsplit=1)
            expected = hmac.new(self._secret, encoded.encode("ascii"), hashlib.sha256).hexdigest()
            padding = "=" * (-len(encoded) % 4)
            payload = json.loads(base64.urlsafe_b64decode(encoded + padding))
        except (binascii.Error, UnicodeError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise PermissionError("invalid sandbox artifact ticket") from error
        if not isinstance(payload, dict):
            raise PermissionError("invalid sandbox artifact ticket")
        if not hmac.compare_digest(signature, expected):
            raise PermissionError("invalid sandbox artifact ticket")
        if payload.get("a") != artifact_id or payload.get("u") != owner_user_id:
            raise PermissionError("sandbox artifact ticket does not match the requested artifact")
        if not isinstance(payload.get("e"), int) or payload["e"] < time.time():
            raise PermissionError("sandbox artifact ticket expired")


def artifact_expired(expires_at: datetime | None, *, now: datetime | None = None) -> bool:
    if expires_at is None:
        return False
    current = now or datetime.now(UTC)
    expiry = expires_at.replace(tzinfo=UTC) if expires_at.tzinfo is None else expires_at
    return expiry <= current


def artifact_access_url(origin: str, *, artifact_id: str, ticket: str) -> str:
    """Build a ticketed URL on the dedicated artifact origin."""
    parsed = urlsplit(origin)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password or parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValueError("sandbox artifact origin must be an absolute HTTPS URL")
    normalized = f"https://{parsed.netloc}"
    return f"{normalized}/api/v1/sandbox/artifacts/{quote(artifact_id, safe='')}/content?ticket={quote(ticket, safe='')}"


def validate_artifact_origin(origin: str, *, application_origin: str) -> str:
    """Require a distinct HTTPS artifact origin in public deployments."""
    parsed_origin = urlsplit(origin)
    parsed_application = urlsplit(application_origin)
    if parsed_origin.scheme != "https" or not parsed_origin.netloc or parsed_origin.username or parsed_origin.password:
        raise ValueError("sandbox artifact origin must be an absolute HTTPS URL")
    if parsed_origin.path not in {"", "/"} or parsed_origin.query or parsed_origin.fragment:
        raise ValueError("sandbox artifact origin must not include a path, query, or fragment")
    artifact_host = (parsed_origin.hostname or "").rstrip(".").lower()
    application_host = (parsed_application.hostname or "").rstrip(".").lower()
    if artifact_host and artifact_host == application_host:
        raise ValueError("sandbox artifact origin must differ from the application origin")
    return f"{parsed_origin.scheme}://{parsed_origin.netloc}"


def artifact_request_origin_matches(request_origin: str, *, configured_origin: str) -> bool:
    """Accept content delivery only on the configured isolated HTTPS origin."""
    actual = urlsplit(request_origin)
    configured = urlsplit(configured_origin)
    if actual.scheme != "https" or configured.scheme != "https":
        return False
    return actual.netloc.rstrip(".").lower() == configured.netloc.rstrip(".").lower()


def resolve_artifact_path(store_root: Path, locator: str) -> Path:
    """Resolve a database locator without allowing traversal or symlink escape."""
    relative = Path(locator)
    if relative.is_absolute() or ".." in relative.parts:
        raise PermissionError("sandbox artifact locator escapes the configured store")
    root = store_root.resolve(strict=True)
    candidate = root / relative
    # Reject every symlink component before the final descriptor open.  The
    # delivery layer also uses O_NOFOLLOW for the final component; together
    # these checks prevent both ordinary traversal and symlink swap escapes.
    current = root
    for component in relative.parts:
        current = current / component
        if current.is_symlink():
            raise PermissionError("sandbox artifact locator contains a symlink")
    candidate = candidate.resolve(strict=True)
    try:
        candidate.relative_to(root)
    except ValueError as error:
        raise PermissionError("sandbox artifact locator escapes the configured store") from error
    if not candidate.is_file():
        raise PermissionError("sandbox artifact locator is not a regular file")
    return candidate

def artifact_security_headers(mime_type: str, *, frame_ancestors: str = "'none'") -> dict[str, str]:
    """Headers required when artifact origin serves HTML/SVG generated by code."""
    csp = f"default-src 'none'; script-src 'none'; connect-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'; img-src data: blob:; style-src 'unsafe-inline'; frame-ancestors {frame_ancestors}; sandbox"
    if mime_type not in {"text/html", "image/svg+xml"}:
        csp = f"default-src 'none'; frame-ancestors {frame_ancestors}; sandbox"
    return {
        "Content-Security-Policy": csp,
        "Cross-Origin-Resource-Policy": "cross-origin",
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
    }
