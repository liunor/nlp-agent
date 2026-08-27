"""Short-lived, session-bound browser capability for an assigned runtime."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class SandboxTicketClaims:
    user_id: str
    auth_session_id: str
    lease_id: str
    runtime_id: str
    generation: int
    nonce: str | None

    def without_nonce(self) -> "SandboxTicketClaims":
        return SandboxTicketClaims(
            self.user_id, self.auth_session_id, self.lease_id, self.runtime_id, self.generation, None
        )


class SandboxTicketSigner:
    def __init__(self, secret: str, *, ttl_seconds: int = 300) -> None:
        if not secret:
            raise ValueError("sandbox ticket signing secret is required")
        self._secret = secret.encode("utf-8")
        self._ttl_seconds = ttl_seconds

    def issue(self, claims: SandboxTicketClaims) -> str:
        payload = json.dumps(
            {
                "u": claims.user_id, "s": claims.auth_session_id, "l": claims.lease_id,
                "r": claims.runtime_id, "g": claims.generation, "n": claims.nonce,
                "e": int(time.time()) + self._ttl_seconds,
            }, separators=(",", ":"), sort_keys=True,
        ).encode("utf-8")
        encoded = base64.urlsafe_b64encode(payload).rstrip(b"=")
        signature = hmac.new(self._secret, encoded, hashlib.sha256).digest()
        return f"{encoded.decode('ascii')}.{base64.urlsafe_b64encode(signature).rstrip(b'=').decode('ascii')}"

    def verify(self, token: str, *, user_id: str, auth_session_id: str) -> SandboxTicketClaims:
        try:
            encoded, supplied_signature = token.split(".", 1)
            expected = hmac.new(self._secret, encoded.encode("ascii"), hashlib.sha256).digest()
            signature = base64.urlsafe_b64decode(supplied_signature + "=" * (-len(supplied_signature) % 4))
            payload = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
        except (ValueError, UnicodeError, json.JSONDecodeError) as error:
            raise PermissionError("sandbox ticket is malformed") from error
        if not hmac.compare_digest(expected, signature):
            raise PermissionError("sandbox ticket signature is invalid")
        if payload.get("u") != user_id or payload.get("s") != auth_session_id:
            raise PermissionError("sandbox ticket belongs to another authenticated session")
        if not isinstance(payload.get("e"), int) or payload["e"] < int(time.time()):
            raise PermissionError("sandbox ticket has expired")
        return SandboxTicketClaims(
            user_id=payload["u"], auth_session_id=payload["s"], lease_id=payload["l"],
            runtime_id=payload["r"], generation=payload["g"], nonce=payload.get("n"),
        )
