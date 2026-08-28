"""Model-facing Sandbox tools for the Phase 4 closed loop.

The model receives a deliberately small control surface.  Scratch execution is
always isolated from the user's Interactive Kernel; active-kernel execution and
reset are high-risk tools and still require the normal ToolRuntime grant plus an
explicit ``confirmed`` argument.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from contextlib import asynccontextmanager
import hashlib
import secrets
from typing import Any
from uuid import uuid4

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from configs.settings import settings
from server.infrastructure.mysql.models import (
    SandboxEnvironmentModel,
    SandboxExecutionModel,
    SandboxLeaseModel,
    SandboxRuntimeInstanceModel,
    SessionModel,
    UserModel,
)

from .contracts import SandboxScope
from .confirmation import SandboxConfirmationSigner, create_confirmation_replay_store
from .events import default_sandbox_event_store
from .execution_events import append_event_with_retry, execution_failure_payload
from .inmemory_runtime import InMemoryRuntime
from .ticket import SandboxTicketClaims, SandboxTicketSigner


def _utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _error(code: str, message: str, **details: object) -> dict[str, object]:
    return {"ok": False, "code": code, "error": message, "details": details}


@dataclass(frozen=True)
class _AuthorizedModelContext:
    context: Any
    scope: SandboxScope | None
    environment_id: str | None
    lease_id: str | None
    runtime_id: str | None


class SandboxModelToolService:
    """Resolve model calls against the authenticated Sandbox boundary."""

    def __init__(
        self,
        *,
        mode: str | None = None,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        manager: Any | None = None,
        interactive: InMemoryRuntime | None = None,
    ) -> None:
        self.mode = (mode or settings.NLP_AGENT_SANDBOX_RUNTIME_MODE).strip().lower()
        self.session_factory = session_factory
        self._manager = manager
        self._interactive = interactive or InMemoryRuntime()
        configured_secret = settings.NLP_AGENT_WEB_SECRET.strip()
        self._signing_secret = configured_secret or secrets.token_urlsafe(32)
        self._signer = SandboxTicketSigner(self._signing_secret)
        self._confirmation_signer = SandboxConfirmationSigner(
            self._signing_secret
        )
        self._confirmation_replay_store = create_confirmation_replay_store()
        self._local_executions: dict[str, dict[str, object]] = {}

    async def _authorize(self, config: RunnableConfig, *, require_lease: bool = False) -> _AuthorizedModelContext | None:
        from core.session_context import SessionContext

        try:
            context = SessionContext.from_config(config, require=True)
        except ValueError:
            return None
        if self.mode not in {"inmemory", "docker"}:
            return None
        if self.session_factory is None:
            if context.user_id != "local":
                return None
            return _AuthorizedModelContext(context, None, None, None, None)

        # In-memory mode is the test/local compatibility backend and has no
        # independently authenticated transport. Docker production must carry
        # the login session explicitly; it must never substitute thread_id.
        auth_session_id = context.auth_session_id or (
            context.session_id if self.mode == "inmemory" else None
        )
        if not auth_session_id:
            # A conversation thread is not a login session.  In production
            # absence of the separately propagated identity must fail closed.
            return None

        async with self.session_factory() as session:
            auth_session = await session.get(SessionModel, auth_session_id)
            user = await session.get(UserModel, context.user_id)
            if auth_session is None or user is None:
                return None
            now = _utc_now()
            if (
                str(auth_session.user_id) != context.user_id
                or str(auth_session.workspace_id) != context.workspace_id
                or auth_session.revoked_at is not None
                or auth_session.expires_at <= now
                or auth_session.authorization_version != user.authorization_version
                or user.status != "active"
                or user.deleted_at is not None
            ):
                return None
            environment = await session.scalar(
                select(SandboxEnvironmentModel).where(
                    SandboxEnvironmentModel.owner_user_id == context.user_id
                )
            )
            lease = None
            if environment is not None:
                lease = await session.scalar(
                    select(SandboxLeaseModel).where(
                        SandboxLeaseModel.environment_id == environment.id,
                        SandboxLeaseModel.user_id == context.user_id,
                        SandboxLeaseModel.auth_session_id == auth_session_id,
                        SandboxLeaseModel.state == "active",
                        SandboxLeaseModel.expires_at > now,
                    )
                )
            if require_lease and lease is None:
                return None
            scope = SandboxScope(
                owner_user_id=context.user_id,
                auth_session_id=auth_session_id,
                workspace_id=context.workspace_id,
                generation=auth_session.authorization_version,
                lease_expires_at=auth_session.expires_at,
            )
            return _AuthorizedModelContext(
                context,
                scope,
                str(environment.id) if environment is not None else None,
                str(lease.id) if lease is not None else None,
                str(lease.runtime_instance_id) if lease is not None and lease.runtime_instance_id else None,
            )

    def _manager_for_docker(self) -> Any | None:
        # Docker access is an explicit dependency supplied by the isolated
        # Manager control plane.  The Web process never constructs an adapter.
        return self._manager

    async def _start_execution(
        self,
        authorized: _AuthorizedModelContext,
        *,
        source: str,
        actor_type: str = "model",
        runtime_id: str | None = None,
        config: RunnableConfig | None = None,
    ) -> str | None:
        execution_id = str(uuid4())
        trace_id: str | None = None
        span_id: str | None = None
        parent_span_id: str | None = None
        try:
            from core.observability.context import TelemetryContext

            trace_context = TelemetryContext.from_config(config)
            if trace_context is not None:
                trace_id = trace_context.trace_id
                parent_span_id = trace_context.span_id
        except Exception:
            trace_context = None
        trace_id = trace_id or uuid4().hex
        span_id = span_id or uuid4().hex
        if self.session_factory is None:
            self._local_executions[execution_id] = {
                "id": execution_id,
                "owner_user_id": authorized.context.user_id,
                "workspace_id": authorized.context.workspace_id,
                "status": "running",
                "runtime_instance_id": runtime_id,
                "started_at": _utc_now().isoformat(),
                "completed_at": None,
                "exit_reason": None,
                "trace_id": trace_id,
                "span_id": span_id,
                "parent_span_id": parent_span_id,
            }
        else:
            if authorized.scope is None:
                return None
            async with self.session_factory.begin() as session:
                environment_id = authorized.environment_id
                if environment_id is None:
                    from .service import sandbox_lifecycle_service

                    environment = await sandbox_lifecycle_service.ensure_environment(
                        session, authorized.scope
                    )
                    environment_id = str(environment.id)
                session.add(
                    SandboxExecutionModel(
                        id=execution_id,
                        environment_id=environment_id,
                        runtime_instance_id=runtime_id,
                        lease_id=authorized.lease_id,
                        owner_user_id=authorized.context.user_id,
                        workspace_id=authorized.context.workspace_id,
                        actor_type=actor_type,
                        request_id=execution_id,
                        code_hash=hashlib.sha256(source.encode("utf-8")).hexdigest(),
                        status="running",
                        generation=authorized.scope.generation,
                        trace_id=trace_id,
                        span_id=span_id,
                        parent_span_id=parent_span_id,
                        started_at=_utc_now(),
                    )
                )
        try:
            await self._append_event(
                execution_id,
                user_id=authorized.context.user_id,
                event_type="execution.started",
                payload={"actor_type": actor_type, "trace_id": trace_id, "span_id": span_id},
            )
        except Exception as error:
            # The execution envelope already committed above.  Close it in a
            # separate transaction before surfacing the delivery failure, so a
            # Redis outage can never leave a durable ``running`` row behind.
            await self._finish_execution(
                execution_id,
                user_id=authorized.context.user_id,
                status="failed",
                error=error,
                emit_events=False,
            )
            try:
                await self._append_event(
                    execution_id,
                    user_id=authorized.context.user_id,
                    event_type="execution.failed",
                    payload=execution_failure_payload(error),
                )
            except Exception:
                # The database status is authoritative when the event store is
                # unavailable; a later replay/reconciliation can republish it.
                pass
            raise
        return execution_id

    async def _finish_execution(
        self,
        execution_id: str,
        *,
        user_id: str,
        status: str,
        error: BaseException | None = None,
        result: dict[str, object] | None = None,
        emit_events: bool = True,
    ) -> BaseException | None:
        reason = f"{type(error).__name__}: {error}"[:128] if error else None
        failed = bool(error) or status.lower() in {"failed", "error", "timeout", "timed_out"}
        event_error: BaseException | None = None
        if self.session_factory is None:
            local = self._local_executions.get(execution_id)
            if local is not None:
                local.update({"status": status, "completed_at": _utc_now().isoformat(), "exit_reason": reason})
            if not emit_events:
                return None
            try:
                if result is not None:
                    for stream, output in (("stdout", result.get("stdout")), ("stderr", result.get("stderr"))):
                        if output:
                            await self._append_event(
                                execution_id,
                                user_id=user_id,
                                event_type="execution.output",
                                payload={"stream": stream, "text": str(output)},
                            )
                await self._append_event(
                    execution_id,
                    user_id=user_id,
                    event_type="execution.failed" if failed else "execution.completed",
                    payload={
                        "status": status,
                        **({"error": type(error).__name__} if error else {}),
                    },
                )
            except Exception as delivery_error:
                event_error = delivery_error
                if local is not None:
                    local.update(
                        {
                            "status": "failed",
                            "exit_reason": f"event delivery failed: {delivery_error}"[:128],
                            "event_delivery": {
                                "status": "failed",
                                "error": str(delivery_error)[:128],
                            },
                        }
                    )
                try:
                    await self._append_event(
                        execution_id,
                        user_id=user_id,
                        event_type="execution.failed",
                        payload=execution_failure_payload(delivery_error),
                    )
                except Exception:
                    pass
        else:
            async with self.session_factory.begin() as session:
                execution = await session.get(SandboxExecutionModel, execution_id, with_for_update=True)
                if execution is not None:
                    execution.status = status
                    execution.exit_reason = reason
                    execution.completed_at = _utc_now()
                    if result is not None:
                        execution.resource_summary_json = {
                            "stdout_bytes": len(str(result.get("stdout") or "").encode("utf-8")),
                            "stderr_bytes": len(str(result.get("stderr") or "").encode("utf-8")),
                        }
                if not emit_events:
                    return None
                try:
                    if result is not None:
                        for stream, output in (("stdout", result.get("stdout")), ("stderr", result.get("stderr"))):
                            if output:
                                await self._append_event(
                                    execution_id,
                                    user_id=user_id,
                                    event_type="execution.output",
                                    payload={"stream": stream, "text": str(output)},
                                )
                    await self._append_event(
                        execution_id,
                        user_id=user_id,
                        event_type="execution.failed" if failed else "execution.completed",
                        payload={
                            "status": status,
                            **({"error": type(error).__name__} if error else {}),
                        },
                    )
                except Exception as delivery_error:
                    event_error = delivery_error
                    execution.status = "failed"
                    execution.completed_at = _utc_now()
                    execution.exit_reason = f"event delivery failed: {delivery_error}"[:128]
                    summary = dict(execution.resource_summary_json or {})
                    summary["event_delivery"] = {
                        "status": "failed",
                        "error": str(delivery_error)[:128],
                    }
                    execution.resource_summary_json = summary
                    try:
                        await self._append_event(
                            execution_id,
                            user_id=user_id,
                            event_type="execution.failed",
                            payload=execution_failure_payload(delivery_error),
                        )
                    except Exception:
                        pass
        return event_error

    async def _append_event(
        self,
        execution_id: str,
        *,
        user_id: str,
        event_type: str,
        payload: dict[str, object],
    ) -> dict[str, object]:
        return await append_event_with_retry(
            lambda: default_sandbox_event_store.append(
                execution_id,
                user_id=user_id,
                event_type=event_type,
                payload=payload,
            )
        )

    async def _verify_confirmation(
        self,
        authorized: _AuthorizedModelContext,
        *,
        tool_name: str,
        source: str,
        confirmation_token: str | None,
    ) -> bool:
        if not confirmation_token:
            return False
        code_hash = hashlib.sha256(source.encode("utf-8")).hexdigest() if source else ""
        try:
            payload = self._confirmation_signer.verify(
                confirmation_token,
                user_id=authorized.context.user_id,
                session_id=authorized.scope.auth_session_id if authorized.scope else authorized.context.session_id,
                tool_name=tool_name,
                code_hash=code_hash,
            )
        except PermissionError:
            return False
        expires_at = payload.get("e")
        if not isinstance(expires_at, int):
            return False
        return await self._confirmation_replay_store.consume(
            confirmation_token, expires_at=float(expires_at)
        )

    async def close(self) -> None:
        await self._confirmation_replay_store.close()

    @staticmethod
    def _trace(name: str, *, config: RunnableConfig | None = None, **payload: object) -> None:
        try:
            from core.observability.runtime import global_telemetry
            from core.observability.context import TelemetryContext

            global_telemetry.event(
                name,
                payload=payload,
                context=TelemetryContext.from_config(config),
            )
        except Exception:
            # Tool execution must remain usable when optional telemetry is not configured.
            return

    @staticmethod
    def _telemetry_ids(config: RunnableConfig | None) -> tuple[str | None, str | None]:
        try:
            from core.observability.context import TelemetryContext

            context = TelemetryContext.from_config(config)
            return (context.trace_id, context.span_id) if context is not None else (None, None)
        except Exception:
            return None, None

    @asynccontextmanager
    async def _tool_span(self, name: str, *, config: RunnableConfig | None = None):
        """Create a durable TOOL span when the caller supplied telemetry context.

        Local/unit-test calls intentionally remain usable without a database-backed
        telemetry runtime; production requests carry the context from the gateway.
        """
        try:
            from core.observability.context import TelemetryContext
            from core.observability.models import SpanKind
            from core.observability.runtime import global_telemetry

            context = TelemetryContext.from_config(config)
            if context is None:
                yield None
                return
            span_context = global_telemetry.span(
                SpanKind.TOOL,
                name,
                context=context,
                attributes={"sandbox": True},
            )
        except Exception:
            yield None
            return
        async with span_context as span:
            yield span

    async def status(self, *, config: RunnableConfig) -> dict[str, object]:
        authorized = await self._authorize(config)
        if authorized is None:
            return _error("not_authorized", "sandbox model context is not authenticated")
        if self.session_factory is None:
            return {"ok": True, "mode": self.mode, "runtime_available": self.mode == "inmemory"}
        from .service import sandbox_lifecycle_service

        async with self.session_factory() as session:
            assert authorized.scope is not None
            payload = await sandbox_lifecycle_service.describe(session, authorized.scope)
        return {"ok": True, "mode": self.mode, **payload}

    async def run_scratch(self, *, source: str, config: RunnableConfig) -> dict[str, object]:
        # Scratch is authenticated but independent from the user's
        # Interactive-Kernel lease.  It records a logical environment fact and
        # may run before the user opens the interactive runtime.
        authorized = await self._authorize(config)
        if authorized is None:
            return _error("not_authorized", "sandbox model context is not authenticated")
        self._trace("sandbox.model.scratch.started", config=config, code_chars=len(source))
        try:
            execution_id = await self._start_execution(authorized, source=source, config=config)
        except Exception as error:
            self._trace("sandbox.model.scratch.failed", config=config, error=type(error).__name__)
            return _error("sandbox_event_failed", str(error)[:500])
        if execution_id is None:
            return _error("lease_required", "an active Sandbox environment is required")
        try:
            async with self._tool_span("sandbox.scratch", config=config):
                if self.mode == "inmemory":
                    result = await InMemoryRuntime().execute(user_id="scratch", source=source)
                else:
                    manager = self._manager_for_docker()
                    if manager is None:
                        error = RuntimeError("Docker Sandbox Manager is not configured")
                        delivery_error = await self._finish_execution(
                            execution_id,
                            user_id=authorized.context.user_id,
                            status="failed",
                            error=error,
                        )
                        if delivery_error is not None:
                            return _error("sandbox_event_failed", str(delivery_error)[:500])
                        return _error("sandbox_unavailable", str(error))
                    trace_id, span_id = self._telemetry_ids(config)
                    result = await manager.run_scratch(
                        source=source,
                        trace_id=trace_id,
                        span_id=span_id,
                    )
        except Exception as error:
            delivery_error = await self._finish_execution(execution_id, user_id=authorized.context.user_id, status="failed", error=error)
            self._trace("sandbox.model.scratch.failed", config=config, error=type(error).__name__)
            if delivery_error is not None:
                return _error("sandbox_event_failed", str(delivery_error)[:500])
            return _error("scratch_failed", str(error)[:500])
        status = str(result.get("status", "completed"))
        delivery_error = await self._finish_execution(execution_id, user_id=authorized.context.user_id, status=status, result=result)
        if delivery_error is not None:
            self._trace("sandbox.model.scratch.failed", config=config, error=type(delivery_error).__name__)
            return _error("sandbox_event_failed", str(delivery_error)[:500], execution_id=execution_id)
        self._trace("sandbox.model.scratch.completed", config=config, status=status)
        return {"ok": True, "execution_id": execution_id, **result}

    async def run_active(
        self, *, source: str, config: RunnableConfig, confirmed: bool = False,
        confirmation_token: str | None = None,
    ) -> dict[str, object]:
        del confirmed  # Boolean input is retained for wire compatibility, never trusted.
        confirmation_context = await self._authorize(config)
        if confirmation_context is None or not await self._verify_confirmation(
            confirmation_context,
            tool_name="sandbox_run_active_kernel",
            source=source,
            confirmation_token=confirmation_token,
        ):
            return {
                "ok": False,
                "code": "confirmation_required",
                "error": "sandbox_run_active_kernel requires explicit user confirmation",
            }
        authorized = await self._authorize(config, require_lease=True)
        if authorized is None:
            return _error("lease_required", "an active authenticated Sandbox lease is required")
        if self.mode == "inmemory":
            try:
                execution_id = await self._start_execution(authorized, source=source, config=config)
            except Exception as error:
                return _error("sandbox_event_failed", str(error)[:500])
            if execution_id is None:
                return _error("lease_required", "an active Sandbox environment is required")
            try:
                async with self._tool_span("sandbox.active_kernel", config=config):
                    result = await self._interactive.execute(user_id=authorized.context.user_id, source=source)
            except Exception as error:
                delivery_error = await self._finish_execution(
                    execution_id,
                    user_id=authorized.context.user_id,
                    status="failed",
                    error=error,
                )
                if delivery_error is not None:
                    return _error("sandbox_event_failed", str(delivery_error)[:500], execution_id=execution_id)
                raise
            delivery_error = await self._finish_execution(
                execution_id,
                user_id=authorized.context.user_id,
                status=str(result.get("status") or "completed"),
                result=result,
            )
            if delivery_error is not None:
                return _error("sandbox_event_failed", str(delivery_error)[:500], execution_id=execution_id)
            return {"ok": True, "execution_id": execution_id, **result}
        manager = self._manager_for_docker()
        if manager is None or authorized.scope is None or authorized.lease_id is None:
            return _error("sandbox_unavailable", "Docker Sandbox Manager is not configured")
        claim = await manager.claim(authorized.scope, lease_id=authorized.lease_id)
        if claim is None:
            return _error("warming", "Sandbox runtime is warming; retry shortly")
        try:
            execution_id = await self._start_execution(
                authorized, source=source, runtime_id=str(claim.runtime.id), config=config
            )
        except Exception as error:
            return _error("sandbox_event_failed", str(error)[:500])
        if execution_id is None:
            return _error("lease_required", "an active Sandbox environment is required")
        ticket = self._signer.issue(
            SandboxTicketClaims(
                authorized.context.user_id,
                authorized.context.session_id,
                authorized.lease_id,
                str(claim.runtime.id),
                claim.runtime.generation,
                claim.nonce,
            )
        )
        claims = self._signer.verify(
            ticket,
            user_id=authorized.context.user_id,
            auth_session_id=authorized.context.session_id,
        )
        try:
            trace_id, span_id = self._telemetry_ids(config)
            async with self._tool_span("sandbox.active_kernel", config=config):
                result = await manager.execute_claimed(
                    authorized.scope,
                    lease_id=claims.lease_id,
                    runtime_id=claims.runtime_id,
                    generation=claims.generation,
                    nonce=claims.nonce,
                    source=source,
                    trace_id=trace_id,
                    span_id=span_id,
                )
        except Exception as error:
            delivery_error = await self._finish_execution(execution_id, user_id=authorized.context.user_id, status="failed", error=error)
            if delivery_error is not None:
                return _error("sandbox_event_failed", str(delivery_error)[:500], execution_id=execution_id)
            raise
        delivery_error = await self._finish_execution(
            execution_id,
            user_id=authorized.context.user_id,
            status=str(result.get("status") or "completed"),
            result=result,
        )
        if delivery_error is not None:
            return _error("sandbox_event_failed", str(delivery_error)[:500], execution_id=execution_id)
        return {"ok": True, "execution_id": execution_id, **result}

    async def explain_execution(self, *, execution_id: str, config: RunnableConfig) -> dict[str, object]:
        authorized = await self._authorize(config)
        if authorized is None or self.session_factory is None:
            if authorized is None:
                return _error("not_authorized", "execution explanation requires an authenticated session")
            local = self._local_executions.get(execution_id)
            if (
                local is None
                or local.get("owner_user_id") != authorized.context.user_id
                or local.get("workspace_id") != authorized.context.workspace_id
            ):
                return _error("not_found", "sandbox execution was not found")
            return {
                "ok": True,
                "execution": {
                    key: value
                    for key, value in local.items()
                    if key not in {"owner_user_id", "workspace_id"}
                },
                "events": (
                    await default_sandbox_event_store.replay(
                        execution_id, user_id=authorized.context.user_id
                    )
                )[-50:],
            }
        async with self.session_factory() as session:
            execution = await session.get(SandboxExecutionModel, execution_id)
            if (
                execution is None
                or str(execution.owner_user_id) != authorized.context.user_id
                or str(execution.workspace_id) != authorized.context.workspace_id
            ):
                return _error("not_found", "sandbox execution was not found")
            summary = {
                "id": str(execution.id),
                "status": execution.status,
                "exit_reason": execution.exit_reason,
                "runtime_instance_id": str(execution.runtime_instance_id) if execution.runtime_instance_id else None,
                "started_at": execution.started_at.isoformat() if execution.started_at else None,
                "completed_at": execution.completed_at.isoformat() if execution.completed_at else None,
                "resource_summary": execution.resource_summary_json,
                "trace_id": execution.trace_id,
                "span_id": execution.span_id,
                "parent_span_id": execution.parent_span_id,
            }
            events = await default_sandbox_event_store.replay(
                execution_id,
                user_id=authorized.context.user_id,
            )
        return {
            "ok": True,
            "execution": summary,
            "events": events[-50:],
        }

    async def interrupt_own(self, *, execution_id: str, config: RunnableConfig) -> dict[str, object]:
        if self.session_factory is None:
            authorized = await self._authorize(config)
            if authorized is None:
                return _error("not_authorized", "interrupt requires an authenticated Sandbox session")
            local = self._local_executions.get(execution_id)
            if local is None or local.get("owner_user_id") != authorized.context.user_id or local.get("status") != "running":
                return _error("not_found", "running Sandbox execution was not found")
            local.update({"status": "interrupted", "exit_reason": "user.interrupt", "completed_at": _utc_now().isoformat()})
            await default_sandbox_event_store.append(
                execution_id,
                user_id=authorized.context.user_id,
                event_type="execution.interrupted",
                payload={"reason": "user.interrupt"},
            )
            return {"ok": True, "execution_id": execution_id, "status": "interrupted"}
        authorized = await self._authorize(config, require_lease=True)
        if authorized is None or self.session_factory is None:
            return _error("not_authorized", "interrupt requires an authenticated Sandbox lease")
        async with self.session_factory() as session:
            execution = await session.get(SandboxExecutionModel, execution_id)
            if (
                execution is None
                or str(execution.owner_user_id) != authorized.context.user_id
                or execution.lease_id != authorized.lease_id
                or execution.status != "running"
                or execution.runtime_instance_id is None
            ):
                return _error("not_found", "running Sandbox execution was not found")
            runtime_id = str(execution.runtime_instance_id)
        if self.mode == "inmemory":
            return _error("unsupported", "in-memory runtime does not expose process interruption")
        manager = self._manager_for_docker()
        if manager is None:
            return _error("sandbox_unavailable", "Docker Sandbox Manager is not configured")
        try:
            trace_id, span_id = self._telemetry_ids(config)
            assert authorized.scope is not None and authorized.lease_id is not None
            await manager.interrupt_runtime(
                authorized.scope,
                lease_id=authorized.lease_id,
                runtime_id=runtime_id,
                generation=None,
                trace_id=trace_id,
                span_id=span_id,
            )
        except Exception as error:
            return _error("interrupt_failed", str(error)[:500])
        async with self.session_factory.begin() as session:
            execution = await session.get(SandboxExecutionModel, execution_id, with_for_update=True)
            if execution is not None:
                execution.status = "interrupted"
                execution.exit_reason = "user.interrupt"
                execution.completed_at = _utc_now()
        await default_sandbox_event_store.append(
            execution_id,
            user_id=authorized.context.user_id,
            event_type="execution.interrupted",
            payload={"runtime_instance_id": runtime_id},
        )
        return {"ok": True, "execution_id": execution_id, "status": "interrupted"}

    async def reset(
        self,
        *,
        config: RunnableConfig,
        confirmed: bool = False,
        confirmation_token: str | None = None,
    ) -> dict[str, object]:
        del confirmed
        confirmation_context = await self._authorize(config)
        if confirmation_context is None or not await self._verify_confirmation(
            confirmation_context,
            tool_name="sandbox_reset",
            source="",
            confirmation_token=confirmation_token,
        ):
            return {"ok": False, "code": "confirmation_required", "error": "sandbox_reset requires explicit user confirmation"}
        authorized = await self._authorize(config, require_lease=True)
        if authorized is None or authorized.runtime_id is None:
            return _error("lease_required", "an assigned Sandbox runtime is required")
        if self.mode == "inmemory":
            await self._interactive.restart(user_id=authorized.context.user_id)
            return {"ok": True, "status": "restarted"}
        manager = self._manager_for_docker()
        if manager is None:
            return _error("sandbox_unavailable", "Docker Sandbox Manager is not configured")
        trace_id, span_id = self._telemetry_ids(config)
        assert authorized.scope is not None and authorized.lease_id is not None
        await manager.reset_runtime(
            authorized.scope,
            lease_id=authorized.lease_id,
            runtime_id=authorized.runtime_id,
            generation=None,
            trace_id=trace_id,
            span_id=span_id,
        )
        return {"ok": True, "status": "restarted", "runtime_id": authorized.runtime_id}


class ExecutionIdInput(BaseModel):
    execution_id: str = Field(min_length=1, max_length=128)


class SourceInput(BaseModel):
    source: str = Field(min_length=1, max_length=20_000)


class ConfirmedSourceInput(SourceInput):
    confirmed: bool = Field(default=False, description="必须由用户明确确认高风险操作")
    confirmation_token: str | None = Field(default=None, min_length=1, max_length=4096)


class ConfirmedInput(BaseModel):
    confirmed: bool = Field(default=False, description="必须由用户明确确认高风险操作")
    confirmation_token: str | None = Field(default=None, min_length=1, max_length=4096)


@tool("sandbox_status")
async def sandbox_status(config: RunnableConfig) -> dict[str, object]:
    """读取当前用户 Sandbox 的状态、租约和运行时摘要。"""
    return await _model_sandbox_service.status(config=config)


@tool("sandbox_run_scratch", args_schema=SourceInput)
async def sandbox_run_scratch(source: str, config: RunnableConfig) -> dict[str, object]:
    """在隔离的 Model Scratch 进程执行代码，不污染用户 Interactive Kernel。"""
    return await _model_sandbox_service.run_scratch(source=source, config=config)


@tool("sandbox_explain_execution", args_schema=ExecutionIdInput)
async def sandbox_explain_execution(execution_id: str, config: RunnableConfig) -> dict[str, object]:
    """读取当前用户自己的 Sandbox 执行摘要和有限事件回放。"""
    return await _model_sandbox_service.explain_execution(execution_id=execution_id, config=config)


@tool("sandbox_interrupt_own", args_schema=ExecutionIdInput)
async def sandbox_interrupt_own(execution_id: str, config: RunnableConfig) -> dict[str, object]:
    """中断当前用户自己的运行中 Sandbox 执行。"""
    return await _model_sandbox_service.interrupt_own(execution_id=execution_id, config=config)


@tool("sandbox_run_active_kernel", args_schema=ConfirmedSourceInput)
async def sandbox_run_active_kernel(
    source: str, confirmed: bool = False, confirmation_token: str | None = None, config: RunnableConfig | None = None
) -> dict[str, object]:
    """经用户确认后在用户 Interactive Kernel 执行代码；这是高风险操作。"""
    return await _model_sandbox_service.run_active(source=source, config=config or {}, confirmed=confirmed, confirmation_token=confirmation_token)


@tool("sandbox_reset", args_schema=ConfirmedInput)
async def sandbox_reset(confirmed: bool = False, confirmation_token: str | None = None, config: RunnableConfig | None = None) -> dict[str, object]:
    """经用户确认后销毁当前 Runtime 并重建干净实例；这是高风险操作。"""
    return await _model_sandbox_service.reset(config=config or {}, confirmed=confirmed, confirmation_token=confirmation_token)


MODEL_SANDBOX_TOOLS: tuple[BaseTool, ...] = (
    sandbox_status,
    sandbox_run_scratch,
    sandbox_explain_execution,
    sandbox_interrupt_own,
    sandbox_run_active_kernel,
    sandbox_reset,
)

_model_sandbox_service = SandboxModelToolService()


def configure_model_sandbox_service(
    *,
    mode: str,
    session_factory: async_sessionmaker[AsyncSession] | None,
    manager: Any | None = None,
) -> SandboxModelToolService:
    """Bind the model tools to the app's authenticated sandbox control plane."""
    global _model_sandbox_service
    _model_sandbox_service = SandboxModelToolService(
        mode=mode,
        session_factory=session_factory,
        manager=manager,
    )
    return _model_sandbox_service
