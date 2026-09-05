"""Composition seam for replacing the Worker state backend."""

from __future__ import annotations

import importlib
from collections.abc import Callable
from typing import Any, cast

from gateway.mysql_repository import MySQLGatewayRepository
from gateway.state import TurnExecutionState


StateFactory = Callable[[dict[str, Any]], TurnExecutionState]


def build_turn_execution_state(config: dict[str, Any]) -> TurnExecutionState:
    """Build Worker persistence without coupling its runtime to SQLite."""
    factory_ref = str(config.get("state_factory") or "").strip()
    if factory_ref:
        module_name, separator, attribute = factory_ref.partition(":")
        if not separator or not module_name or not attribute:
            raise ValueError("gateway.state_factory must use package.module:function")
        factory = cast(
            StateFactory, getattr(importlib.import_module(module_name), attribute)
        )
        return factory(config)

    if str(config.get("persistence", "")).lower() != "mysql":
        raise RuntimeError("runtime persistence must be mysql; SQLite is migration-CLI only")
    from configs.settings import settings

    url = settings.NLP_AGENT_DATABASE_URL.strip()
    if not url:
        raise RuntimeError("NLP_AGENT_DATABASE_URL is required for MySQL worker state")
    return MySQLGatewayRepository(
        url,
        knowledge_point_prompt_budget=max(1, int(config.get("knowledge_point_prompt_budget", 12_000))),
        quota_enforcement=settings.quota_enforcement_enabled,
    )
