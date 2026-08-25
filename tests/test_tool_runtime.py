import asyncio
import json
import sys
from types import ModuleType

import pytest
from langchain_core.messages import AIMessage
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from core.custom_tools import load_custom_tools
from core.mcp_runtime import mcp_tool_name, validate_mcp_url
from core.tool_config import CustomToolsConfig
from core.tool_runtime import (
    ToolCatalog,
    ToolDescriptor,
    ToolGrantRequest,
    ToolRisk,
    ToolRuntime,
    ToolScope,
    ToolSource,
)
from server.tools.runtime_tool_node import RuntimeToolNode


class AddInput(BaseModel):
    left: int = Field(ge=0)
    right: int = Field(ge=0)


async def add(left: int, right: int) -> int:
    return left + right


def descriptor(
    *,
    name="add",
    scopes=None,
    capabilities=None,
    timeout_s=1.0,
    coroutine=add,
    persist_result=True,
):
    def factory():
        return StructuredTool.from_function(
            coroutine=coroutine,
            name=name,
            description="Add non-negative integers",
            args_schema=AddInput,
        )

    return ToolDescriptor(
        name=name,
        description="Add non-negative integers",
        source=ToolSource.BUILTIN,
        scopes=frozenset(scopes or {ToolScope.WORKER}),
        capabilities=frozenset(capabilities or {"math.read"}),
        risk=ToolRisk.LOW,
        read_only=True,
        concurrency_safe=True,
        timeout_s=timeout_s,
        persist_result=persist_result,
        factory=factory,
    )


def test_catalog_rejects_collisions_and_policy_is_role_scoped():
    runtime = ToolRuntime()
    runtime.catalog.register(descriptor())
    with pytest.raises(ValueError, match="collision"):
        runtime.catalog.register(descriptor())

    worker = runtime.build_toolset(
        ToolGrantRequest(role=ToolScope.WORKER, allowed_capabilities={"math.read"})
    )
    coordinator = runtime.build_toolset(
        ToolGrantRequest(role=ToolScope.COORDINATOR, allowed_capabilities={"math.read"})
    )
    assert worker.names == ("add",)
    assert coordinator.names == ()


def test_policy_rejects_unknown_tools_instead_of_silently_dropping_them():
    runtime = ToolRuntime()
    with pytest.raises(ValueError, match="unknown tools"):
        runtime.build_toolset(
            ToolGrantRequest(role=ToolScope.WORKER, allowed_tools={"missing"})
        )


@pytest.mark.asyncio
async def test_executor_uses_pydantic_v2_and_structured_errors():
    runtime = ToolRuntime()
    runtime.catalog.register(descriptor())
    tools = runtime.build_toolset(
        ToolGrantRequest(role=ToolScope.WORKER, allowed_tools={"add"})
    )
    success = await tools.execute("add", {"left": 1, "right": 2})
    invalid = await tools.execute("add", {"left": -1, "right": 2})
    denied = await tools.execute("missing", {})
    assert success.ok and success.output == 3
    assert invalid.error and invalid.error.kind == "validation"
    assert denied.error and denied.error.kind == "permission_denied"


@pytest.mark.asyncio
async def test_executor_preserves_structured_error_code_and_safe_details():
    async def fail_with_json(left: int, right: int) -> str:
        return json.dumps(
            {
                "error": f"cannot add {left} and {right}",
                "code": "image.invalid_input",
                "details": {"media_type": "image/gif", "attempt": 1},
                "internal_debug": "must not be copied",
            }
        )

    async def fail_with_legacy_string(left: int, right: int) -> str:
        return f"Error: legacy failure for {left + right}"

    runtime = ToolRuntime()
    runtime.catalog.register(
        descriptor(
            name="structured_failure",
            coroutine=fail_with_json,
            persist_result=False,
        )
    )
    runtime.catalog.register(
        descriptor(name="legacy_failure", coroutine=fail_with_legacy_string)
    )
    tools = runtime.build_toolset(
        ToolGrantRequest(
            role=ToolScope.WORKER,
            allowed_tools={"structured_failure", "legacy_failure"},
        )
    )

    structured = await tools.execute(
        "structured_failure", {"left": 1, "right": 2}
    )
    legacy = await tools.execute("legacy_failure", {"left": 1, "right": 2})

    assert structured.error is not None
    assert structured.error.kind == "tool_error"
    assert structured.error.message == "cannot add 1 and 2"
    assert structured.error.code == "image.invalid_input"
    assert structured.error.details == {"media_type": "image/gif", "attempt": 1}
    assert "internal_debug" not in structured.error.details
    assert legacy.error is not None
    assert legacy.error.message == "Error: legacy failure for 3"
    assert legacy.error.code == ""
    assert tools.descriptor("structured_failure") is not None
    assert tools.descriptor("structured_failure").persist_result is False
    assert tools.descriptor("legacy_failure").persist_result is True
    assert tools.descriptor("missing") is None


@pytest.mark.asyncio
async def test_runtime_node_executes_the_same_granted_toolset(monkeypatch):
    runtime = ToolRuntime()
    runtime.catalog.register(descriptor())
    tools = runtime.build_toolset(
        ToolGrantRequest(role=ToolScope.WORKER, allowed_tools={"add"})
    )
    monkeypatch.setattr(
        "server.tools.runtime_tool_node.persist_tool_messages",
        lambda messages, _config: messages,
    )
    node = RuntimeToolNode(lambda _config: tools)
    state = {
        "messages": [
            AIMessage(
                content="",
                tool_calls=[{"name": "add", "args": {"left": 2, "right": 4}, "id": "c1"}],
            )
        ]
    }
    output = await node(state, {})
    assert output["messages"][0].content == "6"
    assert output["messages"][0].status == "success"


def test_mcp_names_are_namespaced_stable_and_provider_safe():
    short = mcp_tool_name("github", "search/issues")
    long = mcp_tool_name("very-long-server-" * 8, "very-long-tool-" * 8)
    assert short == "mcp_github_search_issues"
    assert len(long) <= 64
    assert long == mcp_tool_name("very-long-server-" * 8, "very-long-tool-" * 8)


@pytest.mark.asyncio
async def test_remote_mcp_blocks_private_network_by_default():
    with pytest.raises(ValueError, match="blocked private"):
        await validate_mcp_url("http://127.0.0.1:9000/mcp")
    await validate_mcp_url(
        "http://127.0.0.1:9000/mcp", allow_private_network=True
    )


def test_persisted_grant_restores_exact_tools_without_policy_expansion():
    runtime = ToolRuntime()
    runtime.catalog.register(descriptor(name="add"))
    runtime.catalog.register(descriptor(name="sum_more"))
    original = runtime.build_toolset(
        ToolGrantRequest(role=ToolScope.WORKER, allowed_tools={"add"})
    )
    restored = runtime.restore_toolset(original.snapshot)
    assert restored.names == ("add",)


def test_catalog_uses_prompt_priority_without_changing_grant_rules():
    runtime = ToolRuntime()
    runtime.catalog.register(descriptor(name="general_tool"))
    runtime.catalog.register(
        descriptor(name="nlp_ranked", capabilities={"nlp.analyze"}).model_copy(
            update={"source": ToolSource.CUSTOM, "category": "nlp", "prompt_priority": 200}
        )
    )

    worker = runtime.build_toolset(
        ToolGrantRequest(role=ToolScope.WORKER, allowed_capabilities={"math.read", "nlp.analyze"})
    )

    assert worker.names == ("nlp_ranked", "general_tool")


def test_custom_provider_requires_manifest_and_applies_nlp_contract(monkeypatch):
    module_name = "test_nlp_custom_provider"
    module = ModuleType(module_name)
    module.TOOL_MANIFEST = {
        "id": "test-nlp-tools",
        "version": "2.0",
        "category": "nlp",
        "prompt_priority": 250,
        "scopes": ["worker"],
        "capabilities": ["nlp.extract"],
        "risk": "low",
    }
    module.TOOLS = [descriptor(name="nlp_extract_terms").instantiate()]
    monkeypatch.setitem(sys.modules, module_name, module)
    catalog = ToolCatalog()

    assert load_custom_tools(CustomToolsConfig(modules=[module_name]), catalog) == ["nlp_extract_terms"]
    registered = catalog.get("nlp_extract_terms")
    assert registered is not None
    assert registered.provider_id == "test-nlp-tools"
    assert registered.version == "2.0"
    assert registered.prompt_priority == 250
    assert registered.category == "nlp"
    assert registered.capabilities == frozenset({"custom.nlp_extract_terms", "nlp.extract"})


def test_nlp_custom_tool_without_namespace_is_rejected(monkeypatch):
    module_name = "test_invalid_nlp_custom_provider"
    module = ModuleType(module_name)
    module.TOOL_MANIFEST = {
        "id": "invalid-nlp-tools", "version": "1.0", "category": "nlp",
    }
    module.TOOLS = [descriptor(name="extract_terms").instantiate()]
    monkeypatch.setitem(sys.modules, module_name, module)

    with pytest.raises(ValueError, match="nlp_ namespace"):
        load_custom_tools(CustomToolsConfig(modules=[module_name]), ToolCatalog())
