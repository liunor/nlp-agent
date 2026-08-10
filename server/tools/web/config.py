"""Lazy access to the tools.web configuration block."""

from __future__ import annotations

from core.tool_config import WebToolsConfig, load_agent_runtime_config


def get_web_config() -> WebToolsConfig:
    return load_agent_runtime_config().tools.web
