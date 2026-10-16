"""Remove user-owned local files after the database deletion commits."""

from __future__ import annotations

import base64
import shutil
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession

from configs.settings import settings
from server.memory.manager import _scope_key
from server.tools.vision.input_resolver import DEFAULT_UPLOADS_ROOT


DATA_ROOT = Path(__file__).resolve().parents[2] / ".data"


@dataclass(frozen=True)
class LocalCleanup:
    user_id: str
    workspace_ids: tuple[str, ...]
    personal_workspace_id: str | None
    session_ids: tuple[str, ...]
    artifact_locators: tuple[str, ...]


def _remove_tree(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)


def _context_owner(name: str) -> str | None:
    try:
        parts = base64.urlsafe_b64decode(name + "=" * (-len(name) % 4)).decode().split("\0")
    except (ValueError, UnicodeError):
        return None
    return parts[1] if len(parts) == 5 else None


def _memory_owner(name: str) -> str | None:
    try:
        parts = base64.urlsafe_b64decode(name + "=" * (-len(name) % 4)).decode().split("\0")
    except (ValueError, UnicodeError):
        return None
    return parts[1] if len(parts) == 2 else None


def clean_local_data(plan: LocalCleanup) -> None:
    for workspace_id in plan.workspace_ids:
        _remove_tree(DEFAULT_UPLOADS_ROOT / workspace_id / plan.user_id)
        for kind in ("users", "archives", "state"):
            _remove_tree(DATA_ROOT / "memory" / kind / _scope_key(workspace_id, plan.user_id))
    if plan.personal_workspace_id:
        _remove_tree(DEFAULT_UPLOADS_ROOT / plan.personal_workspace_id / plan.user_id)
        _remove_tree(DATA_ROOT / "memory" / "workspaces" / _scope_key(plan.personal_workspace_id))

    # Old memberships may already have been removed; find identity-scoped
    # files by exact directory name rather than relying only on current rows.
    if DEFAULT_UPLOADS_ROOT.is_dir():
        for workspace in DEFAULT_UPLOADS_ROOT.iterdir():
            if workspace.is_dir() and not workspace.is_symlink():
                _remove_tree(workspace / plan.user_id)
    for kind in ("users", "archives", "state"):
        root = DATA_ROOT / "memory" / kind
        if root.is_dir():
            for path in root.iterdir():
                if path.is_dir() and _memory_owner(path.name) == plan.user_id:
                    _remove_tree(path)

    contexts = DATA_ROOT / "session_contexts"
    if contexts.is_dir():
        for path in contexts.iterdir():
            if path.is_dir() and _context_owner(path.name) == plan.user_id:
                _remove_tree(path)
    for session_id in plan.session_ids:
        (DATA_ROOT / "chat_history" / f"{session_id}.jsonl").unlink(missing_ok=True)
        _remove_tree(DATA_ROOT / "sessions" / session_id)
    if plan.session_ids and (DATA_ROOT / "chat_history" / ".sessions.json").is_file():
        from server.agent.session_storage import (
            _INDEX_LOCK, _load_sessions_index, _save_sessions_index,
        )

        with _INDEX_LOCK:
            index = _load_sessions_index()
            sessions = index.get("sessions", {})
            for session_id in plan.session_ids:
                sessions.pop(session_id, None)
            if index.get("active_session") in plan.session_ids:
                index["active_session"] = None
            _save_sessions_index(index)

    if plan.artifact_locators:
        from server.sandbox.artifacts import resolve_artifact_path

        root = Path(settings.NLP_AGENT_SANDBOX_ARTIFACT_STORE_ROOT)
        for locator in plan.artifact_locators:
            try:
                resolve_artifact_path(root, locator).unlink()
            except FileNotFoundError:
                continue


def schedule_local_cleanup(session: AsyncSession, plan: LocalCleanup) -> None:
    # A rollback must leave local files untouched. The function-scoped request
    # transaction commits before FastAPI sends the successful response.
    def after_commit(_session) -> None:
        event.remove(session.sync_session, "after_rollback", after_rollback)
        try:
            clean_local_data(plan)
        except OSError as error:
            raise RuntimeError(
                "Account removed from MySQL, but local files could not be fully removed"
            ) from error

    def after_rollback(_session) -> None:
        event.remove(session.sync_session, "after_commit", after_commit)

    event.listen(session.sync_session, "after_commit", after_commit, once=True)
    event.listen(session.sync_session, "after_rollback", after_rollback, once=True)
