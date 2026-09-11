"""Bounded time budgets shared by the Gateway and visual Workers."""

from __future__ import annotations

import math
import re
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass


IMAGE_TOOL_TIMEOUT_S = 90.0
IMAGE_TOOL_CONCURRENCY = 2
COORDINATOR_COMPLETION_S = 30.0
MAX_BUDGETED_IMAGES = 5


def attached_image_names(content: str) -> tuple[str, ...]:
    """Read the canonical persisted attachment block (also used after Redis dispatch)."""
    block = content.rpartition("---附件---\n")[2]
    if not block.endswith("\n---附件结束---"):
        return ()
    names = re.findall(
        r"^\[图片\] ([^/\\\r\n]+\.(?:png|jpg|jpeg|webp))\r?\n路径: \1$",
        block.removesuffix("\n---附件结束---"),
        flags=re.MULTILINE | re.IGNORECASE,
    )
    return tuple(dict.fromkeys(names))[:MAX_BUDGETED_IMAGES]


def image_turn_timeout(base_s: float, count: int) -> float:
    if count <= 0:
        return base_s
    batches = math.ceil(min(count, MAX_BUDGETED_IMAGES) / IMAGE_TOOL_CONCURRENCY)
    # Includes model planning, Worker summarization, and Coordinator delivery.
    return max(base_s, batches * IMAGE_TOOL_TIMEOUT_S + 90.0)


@dataclass(frozen=True)
class ImageTurnBudget:
    image_count: int
    timeout_s: float
    deadline: float

    def worker_remaining_s(self) -> float:
        return max(0.1, self.deadline - time.monotonic() - COORDINATOR_COMPLETION_S)


_image_turn: ContextVar[ImageTurnBudget | None] = ContextVar("image_turn_budget", default=None)


def current_image_turn() -> ImageTurnBudget | None:
    return _image_turn.get()


@contextmanager
def bind_image_turn(count: int, timeout_s: float):
    budget = ImageTurnBudget(count, timeout_s, time.monotonic() + timeout_s) if count else None
    token = _image_turn.set(budget)
    try:
        yield
    finally:
        _image_turn.reset(token)
