import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_core.tools import StructuredTool
from pydantic import BaseModel

from core.rbac import Permission, required_permission_for_high_risk_tool
from core.tool_runtime import (
    ToolDescriptor,
    ToolGrantRequest,
    ToolLockScope,
    ToolRetryPolicy,
    ToolRisk,
    ToolRuntime,
    ToolScope,
    ToolSource,
)
from core.tool_safety import ToolAuditLog, ToolAuthorizationManager
from core.mcp_runtime import MCPRuntime, _Connection
from core.tool_config import MCPServerConfig
from core.tool_runtime import ToolCatalog


class ValueInput(BaseModel):
    value: str


def test_tool_risk_defines_critical_for_fail_closed_registration() -> None:
    assert {item.value for item in ToolRisk} >= {"low", "medium", "high", "critical"}
    assert ToolRisk.CRITICAL in {ToolRisk.HIGH, ToolRisk.CRITICAL}


def test_high_risk_mcp_tools_use_the_tool_config_permission() -> None:
    assert (
        required_permission_for_high_risk_tool("mcp_github_delete")
        == Permission.SYSTEM_TOOL_CONFIG_MANAGE
    )


def make_descriptor(
    name,
    coroutine,
    *,
    read_only=False,
    idempotent=False,
    risk=ToolRisk.LOW,
    lock_scope=ToolLockScope.NONE,
    max_concurrency=0,
    retry=None,
):
    def factory():
        return StructuredTool.from_function(
            coroutine=coroutine,
            name=name,
            description=f"test tool {name}",
            args_schema=ValueInput,
        )

    return ToolDescriptor(
        name=name,
        description=f"test tool {name}",
        source=ToolSource.CUSTOM,
        provider="tests",
        scopes=frozenset({ToolScope.WORKER}),
        capabilities=frozenset({f"test.{name}"}),
        read_only=read_only,
        idempotent=idempotent,
        risk=risk,
        lock_scope=lock_scope,
        max_concurrency=max_concurrency,
        retry=retry or ToolRetryPolicy(),
        factory=factory,
    )


def runtime(tmp_path: Path):
    authorization = ToolAuthorizationManager()
    audit = ToolAuditLog(tmp_path / "audit")
    return ToolRuntime(authorization=authorization, audit_log=audit), authorization, audit


@pytest.mark.asyncio
async def test_read_only_network_failure_retries_and_audits_without_values(tmp_path):
    calls = 0

    async def flaky(value: str):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ConnectionError("temporary network failure")
        return f"ok:{value}"

    tools_runtime, _authorization, audit = runtime(tmp_path)
    tools_runtime.catalog.register(
        make_descriptor(
            "flaky",
            flaky,
            read_only=True,
            retry=ToolRetryPolicy(max_attempts=3, base_delay_s=0),
        )
    )
    tools = tools_runtime.build_toolset(
        ToolGrantRequest(
            role=ToolScope.WORKER,
            session_id="retry-session",
            allowed_tools={"flaky"},
        )
    )
    result = await tools.execute("flaky", {"value": "secret-value"})
    assert result.ok and result.attempts == 2 and calls == 2
    events = audit.recent(session_id="retry-session")
    assert [event.phase for event in events] == ["attempt", "retry", "attempt", "completed"]
    assert all(event.argument_keys == ("value",) for event in events)
    audit_text = "".join(
        path.read_text(encoding="utf-8") for path in (tmp_path / "audit").glob("*.jsonl")
    )
    assert "secret-value" not in audit_text


def test_non_idempotent_write_tool_cannot_enable_retries():
    async def write(value: str):
        return value

    with pytest.raises(ValueError, match="idempotent"):
        make_descriptor(
            "write",
            write,
            retry=ToolRetryPolicy(max_attempts=2),
        )


@pytest.mark.asyncio
async def test_global_exclusive_lock_serializes_different_sessions(tmp_path):
    active = 0
    maximum = 0

    async def exclusive(value: str):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(0.02)
        active -= 1
        return value

    tools_runtime, _authorization, _audit = runtime(tmp_path)
    tools_runtime.catalog.register(
        make_descriptor("exclusive", exclusive, lock_scope=ToolLockScope.GLOBAL)
    )
    first = tools_runtime.build_toolset(
        ToolGrantRequest(role=ToolScope.WORKER, session_id="one", allowed_tools={"exclusive"})
    )
    second = tools_runtime.build_toolset(
        ToolGrantRequest(role=ToolScope.WORKER, session_id="two", allowed_tools={"exclusive"})
    )
    await asyncio.gather(
        first.execute("exclusive", {"value": "a"}),
        second.execute("exclusive", {"value": "b"}),
    )
    assert maximum == 1


@pytest.mark.asyncio
async def test_session_lock_allows_parallel_calls_in_different_sessions(tmp_path):
    active = 0
    maximum = 0

    async def session_locked(value: str):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(0.02)
        active -= 1
        return value

    tools_runtime, _authorization, _audit = runtime(tmp_path)
    tools_runtime.catalog.register(
        make_descriptor("session_locked", session_locked, lock_scope=ToolLockScope.SESSION)
    )
    first = tools_runtime.build_toolset(
        ToolGrantRequest(role=ToolScope.WORKER, session_id="one", allowed_tools={"session_locked"})
    )
    second = tools_runtime.build_toolset(
        ToolGrantRequest(role=ToolScope.WORKER, session_id="two", allowed_tools={"session_locked"})
    )
    await asyncio.gather(
        first.execute("session_locked", {"value": "a"}),
        second.execute("session_locked", {"value": "b"}),
    )
    assert maximum == 2


@pytest.mark.asyncio
async def test_max_concurrency_is_shared_across_toolsets(tmp_path):
    active = 0
    maximum = 0

    async def limited(value: str):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(0.02)
        active -= 1
        return value

    tools_runtime, _authorization, _audit = runtime(tmp_path)
    tools_runtime.catalog.register(
        make_descriptor("limited", limited, read_only=True, max_concurrency=2)
    )
    tools = tools_runtime.build_toolset(
        ToolGrantRequest(role=ToolScope.WORKER, session_id="limit", allowed_tools={"limited"})
    )
    await asyncio.gather(
        *(tools.execute("limited", {"value": str(index)}) for index in range(5))
    )
    assert maximum == 2


@pytest.mark.asyncio
async def test_high_risk_tool_requires_live_session_grant_at_resolve_and_execute(tmp_path):
    async def dangerous(value: str):
        return value

    tools_runtime, authorization, _audit = runtime(tmp_path)
    tools_runtime.catalog.register(
        make_descriptor("dangerous", dangerous, risk=ToolRisk.HIGH)
    )
    request = ToolGrantRequest(
        role=ToolScope.WORKER,
        session_id="risk-session",
        allowed_tools={"dangerous"},
        allow_high_risk=True,
    )
    assert tools_runtime.build_toolset(request).names == ()

    tools_runtime.grant_high_risk(
        session_id="risk-session",
        tool_name="dangerous",
        granted_by="user-1",
        reason="confirmed operation",
    )
    tools = tools_runtime.build_toolset(request)
    assert (await tools.execute("dangerous", {"value": "run"})).ok
    authorization.revoke("risk-session", "dangerous")
    denied = await tools.execute("dangerous", {"value": "run-again"})
    assert denied.error and denied.error.kind == "permission_denied"
    assert denied.attempts == 0
    with pytest.raises(PermissionError, match="expired"):
        tools_runtime.restore_toolset(tools.snapshot)


@pytest.mark.asyncio
async def test_mcp_transport_failure_reconnects_but_does_not_implicitly_replay(monkeypatch):
    class FailingSession:
        calls = 0

        async def call_tool(self, _name, _arguments):
            self.calls += 1
            raise ConnectionError("transport lost after send")

    class ReplacementSession:
        calls = 0

        async def call_tool(self, _name, _arguments):
            self.calls += 1
            return SimpleNamespace(isError=False, content=[])

    config = MCPServerConfig(command="fake", transport="stdio")
    mcp = MCPRuntime(ToolCatalog())
    first = FailingSession()
    replacement = ReplacementSession()
    mcp._connections["server"] = _Connection(config, SimpleNamespace(), first)

    async def reconnect(name, next_config):
        mcp._connections[name] = _Connection(next_config, SimpleNamespace(), replacement)

    monkeypatch.setattr(mcp, "_connect", reconnect)
    with pytest.raises(ConnectionError):
        await mcp.call_tool("server", "write_operation", {"value": "x"})
    assert first.calls == 1
    assert replacement.calls == 0
