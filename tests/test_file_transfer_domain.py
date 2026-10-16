from __future__ import annotations

import pytest

from server.storage.transfer_domain import (
    InvalidTransferTransition,
    next_available_file_name,
    transition_transfer,
)


def test_only_the_recipient_can_accept_or_reject_a_pending_transfer() -> None:
    assert transition_transfer("pending", "accept", actor="recipient") == "accepted"
    assert transition_transfer("pending", "reject", actor="recipient") == "rejected"
    with pytest.raises(InvalidTransferTransition):
        transition_transfer("pending", "accept", actor="sender")


def test_only_the_sender_can_cancel_and_resolved_requests_are_immutable() -> None:
    assert transition_transfer("pending", "cancel", actor="sender") == "cancelled"
    with pytest.raises(InvalidTransferTransition):
        transition_transfer("pending", "cancel", actor="recipient")
    with pytest.raises(InvalidTransferTransition):
        transition_transfer("accepted", "reject", actor="recipient")


def test_received_files_get_a_deterministic_non_conflicting_name() -> None:
    existing = {"报告.pdf", "报告 (1).pdf", "报告 (2).pdf"}
    assert next_available_file_name("报告.pdf", existing) == "报告 (3).pdf"
    assert next_available_file_name("README", {"README"}) == "README (1)"
