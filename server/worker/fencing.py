"""Worker execution seam that makes MySQL leases authoritative."""

from __future__ import annotations

import asyncio
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from gateway.dispatch import TurnTask
from core.rbac import Permission, authorization_service
from core.rbac import required_permission_for_high_risk_tool
from core.identity import AuthenticatedPrincipal
from server.rbac.service import rbac_service
from server.application.turn_reliability import (
    CancelledTurnClaim,
    TurnCancellationRequested,
    TurnReliabilityService,
)


@dataclass(frozen=True)
class TurnExecutionContext:
    """Fence values that every side-effecting execution boundary must carry."""

    turn_id: str
    claim_generation: int
    operation_id: str = "turn.execution"
    principal: AuthenticatedPrincipal | None = None
    workspace_id: str | None = None
    worker_id: str | None = None

    def require(self, permission: Permission) -> None:
        """Second authorization seam for tools/checkpoint operations in Worker."""
        if self.principal is None:
            raise PermissionError("worker execution principal is missing")
        authorization_service.require(self.principal, permission, workspace_id=self.workspace_id)

    def require_high_risk_tool(self, tool_name: str) -> None:
        self.require(required_permission_for_high_risk_tool(tool_name))


_current_turn_execution_context: ContextVar[TurnExecutionContext | None] = ContextVar(
    "current_turn_execution_context", default=None
)


def current_turn_execution_context() -> TurnExecutionContext | None:
    """Return the lease identity inherited by this execution and child tasks."""

    return _current_turn_execution_context.get()


class FencedTurnExecutor:
    """Claim, heartbeat and execute one Turn without exposing lease mechanics."""

    _CANCEL_DRAIN_TIMEOUT_S = 0.25

    def __init__(
        self,
        unit_of_work_factory: Any,
        reliability: TurnReliabilityService,
        execute: Any,
        *,
        worker_id: str,
        lease_s: int,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._reliability = reliability
        self._execute = execute
        self._worker_id = worker_id
        self._lease_s = max(3, lease_s)

    async def __call__(self, task: TurnTask) -> bool:
        async with self._unit_of_work_factory.begin() as unit_of_work:
            generation = await self._reliability.claim_turn(
                unit_of_work.session,
                turn_id=task.turn_id,
                worker_id=self._worker_id,
                lease_s=self._lease_s,
                user_id=task.context.user_id,
                workspace_id=task.context.workspace_id,
            )
            if isinstance(generation, CancelledTurnClaim):
                await unit_of_work.commit()
                raise TurnCancellationRequested(task.turn_id)
            if generation is None:
                return False
            if task.authorization is None:
                raise PermissionError("worker task lacks authorization context")
            if task.authorization.submitter_user_id != task.context.user_id:
                raise PermissionError("worker authorization user mismatch")
            if task.authorization.workspace_id != task.context.workspace_id:
                raise PermissionError("worker task workspace mismatch")
            principal = await rbac_service.principal_for_user_id(
                unit_of_work.session, task.authorization.submitter_user_id
            )
            if principal.authorization_version != task.authorization.authorization_version:
                raise PermissionError("submitter authorization has changed")
            if task.authorization.workspace_id != task.context.workspace_id:
                raise PermissionError("worker authorization workspace mismatch")
            authorization_service.require(
                principal, Permission.AGENT_TURN_SUBMIT,
                workspace_id=task.authorization.workspace_id,
            )
            await rbac_service.audit(
                unit_of_work.session, actor_user_id=principal.user_id,
                target_user_id=None, decision="allow", reason_code="worker_turn_authorized",
                permission_code=Permission.AGENT_TURN_SUBMIT.value, resource_type="turn",
                resource_id=task.turn_id,
            )
            await unit_of_work.commit()

        heartbeat = asyncio.create_task(
            self._heartbeat(task.turn_id, generation), name=f"turn-lease:{task.turn_id}"
        )
        execution_context = TurnExecutionContext(
            turn_id=task.turn_id,
            claim_generation=generation,
            worker_id=self._worker_id,
            principal=principal,
            workspace_id=task.authorization.workspace_id,
        )
        execution = asyncio.create_task(
            self._execute_with_context(task, execution_context),
            name=f"turn-execution:{task.turn_id}",
        )
        try:
            completed, _pending = await asyncio.wait(
                {execution, heartbeat},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if heartbeat in completed:
                error = heartbeat.exception()
                if error is None:
                    raise RuntimeError("turn lease heartbeat stopped unexpectedly")
                raise error
            await execution
            return True
        finally:
            if not execution.done():
                execution.cancel()
                try:
                    await asyncio.wait_for(
                        asyncio.shield(execution), timeout=self._CANCEL_DRAIN_TIMEOUT_S
                    )
                except asyncio.TimeoutError:
                    execution.add_done_callback(self._consume_task)
                except asyncio.CancelledError:
                    if not execution.done():
                        execution.add_done_callback(self._consume_task)
                else:
                    self._consume_task(execution)
            else:
                self._consume_task(execution)
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)

    async def _execute_with_context(
        self, task: TurnTask, context: TurnExecutionContext
    ) -> Any:
        token = _current_turn_execution_context.set(context)
        try:
            return await self._execute(task, context)
        finally:
            _current_turn_execution_context.reset(token)

    @staticmethod
    def _consume_task(task: asyncio.Future[Any]) -> None:
        if task.cancelled():
            return
        try:
            task.exception()
        except asyncio.CancelledError:
            return
        except Exception:
            return

    async def _heartbeat(self, turn_id: str, generation: int) -> None:
        while True:
            await asyncio.sleep(self._lease_s / 3)
            async with self._unit_of_work_factory.begin() as unit_of_work:
                active = await self._reliability.heartbeat(
                    unit_of_work.session,
                    turn_id=turn_id,
                    generation=generation,
                    worker_id=self._worker_id,
                    lease_s=self._lease_s,
                )
                await unit_of_work.commit()
                if not active:
                    raise TurnCancellationRequested(turn_id)
