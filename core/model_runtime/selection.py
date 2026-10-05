"""Turn-local model profile selection shared by Agent runtime components."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar


_current_model_profile: ContextVar[str | None] = ContextVar(
    "current_model_profile", default=None
)
_current_thinking_enabled: ContextVar[bool | None] = ContextVar(
    "current_thinking_enabled", default=None
)


def current_model_profile() -> str | None:
    return _current_model_profile.get()


def current_thinking_enabled() -> bool | None:
    return _current_thinking_enabled.get()


@contextmanager
def bind_model_profile(profile: str | None) -> Iterator[None]:
    token = _current_model_profile.set(profile)
    try:
        yield
    finally:
        _current_model_profile.reset(token)


@contextmanager
def bind_thinking_enabled(enabled: bool | None) -> Iterator[None]:
    token = _current_thinking_enabled.set(enabled)
    try:
        yield
    finally:
        _current_thinking_enabled.reset(token)
