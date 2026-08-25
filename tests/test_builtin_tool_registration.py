from core.tool_runtime import ToolCatalog, ToolRisk, ToolScope, ToolSource
from server.tools.api.image_analyze_tool import image_analyze
from server.tools.tool_manager import ALL_AVAILABLE_TOOLS, register_builtin_tools


def test_image_analyze_builtin_descriptor_is_worker_scoped_and_read_only():
    catalog = ToolCatalog()

    registered = register_builtin_tools(catalog)
    descriptor = catalog.get("image_analyze")

    assert "image_analyze" in registered
    assert descriptor is not None
    assert descriptor.source is ToolSource.BUILTIN
    assert descriptor.provider == "vision-router"
    assert descriptor.scopes == frozenset({ToolScope.WORKER})
    assert descriptor.capabilities == frozenset({"image.analyze"})
    assert descriptor.risk is ToolRisk.MEDIUM
    assert descriptor.read_only is True
    assert descriptor.concurrency_safe is True
    assert descriptor.timeout_s == 90
    assert descriptor.max_concurrency == 2
    assert descriptor.retry.max_attempts == 1
    assert descriptor.persist_result is False
    assert descriptor.factory().name == image_analyze.name

    # Visual analysis can be expensive and source uploads may expire, so it is
    # deliberately excluded from the generic micro-compaction/re-fetch list.
    assert image_analyze not in ALL_AVAILABLE_TOOLS
