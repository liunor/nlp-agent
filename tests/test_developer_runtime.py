import pytest


@pytest.mark.asyncio
async def test_mcp_connection_test_reuses_stored_hidden_credentials(monkeypatch):
    from core import mcp_runtime
    from server.web import developer_runtime

    captured: dict[str, object] = {}

    class FakeCatalog:
        def names(self) -> list[str]:
            return []

    class FakeRuntime:
        def __init__(self, _catalog: FakeCatalog) -> None:
            pass

        async def connect_all(self, configs: dict[str, object]) -> None:
            captured.update(configs)

        async def close(self) -> None:
            pass

    monkeypatch.setattr(developer_runtime, "ToolCatalog", FakeCatalog)
    monkeypatch.setattr(mcp_runtime, "MCPRuntime", FakeRuntime)
    monkeypatch.setattr(
        developer_runtime,
        "load_runtime_overrides",
        lambda: {
            "tools": {
                "mcp_servers": {
                    "secured": {
                        "env": {"MCP_TOKEN": "secret"},
                        "headers": {"Authorization": "Bearer secret"},
                    }
                }
            }
        },
    )

    result = await developer_runtime.test_mcp_server(
        "secured",
        {"transport": "stdio", "command": "python", "args": []},
    )

    config = captured["secured"]
    assert config.env == {"MCP_TOKEN": "secret"}
    assert config.headers == {"Authorization": "Bearer secret"}
    assert result == {"ok": True, "server": "secured", "tools": []}


def test_developer_tool_snapshot_bootstraps_local_nlp_tools_for_remote_web(monkeypatch):
    """The Web process must list local custom tools even when the Agent engine is remote."""
    from core import tool_registry
    from core.tool_config import AgentRuntimeConfig, CustomToolsConfig, ToolRuntimeConfig
    from core.tool_runtime import ToolRuntime
    from server.tools.tool_manager import register_builtin_tools
    from server.web import developer

    runtime = ToolRuntime()
    register_builtin_tools(runtime.catalog)
    monkeypatch.setattr(tool_registry, "global_tool_runtime", runtime)
    manager = tool_registry.PhysicalToolManager()
    manager.config = AgentRuntimeConfig(
        tools=ToolRuntimeConfig(custom=CustomToolsConfig(modules=["core.nlp_tools"]))
    )
    monkeypatch.setattr(tool_registry, "physical_tool_manager", manager)

    snapshot = developer._tool_snapshot()

    assert {
        "nlp_tfidf_analyzer",
        "nlp_precision_recall_curve",
        "nlp_precision_at_n",
        "nlp_ngram_analyzer",
        "nlp_bleu_score",
    }.issubset({item["name"] for item in snapshot["items"]})
