"""Canonical phone-number identities used by registration and login."""

from __future__ import annotations

import re


def normalize_phone_number(value: str) -> str:
    """Return an E.164-like canonical phone key, rejecting malformed input."""
    compact = re.sub(r"[\s().-]", "", value.strip())
    if compact.startswith("00"):
        compact = "+" + compact[2:]
    digits = compact[1:] if compact.startswith("+") else compact
    if not digits.isdigit():
        raise ValueError("phone number must contain digits only")
    if len(digits) == 11 and digits.startswith("1"):
        digits = "86" + digits
    if not 7 <= len(digits) <= 15:
        raise ValueError("phone number must contain between 7 and 15 digits")
    return "+" + digits
