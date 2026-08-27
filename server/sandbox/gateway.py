"""Authenticated Web-facing façade over development and Docker sandbox runtimes.

Controllers use this module instead of calling a runtime directly.  The caller
always supplies a server-derived ``SandboxScope``; runtime ids and tickets from
the browser are only capabilities to be verified, never ownership inputs.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .contracts import SandboxScope
from .inmemory_runtime import InMemoryRuntime
from .manager import WarmPoolManager
from .service import sandbox_lifecycle_service
from .ticket import SandboxTicketClaims, SandboxTicketSigner


class SandboxGateway:
    def __init__(
        self,
        *,
        mode: str,
        session_factory: async_sessionmaker[AsyncSession],
        ticket_signer: SandboxTicketSigner,
        manager: WarmPoolManager | None = None,
        inmemory: InMemoryRuntime | None = None,
    ) -> None:
        self._mode = mode
        self._session_factory = session_factory
        self._ticket_signer = ticket_signer
        self._manager = manager
        self._inmemory = inmemory or InMemoryRuntime()

    async def open(self, session: AsyncSession, scope: SandboxScope) -> dict[str, object]:
        if self._mode == "docker":
            # The Manager uses its own database connection.  Commit the lease
            # in a short, dedicated transaction before asking it to claim a
            # runtime; otherwise the first request's uncommitted lease is
            # invisible and is incorrectly reported as warming.
            async with self._session_factory.begin() as committed_session:
                lease_payload = await sandbox_lifecycle_service.ensure_current_lease(
                    committed_session, scope
                )
        else:
            lease_payload = await sandbox_lifecycle_service.ensure_current_lease(session, scope)
        if self._mode == "inmemory":
            return {**lease_payload, "runtime_available": True, "runtime": {"kind": "inmemory", "ticket": None}}
        manager = self._require_manager()
        lease = lease_payload["lease"]
        assert isinstance(lease, dict)
        claim = await manager.claim(scope, lease_id=str(lease["id"]))
        if claim is None:
            return {**lease_payload, "runtime_available": False, "runtime": None, "pool_status": "warming"}
        ticket = self._ticket_signer.issue(
            SandboxTicketClaims(
                scope.owner_user_id, scope.auth_session_id, str(lease["id"]), claim.runtime.id,
                claim.runtime.generation, claim.nonce,
            )
        )
        return {
            **lease_payload,
            "runtime_available": True,
            "runtime": {"id": claim.runtime.id, "generation": claim.runtime.generation, "ticket": ticket},
        }

    async def execute(self, scope: SandboxScope, *, source: str, ticket: str | None) -> dict[str, object]:
        if self._mode == "inmemory":
            return await self._inmemory.execute(user_id=scope.owner_user_id, source=source)
        claims = self._claims(scope, ticket)
        result = await self._require_manager().execute_claimed(
            scope, lease_id=claims.lease_id, runtime_id=claims.runtime_id,
            generation=claims.generation, nonce=claims.nonce, source=source,
        )
        return {**result, "ticket": self._ticket_signer.issue(claims.without_nonce())}

    async def restart(self, scope: SandboxScope, *, ticket: str | None) -> dict[str, object]:
        if self._mode == "inmemory":
            await self._inmemory.restart(user_id=scope.owner_user_id)
            return {"status": "restarted", "ticket": None}
        claims = self._claims(scope, ticket)
        await self._require_manager().reset_runtime(
            scope,
            lease_id=claims.lease_id,
            runtime_id=claims.runtime_id,
            generation=claims.generation,
        )
        return {"status": "restarted", "ticket": None}

    def _claims(self, scope: SandboxScope, ticket: str | None) -> SandboxTicketClaims:
        if not ticket:
            raise PermissionError("sandbox ticket is required")
        return self._ticket_signer.verify(
            ticket, user_id=scope.owner_user_id, auth_session_id=scope.auth_session_id
        )

    def _require_manager(self) -> WarmPoolManager:
        if self._manager is None:
            raise RuntimeError("isolated Sandbox Manager is not configured")
        return self._manager
