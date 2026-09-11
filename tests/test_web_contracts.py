"""Cross-transport validation for attachment-only chat turns."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from server.web.contracts import ChatSendPayload, SubmitChatBody


@pytest.mark.parametrize("model", [SubmitChatBody, ChatSendPayload])
def test_chat_contract_accepts_attachment_without_text(model) -> None:
    payload = model(
        session_id="session-1",
        content="",
        attachments=[{"file_name": "safe-image.png"}],
    )

    assert payload.content == ""
    assert payload.attachments[0].file_name == "safe-image.png"


@pytest.mark.parametrize("model", [SubmitChatBody, ChatSendPayload])
def test_chat_contract_rejects_request_without_text_or_attachment(model) -> None:
    with pytest.raises(ValidationError, match="content 或 attachments"):
        model(session_id="session-1", content="")


@pytest.mark.parametrize("model", [SubmitChatBody, ChatSendPayload])
def test_chat_contract_accepts_structured_knowledge_book_context(model) -> None:
    payload = model(
        session_id="session-1",
        content="这是什么意思？",
        knowledge_book_context={
            "workspace_id": "workspace-1",
            "topic_id": "topic-1",
            "topic_name": "基础",
            "knowledge_point_id": "point-1",
            "title": "词法分析",
            "heading": "核心概念",
            "selected_text": "词元",
            "content_markdown": "## 核心概念\n\n词元",
        },
    )

    assert payload.knowledge_book_context.knowledge_point_id == "point-1"
    assert payload.knowledge_book_context.selected_text == "词元"
