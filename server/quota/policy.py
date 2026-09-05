"""Deterministic effective-policy selection for multi-role principals."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime

from server.quota.contracts import PolicyBinding
from server.quota.errors import QuotaDomainError, QuotaErrorCode


_PRECEDENCE = {
    "default": 0,
    "role": 1,
    "classroom": 2,
    "workspace": 2,
    "user": 3,
}


def resolve_effective_policy(
    bindings: Iterable[PolicyBinding],
    *,
    user_id: str,
    workspace_id: str | None,
    role_codes: Sequence[str],
    classroom_ids: Sequence[str] = (),
    at: datetime,
) -> PolicyBinding:
    """Return exactly one policy; never sum limits from multiple roles."""
    eligible: list[PolicyBinding] = []
    role_set = set(role_codes)
    classroom_set = set(classroom_ids)
    for binding in bindings:
        if not binding.is_effective_at(at):
            continue
        if binding.subject_type == "default" and binding.subject_id == "*":
            eligible.append(binding)
        elif binding.subject_type == "user" and binding.subject_id == user_id:
            eligible.append(binding)
        elif (
            binding.subject_type == "workspace"
            and workspace_id is not None
            and binding.subject_id == workspace_id
        ):
            eligible.append(binding)
        elif binding.subject_type == "role" and binding.subject_id in role_set:
            eligible.append(binding)
        elif (
            binding.subject_type == "classroom"
            and binding.subject_id in classroom_set
        ):
            eligible.append(binding)

    if not eligible:
        raise QuotaDomainError(
            QuotaErrorCode.POLICY_NOT_FOUND,
            "No effective quota policy exists for this principal",
        )

    highest_precedence = max(_PRECEDENCE[b.subject_type] for b in eligible)
    candidates = [
        binding for binding in eligible if _PRECEDENCE[binding.subject_type] == highest_precedence
    ]
    highest_priority = max(binding.priority for binding in candidates)
    candidates = [binding for binding in candidates if binding.priority == highest_priority]
    if len(candidates) != 1:
        raise QuotaDomainError(
            QuotaErrorCode.POLICY_AMBIGUOUS,
            "Multiple effective quota policies have the same precedence and priority",
        )
    return candidates[0]
