"""Pure, idempotent migration of one user's persisted settings."""

from typing import Any

from core.model_runtime.thinking import DEFAULT_THINKING_LEVEL, ThinkingLevel


def migrate_thinking_settings(settings: dict[str, Any]) -> dict[str, Any]:
    migrated = dict(settings)
    if migrated.get("thinking_level") is None:
        migrated["thinking_level"] = (
            ThinkingLevel.QUICK.value
            if migrated.get("show_reasoning") is False
            else DEFAULT_THINKING_LEVEL.value
        )
    else:
        migrated["thinking_level"] = ThinkingLevel(migrated["thinking_level"]).value
    return migrated
