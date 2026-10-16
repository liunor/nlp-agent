"""Authenticated query boundary for Gateway observability APIs."""

from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from core.identity import AccessDeniedError, AuthenticatedPrincipal
from core.observability.runtime import TelemetryRuntime, global_telemetry
from core.rbac import Permission, authorization_service
from server.storage.monitoring import current_storage_snapshot


class ObservabilityService:
    def __init__(self, runtime: TelemetryRuntime = global_telemetry) -> None:
        self.runtime = runtime

    @staticmethod
    def _can_monitor_system(principal: AuthenticatedPrincipal) -> bool:
        return authorization_service.allowed(principal, Permission.SYSTEM_RUNTIME_MONITOR)

    @classmethod
    def _require_monitor(cls, principal: AuthenticatedPrincipal) -> None:
        # Monitoring is a capability, not an implicit role check. This allows
        # a least-privilege operations account to inspect all-user signals
        # without granting it unrelated developer or administration powers.
        authorization_service.require(principal, Permission.SYSTEM_RUNTIME_MONITOR)

    async def overview(
        self, principal: AuthenticatedPrincipal, days: int = 30
    ) -> dict[str, Any]:
        self._require_monitor(principal)
        result = await asyncio.to_thread(self.runtime.repository.overview, days)
        return {**result, "runtime": self.runtime.health()}

    async def dependency_health(
        self,
        principal: AuthenticatedPrincipal,
        *,
        days: int = 30,
        window_minutes: int = 120,
        bucket_minutes: int = 5,
    ) -> dict[str, Any]:
        """Return bounded all-user dependency health for model/component triage."""
        self._require_monitor(principal)
        return await asyncio.to_thread(
            self.runtime.repository.dependency_health,
            days,
            window_minutes=window_minutes,
            bucket_minutes=bucket_minutes,
        )

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
            user_id=None if self._can_monitor_system(principal) else principal.user_id,
            workspace_ids=None if self._can_monitor_system(principal) else principal.workspace_ids,
        )

    async def trace(
        self, principal: AuthenticatedPrincipal, trace_id: str
    ) -> dict[str, Any] | None:
        detail = await asyncio.to_thread(self.runtime.repository.trace_detail, trace_id)
        if detail is None:
            return None
        trace = detail["trace"]
        if not self._can_monitor_system(principal) and (
            trace.get("user_id") != principal.user_id
            or (
                "*" not in principal.workspace_ids
                and trace.get("workspace_id") not in principal.workspace_ids
            )
        ):
            raise AccessDeniedError(trace_id)
        return detail

    async def trace_groups(
        self,
        principal: AuthenticatedPrincipal,
        *,
        days: int = 30,
        limit: int = 24,
        offset: int = 0,
        query: str | None = None,
        focus: str = "all",
    ) -> dict[str, Any]:
        """Return problem-oriented chain summaries for all monitor users."""
        self._require_monitor(principal)
        return await asyncio.to_thread(
            self.runtime.repository.trace_groups,
            days=days,
            limit=limit,
            offset=offset,
            query=query,
            focus=focus,
        )

    async def trace_group(
        self, principal: AuthenticatedPrincipal, chain_id: str
    ) -> dict[str, Any] | None:
        self._require_monitor(principal)
        return await asyncio.to_thread(
            self.runtime.repository.trace_group_detail, chain_id
        )

    async def usage(
        self, principal: AuthenticatedPrincipal, days: int = 30
    ) -> list[dict[str, Any]]:
        self._require_monitor(principal)
        return await asyncio.to_thread(self.runtime.repository.usage, days)

    async def system_usage(
        self,
        principal: AuthenticatedPrincipal,
        usage_reader: Any,
        days: int = 30,
        *,
        include_users: bool = False,
    ) -> dict[str, Any]:
        """Read the canonical all-user token/credit ledger for the monitor."""
        self._require_monitor(principal)
        return await asyncio.to_thread(
            usage_reader.system_snapshot,
            days=days,
            include_users=include_users,
        )

    async def system_usage_users(
        self,
        principal: AuthenticatedPrincipal,
        usage_reader: Any,
        *,
        days: int = 30,
        limit: int = 12,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Read one bounded page from the administrator's all-user ledger."""
        self._require_monitor(principal)
        return await asyncio.to_thread(
            usage_reader.system_user_page,
            days=days,
            limit=limit,
            offset=offset,
        )

    async def system_usage_dimension(
        self,
        principal: AuthenticatedPrincipal,
        usage_reader: Any,
        *,
        dimension: str,
        days: int = 30,
        limit: int = 12,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Read one bounded page for any high-cardinality usage dimension."""
        self._require_monitor(principal)
        return await asyncio.to_thread(
            usage_reader.system_dimension_page,
            dimension=dimension,
            days=days,
            limit=limit,
            offset=offset,
        )

    async def system_usage_trend(
        self,
        principal: AuthenticatedPrincipal,
        usage_reader: Any,
        *,
        window_minutes: int = 120,
        bucket_minutes: int = 5,
    ) -> dict[str, Any]:
        """Read the bounded five-minute all-user usage trend."""
        self._require_monitor(principal)
        return await asyncio.to_thread(
            usage_reader.system_trend,
            window_minutes=window_minutes,
            bucket_minutes=bucket_minutes,
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
            self._require_monitor(principal)
        return await asyncio.to_thread(
            self.runtime.repository.recent_events,
            limit=limit,
            level=level,
            trace_id=trace_id,
        )

    async def errors(
        self, principal: AuthenticatedPrincipal, days: int = 30, limit: int = 100
    ) -> list[dict[str, Any]]:
        self._require_monitor(principal)
        return await asyncio.to_thread(self.runtime.repository.errors, days, limit)

    async def error_analysis(
        self,
        principal: AuthenticatedPrincipal,
        *,
        days: int = 30,
        limit: int = 100,
        offset: int = 0,
        window_minutes: int = 120,
        bucket_minutes: int = 5,
    ) -> dict[str, Any]:
        """Return redacted error fingerprints and a bounded incident trend."""
        self._require_monitor(principal)
        return await asyncio.to_thread(
            self.runtime.repository.error_analysis,
            days,
            limit=limit,
            offset=offset,
            window_minutes=window_minutes,
            bucket_minutes=bucket_minutes,
        )

    async def health(self, principal: AuthenticatedPrincipal) -> dict[str, Any]:
        self._require_monitor(principal)
        return await asyncio.to_thread(self.runtime.health)

    async def space(
        self, principal: AuthenticatedPrincipal, db: AsyncSession
    ) -> dict[str, Any]:
        """Return storage for this deployment's database and visible disk only."""

        self._require_monitor(principal)
        return await current_storage_snapshot(db)

    def subscribe(
        self, principal: AuthenticatedPrincipal, maxsize: int = 500
    ) -> asyncio.Queue[dict[str, Any]]:
        self._require_monitor(principal)
        return self.runtime.subscribe(maxsize=maxsize)

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self.runtime.unsubscribe(queue)


global_observability_service = ObservabilityService()
