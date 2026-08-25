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
