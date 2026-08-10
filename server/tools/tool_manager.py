from server.tools.api.file_read_tool import read_local_file
from server.tools.api.time_tool import get_current_time
from server.tools.api.web_fetch_tool import web_fetch
from server.tools.api.web_search_tool import web_search
from core.tool_runtime import (
    ToolCatalog,
    ToolDescriptor,
    ToolRisk,
    ToolRetryPolicy,
    ToolScope,
    ToolSource,
    global_tool_runtime,
)


ALL_AVAILABLE_TOOLS = [
    read_local_file,
    get_current_time,
    web_search,
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
            name=web_search.name,
            description=web_search.description,
            source=ToolSource.BUILTIN,
            provider="web-access",
            scopes=frozenset({ToolScope.WORKER}),
            capabilities=frozenset({"web.search"}),
            risk=ToolRisk.LOW,
            read_only=True,
            concurrency_safe=True,
            timeout_s=25,
            max_concurrency=4,
            retry=ToolRetryPolicy(max_attempts=3, base_delay_s=0.5),
            factory=lambda: web_search.model_copy(deep=True),
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
    ]
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
