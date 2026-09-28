"""Email address normalization used by registration, login and email delivery."""

from __future__ import annotations

import re

# Pragmatic RFC-5322-subset: local@domain with a dotted TLD of >=2 chars.
_EMAIL_RE = re.compile(r"^[A-Za-z0-9!#$%&'*+/=?^_`{|}~.-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}$")


class InvalidEmailError(ValueError):
    """Raised when a value cannot be represented as a valid email address."""


def normalize_email(value: str) -> str:
    """Return a canonical (case-folded, trimmed) email address.

    The local part is kept as-entered but case-folded for identity purposes;
    the domain is always lower-cased.  Raises :class:`InvalidEmailError` for
    blank or malformed input.
    """
    raw = str(value or "").strip()
    if not raw:
        raise InvalidEmailError("email is required")
    folded = raw.casefold()
    if len(folded) > 254 or not _EMAIL_RE.fullmatch(folded):
        raise InvalidEmailError("invalid email format")
    return folded
