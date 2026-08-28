from __future__ import annotations

import asyncio
import json
import time

import pytest


def test_manager_rpc_client_round_trips_signed_request() -> None:
    from server.sandbox.manager_rpc import RedisSandboxManagerRpcClient

    class FakeRedis:
        def __init__(self) -> None:
            self.response_stream = ""

        async def xadd(self, stream, fields, **_kwargs):
            if stream.startswith("nova:sandbox:manager:rpc:responses:"):
                self.response_stream = stream
            else:
                self.response_stream = fields["response_stream"]

        async def xread(self, streams, **_kwargs):
            stream = next(iter(streams))
            if stream != self.response_stream:
                return []
            return [
                (
                    stream,
                    [
                        (
                            "1-0",
                            {
                                "request_id": stream.rsplit(":", 1)[-1],
                                "ok": "1",
                                "payload": json.dumps({"status": "completed"}),
                                "error": "",
                            },
                        )
                    ],
                )
            ]

    async def exercise() -> dict[str, object]:
        client = RedisSandboxManagerRpcClient(FakeRedis(), secret="rpc-secret", timeout_seconds=20)
        return await client.run_scratch(source="print(1)")

    assert asyncio.run(exercise()) == {"status": "completed"}


def test_manager_rpc_scratch_timeout_requires_response_budget() -> None:
    from server.sandbox.manager_rpc import RedisSandboxManagerRpcClient

    class FakeRedis:
        def __init__(self) -> None:
            self.response_stream = ""

        async def xadd(self, stream, fields, **_kwargs):
            self.response_stream = stream if stream.startswith("nova:sandbox:manager:rpc:responses:") else fields["response_stream"]

        async def xread(self, streams, **_kwargs):
            stream = next(iter(streams))
            if stream != self.response_stream:
                return []
            return [(stream, [("1-0", {"request_id": stream.rsplit(":", 1)[-1], "ok": "1", "payload": "{}", "error": ""})])]

    async def exercise() -> dict[str, object]:
        client = RedisSandboxManagerRpcClient(FakeRedis(), secret="rpc-secret", timeout_seconds=64)
        with pytest.raises(ValueError, match="RPC timeout"):
            await client.run_scratch(source="print(1)", timeout_seconds=60)
        return await client.run_scratch(source="print(1)", timeout_seconds=59)

    assert asyncio.run(exercise()) == {}


def test_manager_rpc_server_dispatches_claim_without_docker_in_web_process() -> None:
    from datetime import UTC, datetime, timedelta
    from server.sandbox.contracts import SandboxScope
    from server.sandbox.manager_rpc import RedisSandboxManagerRpcServer

    class FakeManager:
        async def claim(self, _scope, *, lease_id):
            assert lease_id == "lease-1"
            from server.sandbox.manager_rpc import RemoteRuntime, RemoteRuntimeClaim

            return RemoteRuntimeClaim(
                runtime=RemoteRuntime("runtime-1", 4, "environment-1", "docker-1"),
                nonce="nonce-1",
            )

    class FakeRedis:
        def __init__(self) -> None:
            self.done = False
            self.response: dict[str, str] | None = None

        async def xread(self, _streams, **_kwargs):
            if self.done:
                return []
            self.done = True
            scope = SandboxScope(
                owner_user_id="user-1",
                auth_session_id="session-1",
                workspace_id="workspace-1",
                generation=1,
                lease_expires_at=datetime.now(UTC) + timedelta(minutes=5),
            )
            from server.sandbox.manager_rpc import _json, _scope_payload, _signature

            now = time.time()
            payload = _json({
                "scope": _scope_payload(scope),
                "lease_id": "lease-1",
                "issued_at": now,
                "expires_at": now + 60,
            })
            return [
                (
                    "nova:sandbox:manager:rpc:requests",
                    [
                        (
                            "1-0",
                            {
                                "request_id": "request-1",
                                "response_stream": "nova:sandbox:manager:rpc:responses:request-1",
                                "method": "claim",
                                "payload": payload,
                                "signature": _signature("rpc-secret", "request-1", "claim", payload),
                            },
                        )
                    ],
                )
            ]

        async def xadd(self, _stream, fields, **_kwargs):
            self.response = fields

        async def expire(self, *_args):
            return True

    async def exercise() -> dict[str, str]:
        redis = FakeRedis()
        server = RedisSandboxManagerRpcServer(redis, manager=FakeManager(), secret="rpc-secret")
        assert await server.process_once(block_ms=1)
        await asyncio.sleep(0)
        assert redis.response is not None
        return redis.response

    response = asyncio.run(exercise())
    assert response["ok"] == "1"
    assert json.loads(response["payload"])["claim"]["runtime"]["generation"] == 4


def test_manager_rpc_server_rejects_expired_requests_after_restart() -> None:
    """A restarted Manager must not replay stale requests from the stream."""
    from server.sandbox.manager_rpc import RedisSandboxManagerRpcServer

    class FakeManager:
        called = False

        async def capacity_snapshot(self):
            self.called = True
            return {"target": 1}

    class FakeRedis:
        def __init__(self) -> None:
            self.response: dict[str, str] | None = None

        async def xadd(self, _stream, fields, **_kwargs):
            self.response = fields

        async def expire(self, *_args):
            return True

    async def exercise() -> dict[str, str]:
        from server.sandbox.manager_rpc import _json, _signature

        request_id = "expired-request"
        body = _json({
            "issued_at": time.time() - 120,
            "expires_at": time.time() - 60,
        })
        fields = {
            "request_id": request_id,
            "response_stream": "nova:sandbox:manager:rpc:responses:expired-request",
            "method": "capacity_snapshot",
            "payload": body,
            "signature": _signature("rpc-secret", request_id, "capacity_snapshot", body),
        }
        redis = FakeRedis()
        manager = FakeManager()
        server = RedisSandboxManagerRpcServer(redis, manager=manager, secret="rpc-secret")
        await server._handle(fields)
        assert manager.called is False
        assert redis.response is not None
        return redis.response

    response = asyncio.run(exercise())
    assert response["ok"] == "0"
    assert "expired" in response["error"]


@pytest.mark.parametrize("redis_result", (False, None))
def test_duplicate_manager_rpc_request_does_not_publish_a_competing_error(redis_result) -> None:
    from server.sandbox.manager_rpc import (
        RedisSandboxManagerRpcServer,
        _json,
        _signature,
    )

    class FakeManager:
        called = False

    class FakeRedis:
        def __init__(self) -> None:
            self.responses = 0

        async def set(self, *_args, **_kwargs):
            return redis_result

        async def xadd(self, stream, _fields, **_kwargs):
            if stream.startswith("nova:sandbox:manager:rpc:responses:"):
                self.responses += 1

        async def expire(self, *_args):
            return True

    async def exercise() -> int:
        request_id = "duplicate-request"
        body = _json({"issued_at": time.time(), "expires_at": time.time() + 30})
        fields = {
            "request_id": request_id,
            "response_stream": f"nova:sandbox:manager:rpc:responses:{request_id}",
            "method": "capacity_snapshot",
            "payload": body,
            "signature": _signature("rpc-secret", request_id, "capacity_snapshot", body),
        }
        redis = FakeRedis()
        server = RedisSandboxManagerRpcServer(redis, manager=FakeManager(), secret="rpc-secret")
        await server._handle(fields)
        return redis.responses

    assert asyncio.run(exercise()) == 0


def test_manager_rpc_persists_result_after_dispatch_for_crash_recovery() -> None:
    from server.sandbox.manager_rpc import (
        RPC_PROCESSING_TTL_SECONDS,
        RedisSandboxManagerRpcServer,
        _json,
        _signature,
    )

    class FakeManager:
        def __init__(self, order: list[tuple[str, str]]) -> None:
            self.order = order

        async def capacity_snapshot(self):
            self.order.append(("dispatch", "capacity_snapshot"))
            return {"target": 2}

    class FakeRedis:
        def __init__(self) -> None:
            self.order: list[tuple[str, str]] = []
            self.state: dict[str, str] = {}
            self.responses = 0

        async def set(self, key, value, *, nx=False, ex=None):
            self.order.append(("set", f"{key}|{value}|{ex}"))
            if nx and key in self.state:
                return False
            self.state[key] = str(value)
            return True

        async def get(self, key):
            return self.state.get(key)

        async def xadd(self, stream, _fields, **_kwargs):
            if stream.startswith("nova:sandbox:manager:rpc:responses:"):
                self.responses += 1

        async def expire(self, *_args):
            return True

    async def exercise() -> FakeRedis:
        request_id = "recoverable-request"
        now = time.time()
        body = _json({"issued_at": now, "expires_at": now + 30})
        fields = {
            "request_id": request_id,
            "response_stream": f"nova:sandbox:manager:rpc:responses:{request_id}",
            "method": "capacity_snapshot",
            "payload": body,
            "signature": _signature("rpc-secret", request_id, "capacity_snapshot", body),
        }
        redis = FakeRedis()
        server = RedisSandboxManagerRpcServer(
            redis, manager=FakeManager(redis.order), secret="rpc-secret"
        )
        await server._handle(fields)
        return redis

    redis = asyncio.run(exercise())
    kinds = [kind for kind, _value in redis.order]
    assert kinds == ["set", "dispatch", "set", "set"]
    assert redis.order[0][1].rsplit("|", 2)[1] != "processing"
    assert f"|{RPC_PROCESSING_TTL_SECONDS}" in redis.order[0][1]
    assert ":result:" in redis.order[2][1]
    assert "|done|600" in redis.order[3][1]
    assert redis.responses == 1


def test_manager_rpc_replays_cached_result_without_redispatch() -> None:
    from server.sandbox.manager_rpc import RedisSandboxManagerRpcServer, _json, _signature

    class FakeManager:
        calls = 0

        async def capacity_snapshot(self):
            self.calls += 1
            return {"target": 2}

    class FakeRedis:
        def __init__(self) -> None:
            self.values: dict[str, str] = {}
            self.responses: list[dict[str, str]] = []

        async def get(self, key):
            return self.values.get(key)

        async def set(self, key, value, *, nx=False, ex=None):
            del ex
            if nx and key in self.values:
                return False
            self.values[key] = str(value)
            return True

        async def xadd(self, stream, fields, **_kwargs):
            if stream.startswith("nova:sandbox:manager:rpc:responses:"):
                self.responses.append(fields)

        async def expire(self, *_args):
            return True

    async def exercise() -> tuple[FakeManager, FakeRedis]:
        request_id = "cached-request"
        now = time.time()
        body = _json({"issued_at": now, "expires_at": now + 30})
        fields = {
            "request_id": request_id,
            "response_stream": f"nova:sandbox:manager:rpc:responses:{request_id}",
            "method": "capacity_snapshot",
            "payload": body,
            "signature": _signature("rpc-secret", request_id, "capacity_snapshot", body),
        }
        redis = FakeRedis()
        manager = FakeManager()
        server = RedisSandboxManagerRpcServer(redis, manager=manager, secret="rpc-secret")
        await server._handle(fields)
        await server._handle(fields)
        return manager, redis

    manager, redis = asyncio.run(exercise())
    assert manager.calls == 1
    assert len(redis.responses) == 2
    assert all(response["ok"] == "1" for response in redis.responses)


def test_manager_rpc_duplicate_takes_over_after_processing_owner_expires() -> None:
    from server.sandbox.manager_rpc import (
        HANDLED_PREFIX,
        RedisSandboxManagerRpcServer,
        RESULT_PREFIX,
        _json,
        _signature,
    )

    class FakeManager:
        calls = 0

        async def capacity_snapshot(self):
            self.calls += 1
            return {"target": 2}

    class FakeRedis:
        def __init__(self) -> None:
            self.values = {f"{HANDLED_PREFIX}takeover-request": "processing"}
            self.processing_reads = 0
            self.responses: list[dict[str, str]] = []

        async def get(self, key):
            if key.startswith(HANDLED_PREFIX):
                self.processing_reads += 1
                if self.processing_reads == 2:
                    self.values.pop(key, None)
            return self.values.get(key)

        async def set(self, key, value, *, nx=False, ex=None):
            del ex
            if nx and key in self.values:
                return False
            self.values[key] = str(value)
            return True

        async def xadd(self, stream, fields, **_kwargs):
            if stream.startswith("nova:sandbox:manager:rpc:responses:"):
                self.responses.append(fields)

        async def expire(self, *_args):
            return True

    async def exercise() -> tuple[FakeManager, FakeRedis]:
        request_id = "takeover-request"
        now = time.time()
        body = _json({"issued_at": now, "expires_at": now + 30})
        fields = {
            "request_id": request_id,
            "response_stream": f"nova:sandbox:manager:rpc:responses:{request_id}",
            "method": "capacity_snapshot",
            "payload": body,
            "signature": _signature("rpc-secret", request_id, "capacity_snapshot", body),
        }
        redis = FakeRedis()
        manager = FakeManager()
        server = RedisSandboxManagerRpcServer(redis, manager=manager, secret="rpc-secret")
        await server._handle(fields)
        return manager, redis

    manager, redis = asyncio.run(exercise())
    assert manager.calls == 1
    assert len(redis.responses) == 1
    assert redis.responses[0]["ok"] == "1"
    assert any(key.startswith(RESULT_PREFIX) for key in redis.values)


def test_manager_rpc_does_not_publish_after_processing_owner_is_fenced() -> None:
    from server.sandbox.manager_rpc import HANDLED_PREFIX, RedisSandboxManagerRpcServer, _json, _signature

    class FakeRedis:
        def __init__(self) -> None:
            self.values: dict[str, str] = {}
            self.responses: list[dict[str, str]] = []

        async def set(self, key, value, *, nx=False, ex=None):
            del ex
            if nx and key in self.values:
                return False
            self.values[key] = str(value)
            return True

        async def get(self, key):
            return self.values.get(key)

        async def eval(self, script, numkeys, *args):
            assert numkeys == 2
            handled_key, result_key = args[:2]
            owner, encoded, ttl = args[2:]
            del script, ttl
            if self.values.get(handled_key) != owner:
                return 0
            self.values[result_key] = encoded
            self.values[handled_key] = "done"
            return 1

        async def xadd(self, stream, fields, **_kwargs):
            if stream.startswith("nova:sandbox:manager:rpc:responses:"):
                self.responses.append(fields)

        async def expire(self, *_args):
            return True

    class FakeManager:
        def __init__(self, redis_client: FakeRedis) -> None:
            self.redis = redis_client

        async def capacity_snapshot(self):
            handled = next(key for key in self.redis.values if key.startswith(HANDLED_PREFIX))
            self.redis.values[handled] = "owner-from-another-manager"
            return {"target": 2}

    async def exercise() -> tuple[FakeRedis, FakeManager]:
        request_id = "fenced-request"
        now = time.time()
        body = _json({"issued_at": now, "expires_at": now + 30})
        fields = {
            "request_id": request_id,
            "response_stream": f"nova:sandbox:manager:rpc:responses:{request_id}",
            "method": "capacity_snapshot",
            "payload": body,
            "signature": _signature("rpc-secret", request_id, "capacity_snapshot", body),
        }
        redis = FakeRedis()
        manager = FakeManager(redis)
        server = RedisSandboxManagerRpcServer(redis, manager=manager, secret="rpc-secret")
        await server._handle(fields)
        return redis, manager

    redis, _manager = asyncio.run(exercise())
    assert redis.responses == []


def test_manager_rpc_renewal_uses_owner_fenced_cas(monkeypatch) -> None:
    import server.sandbox.manager_rpc as manager_rpc

    class FakeRedis:
        def __init__(self) -> None:
            self.eval_call: tuple[object, ...] | None = None

        async def eval(self, *args):
            self.eval_call = args
            return 0

    async def immediate_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(manager_rpc.asyncio, "sleep", immediate_sleep)
    redis = FakeRedis()
    server = manager_rpc.RedisSandboxManagerRpcServer(
        redis, manager=object(), secret="rpc-secret"
    )
    asyncio.run(server._renew_processing_lease("handled-key", "owner-token"))

    assert redis.eval_call is not None
    script, numkeys, handled_key, owner_token, ttl = redis.eval_call
    assert numkeys == 1
    assert handled_key == "handled-key"
    assert owner_token == "owner-token"
    assert str(ttl) == str(manager_rpc.RPC_PROCESSING_TTL_SECONDS)
    assert "GET" in script
    assert "EXPIRE" in script


def test_manager_rpc_removes_completed_source_payload_from_request_stream() -> None:
    from server.sandbox.manager_rpc import RedisSandboxManagerRpcServer, _json, _signature

    class FakeManager:
        async def capacity_snapshot(self):
            return {"target": 1}

    class FakeRedis:
        def __init__(self) -> None:
            self.values: dict[str, str] = {}
            self.deleted: list[tuple[str, str]] = []

        async def get(self, key):
            return self.values.get(key)

        async def set(self, key, value, *, nx=False, ex=None):
            if nx and key in self.values:
                return False
            self.values[key] = str(value)
            return True

        async def xadd(self, *_args, **_kwargs):
            return "1-0"

        async def expire(self, *_args):
            return True

        async def xdel(self, stream, message_id):
            self.deleted.append((stream, message_id))
            return 1

    async def exercise() -> list[tuple[str, str]]:
        request_id = "source-retention-request"
        body = _json({"issued_at": time.time(), "expires_at": time.time() + 30})
        fields = {
            "request_id": request_id,
            "response_stream": f"nova:sandbox:manager:rpc:responses:{request_id}",
            "method": "capacity_snapshot",
            "payload": body,
            "signature": _signature("rpc-secret", request_id, "capacity_snapshot", body),
            "_stream_message_id": "17-1",
        }
        redis = FakeRedis()
        server = RedisSandboxManagerRpcServer(redis, manager=FakeManager(), secret="rpc-secret")
        await server._handle(fields)
        return redis.deleted

    assert asyncio.run(exercise()) == [("nova:sandbox:manager:rpc:requests", "17-1")]
