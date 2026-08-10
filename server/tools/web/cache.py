"""Process-local TTL cache holding only cleaned web tool responses."""

from __future__ import annotations

import hashlib
import threading
import time

MAX_ENTRIES = 256


def cache_key(*parts: str) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(part.encode("utf-8"))
        digest.update(b"\x1f")
    return digest.hexdigest()


class TTLCache:
    def __init__(self, ttl_s: float, *, max_entries: int = MAX_ENTRIES) -> None:
        self.ttl_s = max(0.0, float(ttl_s))
        self.max_entries = max(1, max_entries)
        self._entries: dict[str, tuple[float, str]] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> str | None:
        if self.ttl_s <= 0:
            return None
        now = time.monotonic()
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            expires_at, value = entry
            if now >= expires_at:
                self._entries.pop(key, None)
                return None
            return value

    def put(self, key: str, value: str) -> None:
        if self.ttl_s <= 0:
            return
        now = time.monotonic()
        with self._lock:
            if len(self._entries) >= self.max_entries:
                for stale_key in [
                    item
                    for item, (_, expires_at) in self._entries.items()
                    if now >= expires_at
                ]:
                    self._entries.pop(stale_key, None)
                while len(self._entries) >= self.max_entries:
                    oldest_key = next(iter(self._entries))
                    self._entries.pop(oldest_key, None)
            self._entries[key] = (now + self.ttl_s, value)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
