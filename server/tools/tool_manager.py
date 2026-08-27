from server.tools.api.file_read_tool import read_local_file
from server.tools.api.image_analyze_tool import image_analyze
from server.tools.api.time_tool import get_current_time
from server.tools.api.web_fetch_tool import web_fetch
from server.sandbox.model_tools import MODEL_SANDBOX_TOOLS
from core.tool_runtime import (
    ToolCatalog,
    ToolDescriptor,
    ToolRisk,
    ToolRetryPolicy,
    ToolLockScope,
    ToolScope,
    ToolSource,
    global_tool_runtime,
)


ALL_AVAILABLE_TOOLS = [
    read_local_file,
    get_current_time,
    web_fetch,
]


def register_builtin_tools(catalog: ToolCatalog | None = None) -> list[str]:
    catalog = catalog or global_tool_runtime.catalog
    definitions = [
        ToolDescriptor(
            name=read_local_file.name,
            description=read_local_file.description,
            source=ToolSource.BUILTIN,
            provider="core",
            scopes=frozenset({ToolScope.COORDINATOR, ToolScope.WORKER}),
            capabilities=frozenset({"artifact.read", "file.read_limited"}),
            read_only=True,
            concurrency_safe=True,
            timeout_s=10,
            retry=ToolRetryPolicy(max_attempts=2),
            factory=lambda: read_local_file.model_copy(deep=True),
        ),
        ToolDescriptor(
            name=get_current_time.name,
            description=get_current_time.description,
            source=ToolSource.BUILTIN,
            provider="core",
            scopes=frozenset({ToolScope.COORDINATOR, ToolScope.WORKER}),
            capabilities=frozenset({"system.time"}),
            read_only=True,
            concurrency_safe=True,
            timeout_s=5,
            retry=ToolRetryPolicy(max_attempts=2),
            factory=lambda: get_current_time.model_copy(deep=True),
        ),
        ToolDescriptor(
            name=web_fetch.name,
            description=web_fetch.description,
            source=ToolSource.BUILTIN,
            provider="web-access",
            scopes=frozenset({ToolScope.WORKER}),
            capabilities=frozenset({"web.fetch"}),
            risk=ToolRisk.MEDIUM,
            read_only=True,
            concurrency_safe=True,
            timeout_s=35,
            max_concurrency=2,
            retry=ToolRetryPolicy(max_attempts=2),
            factory=lambda: web_fetch.model_copy(deep=True),
        ),
        ToolDescriptor(
            name=image_analyze.name,
            description=image_analyze.description,
            source=ToolSource.BUILTIN,
            provider="vision-router",
            scopes=frozenset({ToolScope.WORKER}),
            capabilities=frozenset({"image.analyze"}),
            risk=ToolRisk.MEDIUM,
            read_only=True,
            concurrency_safe=True,
            timeout_s=90,
            max_concurrency=2,
            # The Model Runtime already owns retry and fallback for VLM calls.
            # Retrying the whole vision pipeline here can duplicate OCR work and
            # paid model requests.
            retry=ToolRetryPolicy(max_attempts=1),
            persist_result=False,
            factory=lambda: image_analyze.model_copy(deep=True),
        ),
    ]
    sandbox_policies = {
        "sandbox_status": dict(
            risk=ToolRisk.LOW,
            read_only=True,
            concurrency_safe=True,
            capabilities=frozenset({"sandbox.observe"}),
            timeout_s=15,
        ),
        "sandbox_run_scratch": dict(
            risk=ToolRisk.MEDIUM,
            capabilities=frozenset({"sandbox.scratch"}),
            timeout_s=60,
            lock_scope=ToolLockScope.SESSION,
        ),
        "sandbox_explain_execution": dict(
            risk=ToolRisk.LOW,
            read_only=True,
            concurrency_safe=True,
            capabilities=frozenset({"sandbox.observe"}),
            timeout_s=15,
        ),
        "sandbox_interrupt_own": dict(
            risk=ToolRisk.MEDIUM,
            capabilities=frozenset({"sandbox.interrupt"}),
            timeout_s=30,
            lock_scope=ToolLockScope.SESSION,
        ),
        "sandbox_run_active_kernel": dict(
            risk=ToolRisk.HIGH,
            capabilities=frozenset({"sandbox.active_kernel"}),
            timeout_s=180,
            exclusive=True,
            lock_scope=ToolLockScope.SESSION,
        ),
        "sandbox_reset": dict(
            risk=ToolRisk.CRITICAL,
            capabilities=frozenset({"sandbox.reset"}),
            timeout_s=180,
            exclusive=True,
            lock_scope=ToolLockScope.SESSION,
        ),
    }
    for sandbox_tool in MODEL_SANDBOX_TOOLS:
        policy = sandbox_policies[sandbox_tool.name]
        definitions.append(
            ToolDescriptor(
                name=sandbox_tool.name,
                description=sandbox_tool.description,
                source=ToolSource.BUILTIN,
                provider="sandbox",
                scopes=frozenset({ToolScope.COORDINATOR, ToolScope.WORKER}),
                factory=lambda sandbox_tool=sandbox_tool: sandbox_tool.model_copy(deep=True),
                **policy,
            )
        )
    registered: list[str] = []
    for descriptor in definitions:
        existing = catalog.get(descriptor.name)
        if existing is None:
            catalog.register(descriptor)
            registered.append(descriptor.name)
        elif existing.source != ToolSource.BUILTIN:
            raise ValueError(f"built-in tool collision: {descriptor.name}")
    return registered


def _register_compactable_tools() -> None:
    """注册可安全重取结果的通用工具。"""

    try:
        from server.agent.compression.micro_compact import register_compactable_tool

        for tool in ALL_AVAILABLE_TOOLS:
            register_compactable_tool(tool.name)
    except Exception:
        pass


_register_compactable_tools()
register_builtin_tools()
