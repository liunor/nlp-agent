"""Redis RPC boundary between the Web process and the isolated Sandbox Manager.

The Web process owns authentication and tickets, but it never imports Docker
or constructs ``WarmPoolManager``.  The Manager process consumes these signed
internal requests and is the only process allowed to execute Docker calls.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import hmac
import json
import asyncio
import time
from asyncio import Task, create_task, gather
from typing import Any
from uuid import uuid4

from configs.settings import settings

from .contracts import SandboxScope


REQUEST_STREAM = "nova:sandbox:manager:rpc:requests"
RESPONSE_PREFIX = "nova:sandbox:manager:rpc:responses:"
HANDLED_PREFIX = "nova:sandbox:manager:rpc:handled:"
RESULT_PREFIX = "nova:sandbox:manager:rpc:result:"
# A signed request must remain valid for the full 60-second Scratch ceiling
# plus queueing/transport headroom.
REQUEST_TTL_SECONDS = 90
REQUEST_CLOCK_SKEW_SECONDS = 5
# Keep takeover below the Web RPC timeout; heartbeat renewal covers healthy
# dispatches that run longer (for example a 15-second scratch execution).
RPC_PROCESSING_TTL_SECONDS = 10
RPC_RESULT_TTL_SECONDS = 600
SANDBOX_SCRATCH_MAX_TIMEOUT_SECONDS = 60
RPC_RESPONSE_HEADROOM_SECONDS = 5

_RENEW_PROCESSING_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('EXPIRE', KEYS[1], ARGV[2])
end
return 0
"""

_COMPLETE_PROCESSING_SCRIPT = """
if redis.call('GET', KEYS[1]) ~= ARGV[1] then
    return 0
end
redis.call('SET', KEYS[2], ARGV[2], 'EX', ARGV[3])
redis.call('SET', KEYS[1], 'done', 'EX', ARGV[3])
return 1
"""


@dataclass(frozen=True, slots=True)
class RemoteRuntime:
    id: str
    generation: int
    environment_id: str | None
    external_runtime_id: str | None = None


@dataclass(frozen=True, slots=True)
class RemoteRuntimeClaim:
    runtime: RemoteRuntime
    nonce: str | None


def _text(value: object) -> str:
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)


def _json(value: object) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True, default=str)


def _signature(secret: str, request_id: str, method: str, payload: str) -> str:
    message = f"{request_id}.{method}.{payload}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def _scope_payload(scope: SandboxScope) -> dict[str, object]:
    return {
        "owner_user_id": scope.owner_user_id,
        "auth_session_id": scope.auth_session_id,
        "workspace_id": scope.workspace_id,
        "generation": scope.generation,
        "lease_expires_at": scope.lease_expires_at.isoformat(),
    }


def _scope_from_payload(payload: dict[str, object]) -> SandboxScope:
    expires = datetime.fromisoformat(str(payload["lease_expires_at"]))
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)
    return SandboxScope(
        owner_user_id=str(payload["owner_user_id"]),
        auth_session_id=str(payload["auth_session_id"]),
        workspace_id=str(payload["workspace_id"]),
        generation=int(payload["generation"]),
        lease_expires_at=expires,
    )


class RedisSandboxManagerRpcClient:
    """Web-side Manager port implemented over a bounded Redis RPC stream."""

    def __init__(
        self,
        client: Any,
        *,
        secret: str,
        request_stream: str = REQUEST_STREAM,
        timeout_seconds: float = 75.0,
    ) -> None:
        self._client = client
        self._secret = secret
        self._request_stream = request_stream
        self._timeout_seconds = max(1.0, timeout_seconds)

    async def _request(self, method: str, payload: dict[str, object]) -> dict[str, object]:
        request_id = uuid4().hex
        response_stream = f"{RESPONSE_PREFIX}{request_id}"
        issued_at = time.time()
        body = _json(
            {
                **payload,
                "issued_at": issued_at,
                "expires_at": issued_at + REQUEST_TTL_SECONDS,
            }
        )
        await self._client.xadd(
            self._request_stream,
            {
                "request_id": request_id,
                "response_stream": response_stream,
                "method": method,
                "payload": body,
                "signature": _signature(self._secret, request_id, method, body),
            },
            maxlen=10_000,
            approximate=True,
        )
        deadline = time.monotonic() + self._timeout_seconds
        while time.monotonic() < deadline:
            rows = await self._client.xread({response_stream: "0-0"}, count=1, block=1_000)
            for _stream, messages in rows or ():
                for _message_id, fields in messages:
                    parsed = {_text(key): _text(value) for key, value in fields.items()}
                    if parsed.get("request_id") != request_id:
                        continue
                    if parsed.get("ok") != "1":
                        raise RuntimeError(parsed.get("error", "Sandbox Manager RPC failed"))
                    try:
                        result = json.loads(parsed.get("payload", "{}"))
                    except json.JSONDecodeError as error:
                        raise RuntimeError("Sandbox Manager returned invalid RPC JSON") from error
                    if not isinstance(result, dict):
                        raise RuntimeError("Sandbox Manager returned an invalid RPC payload")
                    return result
        raise TimeoutError(f"Sandbox Manager RPC timed out: {method}")

    async def claim(self, scope: SandboxScope, *, lease_id: str) -> RemoteRuntimeClaim | None:
        result = await self._request("claim", {"scope": _scope_payload(scope), "lease_id": lease_id})
        claim = result.get("claim")
        if claim is None:
            return None
        if not isinstance(claim, dict) or not isinstance(claim.get("runtime"), dict):
            raise RuntimeError("Sandbox Manager returned an invalid claim")
        runtime = claim["runtime"]
        return RemoteRuntimeClaim(
            runtime=RemoteRuntime(
                id=str(runtime["id"]),
                generation=int(runtime["generation"]),
                environment_id=str(runtime["environment_id"]) if runtime.get("environment_id") else None,
                external_runtime_id=str(runtime["external_runtime_id"]) if runtime.get("external_runtime_id") else None,
            ),
            nonce=str(claim["nonce"]) if claim.get("nonce") else None,
        )

    async def execute_claimed(
        self,
        scope: SandboxScope,
        *,
        lease_id: str,
        runtime_id: str,
        generation: int,
        nonce: str | None,
        source: str,
        trace_id: str | None = None,
        span_id: str | None = None,
    ) -> dict[str, object]:
        return await self._request(
            "execute_claimed",
            {
                "scope": _scope_payload(scope),
                "lease_id": lease_id,
                "runtime_id": runtime_id,
                "generation": generation,
                "nonce": nonce,
                "source": source,
                "trace_id": trace_id,
                "span_id": span_id,
            },
        )

    async def runtime_usage(
        self,
        scope: SandboxScope,
        *,
        lease_id: str,
        runtime_id: str,
        generation: int,
    ) -> dict[str, object]:
        return await self._request(
            "runtime_usage",
            {
                "scope": _scope_payload(scope),
                "lease_id": lease_id,
                "runtime_id": runtime_id,
                "generation": generation,
            },
        )

    async def reset_runtime(
        self,
        scope: SandboxScope,
        *,
        lease_id: str,
        runtime_id: str,
        generation: int | None = None,
        trace_id: str | None = None,
        span_id: str | None = None,
    ) -> None:
        await self._request(
            "reset_runtime",
            {
                "scope": _scope_payload(scope),
                "lease_id": lease_id,
                "runtime_id": runtime_id,
                "generation": generation,
                "trace_id": trace_id,
                "span_id": span_id,
            },
        )

    async def interrupt_runtime(
        self,
        scope: SandboxScope,
        *,
        lease_id: str,
        runtime_id: str,
        generation: int | None = None,
        trace_id: str | None = None,
        span_id: str | None = None,
    ) -> None:
        await self._request(
            "interrupt_runtime",
            {
                "scope": _scope_payload(scope),
                "lease_id": lease_id,
                "runtime_id": runtime_id,
                "generation": generation,
                "trace_id": trace_id,
                "span_id": span_id,
            },
        )

    async def capacity_snapshot(self) -> dict[str, object]:
        return await self._request("capacity_snapshot", {})

    async def run_scratch(
        self,
        *,
        source: str,
        timeout_seconds: int = 15,
        output_limit_bytes: int = 1_000_000,
        trace_id: str | None = None,
        span_id: str | None = None,
    ) -> dict[str, object]:
        if (
            not isinstance(timeout_seconds, int)
            or not 1 <= timeout_seconds <= SANDBOX_SCRATCH_MAX_TIMEOUT_SECONDS
        ):
            raise ValueError(
                "Sandbox Scratch timeout_seconds must be between 1 and "
                f"{SANDBOX_SCRATCH_MAX_TIMEOUT_SECONDS}"
            )
        if timeout_seconds + RPC_RESPONSE_HEADROOM_SECONDS > self._timeout_seconds:
            raise ValueError(
                "Sandbox Scratch timeout exceeds the configured Manager RPC timeout"
            )
        return await self._request(
            "run_scratch",
            {
                "source": source,
                "timeout_seconds": timeout_seconds,
                "output_limit_bytes": output_limit_bytes,
                "trace_id": trace_id,
                "span_id": span_id,
            },
        )

    async def close(self) -> None:
        close = getattr(self._client, "aclose", None)
        if close is not None:
            await close()


class RedisSandboxManagerRpcServer:
    """Manager-side dispatcher for the Redis RPC stream."""

    def __init__(self, client: Any, *, manager: Any, secret: str, request_stream: str = REQUEST_STREAM) -> None:
        self._client = client
        self._manager = manager
        self._secret = secret
        self._request_stream = request_stream
        self._cursor = "0-0"
        self._tasks: set[Task[None]] = set()

    async def _publish_response(
        self, response_stream: str, request_id: str, *, ok: str, payload: str, error: str
    ) -> None:
        await self._client.xadd(
            response_stream,
            {"request_id": request_id, "ok": ok, "payload": payload, "error": error},
            maxlen=10,
            approximate=True,
        )
        await self._client.expire(response_stream, 60)

    async def _publish_cached_response(self, response_stream: str, request_id: str) -> bool:
        get = getattr(self._client, "get", None)
        if not callable(get):
            return False
        try:
            cached = await get(f"{RESULT_PREFIX}{request_id}")
            if cached is None:
                return False
            result = json.loads(_text(cached))
            if not isinstance(result, dict):
                return False
            await self._publish_response(
                response_stream,
                request_id,
                ok=str(result.get("ok", "0")),
                payload=str(result.get("payload", "{}")),
                error=str(result.get("error", "")),
            )
            return True
        except Exception:
            return False

    async def _renew_processing_lease(self, handled_key: str, owner_token: str) -> None:
        """Keep a live dispatch lease only while this Manager owns it."""
        renew = getattr(self._client, "eval", None)
        if not callable(renew):
            return
        interval = max(1, RPC_PROCESSING_TTL_SECONDS // 3)
        while True:
            await asyncio.sleep(interval)
            try:
                renewed = await renew(
                    _RENEW_PROCESSING_SCRIPT,
                    1,
                    handled_key,
                    owner_token,
                    str(RPC_PROCESSING_TTL_SECONDS),
                )
                if not renewed:
                    return
            except Exception:
                return

    async def _complete_processing(
        self,
        handled_key: str,
        result_key: str,
        owner_token: str,
        encoded_result: str,
    ) -> bool:
        """Persist a result and mark completion only for the current owner."""
        eval_fn = getattr(self._client, "eval", None)
        if callable(eval_fn):
            try:
                completed = await eval_fn(
                    _COMPLETE_PROCESSING_SCRIPT,
                    2,
                    handled_key,
                    result_key,
                    owner_token,
                    encoded_result,
                    str(RPC_RESULT_TTL_SECONDS),
                )
                return bool(completed)
            except Exception:
                return False

        # Test doubles and older Redis-compatible clients may not expose
        # EVAL. Production Redis uses the atomic Lua branch above.
        get = getattr(self._client, "get", None)
        set_value = getattr(self._client, "set", None)
        if not callable(get) or not callable(set_value):
            return False
        try:
            if _text(await get(handled_key)) != owner_token:
                return False
            await set_value(result_key, encoded_result, ex=RPC_RESULT_TTL_SECONDS)
            if _text(await get(handled_key)) != owner_token:
                return False
            await set_value(handled_key, "done", ex=RPC_RESULT_TTL_SECONDS)
            return True
        except Exception:
            return False

    async def _wait_for_processing_recovery(
        self, fields: dict[str, str], handled_key: str
    ) -> None:
        """Take over a request if its Manager owner disappears mid-dispatch.

        Every Manager reads the request stream independently.  A duplicate
        therefore cannot simply return when another instance owns the short
        processing lease: if that owner crashes, no new stream delivery is
        guaranteed.  Keep the duplicate task alive until the lease expires,
        then retry the same signed request; a cached result wins whenever the
        original owner completed successfully.
        """
        get = getattr(self._client, "get", None)
        if not callable(get):
            return
        request_id = fields.get("request_id", "")
        response_stream = fields.get("response_stream", f"{RESPONSE_PREFIX}{request_id}")
        deadline = time.monotonic() + RPC_PROCESSING_TTL_SECONDS + REQUEST_CLOCK_SKEW_SECONDS
        while time.monotonic() < deadline:
            if await self._publish_cached_response(response_stream, request_id):
                return
            try:
                state = await get(handled_key)
            except Exception:
                return
            if state is None:
                # The owner lease expired.  Re-enter the normal path so NX
                # ownership, validation, dispatch, and result persistence are
                # applied exactly as for the first delivery.
                await self._handle(fields)
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            await asyncio.sleep(min(1.0, remaining))

    async def process_once(self, *, block_ms: int = 100) -> bool:
        # Consume a bounded batch so a Manager restart cannot spend minutes
        # replaying retained request payloads one-by-one.  Each request is
        # still protected by its signed expiry and ownership lease.
        rows = await self._client.xread({self._request_stream: self._cursor}, count=100, block=max(1, block_ms))
        if not rows:
            return False
        for _stream, messages in rows:
            for message_id, raw_fields in messages:
                self._cursor = _text(message_id)
                fields = {_text(key): _text(value) for key, value in raw_fields.items()}
                fields["_stream_message_id"] = self._cursor
                task = create_task(self._handle(fields), name=f"sandbox-manager-rpc:{fields.get('request_id', '')}")
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)
        return True

    async def _handle(self, fields: dict[str, str]) -> None:
        request_id = fields.get("request_id", "")
        response_stream = fields.get("response_stream", f"{RESPONSE_PREFIX}{request_id}")
        method = fields.get("method", "")
        body = fields.get("payload", "{}")
        dedupe = getattr(self._client, "set", None)
        handled_key = f"{HANDLED_PREFIX}{request_id}"
        owner_token = uuid4().hex
        try:
            if not hmac.compare_digest(
                fields.get("signature", ""), _signature(self._secret, request_id, method, body)
            ):
                raise PermissionError("invalid Sandbox Manager RPC signature")
        except Exception as error:
            await self._publish_response(
                response_stream,
                request_id,
                ok="0",
                payload="{}",
                error=f"{type(error).__name__}: {error}"[:500],
            )
            return

        renew_task: Task[None] | None = None
        if callable(dedupe):
            # A result may have been persisted immediately before a Manager
            # crashed while finalizing the completion marker.  Replay that
            # response first so expiry of the short processing lease cannot
            # dispatch the operation a second time.
            if await self._publish_cached_response(response_stream, request_id):
                return
            try:
                # This is a short processing lease, not completion state.
                # If the Manager dies before dispatch, another instance can
                # take over after the lease expires.
                accepted = await dedupe(
                    handled_key,
                    owner_token,
                    nx=True,
                    ex=RPC_PROCESSING_TTL_SECONDS,
                )
            except Exception as error:
                await self._publish_response(
                    response_stream,
                    request_id,
                    ok="0",
                    payload="{}",
                    error=f"RuntimeError: Sandbox Manager RPC replay guard is unavailable: {error}"[:500],
                )
                return
            if not accepted:
                # A completed request has a durable response record.  An
                # in-flight request has no response yet and is owned by
                # another Manager.  Keep this duplicate delivery around so
                # it can take over if that Manager crashes before completion.
                await self._wait_for_processing_recovery(fields, handled_key)
                return
            renew_task = create_task(
                self._renew_processing_lease(handled_key, owner_token),
                name=f"sandbox-manager-rpc-lease:{request_id}",
            )
        try:
            try:
                payload = json.loads(body)
                if not isinstance(payload, dict):
                    raise ValueError("Sandbox Manager RPC payload must be an object")
                now = time.time()
                issued_at = payload.get("issued_at")
                expires_at = payload.get("expires_at")
                if (
                    not isinstance(issued_at, (int, float))
                    or not isinstance(expires_at, (int, float))
                    or issued_at > now + REQUEST_CLOCK_SKEW_SECONDS
                    or expires_at <= now
                    or expires_at - issued_at > REQUEST_TTL_SECONDS + REQUEST_CLOCK_SKEW_SECONDS
                ):
                    raise PermissionError("expired or invalid Sandbox Manager RPC request")
                result = await self._dispatch(method, payload)
                ok, encoded, error = "1", _json(result), ""
            except Exception as exc:
                ok, encoded, error = "0", "{}", f"{type(exc).__name__}: {exc}"[:500]
            if callable(dedupe):
                completed = await self._complete_processing(
                    handled_key,
                    f"{RESULT_PREFIX}{request_id}",
                    owner_token,
                    _json({"ok": ok, "payload": encoded, "error": error}),
                )
                if not completed:
                    # A stale Manager must not publish or overwrite a result
                    # after another Manager has taken the lease. The current
                    # owner (or a later takeover) will publish the response.
                    await self._publish_cached_response(response_stream, request_id)
                    return
            await self._publish_response(response_stream, request_id, ok=ok, payload=encoded, error=error)
            message_id = fields.get("_stream_message_id")
            delete = getattr(self._client, "xdel", None)
            if message_id and callable(delete):
                try:
                    await delete(self._request_stream, message_id)
                except Exception:
                    # Retention is best-effort after the durable result is
                    # recorded; a later trim may remove any residual payload.
                    pass
        finally:
            if renew_task is not None:
                renew_task.cancel()
                await gather(renew_task, return_exceptions=True)

    async def _dispatch(self, method: str, payload: dict[str, object]) -> dict[str, object]:
        trace = getattr(self._manager, "_trace", None)
        if trace is not None and payload.get("trace_id"):
            trace(
                "sandbox.manager.rpc.dispatch",
                trace_id=str(payload["trace_id"]),
                span_id=str(payload.get("span_id") or ""),
                method=method,
            )
        if method == "claim":
            claim = await self._manager.claim(_scope_from_payload(payload["scope"]), lease_id=str(payload["lease_id"]))
            if claim is None:
                return {"claim": None}
            runtime = claim.runtime
            return {
                "claim": {
                    "runtime": {
                        "id": str(runtime.id),
                        "generation": runtime.generation,
                        "environment_id": str(runtime.environment_id) if runtime.environment_id else None,
                        "external_runtime_id": runtime.external_runtime_id,
                    },
                    "nonce": claim.nonce,
                }
            }
        if method == "execute_claimed":
            return await self._manager.execute_claimed(
                _scope_from_payload(payload["scope"]),
                lease_id=str(payload["lease_id"]),
                runtime_id=str(payload["runtime_id"]),
                generation=int(payload["generation"]),
                nonce=str(payload["nonce"]) if payload.get("nonce") else None,
                source=str(payload["source"]),
                trace_id=str(payload["trace_id"]) if payload.get("trace_id") else None,
                span_id=str(payload["span_id"]) if payload.get("span_id") else None,
            )
        if method == "runtime_usage":
            return await self._manager.runtime_usage(
                _scope_from_payload(payload["scope"]),
                lease_id=str(payload["lease_id"]),
                runtime_id=str(payload["runtime_id"]),
                generation=int(payload["generation"]),
            )
        if method == "reset_runtime":
            await self._manager.reset_runtime(
                _scope_from_payload(payload["scope"]),
                lease_id=str(payload["lease_id"]),
                runtime_id=str(payload["runtime_id"]),
                generation=int(payload["generation"]) if payload.get("generation") is not None else None,
                trace_id=str(payload["trace_id"]) if payload.get("trace_id") else None,
                span_id=str(payload["span_id"]) if payload.get("span_id") else None,
            )
            return {"ok": True}
        if method == "interrupt_runtime":
            await self._manager.interrupt_runtime(
                _scope_from_payload(payload["scope"]),
                lease_id=str(payload["lease_id"]),
                runtime_id=str(payload["runtime_id"]),
                generation=int(payload["generation"]) if payload.get("generation") is not None else None,
                trace_id=str(payload["trace_id"]) if payload.get("trace_id") else None,
                span_id=str(payload["span_id"]) if payload.get("span_id") else None,
            )
            return {"ok": True}
        if method == "capacity_snapshot":
            return dict(await self._manager.capacity_snapshot())
        if method == "run_scratch":
            timeout_seconds = payload.get("timeout_seconds", 15)
            if (
                type(timeout_seconds) is not int
                or not 1 <= timeout_seconds <= SANDBOX_SCRATCH_MAX_TIMEOUT_SECONDS
            ):
                raise ValueError(
                    "Sandbox Scratch timeout_seconds must be between 1 and "
                    f"{SANDBOX_SCRATCH_MAX_TIMEOUT_SECONDS}"
                )
            return await self._manager.run_scratch(
                source=str(payload["source"]),
                timeout_seconds=timeout_seconds,
                output_limit_bytes=int(payload.get("output_limit_bytes", 1_000_000)),
                trace_id=str(payload["trace_id"]) if payload.get("trace_id") else None,
                span_id=str(payload["span_id"]) if payload.get("span_id") else None,
            )
        raise ValueError(f"unsupported Sandbox Manager RPC method: {method}")

    async def close(self) -> None:
        if self._tasks:
            await gather(*self._tasks, return_exceptions=True)
        close = getattr(self._client, "aclose", None)
        if close is not None:
            await close()


def create_sandbox_manager_rpc_client() -> RedisSandboxManagerRpcClient | None:
    redis_url = settings.NLP_AGENT_REDIS_URL.strip()
    secret = settings.NLP_AGENT_WEB_SECRET.strip()
    if not redis_url or not secret:
        return None
    from redis.asyncio import Redis

    return RedisSandboxManagerRpcClient(
        Redis.from_url(redis_url, decode_responses=True),
        secret=secret,
        timeout_seconds=settings.NLP_AGENT_SANDBOX_MANAGER_RPC_TIMEOUT_S,
    )


def create_sandbox_manager_rpc_server(manager: Any) -> RedisSandboxManagerRpcServer | None:
    redis_url = settings.NLP_AGENT_REDIS_URL.strip()
    secret = settings.NLP_AGENT_WEB_SECRET.strip()
    if not redis_url or not secret:
        return None
    from redis.asyncio import Redis

    return RedisSandboxManagerRpcServer(
        Redis.from_url(redis_url, decode_responses=True), manager=manager, secret=secret
    )
