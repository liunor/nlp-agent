from __future__ import annotations

import re

import pytest

from server.user.identity import (
    generate_public_identity_id,
    normalize_public_identity_id,
    validate_public_identity_id,
)


def test_generated_public_identity_ids_are_copy_safe_and_unique() -> None:
    generated = {generate_public_identity_id() for _ in range(2_000)}

    assert len(generated) == 2_000
    assert all(re.fullmatch(r"NV[0-9][A-HJ-NP-Z2-9]{13}", value) for value in generated)


def test_public_identity_lookup_is_exact_but_case_and_space_tolerant() -> None:
    assert normalize_public_identity_id("  nv7abc234def567  ") == "NV7ABC234DEF567"


def test_validate_public_identity_id_normalizes_and_rejects_invalid_values() -> None:
    assert validate_public_identity_id("  teacher-01  ") == "TEACHER-01"
    assert validate_public_identity_id("t_zhang_01") == "T_ZHANG_01"

    with pytest.raises(ValueError):
        validate_public_identity_id("ab")

    with pytest.raises(ValueError):
        validate_public_identity_id("-badstart")

    with pytest.raises(ValueError):
        validate_public_identity_id("TOO_LONG_IDENTITY_123")

    with pytest.raises(ValueError):
        validate_public_identity_id("教师01")
