"""Generation rules for public account identity IDs."""

from __future__ import annotations

import re
import secrets


_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_IDENTITY_ID_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9_-]{2,15}$")


def generate_public_identity_id() -> str:
    digit = secrets.choice("23456789")
    suffix = "".join(secrets.choice(_ALPHABET) for _ in range(13))
    return f"NV{digit}{suffix}"


def normalize_public_identity_id(value: str) -> str:
    return value.strip().upper()


def validate_public_identity_id(value: str) -> str:
    normalized = normalize_public_identity_id(value)
    if not _IDENTITY_ID_PATTERN.fullmatch(normalized):
        raise ValueError("身份 ID 需为 3~16 位字母、数字、下划线或连字符，且以字母或数字开头")
    return normalized
