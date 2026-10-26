"""Versioned product levels and documented model mappings; no per-user state.

This catalog describes parameters, not runtime availability. A Provider must
also be configured and have an implemented Adapter before offering its levels.
Sources (checked 2026-10-25):
https://api-docs.deepseek.com/guides/thinking_mode/
https://api-docs.deepseek.com/updates/
https://docs.bigmodel.cn/cn/guide/capabilities/thinking
"""

from __future__ import annotations

from enum import Enum
from types import MappingProxyType

from core.model_runtime.contracts import ModelRuntimeConfig, ReasoningEffort, ThinkingConfig


class ThinkingLevel(str, Enum):
    QUICK = "quick"
    LIGHT = "light"
    DEEP = "deep"
    EXTREME = "extreme"


DEFAULT_THINKING_LEVEL = ThinkingLevel.LIGHT
THINKING_MAPPING_VERSION = "thinking-mapping-v1"


class UnsupportedThinkingLevelError(ValueError):
    """No verified, distinct mapping exists for this model and product level."""


_LABELS = MappingProxyType({
    ThinkingLevel.QUICK: "快速",
    ThinkingLevel.LIGHT: "轻度",
    ThinkingLevel.DEEP: "深度",
    ThinkingLevel.EXTREME: "极致",
})
_EFFORTS = MappingProxyType({
    ThinkingLevel.QUICK: ReasoningEffort.NONE,
    ThinkingLevel.LIGHT: ReasoningEffort.LOW,
    ThinkingLevel.DEEP: ReasoningEffort.HIGH,
    ThinkingLevel.EXTREME: ReasoningEffort.MAX,
})
_MODEL_LEVELS = MappingProxyType({
    ("deepseek", "deepseek-v4-pro"): tuple(ThinkingLevel),
    ("deepseek", "deepseek-v4-flash"): tuple(ThinkingLevel),
    ("deepseek", "deepseek-flash"): tuple(ThinkingLevel),
    # GLM-5.3 rejects disabled thinking. GLM-5.2 maps low to high, so exposing
    # light and deep there would charge different prices for identical effort.
    ("glm", "glm-5.3"): (ThinkingLevel.LIGHT, ThinkingLevel.DEEP, ThinkingLevel.EXTREME),
    ("glm", "glm-5.2"): (ThinkingLevel.QUICK, ThinkingLevel.DEEP, ThinkingLevel.EXTREME),
})


def supported_thinking_levels(adapter: str, model_id: str) -> tuple[ThinkingLevel, ...]:
    """Fail closed for unverified models, aliases and generic adapters."""
    return _MODEL_LEVELS.get((adapter, model_id), ())


def resolve_thinking_config(
    adapter: str,
    model_id: str,
    level: ThinkingLevel | str,
) -> ThinkingConfig:
    """Resolve a fresh immutable value without mutating shared model presets."""
    selected = ThinkingLevel(level)
    if selected not in supported_thinking_levels(adapter, model_id):
        raise UnsupportedThinkingLevelError(
            f"Unsupported thinking level {selected.value!r} for {adapter}/{model_id}"
        )
    return ThinkingConfig(
        enabled=selected is not ThinkingLevel.QUICK,
        effort=_EFFORTS[selected],
    )


def public_thinking_options(adapter: str, model_id: str) -> list[dict[str, str]]:
    """Return only labels and level IDs; prices belong to the application policy.

    Callers must additionally check configured Adapter availability and intersect
    the ordinary Coordinator/Worker/fallback model capabilities for a profile.
    """
    return [
        {"id": level.value, "label": _LABELS[level]}
        for level in supported_thinking_levels(adapter, model_id)
    ]


def supported_profile_thinking_levels(
    config: ModelRuntimeConfig, profile_name: str,
) -> tuple[ThinkingLevel, ...]:
    """Intersect ordinary primary presets; incompatible fallbacks are excluded
    when the Turn snapshot is captured. Utility uses a fixed system policy.
    """
    profile = config.profile(profile_name)
    supported = set(ThinkingLevel)
    for preset_name in (profile.coordinator, profile.worker):
        preset = config.preset(preset_name)
        model = config.models[preset.model]
        adapter = config.providers[model.provider].adapter
        if preset.native_search.enabled:
            return ()
        supported.intersection_update(supported_thinking_levels(adapter, model.model_id))
        if not model.capabilities.thinking:
            supported.intersection_update((ThinkingLevel.QUICK,))
    return tuple(level for level in ThinkingLevel if level in supported)
