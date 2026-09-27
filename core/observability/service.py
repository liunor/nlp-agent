"""Authenticated query boundary for Gateway observability APIs."""

from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from core.identity import AccessDeniedError, AuthenticatedPrincipal
from core.observability.runtime import TelemetryRuntime, global_telemetry
from server.storage.monitoring import current_storage_snapshot


class ObservabilityService:
    def __init__(self, runtime: TelemetryRuntime = global_telemetry) -> None:
        self.runtime = runtime

    @staticmethod
    def _require_admin(principal: AuthenticatedPrincipal) -> None:
        if not principal.is_admin:
            raise AccessDeniedError("administrator role is required")

    async def overview(
        self, principal: AuthenticatedPrincipal, days: int = 30
    ) -> dict[str, Any]:
        self._require_admin(principal)
        result = await asyncio.to_thread(self.runtime.repository.overview, days)
        return {**result, "runtime": self.runtime.health()}

    async def traces(
        self,
        principal: AuthenticatedPrincipal,
        *,
        limit: int = 100,
        session_id: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        return await asyncio.to_thread(
            self.runtime.repository.list_traces,
            limit=limit,
            session_id=session_id,
            status=status,
            user_id=None if principal.is_admin else principal.user_id,
            workspace_ids=None if principal.is_admin else principal.workspace_ids,
        )

    async def trace(
        self, principal: AuthenticatedPrincipal, trace_id: str
    ) -> dict[str, Any] | None:
        detail = await asyncio.to_thread(self.runtime.repository.trace_detail, trace_id)
        if detail is None:
            return None
        trace = detail["trace"]
        if not principal.is_admin and (
            trace.get("user_id") != principal.user_id
            or (
                "*" not in principal.workspace_ids
                and trace.get("workspace_id") not in principal.workspace_ids
            )
        ):
            raise AccessDeniedError(trace_id)
        return detail

    async def usage(
        self, principal: AuthenticatedPrincipal, days: int = 30
    ) -> list[dict[str, Any]]:
        self._require_admin(principal)
        return await asyncio.to_thread(self.runtime.repository.usage, days)

    async def sessions(
        self, principal: AuthenticatedPrincipal, days: int = 30, limit: int = 100
    ) -> list[dict[str, Any]]:
        return await asyncio.to_thread(
            self.runtime.repository.sessions,
            days,
            limit,
            user_id=None if principal.is_admin else principal.user_id,
            workspace_ids=None if principal.is_admin else principal.workspace_ids,
        )

    async def events(
        self,
        principal: AuthenticatedPrincipal,
        *,
        limit: int = 200,
        level: str | None = None,
        trace_id: str | None = None,
    ) -> list[dict[str, Any]]:
        if trace_id:
            await self.trace(principal, trace_id)
        else:
            self._require_admin(principal)
        return await asyncio.to_thread(
            self.runtime.repository.recent_events,
            limit=limit,
            level=level,
            trace_id=trace_id,
        )

    async def errors(
        self, principal: AuthenticatedPrincipal, days: int = 30, limit: int = 100
    ) -> list[dict[str, Any]]:
        self._require_admin(principal)
        return await asyncio.to_thread(self.runtime.repository.errors, days, limit)

    async def health(self, principal: AuthenticatedPrincipal) -> dict[str, Any]:
        self._require_admin(principal)
        return await asyncio.to_thread(self.runtime.health)

    async def space(
        self, principal: AuthenticatedPrincipal, db: AsyncSession
    ) -> dict[str, Any]:
        """Return storage for this deployment's database and visible disk only."""

        self._require_admin(principal)
        return await current_storage_snapshot(db)

    def subscribe(
        self, principal: AuthenticatedPrincipal, maxsize: int = 500
    ) -> asyncio.Queue[dict[str, Any]]:
        self._require_admin(principal)
        return self.runtime.subscribe(maxsize=maxsize)

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self.runtime.unsubscribe(queue)


global_observability_service = ObservabilityService()
