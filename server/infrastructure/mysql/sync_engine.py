"""Reference-counted synchronous engines shared by process and configuration."""

from __future__ import annotations

import json
import os
from threading import RLock
from typing import Any

from sqlalchemy import create_engine as sqlalchemy_create_engine
from sqlalchemy.engine import Engine

from .config import DatabaseConfig
from .pool_metrics import MeasuredQueuePool

_LOCK = RLock()
_ENGINES: dict[tuple[int, str, str], tuple[Engine, int]] = {}


def create_engine(url: str, **options: Any) -> Engine:
    key = (os.getpid(), url, json.dumps(options, sort_keys=True))
    with _LOCK:
        current = _ENGINES.get(key)
        if current is not None:
            engine, references = current
            _ENGINES[key] = (engine, references + 1)
            return engine
        options.setdefault("poolclass", MeasuredQueuePool)
        engine = sqlalchemy_create_engine(url, **options)
        _ENGINES[key] = (engine, 1)
        return engine


def release_engine(engine: Engine) -> None:
    with _LOCK:
        for key, (current, references) in tuple(_ENGINES.items()):
            if current is not engine:
                continue
            if references > 1:
                _ENGINES[key] = (current, references - 1)
            else:
                del _ENGINES[key]
                current.dispose()
            return


def configured_engine(url: str, runtime: dict[str, Any]) -> Engine:
    config = DatabaseConfig.from_runtime({**runtime, "url": url}, synchronous=True)
    return create_engine(
        url.replace("mysql+aiomysql://", "mysql+pymysql://"),
        pool_pre_ping=True, pool_size=config.pool_size,
        max_overflow=config.max_overflow, pool_timeout=config.pool_timeout_s,
        pool_recycle=config.pool_recycle_s,
        connect_args={
            "connect_timeout": config.connect_timeout_s,
            "init_command": f"SET SESSION max_execution_time = {config.statement_timeout_s * 1000}",
        },
    )
