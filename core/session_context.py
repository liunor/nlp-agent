"""Explicit, WebUI-safe session identity and local context-state storage."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import time
import uuid
import shutil
from pathlib import Path
from typing import Any

from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, ConfigDict, Field, field_validator


_SESSION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


class SessionContext(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    session_id: str
    user_id: str = "local"
    workspace_id: str = "default"
    channel: str = "local"
    agent_id: str = "coordinator"
    # Login-session identity is distinct from the conversation thread.  It is
    # propagated only across the trusted Gateway/Worker execution path so
    # tools can validate a live database session without changing chat storage.
    auth_session_id: str | None = None
    # Runtime-only labels for observability.  They deliberately do not affect
    # the storage key, so evaluation cases retain the same session semantics.
    observability_attributes: dict[str, str] = Field(default_factory=dict)

    @field_validator("session_id", "user_id", "workspace_id", "channel", "agent_id", "auth_session_id")
    @classmethod
    def validate_identifier(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not _SESSION_ID.fullmatch(value):
            raise ValueError("session identifiers may contain letters, digits, . _ : - only")
        return value

    @classmethod
    def from_config(
        cls, config: RunnableConfig | None, *, require: bool = False
    ) -> "SessionContext":
        configurable = (config or {}).get("configurable", {})
        if require and not configurable.get("thread_id"):
            raise ValueError("configurable.thread_id is required for session isolation")
        return cls(
            session_id=str(configurable.get("thread_id") or "default_session"),
            user_id=str(configurable.get("user_id") or "local"),
            workspace_id=str(configurable.get("workspace_id") or "default"),
            channel=str(configurable.get("channel") or "local"),
            agent_id=str(configurable.get("worker_id") or "coordinator"),
            auth_session_id=(
                str(configurable["auth_session_id"])
                if configurable.get("auth_session_id")
                else None
            ),
        )

    @classmethod
    def create(cls, **identity: str) -> "SessionContext":
        return cls(session_id=f"session_{uuid.uuid4().hex}", **identity)

    @property
    def storage_key(self) -> str:
        raw = (
            f"{self.workspace_id}\0{self.user_id}\0{self.channel}\0"
            f"{self.session_id}\0{self.agent_id}"
        )
        return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


class PersistedContextState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision: int = Field(default=0, ge=0)
    collapse_commits: list[dict[str, Any]] = Field(default_factory=list)
    compacted_tool_call_ids: set[str] = Field(default_factory=set)
    updated_at: float = Field(default_factory=time.time)


class LocalContextStateRepository:
    """Atomic per-session JSON state; transcripts remain append-only elsewhere."""

    def __init__(self, root: Path | None = None) -> None:
        project = Path(__file__).resolve().parent.parent
        self.root = root or project / ".data" / "session_contexts"
        self.root.mkdir(parents=True, exist_ok=True)
        self._locks: dict[str, asyncio.Lock] = {}

    def lock_for(self, context: SessionContext) -> asyncio.Lock:
        return self._locks.setdefault(context.storage_key, asyncio.Lock())

    def path_for(self, context: SessionContext) -> Path:
        return self.root / context.storage_key / "context-state.json"

    def load(self, context: SessionContext) -> PersistedContextState:
        path = self.path_for(context)
        if not path.exists():
            return PersistedContextState()
        return PersistedContextState.model_validate_json(path.read_text(encoding="utf-8"))

    def save(self, context: SessionContext, state: PersistedContextState) -> None:
        path = self.path_for(context)
        path.parent.mkdir(parents=True, exist_ok=True)
        updated = state.model_copy(
            update={"revision": state.revision + 1, "updated_at": time.time()}
        )
        temporary = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
        temporary.write_text(updated.model_dump_json(indent=2), encoding="utf-8")
        os.replace(temporary, path)

    def delete(self, context: SessionContext) -> bool:
        path = self.path_for(context)
        if not path.exists():
            return False
        path.unlink()
        try:
            path.parent.rmdir()
        except OSError:
            pass
        return True

    def delete_session(self, context: SessionContext) -> int:
        """Delete Coordinator and Worker context state for one owned session."""
        removed = 0
        for directory in self.root.iterdir():
            if not directory.is_dir():
                continue
            try:
                encoded = directory.name + "=" * (-len(directory.name) % 4)
                parts = base64.urlsafe_b64decode(encoded).decode().split("\0")
            except Exception:
                continue
            if len(parts) != 5:
                continue
            workspace_id, user_id, _channel, session_id, _agent_id = parts
            if (
                workspace_id == context.workspace_id
                and user_id == context.user_id
                and session_id == context.session_id
            ):
                shutil.rmtree(directory, ignore_errors=True)
                self._locks.pop(directory.name, None)
                removed += 1
        return removed


local_context_repository = LocalContextStateRepository()
