"""Cross-transport validation for attachment-only chat turns."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from server.web.contracts import (
    ChatSendPayload,
    QuotaMeterPricingRuleBody,
    SubmitChatBody,
)


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


def test_meter_pricing_contract_accepts_spec_field_names() -> None:
    body = QuotaMeterPricingRuleBody.model_validate(
        {
            "pricing_key": "qwen/cn-beijing/web-search/turbo",
            "version": "2026-02-27",
            "meter": "search.requests",
            "unit": "call",
            "rate_unit": 1000,
            "rate_micro": 3_000_000,
            "minimum_charge_micro": 25,
            "effective_from": "2026-02-27T00:00:00Z",
            "effective_until": None,
        }
    )

    assert body.capability_type is None
    assert body.min_charge_micro == 25
