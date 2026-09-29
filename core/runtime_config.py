"""Non-destructive runtime configuration overrides managed by the Developer UI."""

from __future__ import annotations

import os
import tempfile
import threading
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterator

import yaml

if os.name == "nt":
    import msvcrt
else:
    import fcntl


BASE_DIR = Path(__file__).resolve().parent.parent
BASE_CONFIG_PATH = BASE_DIR / "configs" / "agent_config.yaml"
OVERRIDE_PATH = BASE_DIR / ".data" / "developer" / "runtime-overrides.yaml"
RESERVED_WORKER_PROFILES = frozenset({"web_researcher", "web_reader"})
_RUNTIME_OVERRIDE_THREAD_LOCK = threading.Lock()


def _compatible_overrides(value: dict[str, Any]) -> dict[str, Any]:
    """Drop obsolete or reserved overrides while preserving unrelated UI state."""
    override = deepcopy(value)
    profiles = override.get("worker_profiles")
    if isinstance(profiles, dict):
        for name in RESERVED_WORKER_PROFILES:
            profiles.pop(name, None)

    tools = override.get("tools")
    web = tools.get("web") if isinstance(tools, dict) else None
    if isinstance(web, dict):
        for key in ("search", "allow_provider_override", "trusted_service_hosts"):
            web.pop(key, None)
        fetch = web.get("fetch")
        if isinstance(fetch, dict):
            fetch.pop("remote_reader", None)
    return override


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def load_runtime_config() -> dict[str, Any]:
    with BASE_CONFIG_PATH.open("r", encoding="utf-8") as file:
        base = yaml.safe_load(file) or {}
    if not OVERRIDE_PATH.exists():
        return base
    with OVERRIDE_PATH.open("r", encoding="utf-8") as file:
        override = yaml.safe_load(file) or {}
    if not isinstance(override, dict):
        raise ValueError(f"runtime override must be a mapping: {OVERRIDE_PATH}")
    return _merge(base, _compatible_overrides(override))


def load_runtime_overrides() -> dict[str, Any]:
    if not OVERRIDE_PATH.exists():
        return {}
    with OVERRIDE_PATH.open("r", encoding="utf-8") as file:
        value = yaml.safe_load(file) or {}
    if not isinstance(value, dict):
        raise ValueError(f"runtime override must be a mapping: {OVERRIDE_PATH}")
    return value


@contextmanager
def runtime_overrides_transaction() -> Iterator[dict[str, Any]]:
    """Serialize read-modify-write updates to the developer override file."""
    OVERRIDE_PATH.parent.mkdir(parents=True, exist_ok=True)
    lock_path = Path(f"{OVERRIDE_PATH}.lock")
    with _RUNTIME_OVERRIDE_THREAD_LOCK:
        with lock_path.open("a+b") as lock_file:
            if os.name == "nt":
                lock_file.seek(0, os.SEEK_END)
                if lock_file.tell() == 0:
                    lock_file.write(b"\0")
                    lock_file.flush()
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
            else:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                overrides = load_runtime_overrides()
                yield overrides
                save_runtime_overrides(overrides)
            finally:
                if os.name == "nt":
                    lock_file.seek(0)
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def save_runtime_overrides(overrides: dict[str, Any]) -> None:
    """Atomically persist UI-managed overrides without rewriting the commented base YAML."""
    OVERRIDE_PATH.parent.mkdir(parents=True, exist_ok=True)
    rendered = yaml.safe_dump(overrides, allow_unicode=True, sort_keys=False)
    fd, name = tempfile.mkstemp(prefix="runtime-overrides-", suffix=".yaml", dir=OVERRIDE_PATH.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as file:
            file.write(rendered)
            file.flush()
            os.fsync(file.fileno())
        os.replace(name, OVERRIDE_PATH)
    finally:
        if os.path.exists(name):
            os.unlink(name)
