"""Generation rules for public account identity IDs."""

from __future__ import annotations

import secrets


_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def generate_public_identity_id() -> str:
    digit = secrets.choice("23456789")
    suffix = "".join(secrets.choice(_ALPHABET) for _ in range(13))
    return f"NV{digit}{suffix}"


def normalize_public_identity_id(value: str) -> str:
    return value.strip().upper()
