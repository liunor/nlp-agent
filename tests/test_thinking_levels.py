"""Product thinking rules, without user state or live Provider requests."""

import pytest


def test_default_level_resolves_to_light_thinking():
    from core.model_runtime.thinking import (
        DEFAULT_THINKING_LEVEL,
        resolve_thinking_config,
    )

    resolved = resolve_thinking_config(
        "deepseek", "deepseek-v4-pro", DEFAULT_THINKING_LEVEL,
    )

    assert resolved.model_dump(mode="json") == {"enabled": True, "effort": "low"}


@pytest.mark.parametrize("model_id", ["deepseek-v4-pro", "deepseek-v4-flash", "deepseek-flash"])
@pytest.mark.parametrize(
    ("level", "enabled", "effort"),
    [("quick", False, "none"), ("light", True, "low"),
     ("deep", True, "high"), ("extreme", True, "max")],
)
def test_deepseek_resolves_each_product_level(model_id, level, enabled, effort):
    from core.model_runtime.thinking import resolve_thinking_config

    assert resolve_thinking_config("deepseek", model_id, level).model_dump(mode="json") == {
        "enabled": enabled, "effort": effort,
    }


@pytest.mark.parametrize(
    ("model_id", "level", "enabled", "effort"),
    [("glm-5.3", "light", True, "low"),
     ("glm-5.3", "deep", True, "high"),
     ("glm-5.3", "extreme", True, "max"),
     ("glm-5.2", "quick", False, "none"),
     ("glm-5.2", "deep", True, "high"),
     ("glm-5.2", "extreme", True, "max")],
)
def test_glm_resolves_only_documented_distinct_levels(model_id, level, enabled, effort):
    from core.model_runtime.thinking import resolve_thinking_config

    assert resolve_thinking_config("glm", model_id, level).model_dump(mode="json") == {
        "enabled": enabled, "effort": effort,
    }


@pytest.mark.parametrize(
    ("adapter", "model_id", "level"),
    [("glm", "glm-5.3", "quick"), ("glm", "glm-5.2", "light"),
     ("glm", "glm-4.7", "extreme"), ("glm", "glm-unknown", "quick"),
     ("openai_compatible", "glm-5.3", "deep"),
     ("deepseek", "deepseek-v3", "light"),
     ("deepseek", "deepseek-v4-pro-proxy", "light"),
     ("qwen", "qwen3.8-max", "extreme")],
)
def test_unverified_models_and_collapsed_or_forbidden_levels_are_rejected(adapter, model_id, level):
    from core.model_runtime.thinking import UnsupportedThinkingLevelError, resolve_thinking_config

    with pytest.raises(UnsupportedThinkingLevelError):
        resolve_thinking_config(adapter, model_id, level)


@pytest.mark.parametrize("level", ["medium", "high", "max", "none", "", None, True])
def test_invalid_product_levels_cannot_be_silently_defaulted(level):
    from core.model_runtime.thinking import resolve_thinking_config

    with pytest.raises(ValueError):
        resolve_thinking_config("deepseek", "deepseek-v4-pro", level)


@pytest.mark.parametrize(
    ("adapter", "model_id", "expected"),
    [("deepseek", "deepseek-v4-pro", ["quick", "light", "deep", "extreme"]),
     ("glm", "glm-5.3", ["light", "deep", "extreme"]),
     ("glm", "glm-5.2", ["quick", "deep", "extreme"]),
     ("glm", "glm-unknown", []), ("unknown", "deepseek-v4-pro", [])],
)
def test_public_options_filter_unsupported_levels_and_never_expose_billing(adapter, model_id, expected):
    from core.model_runtime.thinking import public_thinking_options

    options = public_thinking_options(adapter, model_id)

    assert [option["id"] for option in options] == expected
    labels = {"quick": "快速", "light": "轻度", "deep": "深度", "extreme": "极致"}
    assert options == [{"id": level, "label": labels[level]} for level in expected]


def test_resolving_another_level_does_not_mutate_an_existing_users_config():
    from core.model_runtime.thinking import public_thinking_options, resolve_thinking_config

    first = resolve_thinking_config("deepseek", "deepseek-v4-pro", "light")
    resolve_thinking_config("deepseek", "deepseek-v4-pro", "extreme")
    options = public_thinking_options("deepseek", "deepseek-v4-pro")
    options[0]["label"] = "changed by client"

    assert first.model_dump(mode="json") == {"enabled": True, "effort": "low"}
    assert public_thinking_options("deepseek", "deepseek-v4-pro")[0]["label"] == "快速"


@pytest.mark.parametrize(("level", "credits"), [("quick", 1), ("light", 2), ("deep", 4), ("extreme", 8)])
def test_fixed_turn_charge_is_server_only_and_uses_the_selected_level(level, credits):
    from server.application.thinking_policy import fixed_turn_thinking_charge

    charge = fixed_turn_thinking_charge(level)

    assert charge.level.value == level
    assert charge.credits == credits
    assert charge.billing_mode == "fixed_turn"
    assert charge.policy_version == "thinking-fixed-turn-v1"


def test_invalid_level_cannot_receive_a_free_or_default_charge():
    from server.application.thinking_policy import fixed_turn_thinking_charge

    with pytest.raises(ValueError):
        fixed_turn_thinking_charge("invalid")
