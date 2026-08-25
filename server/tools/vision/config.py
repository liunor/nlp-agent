"""Lazy access to the ``tools.vision`` runtime configuration block."""

from __future__ import annotations

from core.tool_config import VisionToolsConfig, load_agent_runtime_config


def get_vision_config() -> VisionToolsConfig:
    """Return the strict runtime vision config added by the integration layer."""

    return load_agent_runtime_config().tools.vision
