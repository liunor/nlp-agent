"""Destructive but scoped reset of local learner runtime data."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import Any

from configs.settings import settings
from core.identity import AuthenticatedPrincipal
from core.observability.mysql_repository import MySQLTelemetryRepository
from core.observability.repository import TelemetryRepository
from core.observability.runtime import TelemetryRuntime
from core.observability.reset_lock import runtime_reset_lock
from core.session_context import local_context_repository
from gateway.mysql_repository import MySQLGatewayRepository
from server.agent.node.session_storage import DATA_DIR as WORKER_SESSIONS_DIR
from server.agent.session_service import local_session_service
from server.agent.session_storage import CHAT_HISTORY_DIR, _save_sessions_index
from server.memory.manager import MEMORY_DIR


def _clear_directory(path: str | Path, *, preserve_names: set[str] | frozenset[str] = frozenset()) -> int:
    root = Path(path)
    if not root.exists():
        return 0
    removed = 0
    for child in root.iterdir():
        if child.name in preserve_names:
            continue
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
        else:
            child.unlink(missing_ok=True)
        removed += 1
    return removed


def _clear_checkpoints() -> int:
    # Checkpoints are owned by MySQL and removed by the LangGraph saver on session deletion.
    return 0


class LocalRuntimeResetter:
    """Coordinates reset across the otherwise separate monitor and chat processes."""

    def __init__(self, runtime: TelemetryRuntime, gateway_repository: MySQLGatewayRepository | None = None) -> None:
        self.runtime = runtime
        if gateway_repository is not None:
            self.gateway_repository = gateway_repository
        elif isinstance(getattr(runtime, "repository", None), TelemetryRepository):
            # A runtime created with an explicit SQLite path is a genuinely
            # isolated monitor/test store. Never manufacture a MySQL gateway
            # repository here, otherwise a local reset could touch production
            # learner data despite using a temporary telemetry database.
            self.gateway_repository = None
        else:
            dsn = settings.NLP_AGENT_DATABASE_URL.strip()
            if not dsn:
                raise RuntimeError("NLP_AGENT_DATABASE_URL is required for runtime reset")
            self.gateway_repository = MySQLGatewayRepository(dsn)

    async def reset(self) -> dict[str, Any]:
        principal = AuthenticatedPrincipal.system_admin()
        sessions = await local_session_service.list(principal)
        for session in sessions:
            await local_session_service.delete(principal, str(session["session_id"]))

        await self.runtime.flush()
        gateway: dict[str, Any] = {}
        telemetry: dict[str, Any]
        if (
            self.gateway_repository is not None
            and isinstance(self.runtime.repository, MySQLTelemetryRepository)
        ):
            # Keep the cross-process MySQL fence while both stores are
            # cleared. Gateway and Monitor otherwise have separate SQLAlchemy
            # engines and a reset could leave telemetry written between them.
            gateway, telemetry = await asyncio.to_thread(self._clear_mysql_stores)
        else:
            if self.gateway_repository is not None:
                gateway = await asyncio.to_thread(
                    self.gateway_repository.clear_learning_sessions
                )
            telemetry = await asyncio.to_thread(self.runtime.repository.clear)
        files = await asyncio.to_thread(self._clear_orphaned_runtime_files)
        return {"sessions": len(sessions), "gateway": gateway, "telemetry": telemetry, "files": files}

    def _clear_mysql_stores(self) -> tuple[dict[str, Any], dict[str, Any]]:
        assert self.gateway_repository is not None
        assert isinstance(self.runtime.repository, MySQLTelemetryRepository)
        with runtime_reset_lock(self.gateway_repository._engine):
            gateway = self.gateway_repository.clear_learning_sessions(_lock_held=True)
            telemetry = self.runtime.repository.clear(_lock_held=True)
        return gateway, telemetry

    @staticmethod
    def _clear_orphaned_runtime_files() -> dict[str, int]:
        files = {
            "chat_history": _clear_directory(CHAT_HISTORY_DIR),
            "worker_sessions": _clear_directory(WORKER_SESSIONS_DIR),
            "session_contexts": _clear_directory(local_context_repository.root),
            "memory": _clear_directory(MEMORY_DIR),
            "tool_audit": _clear_directory(settings.BASE_DIR / ".data" / "tool-audit"),
        }
        _save_sessions_index({"active_session": None, "sessions": {}})
        files["checkpoints"] = 0
        return files
