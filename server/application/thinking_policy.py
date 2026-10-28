"""Internal fixed-Turn tariff; independent of Provider Token-cost accounting.

Amounts here are whole product credits, not ledger microcredits. Conversion
belongs to the quota integration and must use that ledger's established unit.
This module neither holds balances nor performs settlement or user selection.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

from core.model_runtime.thinking import ThinkingLevel


THINKING_CHARGE_POLICY_VERSION = "thinking-fixed-turn-v1"
_CREDITS = MappingProxyType({
    ThinkingLevel.QUICK: 1,
    ThinkingLevel.LIGHT: 2,
    ThinkingLevel.DEEP: 4,
    ThinkingLevel.EXTREME: 8,
})


@dataclass(frozen=True)
class FixedTurnThinkingCharge:
    level: ThinkingLevel
    credits: int
    policy_version: str
    billing_mode: Literal["fixed_turn"] = "fixed_turn"


def fixed_turn_thinking_charge(level: ThinkingLevel | str) -> FixedTurnThinkingCharge:
    """Freeze the internal tariff for a validated level; never default invalid IDs.

    Admission must validate the configured profile's supported levels before
    requesting a charge. Calling this function does not deduct any credits.
    """
    selected = ThinkingLevel(level)
    return FixedTurnThinkingCharge(
        level=selected,
        credits=_CREDITS[selected],
        policy_version=THINKING_CHARGE_POLICY_VERSION,
    )
