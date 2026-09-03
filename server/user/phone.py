"""Phone number normalization used by registration, login and SMS delivery."""

from __future__ import annotations

import re


class InvalidPhoneNumberError(ValueError):
    """Raised when a phone number cannot be represented as E.164."""


def normalize_phone_number(value: str) -> str:
    """Return a canonical E.164 phone number.

    Chinese domestic numbers are accepted as ``1XXXXXXXXXX`` and receive the
    ``+86`` country code. International input may use ``+`` or ``00`` and may
    contain spaces, hyphens or parentheses. The stored result always contains
    one leading ``+`` and at most 15 digits.
    """
    raw = str(value or "").strip()
    if not raw:
        raise InvalidPhoneNumberError("phone number is required")
    if not re.fullmatch(r"[+0-9][0-9 +()\-\.]*", raw):
        raise InvalidPhoneNumberError("invalid phone number format")
    compact = re.sub(r"[\s().-]", "", raw)
    if compact.startswith("00"):
        compact = "+" + compact[2:]
    if compact.startswith("+"):
        digits = compact[1:]
    else:
        digits = compact
        if len(digits) == 11 and digits.startswith("1"):
            digits = "86" + digits
    if not digits.isdigit() or not (7 <= len(digits) <= 15):
        raise InvalidPhoneNumberError("phone number must contain 7-15 digits")
    return "+" + digits
