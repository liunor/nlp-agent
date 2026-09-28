"""Knowledge-book context is exposed through a read-only Nova tool."""

from __future__ import annotations

from core.tool_runtime import ToolCatalog
from server.tools.api.knowledge_book_tool import get_knowledge_book_context
from server.tools.tool_manager import register_builtin_tools


def test_knowledge_book_context_tool_is_registered_for_coordinator() -> None:
    catalog = ToolCatalog()
    register_builtin_tools(catalog)

    descriptor = catalog.get("get_knowledge_book_context")

    assert descriptor is not None
    assert descriptor.read_only is True
    assert descriptor.concurrency_safe is True
    assert descriptor.scopes


def test_knowledge_book_context_tool_returns_context_from_runtime_config() -> None:
    context = {
        "topic_name": "基础",
        "title": "词法分析",
        "heading": "核心概念",
        "selected_text": "词元",
        "code": "print('hello')",
        "language": "python",
        "content_markdown": "## 核心概念\n\n词元\n\n" + chr(96) * 3 + "python\nprint('hello')\n" + chr(96) * 3,
    }
    result = get_knowledge_book_context.invoke({
        "focus": "这是什么意思？",
    }, config={"configurable": {"knowledge_book_context": context}})

    assert "词法分析" in result
    assert "词元" in result
    assert "print('hello')" in result
